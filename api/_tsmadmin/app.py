"""
TSM Token Manager - WSGI application (standard library only).

Runs unchanged on Vercel (api/index.py exposes `app`) and locally
(local_server.py, emergency/offline mode). All routes live under /api and are
selected with ?op=<name> so no URL rewriting is needed on Vercel.

Roles
  owner    : TSM_OWNERS (env). Everything a manager can do + add/remove managers
             + replace the token list from an uploaded CSV.
  manager  : approved GitHub login in managers.json. Issue, edit/extend, revoke,
             restore, view, download. All managers have the same access.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie

from . import tokens as T
from .store import Conflict, GitHubStore, LocalStore, StoreError, manifest_revoked, manifest_with_revoked

# Current TSM public key (same as tsm_dev_guidelines/tsm_auth/_public_key.py).
DEFAULT_PUBLIC_KEY_B64 = "wqWCSalOD82OV0xPaZYASaJTxGZzzkWhqxS4MIf7EtA"
SESSION_HOURS = 12


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

class Config:
    def __init__(self, env=None):
        e = env if env is not None else os.environ
        self.mode = e.get("TSM_MODE", "github").lower()
        self.owners = [x.strip().lower() for x in e.get("TSM_OWNERS", "").split(",") if x.strip()]
        self.public_key = T.b64url_decode(e.get("TSM_PUBLIC_KEY_B64", DEFAULT_PUBLIC_KEY_B64))
        self.live_manifest_url = e.get("TSM_LIVE_MANIFEST_URL", "https://auth.tsm-nextgen.com/tsm_manifest.json")
        self.default_expiry = e.get("TSM_DEFAULT_EXPIRY", "")
        self.contact = e.get("TSM_CONTACT", "TSM Model Development Team")
        self._seed_b64 = e.get("TSM_SEED_B64", "")
        self._seed_file = e.get("TSM_SEED_FILE", "")
        self.session_secret = (e.get("TSM_SESSION_SECRET") or "").encode()
        self.gh_client_id = e.get("GITHUB_CLIENT_ID", "")
        self.gh_client_secret = e.get("GITHUB_CLIENT_SECRET", "")
        self.local_user = e.get("TSM_LOCAL_USER") or os.environ.get("USERNAME") or "local-owner"
        # Local emergency copy issues from its own id series (tsm-YYYY-L0001...).
        self.jti_series = e.get("TSM_JTI_SERIES", "L" if self.mode == "local" else "")
        if self.mode == "local":
            self.store = LocalStore(e.get("TSM_LOCAL_REGISTRY_DIR", "../tsm-token-registry"),
                                    e.get("TSM_LOCAL_MANIFEST", "../tsm-auth-endpoint/public/tsm_manifest.json"))
        else:
            self.store = GitHubStore(
                e.get("TSM_GH_TOKEN", ""),
                e.get("TSM_REGISTRY_REPO", "TSMv6/tsm-token-registry"), e.get("TSM_REGISTRY_BRANCH", "main"),
                e.get("TSM_MANIFEST_REPO", "TSMv6/tsm-auth-endpoint"), e.get("TSM_MANIFEST_BRANCH", "master"),
                e.get("TSM_MANIFEST_PATH", "public/tsm_manifest.json"))

    def seed(self) -> bytes:
        if self._seed_b64:
            seed = base64.b64decode(self._seed_b64 + "=" * (-len(self._seed_b64) % 4))
        elif self._seed_file:
            seed = open(self._seed_file, "rb").read()
        else:
            raise HttpError(500, "Signing seed is not configured (TSM_SEED_B64 / TSM_SEED_FILE).")
        from .ed25519 import public_key
        if len(seed) != 32 or public_key(seed) != self.public_key:
            raise HttpError(500, "Configured signing seed does not match the TSM public key.")
        return seed

    def problems(self):
        out = []
        if self.mode != "local":
            if not self.gh_client_id or not self.gh_client_secret:
                out.append("GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET not set (login disabled)")
            if len(self.session_secret) < 16:
                out.append("TSM_SESSION_SECRET missing or too short")
            if not self.store.token:
                out.append("TSM_GH_TOKEN not set (cannot read/write the registry)")
            if not self.owners:
                out.append("TSM_OWNERS not set")
        if not self._seed_b64 and not self._seed_file:
            out.append("TSM_SEED_B64 not set (cannot issue tokens)")
        return out


# --------------------------------------------------------------------------- #
# Sessions (HMAC-signed cookie)                                               #
# --------------------------------------------------------------------------- #

def _sign(secret: bytes, data: str) -> str:
    return T.b64url(hmac.new(secret, data.encode(), hashlib.sha256).digest())


def make_session(cfg, user) -> str:
    body = T.b64url(json.dumps({**user, "exp": int(time.time()) + SESSION_HOURS * 3600}).encode())
    return f"{body}.{_sign(cfg.session_secret, body)}"


def read_session(cfg, value):
    try:
        body, sig = value.split(".", 1)
        if not hmac.compare_digest(sig, _sign(cfg.session_secret, body)):
            return None
        data = json.loads(T.b64url_decode(body))
        return data if data.get("exp", 0) > time.time() else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Registry state                                                              #
# --------------------------------------------------------------------------- #

class State:
    """Registry contents. tokens.csv stores NO token strings when they can be
    regenerated (Ed25519 is deterministic): the token column is filled from the
    seed on load and blanked again on save, so the registry repo holds no usable
    credentials. Rows whose token can't be reproduced keep it verbatim."""

    def __init__(self, files, version, seed=None):
        self.version = version
        self.seed = seed
        self.tokens = T.read_csv(files.get("tokens.csv", ""))
        for r in self.tokens:
            if not (r.get("token") or "").strip() and seed:
                r["token"] = self._regen(r)
        self.audit = T.read_csv(files.get("audit.csv", ""))
        m = json.loads(files.get("managers.json") or "{}")
        self.managers = m.get("managers", [])

    def _regen(self, r):
        return T.make_token(self.seed, r.get("email", ""), r.get("expires", ""),
                            r.get("jti", ""), r.get("tool") or "*")

    def files(self):
        stored = []
        for r in self.tokens:
            r2 = dict(r)
            if self.seed and r2.get("token") and r2["token"] == self._regen(r2):
                r2["token"] = ""
            stored.append(r2)
        return {
            "tokens.csv": T.write_csv(stored, T.TOKEN_FIELDS),
            "audit.csv": T.write_csv(self.audit, T.AUDIT_FIELDS),
            "managers.json": json.dumps({"managers": self.managers}, indent=2) + "\n",
        }

    def log(self, actor, action, jti="", email="", details=""):
        self.audit.append({"time": T.now_iso(), "actor": actor, "action": action,
                           "jti": jti, "email": email, "details": details})

    def find(self, jti, email=""):
        """One row by token id (+ email when an id is shared by several people)."""
        rows = [r for r in self.tokens if r.get("jti") == jti]
        if email and len(rows) > 1:
            rows = [r for r in rows if r.get("email", "").lower() == email.lower()]
        if not rows:
            raise HttpError(404, f"Token {jti} not found.")
        if len(rows) > 1:
            raise HttpError(409, f"Token id {jti} is shared by several people; pick the row.")
        return rows[0]

    def sharing(self, jti):
        return [r for r in self.tokens if r.get("jti") == jti]

    def active_managers(self):
        return [m for m in self.managers if not m.get("removed_at")]

    def revoked_ids(self):
        return [r["jti"] for r in self.tokens if (r.get("revoked") or "").strip()]


