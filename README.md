# TSM Token Manager

Web app for TSM token managers to **issue, edit/extend, revoke and restore** TSM
model access tokens, see who did what and when, and download the token list.

- Hosted: `https://admin.tsm-nextgen.com` (Vercel), sign in with **GitHub**.
- Emergency/offline: the same app on your PC — `start_local.bat` (no login).

Standard-library Python only (no packages). Tokens are byte-compatible with
`tsm_dev_guidelines/tsm_auth` and the `tsm_auth_check.exe` sidecar: nothing in the
models or tsm_panel changes.

## How it fits together

| Piece | Where | What |
|---|---|---|
| This app | repo `TSMv6/tsm-token-admin` → Vercel → `admin.tsm-nextgen.com` | UI + API (`api/index.py`, `public/`) |
| Registry | **private** repo `TSMv6/tsm-token-registry` | `tokens.csv`, `managers.json`, `audit.csv` |
| Public manifest | repo `TSMv6/tsm-auth-endpoint` → `auth.tsm-nextgen.com` | the app edits only `revoked_token_ids` |
| Signing seed | Vercel env var `TSM_SEED_B64` (hosted); `tsm_dev_guidelines/keys/` (local) | never in any repo |

- **Revoke** → registry commit + manifest commit → Vercel redeploys the manifest
  (~30 s) → models block that token at their next online check.
- The registry stores **no token strings**: they are regenerated from the seed
  (Ed25519 is deterministic), so a leak of the registry exposes no credentials.
- Every change is one commit by the app; `audit.csv` records the manager's GitHub
  login, time, action and details. Two managers acting at once can't overwrite
  each other (fast-forward-only commits, automatic retry).
- Removing an id from the manifest happens **only** through *Restore*; a sync
  never un-revokes anything.

## Roles

- **Owner** (`TSM_OWNERS`): everything below + add/remove managers + replace the
  token list from an uploaded CSV.
- **Manager** (listed in `managers.json`): issue, edit/extend, revoke, restore,
  view, download. All managers have the same access and see each other.

## One-time setup (owner)

1. **GitHub OAuth app** (login): GitHub → TSMv6 org → Settings → Developer
   settings → OAuth Apps → New.
   - Homepage URL `https://admin.tsm-nextgen.com`
   - Callback URL `https://admin.tsm-nextgen.com/api`
   - Copy the Client ID and generate a Client secret.
2. **Fine-grained personal access token** (data access): GitHub → Settings →
   Developer settings → Fine-grained tokens → Generate.
   - Resource owner **TSMv6**; Repositories: `tsm-token-registry`, `tsm-auth-endpoint`
   - Permissions: **Contents: Read and write** (nothing else). Expiry: 1 year (set a reminder).
3. **Vercel**: New Project → import `TSMv6/tsm-token-admin` (Framework preset:
   *Other*, no build command). Environment variables (mark secrets *Sensitive*):

   | Name | Value |
   |---|---|
   | `TSM_OWNERS` | your GitHub login(s), comma-separated, e.g. `4Step` |
   | `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | from step 1 |
   | `TSM_GH_TOKEN` | from step 2 |
   | `TSM_SEED_B64` | `scripts\seed_to_clipboard.bat` copies it for you |
   | `TSM_SESSION_SECRET` | any long random string (e.g. `python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
   | `TSM_CONTACT` *(optional)* | signature used in the "copy email message" text |

   Optional overrides: `TSM_REGISTRY_REPO`, `TSM_REGISTRY_BRANCH` (`main`),
   `TSM_MANIFEST_REPO`, `TSM_MANIFEST_BRANCH` (`master`), `TSM_MANIFEST_PATH`,
   `TSM_DEFAULT_EXPIRY`.
4. **Domain**: Vercel project → Settings → Domains → add `admin.tsm-nextgen.com`
   (DNS is already on Vercel, so it is automatic).
5. Open `https://admin.tsm-nextgen.com/api?op=health` — `problems` must be `[]`.

## Adding a manager

Managers tab → *Add a manager* → their **GitHub username**. They open
`https://admin.tsm-nextgen.com` and click *Sign in with GitHub*. Removing a
manager takes effect immediately; their history stays.

## Local emergency copy

```
git clone https://github.com/TSMv6/tsm-token-registry.git   (once, next to this folder)
start_local.bat
```

Runs at `http://127.0.0.1:8765` against the sibling clones and the seed in
`tsm_dev_guidelines/keys/`. When back online: *Data & sync → Publish to GitHub*.
The owner can also *Replace token list from CSV* (this app's download or the old
`keys/issued_tokens.csv`); a preview shows exactly what will change.

## Development

```
python local_server.py          # local mode against ../ clones
python scripts/init_registry.py  # one-time registry build from issued_tokens.csv
```
