"""
Token records: CSV schema, signing, decoding, import normalisation.

The token format is unchanged from tsm_dev_guidelines/tsm_auth/issue_token.py:
    base64url(payload_json) + "." + base64url(ed25519_signature)
with payload = {"exp","iss","jti","tool","user"} serialised with sorted keys and
compact separators, so every TSM exe/sidecar already in the field verifies the
tokens this app issues.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
from datetime import date, datetime

from . import ed25519

# Column order of tokens.csv. The first eight columns are the original
# keys/issued_tokens.csv schema (so that file can be uploaded as-is); the rest
# are the audit columns added by this app.
TOKEN_FIELDS = [
    "name", "email", "tool", "jti", "issued", "expires", "revoked", "token",
    "issued_by", "modified_by", "modified_at", "revoked_by", "notes",
]

AUDIT_FIELDS = ["time", "actor", "action", "jti", "email", "details"]


# --------------------------------------------------------------------------- #
# base64url / dates                                                           #
# --------------------------------------------------------------------------- #

def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def norm_date(s: str) -> str:
    """Accept YYYY-MM-DD, M/D/YYYY (Excel) or blank; return YYYY-MM-DD or ''."""
    s = (s or "").strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date '{s}' (use YYYY-MM-DD)")


def today() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# --------------------------------------------------------------------------- #
# Signing / decoding                                                          #
# --------------------------------------------------------------------------- #

def make_token(seed: bytes, email: str, expires: str, jti: str, tool: str = "*") -> str:
    payload = {"user": email, "tool": tool or "*", "exp": expires, "jti": jti, "iss": "tsm-dev"}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{b64url(payload_bytes)}.{b64url(ed25519.sign(seed, payload_bytes))}"


def decode_token(token: str, public_key: bytes):
    """Return (claims dict, signature_ok bool). Raises ValueError if malformed."""
    try:
        a, b = token.strip().split(".", 1)
        payload = b64url_decode(a)
        sig = b64url_decode(b)
        claims = json.loads(payload.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ValueError("malformed token") from e
    return claims, ed25519.verify(public_key, payload, sig)


def next_jti(rows, year: int | None = None) -> str:
    year = year or date.today().year
    maxn = 0
    for r in rows:
        m = re.match(rf"tsm-{year}-(\d+)$", (r.get("jti") or "").strip())
        if m:
            maxn = max(maxn, int(m.group(1)))
    return f"tsm-{year}-{maxn + 1:04d}"


def status(row) -> str:
    if (row.get("revoked") or "").strip():
        return "revoked"
    exp = (row.get("expires") or "").strip()
    if exp and exp < today():
        return "expired"
    return "active"


# --------------------------------------------------------------------------- #
# CSV I/O                                                                      #
# --------------------------------------------------------------------------- #

def read_csv(text: str):
    if not text or not text.strip():
        return []
    text = text.lstrip("﻿")
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def write_csv(rows, fields) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (r.get(k) or "") for k in fields})
    return buf.getvalue()


def normalise_upload(text: str, public_key: bytes, actor: str):
    """Parse an uploaded tokens CSV (this app's export or the original
    keys/issued_tokens.csv). The token itself is authoritative for email /
    expiry / jti / tool; rows whose token does not verify against the TSM public
    key are rejected. Returns (rows, problems)."""
    rows, problems, seen = [], [], set()  # seen: {(jti, email_lower)}
    for i, r in enumerate(read_csv(text), start=2):  # line 1 = header
        tok = (r.get("token") or "").strip()
        if not tok:
            problems.append(f"line {i}: no token - skipped")
            continue
        try:
            claims, ok = decode_token(tok, public_key)
        except ValueError:
            problems.append(f"line {i}: malformed token - skipped")
            continue
        if not ok:
            problems.append(f"line {i}: token signature does not match the TSM key - skipped")
            continue
        jti = claims.get("jti", "")
        email_l = (claims.get("user", "") or r.get("email", "")).strip().lower()
        if (jti, email_l) in seen:
            # Same person re-issued under the same id (e.g. extended): newest row wins.
            problems.append(f"line {i}: {jti} re-issued to the same person - newer token kept")
            rows = [x for x in rows if not (x["jti"] == jti and x["email"].lower() == email_l)]
        elif any(j == jti for j, _ in seen):
            others = ", ".join(x["email"] for x in rows if x["jti"] == jti)
            problems.append(f"line {i}: WARNING {jti} is also used by {others} - both kept; "
                            f"revoking this id blocks everyone who shares it")
        seen.add((jti, email_l))
        try:
            issued = norm_date(r.get("issued", ""))
            revoked = norm_date(r.get("revoked", ""))
        except ValueError as e:
            problems.append(f"line {i}: {e} - skipped")
            continue
        rows.append({
            "name": (r.get("name") or "").strip(),
            "email": claims.get("user", "") or (r.get("email") or "").strip(),
            "tool": claims.get("tool", "*") or "*",
            "jti": jti,
            "issued": issued,
            "expires": claims.get("exp", ""),
            "revoked": revoked,
            "token": tok,
            "issued_by": (r.get("issued_by") or "").strip() or "import",
            "modified_by": (r.get("modified_by") or "").strip(),
            "modified_at": (r.get("modified_at") or "").strip(),
            "revoked_by": (r.get("revoked_by") or "").strip() or ("import" if revoked else ""),
            "notes": (r.get("notes") or "").strip(),
        })
    return rows, problems