def load(cfg):
    files, version = cfg.store.read_registry()
    try:
        seed = cfg.seed()
    except HttpError:
        seed = None  # tokens not regenerable; registry still viewable
    return State(files, version, seed)


def mutate(cfg, fn, message, unrevoke=()):
    """Load -> fn(state) -> commit; retry on concurrent change. Then add any
    newly revoked ids to the public manifest (and remove `unrevoke` ids)."""
    for _ in range(4):
        st = load(cfg)
        result = fn(st)
        try:
            cfg.store.write_registry(st.files(), st.version, message)
            break
        except Conflict:
            continue
    else:
        raise HttpError(409, "Another manager changed the data at the same moment; please try again.")
    sync = sync_manifest(cfg, st, message, unrevoke)
    return result, sync


def sync_manifest(cfg, st, message="sync", unrevoke=()):
    """Add every registry-revoked id to the manifest. Ids are only ever REMOVED
    from the manifest when explicitly restored (`unrevoke`), never because the
    registry happens not to list them - a sync must not unblock anyone."""
    try:
        for _ in range(3):
            text, mver = cfg.store.read_manifest()
            current = manifest_revoked(text)
            desired = [j for j in current if j not in unrevoke]
            desired += [j for j in st.revoked_ids() if j not in desired and j not in unrevoke]
            if desired == current:
                return {"changed": False, "ok": True}
            try:
                cfg.store.write_manifest(manifest_with_revoked(text, desired), mver,
                                         f"revoked_token_ids: {message}")
                return {"changed": True, "ok": True}
            except Conflict:
                continue
        return {"ok": False, "error": "manifest kept changing; use Sync manifest"}
    except (StoreError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- #
# Handlers                                                                    #
# --------------------------------------------------------------------------- #

def _req(body, key, required=True):
    v = (body.get(key) or "").strip() if isinstance(body.get(key), str) else body.get(key)
    if required and not v:
        raise HttpError(400, f"'{key}' is required.")
    return v or ""


def _row_public(r):
    out = {k: r.get(k, "") for k in T.TOKEN_FIELDS}
    out["status"] = T.status(r)
    return out


def op_state(cfg, user, body, q):
    st = load(cfg)
    return {
        "tokens": [_row_public(r) for r in st.tokens],
        "managers": st.managers,
        "owners": cfg.owners if cfg.mode != "local" else [cfg.local_user],
        "audit": st.audit[-2000:],
        "version": st.version,
        "today": T.today(),
        "next_jti": T.next_jti(st.tokens, series=cfg.jti_series),
        "default_expiry": cfg.default_expiry or f"{int(T.today()[:4]) + 1}-12-31",
        "contact": cfg.contact,
    }


def op_issue(cfg, user, body, q):
    name = _req(body, "name")
    email = _req(body, "email").lower()
    expires = T.norm_date(_req(body, "expires"))
    notes = _req(body, "notes", False)
    if expires < T.today():
        raise HttpError(400, "Expiry date is in the past.")
    if "@" not in email:
        raise HttpError(400, "Enter a valid email address.")
    seed = cfg.seed()

    def fn(st):
        jti = T.next_jti(st.tokens, series=cfg.jti_series)
        if any(r.get("jti") == jti for r in st.tokens):  # belt and braces
            raise HttpError(409, f"Token id {jti} already exists; please try again.")
        tok = T.make_token(seed, email, expires, jti)
        row = {"name": name, "email": email, "tool": "*", "jti": jti, "issued": T.today(),
               "expires": expires, "revoked": "", "token": tok, "issued_by": user["login"],
               "modified_by": "", "modified_at": "", "revoked_by": "", "notes": notes}
        st.tokens.append(row)
        st.log(user["login"], "issue", jti, email, f"expires {expires}" + (f"; {notes}" if notes else ""))
        return row

    row, sync = mutate(cfg, fn, f"issue token for {email} by {user['login']}")
    return {"token": _row_public(row), "manifest": sync}


def op_update(cfg, user, body, q):
    jti = _req(body, "jti")

    def fn(st):
        r = st.find(jti, body.get("row_email", ""))
        changes = []
        name = body.get("name")
        email = (body.get("email") or "").strip().lower()
        expires = T.norm_date(body.get("expires") or "") if body.get("expires") else ""
        notes = body.get("notes")
        if name is not None and name.strip() != r.get("name", ""):
            changes.append(f"name '{r.get('name', '')}' -> '{name.strip()}'")
            r["name"] = name.strip()
        if notes is not None and notes.strip() != r.get("notes", ""):
            changes.append("notes updated")
            r["notes"] = notes.strip()
        resign = False
        if email and email != r.get("email", ""):
            if "@" not in email:
                raise HttpError(400, "Enter a valid email address.")
            changes.append(f"email {r.get('email', '')} -> {email}")
            r["email"] = email
            resign = True
        if expires and expires != r.get("expires", ""):
            if expires < T.today():
                raise HttpError(400, "Expiry date is in the past.")
            changes.append(f"expires {r.get('expires', '')} -> {expires}")
            r["expires"] = expires
            resign = True
        if not changes:
            raise HttpError(400, "Nothing changed.")
        if resign:
            r["token"] = T.make_token(cfg.seed(), r["email"], r["expires"], r["jti"], r.get("tool") or "*")
            changes.append("new token string issued (send it to the user)")
        r["modified_by"], r["modified_at"] = user["login"], T.now_iso()
        st.log(user["login"], "modify", jti, r.get("email", ""), "; ".join(changes))
        return r, resign

    (row, resigned), sync = mutate(cfg, fn, f"modify {jti} by {user['login']}")
    return {"token": _row_public(row), "resigned": resigned, "manifest": sync}


def op_revoke(cfg, user, body, q):
    jti = _req(body, "jti")
    reason = _req(body, "reason", False)

    def fn(st):
        r = st.find(jti, body.get("row_email", ""))
        if (r.get("revoked") or "").strip():
            raise HttpError(400, f"{jti} is already revoked.")
        # Revocation is per token id in the manifest, so every row sharing the
        # id is blocked; record that truthfully on each row.
        for x in st.sharing(jti):
            if not (x.get("revoked") or "").strip():
                x["revoked"], x["revoked_by"] = T.today(), user["login"]
                x["modified_by"], x["modified_at"] = user["login"], T.now_iso()
                extra = "" if x is r else f"shares id with {r.get('email', '')}"
                st.log(user["login"], "revoke", jti, x.get("email", ""),
                       "; ".join(v for v in (reason, extra) if v))
        return r

    row, sync = mutate(cfg, fn, f"revoke {jti} by {user['login']}")
    return {"token": _row_public(row), "manifest": sync}


def op_restore(cfg, user, body, q):
    jti = _req(body, "jti")
    reason = _req(body, "reason", False)

    def fn(st):
        r = st.find(jti, body.get("row_email", ""))
        if not (r.get("revoked") or "").strip():
            raise HttpError(400, f"{jti} is not revoked.")
        for x in st.sharing(jti):
            if (x.get("revoked") or "").strip():
                prev = f"was revoked {x['revoked']} by {x.get('revoked_by') or '?'}"
                x["revoked"], x["revoked_by"] = "", ""
                x["modified_by"], x["modified_at"] = user["login"], T.now_iso()
                st.log(user["login"], "restore", jti, x.get("email", ""),
                       "; ".join(v for v in (prev, reason) if v))
        return r

    row, sync = mutate(cfg, fn, f"restore {jti} by {user['login']}", unrevoke={jti})
    return {"token": _row_public(row), "manifest": sync}


def _require_owner(user):
    if user.get("role") != "owner":
        raise HttpError(403, "Only the owner can do this.")


def op_manager_add(cfg, user, body, q):
    _require_owner(user)
    login = _req(body, "login").lstrip("@")
    name = _req(body, "name", False)

    def fn(st):
        for m in st.active_managers():
            if m["login"].lower() == login.lower():
                raise HttpError(400, f"{login} is already a manager.")
        m = {"login": login, "name": name, "added_by": user["login"], "added_at": T.now_iso(),
             "removed_by": "", "removed_at": ""}
        st.managers.append(m)
        st.log(user["login"], "manager-add", details=f"{login}" + (f" ({name})" if name else ""))
        return m

    m, _ = mutate(cfg, fn, f"add manager {login} by {user['login']}")
    return {"manager": m}


def op_manager_remove(cfg, user, body, q):
    _require_owner(user)
    login = _req(body, "login")

    def fn(st):
        for m in st.active_managers():
            if m["login"].lower() == login.lower():
                m["removed_by"], m["removed_at"] = user["login"], T.now_iso()
                st.log(user["login"], "manager-remove", details=login)
                return m
        raise HttpError(404, f"{login} is not an active manager.")

    m, _ = mutate(cfg, fn, f"remove manager {login} by {user['login']}")
    return {"manager": m}


def op_import(cfg, user, body, q):
    """Owner: replace the token list with an uploaded CSV. apply=false -> preview."""
    _require_owner(user)
    text = body.get("csv") or ""
    rows, problems = T.normalise_upload(text, cfg.public_key, user["login"])
    try:
        mark_manifest_revoked(rows, manifest_revoked(cfg.store.read_manifest()[0]), problems)
    except (StoreError, OSError, ValueError) as e:
        problems.append(f"could not read the manifest to cross-check revocations: {e}")
    st = load(cfg)
    # Rows are identified by (token id, email): a few legacy ids are shared.
    old = {(r["jti"], r["email"].lower()): r for r in st.tokens}
    new = {(r["jti"], r["email"].lower()): r for r in rows}
    shown = lambda ks: sorted(f"{j} ({e})" for j, e in ks)  # noqa: E731
    cmp_keys = ("name", "expires", "revoked", "token", "notes")
    added = shown(set(new) - set(old))
    removed = shown(set(old) - set(new))
    changed = shown(k for k in set(new) & set(old)
                    if any((new[k].get(f) or "") != (old[k].get(f) or "") for f in cmp_keys))
    preview = {"rows": len(rows), "added": added, "removed": removed, "changed": changed,
               "problems": problems}
    if not body.get("apply"):
        return {"preview": preview}
    if not rows:
        raise HttpError(400, "The uploaded file has no valid tokens; nothing was changed.")

    def fn(st2):
        if st2.version != st.version:
            raise HttpError(409, "The data changed since your preview; preview again.")
        st2.tokens = rows
        st2.log(user["login"], "import",
                details=f"replaced token list from upload: {len(rows)} rows, +{len(added)} "
                        f"-{len(removed)} ~{len(changed)}, {len(problems)} skipped")
        return preview

    _, sync = mutate(cfg, fn, f"replace token list from upload by {user['login']}")
    return {"preview": preview, "applied": True, "manifest": sync}


def mark_manifest_revoked(rows, manifest_ids, problems):
    """Rows whose id is revoked in the live manifest ARE blocked; say so."""
    ids = set(manifest_ids)
    for r in rows:
        if r["jti"] in ids and not (r.get("revoked") or "").strip():
            r["revoked"], r["revoked_by"] = T.today(), "manifest"
            problems.append(f"{r['jti']} ({r['email']}) is revoked in the published manifest - marked revoked")


def op_manifest_status(cfg, user, body, q):
    st = load(cfg)
    want = set(st.revoked_ids())
    out = {"registry_revoked": sorted(want)}
    try:
        text, _ = cfg.store.read_manifest()
        repo_ids = set(manifest_revoked(text))
        out["repo_missing"] = sorted(want - repo_ids)
        out["repo_extra"] = sorted(repo_ids - want)
    except Exception as e:  # noqa: BLE001
        out["repo_error"] = str(e)
    try:
        url = cfg.live_manifest_url + ("&" if "?" in cfg.live_manifest_url else "?") + f"t={int(time.time())}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tsm-token-admin"}),
                                    timeout=8) as r:
            live_ids = set(json.loads(r.read()).get("revoked_token_ids", []))
        out["live_missing"] = sorted(want - live_ids)
        out["live_extra"] = sorted(live_ids - want)
    except Exception as e:  # noqa: BLE001
        out["live_error"] = str(e)
    out["in_sync"] = not out.get("repo_missing") and not out.get("live_missing") \
        and "repo_error" not in out and "live_error" not in out
    return out


