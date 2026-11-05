# InvoiceWarden

Local Windows desktop app that watches an Outlook inbox for invoice emails,
uses an AI parser to pull the **customer name** and **job / invoice reference**,
matches the customer against a local SQLite database, and pushes the attachment
to **ServiceM8** and/or an accounting system (**MYOB, Xero, QuickBooks Online**,
selectable per deployment).

Styled to match the RamBo desktop utility (same near-black palette, Segoe UI /
Consolas type, flat accent buttons).

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Python 3.11+ recommended. Windows only (uses pywin32 COM + `winreg`).

## Build and release

```bash
:: bump version.py + add a CHANGELOG.md entry, commit, then:
release.bat
```

`release.bat` runs the whole pipeline through `release.py`: it runs the test
suite, then asks two questions -

1. **Build a fresh InvoiceWarden.exe?** PyInstaller against `InvoiceWarden.spec`,
   producing a single windowed `dist\InvoiceWarden.exe` (~100 MB, no Python needed
   on the target). Build from an environment with the full `requirements.txt`
   installed so the AI / Graph / attachment libraries get bundled - the spec
   silently skips any that are missing. The build is also copied to
   `dist\InvoiceWarden-<version>.exe`, the file that gets published.
2. **Publish it to the update store?** Answer no and you just have a local
   build. Answer yes and `release.py` runs `tools
elease_upload.py`, which
   signs the update (Ed25519, key in `SDS_UPDATE_SIGNING_KEY` of
   `Y:\Guardian\.env`), uploads the exe and `latest.json` to the Sixth Day
   Studios store and reads it back to verify. Release notes are the version's
   bullets in `CHANGELOG.md`. To publish by hand:
   `python tools
elease_upload.py --installer dist\InvoiceWarden-X.Y.Z.exe --env-file Y:\Guardian\.env [--dry-run]`.

Scripting overrides: `--yes` (yes to both), `--build-only` (build, never
publish), `--skip-build` (publish the exe already in `dist\`), `--dry-run`
(sign and print, upload nothing).

**One-time GitHub transition.** Copies up to 1.0.41 still poll GitHub Releases.
The first store release (1.0.42) must therefore also be published there once,
with `release.py --github-transition`, so those installs download a build that
knows the new source. After that the GitHub step (`--github-transition`,
`publish_github_transition`, `gh`, `LEGACY_EXE`, `GITHUB_REPO` in `release.py`)
should be deleted and the repo can go private.

Settings and the database live in `%LOCALAPPDATA%\InvoiceWarden`, so the exe stays
stateless and can be replaced in place. Installed builds check the update store
on launch (and via **Settings > Updates > Check for updates now**), verify the
signature, download the new exe, check its size and SHA-256 against the signed
manifest, and swap it over the running exe via a detached helper. Auto-check can
be turned off in Settings; running from source never prompts.

Permanent download link:
`https://sixthdaystudios.com/api/updates/invoicewarden/download`

## First run

1. **Settings** (header button) – choose the service and accounting systems, enter
   credentials, save. Secrets are encrypted with a Fernet key held in Windows
   Credential Manager (DPAPI). If Credential Manager is unavailable the key is
   kept in a local `.masterkey` file instead, which is weaker.
2. For MYOB / Xero / QuickBooks click **Authorise OAuth** once to get a
   refresh token.
3. **Suppliers tab** – review auto-added suppliers and set their routing toggles.
4. Press **Start watcher** (or enable auto-start in Settings).

## Data location

`%LOCALAPPDATA%\InvoiceWarden\` – `invoicewarden.sqlite3`, `attachments\`, `logs\`.

## Module map

| Area      | Files |
|-----------|-------|
| Entry     | `main.py`, `config.py` |
| Security  | `core/crypto.py`, `core/settings_store.py` |
| Database  | `core/database.py` |
| Email     | `integrations/email_outlook.py` (COM + Graph), `email_imap.py` |
| AI parse  | `core/parser_ai.py` (OpenAI / Gemini / Anthropic / compatible + regex fallback) |
| Routing   | `core/router.py`, `core/watcher.py` |
| Providers | `integrations/accounting/*` + `registry.py` |
| Startup   | `core/startup.py` (HKCU Run key) |
| Updates   | `core/updater.py`, `core/ed25519.py`, `gui/update_dialog.py`, `release.py`, `tools/release_upload.py`, `version.py` |
| About     | `gui/about_dialog.py` + bundled `CHANGELOG.md` |
| GUI       | `gui/*` (CustomTkinter, RamBo theme in `gui/theme.py`) |

## Caveats

- ServiceM8 / MYOB / Xero / QBO endpoints are implemented to their public docs
  but **field names and the exact bill/attachment model vary by account** –
  test against a sandbox company file before production use.
- Graph sign-in uses the built-in client ID (device-code flow, `Mail.Read`); you only need your own Azure app registration to override it.
- Only one copy of InvoiceWarden can run at a time (a second launch exits).
