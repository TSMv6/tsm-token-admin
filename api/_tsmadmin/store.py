"""
Storage backends.

Registry (private repo TSMv6/tsm-token-registry, or a local folder):
    tokens.csv      one row per token (see tokens.TOKEN_FIELDS)
    managers.json   approved manager GitHub logins
    audit.csv       append-only activity log

Manifest (public repo TSMv6/tsm-auth-endpoint -> auth.tsm-nextgen.com):
    public/tsm_manifest.json   only its revoked_token_ids list is edited here.

GitHubStore writes the three registry files in ONE commit (Git Data API) and
refuses to overwrite a newer commit (fast-forward only), so two managers acting
at the same moment can't lose each other's change: the loser retries.
LocalStore is the emergency/offline copy: plain files in local git clones.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

REGISTRY_FILES = ("tokens.csv", "managers.json", "audit.csv")


class Conflict(Exception):
    """Someone else changed the data first; reload and retry."""


class StoreError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Manifest text helpers (shared)                                              #
# --------------------------------------------------------------------------- #

def manifest_revoked(text: str):
    return list(json.loads(text).get("revoked_token_ids", []))


def manifest_with_revoked(text: str, ids) -> str:
    """Rewrite only the revoked_token_ids array, preserving the file's layout."""
    arr = json.dumps(list(ids))
    new, n = re.subn(r'("revoked_token_ids"\s*:\s*)\[[^\]]*\]',
                     lambda m: m.group(1) + arr, text, count=1)
    if n == 0:
        m = json.loads(text)
        m["revoked_token_ids"] = list(ids)
        new = json.dumps(m, indent=2) + "\n"
    json.loads(new)  # must stay valid JSON
    return new


# --------------------------------------------------------------------------- #
# GitHub                                                                       #
# --------------------------------------------------------------------------- #

class GitHubStore:
    mode = "github"

    def __init__(self, token, registry_repo, registry_branch,
                 manifest_repo, manifest_branch, manifest_path):
        self.token = token
        self.rrepo, self.rbranch = registry_repo, registry_branch
        self.mrepo, self.mbranch, self.mpath = manifest_repo, manifest_branch, manifest_path

    # -- low-level ---------------------------------------------------------
    def _api(self, method, path, body=None, raw=False):
        url = "https://api.github.com" + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tsm-token-admin",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (409, 422) and method in ("PATCH", "PUT"):
                raise Conflict(msg) from e
            if e.code == 404 and raw:
                return None
            raise StoreError(f"GitHub {method} {path}: HTTP {e.code} {msg}") from e
        if raw:
            return payload.decode("utf-8")
        return json.loads(payload) if payload else {}

    # -- registry ----------------------------------------------------------
    def read_registry(self):
        """Return ({filename: text}, version) where version = commit sha."""
        ref = self._api("GET", f"/repos/{self.rrepo}/git/ref/heads/{self.rbranch}")
        sha = ref["object"]["sha"]
        files = {}
        for name in REGISTRY_FILES:
            files[name] = self._api("GET", f"/repos/{self.rrepo}/contents/{name}?ref={sha}", raw=True) or ""
        return files, sha

    def write_registry(self, files, version, message):
        commit = self._api("GET", f"/repos/{self.rrepo}/git/commits/{version}")
        tree = self._api("POST", f"/repos/{self.rrepo}/git/trees", {
            "base_tree": commit["tree"]["sha"],
            "tree": [{"path": n, "mode": "100644", "type": "blob", "content": t}
                     for n, t in files.items()],
        })
        new = self._api("POST", f"/repos/{self.rrepo}/git/commits", {
            "message": message, "tree": tree["sha"], "parents": [version],
        })
        # force=false -> rejected (422) unless fast-forward => Conflict
        self._api("PATCH", f"/repos/{self.rrepo}/git/refs/heads/{self.rbranch}",
                  {"sha": new["sha"], "force": False})
        return new["sha"]

    # -- manifest ----------------------------------------------------------
    def read_manifest(self):
        meta = self._api("GET", f"/repos/{self.mrepo}/contents/{self.mpath}?ref={self.mbranch}")
        text = base64.b64decode(meta["content"]).decode("utf-8")
        return text, meta["sha"]

    def write_manifest(self, text, version, message):
        self._api("PUT", f"/repos/{self.mrepo}/contents/{self.mpath}", {
            "message": message, "branch": self.mbranch, "sha": version,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        })

    def describe(self):
        return {"mode": "github", "registry": f"{self.rrepo}@{self.rbranch}",
                "manifest": f"{self.mrepo}@{self.mbranch}:{self.mpath}"}


# --------------------------------------------------------------------------- #
# Local folder (emergency / offline)                                           #
# --------------------------------------------------------------------------- #

class LocalStore:
    mode = "local"

    def __init__(self, registry_dir, manifest_file):
        self.rdir = os.path.abspath(registry_dir)
        self.mfile = os.path.abspath(manifest_file)

    def _version(self):
        parts = []
        for n in REGISTRY_FILES:
            p = os.path.join(self.rdir, n)
            parts.append(str(os.path.getmtime(p)) if os.path.exists(p) else "-")
        return "|".join(parts)

    def read_registry(self):
        files = {}
        for n in REGISTRY_FILES:
            p = os.path.join(self.rdir, n)
            files[n] = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        return files, self._version()

    def write_registry(self, files, version, message):
        if version != self._version():
            raise Conflict("registry files changed on disk")
        os.makedirs(self.rdir, exist_ok=True)
        for n, t in files.items():
            with open(os.path.join(self.rdir, n), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(t)
        return self._version()

    def read_manifest(self):
        text = open(self.mfile, encoding="utf-8").read()
        return text, str(os.path.getmtime(self.mfile))

    def write_manifest(self, text, version, message):
        if version != str(os.path.getmtime(self.mfile)):
            raise Conflict("manifest changed on disk")
        with open(self.mfile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def describe(self):
        return {"mode": "local", "registry": self.rdir, "manifest": self.mfile}

    # -- publish local changes to GitHub (when back online) -----------------
    def publish(self, actor):
        out = []
        for repo_dir, paths in ((self.rdir, list(REGISTRY_FILES)), (_git_root(self.mfile), [self.mfile])):
            if not repo_dir:
                out.append(f"{paths[0]}: not inside a git clone - copy it by hand")
                continue
            out.append(f"== {repo_dir}")
            out.append(_git(repo_dir, "add", *paths))
            st = _git(repo_dir, "status", "--porcelain", *paths)
            if st.strip():
                out.append(_git(repo_dir, "commit", "-m", f"tsm-token-admin (local, {actor}): publish offline changes"))
            out.append(_git(repo_dir, "pull", "--rebase"))
            out.append(_git(repo_dir, "push"))
        return "\n".join(x for x in out if x)


def _git_root(path):
    d = os.path.dirname(os.path.abspath(path))
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    txt = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        raise StoreError(f"git {' '.join(args)} failed in {cwd}:\n{txt}")
    return f"$ git {' '.join(args)}\n{txt}" if txt else ""