def op_manifest_sync(cfg, user, body, q):
    return {"manifest": sync_manifest(cfg, load(cfg), f"manual sync by {user['login']}")}


def op_publish(cfg, user, body, q):
    if cfg.mode != "local":
        raise HttpError(400, "Publish is only for the local copy.")
    try:
        return {"output": cfg.store.publish(user["login"])}
    except StoreError as e:
        raise HttpError(500, str(e))


OPS_GET = {"state": op_state, "manifest_status": op_manifest_status}
OPS_POST = {"issue": op_issue, "update": op_update, "revoke": op_revoke, "restore": op_restore,
            "manager_add": op_manager_add, "manager_remove": op_manager_remove,
            "import": op_import, "manifest_sync": op_manifest_sync, "publish": op_publish}


# --------------------------------------------------------------------------- #
# WSGI plumbing                                                               #
# --------------------------------------------------------------------------- #

def _cookies(environ):
    c = SimpleCookie()
    try:
        c.load(environ.get("HTTP_COOKIE", ""))
    except Exception:  # noqa: BLE001
        pass
    return {k: v.value for k, v in c.items()}


def _base_url(environ):
    scheme = environ.get("HTTP_X_FORWARDED_PROTO") or environ.get("wsgi.url_scheme", "https")
    host = environ.get("HTTP_X_FORWARDED_HOST") or environ.get("HTTP_HOST", "localhost")
    return f"{scheme}://{host}"


