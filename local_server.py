"""
Emergency / offline copy of the TSM Token Manager.

Runs the SAME code as the hosted app, on this PC only (http://127.0.0.1:8765),
with no GitHub login. It reads and writes local clones:

    ../tsm-token-registry/            tokens.csv, managers.json, audit.csv
    ../tsm-auth-endpoint/public/tsm_manifest.json
    ../tsm_dev_guidelines/keys/tsm_ed25519_seed.bin   (signing seed)

Use the "Publish to GitHub" button when back online (git commit + pull + push).
Override any path with the TSM_LOCAL_* / TSM_SEED_FILE environment variables.

    python local_server.py            (or double-click start_local.bat)
"""

import mimetypes
import os
import sys
import webbrowser
from wsgiref.simple_server import WSGIRequestHandler, make_server

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "api"))

os.environ.setdefault("TSM_MODE", "local")
os.environ.setdefault("TSM_LOCAL_REGISTRY_DIR", os.path.join(ROOT, "tsm-token-registry"))
os.environ.setdefault("TSM_LOCAL_MANIFEST", os.path.join(ROOT, "tsm-auth-endpoint", "public", "tsm_manifest.json"))
os.environ.setdefault("TSM_SEED_FILE", os.path.join(ROOT, "tsm_dev_guidelines", "keys", "tsm_ed25519_seed.bin"))

from _tsmadmin.app import app as api_app  # noqa: E402

PUBLIC = os.path.join(HERE, "public")
PORT = int(os.environ.get("TSM_LOCAL_PORT", "8765"))


def site(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path == "/api" or path.startswith("/api/"):
        return api_app(environ, start_response)
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    full = os.path.normpath(os.path.join(PUBLIC, rel))
    if not full.startswith(PUBLIC) or not os.path.isfile(full):
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    start_response("200 OK", [("Content-Type", ctype), ("Cache-Control", "no-store")])
    with open(full, "rb") as fh:
        return [fh.read()]


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *a):
        pass


if __name__ == "__main__":
    for k in ("TSM_LOCAL_REGISTRY_DIR", "TSM_LOCAL_MANIFEST", "TSM_SEED_FILE"):
        print(f"  {k:24s} {os.environ[k]}{'' if os.path.exists(os.environ[k]) else '   (MISSING)'}")
    url = f"http://127.0.0.1:{PORT}/"
    print(f"\nTSM Token Manager (LOCAL) at {url}   - Ctrl+C to stop\n")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    make_server("127.0.0.1", PORT, site, handler_class=QuietHandler).serve_forever()
