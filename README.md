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
   silently skips any that are missing.
2. **Publish it as a GitHub release `v<version>`?** Answer no and you just have
   a local build. Answer yes and it uploads the exe and the CHANGELOG notes.

Scripting overrides: `--yes` (yes to both), `--build-only` (build, never
publish), `--skip-build` (publish the exe already in `dist\`).

Settings and the database live in `%LOCALAPPDATA%\InvoiceWarden`, so the exe stays
stateless and can be replaced in place. Installed builds check `releases/latest`
on launch (and via **Settings > Updates > Check for updates now**), download the
new `InvoiceWarden.exe`, and swap it in via a detached helper. Auto-check can be
turned off in Settings; running from source never prompts.

Permanent download link:
`https://github.com/Mikeyau-ai/InvoiceWarden/releases/latest/download/InvoiceWarden.exe`

## First run

1. **Settings tab** – choose the accounting system, enter credentials, save.
   Secrets are encrypted with a Fernet key held in Windows Credential Manager
   (DPAPI). Nothing is stored in plain text.
2. For MYOB / Xero / QuickBooks click **Authorise OAuth provider** once to get a
   refresh token.
3. **Customers tab** – add customers and set their routing toggles.
4. Press **Start watcher** (or enable auto-start in Settings).

## Data location

`%LOCALAPPDATA%\InvoiceWarden\` – `invoicewarden.sqlite3`, `attachments\`, `logs\`.

## Module map

| Area      | Files |
|-----------|-------|
| Entry     | `main.py`, `config.py` |
| Security  | `core/crypto.py`, `core/settings_store.py` |
| Database  | `core/database.py` |
| Email     | `integrations/email_outlook.py` (COM + Graph) |
| AI parse  | `core/parser_ai.py` (Gemini / Anthropic + regex fallback) |
| Routing   | `core/router.py`, `core/watcher.py` |
| Providers | `integrations/accounting/*` + `registry.py` |
| Startup   | `core/startup.py` (HKCU Run key) |
| Updates   | `core/updater.py`, `gui/update_dialog.py`, `release.py`, `version.py` |
| About     | `gui/about_dialog.py` + bundled `CHANGELOG.md` |
| GUI       | `gui/*` (CustomTkinter, RamBo theme in `gui/theme.py`) |

## Caveats

- ServiceM8 / MYOB / Xero / QBO endpoints are implemented to their public docs
  but **field names and the exact bill/attachment model vary by account** –
  test against a sandbox company file before production use.
- Graph backend needs an Azure app registration with `Mail.Read`.