def _set_cookie(name, value, max_age, secure):
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return ("Set-Cookie", "; ".join(parts))


def _current_user(cfg, environ):
    if cfg.mode == "local":
        return {"login": cfg.local_user, "name": cfg.local_user, "avatar": "", "role": "owner"}
    sess = read_session(cfg, _cookies(environ).get("tsm_session", "")) if cfg.session_secret else None
    if not sess:
        return None
    login = sess["login"].lower()
    if login in cfg.owners:
        sess["role"] = "owner"
        return sess
    # Re-check manager approval on every request so removal takes effect at once.
    st = load(cfg)
    if any(m["login"].lower() == login for m in st.active_managers()):
        sess["role"] = "manager"
        return sess
    return None


def _gh_json(url, data=None, token=None):
    headers = {"Accept": "application/json", "User-Agent": "tsm-token-admin"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data else None
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=15) as r:
        return json.loads(r.read())


def _auth_login(cfg, environ):
    if cfg.mode == "local":
        return "302 Found", [("Location", "/")], b""
    if not cfg.gh_client_id:
        raise HttpError(500, "GitHub login is not configured.")
    state = secrets.token_urlsafe(24)
    # No redirect_uri: GitHub returns to the app's registered callback URL
    # (https://<host>/api) exactly, and handle() treats ?code=&state= as the callback.
    url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": cfg.gh_client_id, "state": state,
        "scope": "read:user", "allow_signup": "false"})
    return "302 Found", [("Location", url), _set_cookie("tsm_oauth_state", state, 600, True)], b""


