"""
One-time: build the registry files (tokens.csv, managers.json, audit.csv) from the
original keys/issued_tokens.csv.

  * Each token is verified against the TSM public key; its email/expiry/id are
    taken from the signed token itself.
  * Ids already revoked in the published manifest are marked revoked.
  * Token strings are NOT stored when they can be regenerated from the seed
    (the app rebuilds them on load), so the registry holds no usable credentials.

    python scripts/init_registry.py <issued_tokens.csv> <out_dir> \
        --manifest <tsm_manifest.json> --seed <tsm_ed25519_seed.bin>
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))

from _tsmadmin import tokens as T  # noqa: E402
from _tsmadmin.app import DEFAULT_PUBLIC_KEY_B64, State, mark_manifest_revoked  # noqa: E402
from _tsmadmin.store import manifest_revoked  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("out_dir")
    ap.add_argument("--actor", default="import")
    ap.add_argument("--manifest", required=True, help="current tsm_manifest.json")
    ap.add_argument("--seed", required=True, help="tsm_ed25519_seed.bin")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    for n in ("tokens.csv", "managers.json", "audit.csv"):
        if os.path.exists(os.path.join(a.out_dir, n)):
            sys.exit(f"{n} already exists in {a.out_dir}; refusing to overwrite.")

    text = open(a.csv, encoding="utf-8").read()
    rows, problems = T.normalise_upload(text, T.b64url_decode(DEFAULT_PUBLIC_KEY_B64), a.actor)
    mark_manifest_revoked(rows, manifest_revoked(open(a.manifest, encoding="utf-8").read()), problems)

    st = State({}, None, open(a.seed, "rb").read())
    st.tokens, st.managers = rows, []
    st.audit = [{"time": T.now_iso(), "actor": a.actor, "action": "import", "jti": "", "email": "",
                 "details": f"initial import of {len(rows)} tokens from {os.path.basename(a.csv)}"}]
    files = st.files()  # token strings blanked where regenerable
    kept = sum(1 for r in T.read_csv(files["tokens.csv"]) if r.get("token"))
    for n, t in files.items():
        with open(os.path.join(a.out_dir, n), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(t)
    print(f"{len(rows)} tokens written to {a.out_dir} "
          f"({kept} token strings stored verbatim because they are not regenerable)")
    for p in problems:
        print("  ", p)


if __name__ == "__main__":
    main()