def _auth_callback(cfg, environ, q):
    state = q.get("state", [""])[0]
    if not state or not hmac.compare_digest(state, _cookies(environ).get("tsm_oauth_state", "")):
        raise HttpError(400, "Login expired or invalid - please try again.")
    tok = _gh_json("https://github.com/login/oauth/access_token", {
        "client_id": cfg.gh_client_id, "client_secret": cfg.gh_client_secret,
        "code": q.get("code", [""])[0]})
    if "access_token" not in tok:
        raise HttpError(400, "GitHub did not accept the login - please try again.")
    gh = _gh_json("https://api.github.com/user", token=tok["access_token"])
    user = {"login": gh["login"], "name": gh.get("name") or gh["login"], "avatar": gh.get("avatar_url", "")}
    login = user["login"].lower()
    allowed = login in cfg.owners or any(
        m["login"].lower() == login for m in load(cfg).active_managers())
    if not allowed:
        return "302 Found", [("Location", "/?denied=" + urllib.parse.quote(user["login"])),
                             _set_cookie("tsm_oauth_state", "", 0, True)], b""
    return "302 Found", [("Location", "/"),
                         _set_cookie("tsm_session", make_session(cfg, user), SESSION_HOURS * 3600, True),
                         _set_cookie("tsm_oauth_state", "", 0, True)], b""


def _download(cfg, user, which):
    st = load(cfg)
    stamp = T.today()
    if which == "audit":
        data, name = T.write_csv(st.audit, T.AUDIT_FIELDS), f"tsm_token_activity_{stamp}.csv"
    else:
        data, name = T.write_csv(st.tokens, T.TOKEN_FIELDS), f"tsm_tokens_{stamp}.csv"
    return "200 OK", [("Content-Type", "text/csv; charset=utf-8"),
                      ("Content-Disposition", f'attachment; filename="{name}"'),
                      ("Cache-Control", "no-store")], ("﻿" + data).encode("utf-8")


def _json(code, obj, extra=None):
    body = json.dumps(obj).encode()
    return code, [("Content-Type", "application/json"), ("Cache-Control", "no-store")] + (extra or []), body


def app(environ, start_response):
    status, headers, body = handle(environ)
    start_response(status, headers)
    return [body]


_CFG = None


def get_config():
    global _CFG
    if _CFG is None:
        _CFG = Config()
    return _CFG


def handle(environ, cfg=None):
    cfg = cfg or get_config()
    q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    op = q.get("op", [""])[0]
    if not op and "code" in q and "state" in q:
        op = "callback"  # GitHub OAuth return to the registered callback URL
    method = environ.get("REQUEST_METHOD", "GET")
    try:
        if op == "login":
            return _auth_login(cfg, environ)
        if op == "callback":
            return _auth_callback(cfg, environ, q)
        if op == "logout":
            return "302 Found", [("Location", "/"), _set_cookie("tsm_session", "", 0, True)], b""
        if op == "health":
            return _json("200 OK", {"mode": cfg.mode, "problems": cfg.problems(), **cfg.store.describe()})

        user = _current_user(cfg, environ)
        if op == "me":
            if not user:
                return _json("401 Unauthorized", {"error": "not signed in", "mode": cfg.mode})
            return _json("200 OK", {**user, "mode": cfg.mode, "problems": cfg.problems(),
                                    **cfg.store.describe()})
        if not user:
            raise HttpError(401, "Please sign in.")
        if op in ("download_tokens", "download_audit"):
            return _download(cfg, user, "audit" if op == "download_audit" else "tokens")
        if method == "GET" and op in OPS_GET:
            return _json("200 OK", OPS_GET[op](cfg, user, {}, q))
        if method == "POST" and op in OPS_POST:
            # CSRF guard: browsers can't send this header cross-site without CORS.
            if environ.get("HTTP_X_TSM_REQUEST") != "1":
                raise HttpError(403, "Missing request header.")
            size = int(environ.get("CONTENT_LENGTH") or 0)
            if size > 5_000_000:
                raise HttpError(413, "Upload too large.")
            raw = environ["wsgi.input"].read(size) if size else b"{}"
            body = json.loads(raw or b"{}")
            return _json("200 OK", OPS_POST[op](cfg, user, body, q))
        raise HttpError(404, f"Unknown operation '{op}'.")
    except HttpError as e:
        return _json(f"{e.code} Error", {"error": e.msg})
    except (StoreError, ValueError) as e:
        return _json("500 Error", {"error": str(e)})
