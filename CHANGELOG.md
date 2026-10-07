# Changelog

All notable changes to InvoiceWarden (called InvoiceM8 until 2026-10). Newest first.
Bump `version.py` and add an entry here for every release.

## 1.1.0 (pre-release for testing; not yet the Latest release)
- **New name: InvoiceWarden**, from Sixth Day Studios ("M8" clashed with ServiceM8). On first
  start the old `%LOCALAPPDATA%\InvoiceM8` folder is copied to `%LOCALAPPDATA%\InvoiceWarden`
  (the old one is kept as a backup), so suppliers, history, mailboxes and saved keys all carry
  over. Saved keys stay readable: the master key keeps its old Credential Manager name. The
  "Start with Windows" entry is renamed too. Releases ship `InvoiceWarden.exe` plus a copy
  named `InvoiceM8.exe`, so copies still on the old version find and install the update.
- **New main screen (Activity)**: an On/Off switch with "checked 2 min ago", Check now, tiles
  for invoices filed today and this week, and a **Needs attention** list (problems with what
  to do, Retry/Dismiss, new suppliers to review). The Error Log tab is folded into it. Recent
  activity reads as plain sentences; the full technical log is behind a "Full log" switch.
  "Catch up" is now "Go back further…".
- **Suppliers**: only the systems you've set up are offered as "Send their invoices to";
  IDs, file types and notes sit under "More options"; new suppliers get a note saying what to
  check; Delete asks first.
- **Settings as cards**: Email, Where invoices go, AI, General, and a folded Advanced section.
  Each card has its own Test button; the mail options read in plain English; the red wall of
  unreadable keys is gone (one line, only for keys actually in use).
- **Setup wizard** for new installs: email (Microsoft sign-in, classic Outlook or other
  email), ServiceM8 key and AI key, each with Test and a guide. Settings > Run setup again.
- The accounting side (Xero, MYOB, QuickBooks) is switched off in the app for now: built but
  untested. The job-system list only offers systems that work (ServiceM8).
- Studio teal throughout, matching RamWarden.

## 1.0.40
- **Fixed: "Test AI" always failed with `'sender'`.** The button never
  actually reached the AI provider - it crashed building the test prompt
  because a `{sender}` field added to the prompt earlier was not being filled
  in on this path. It now sends the test invoice properly. (A test locks the
  prompt fields down so this can't silently break again.)
- **Activity Log is now a simple view by default.** It shows only outcomes -
  invoices filed, credits linked, duplicates skipped, errors, and suppliers
  added. The routine step-by-step chatter (email seen, parsed, polled, inline
  signature images skipped as "not a payable document") is hidden until you
  tick the new **Advanced** box. A successful filing is now coloured green so
  it stands out from the warnings and noise.
- **The Activity Log filter is live.** It filters as you type, and clearing the
  box drops straight back to the full log - no "Search" button to press, no
  "Clear filter" needed (the button is still there if you like it).
- **Suppliers list: long names get room to breathe.** The list column is wider
  and each supplier's name has its own line, with the routing summary (and the
  NEW marker) on a quieter line below, so names like "Mate Freight Pty Ltd ATF
  Mate Freight Business …" are no longer chopped off.
- **"Check for updates now" tells you when the check itself failed** (no
  internet, GitHub rate-limiting, GitHub down) instead of always saying
  "you're up to date". The automatic check on launch is unchanged - still a
  silent no-op when it can't reach GitHub.

## 1.0.39
- The "Catch up…" job filter now takes a **range**, not just a floor. The
  field accepts a bare number (skip anything below it, as before), `low-high`
  for an inclusive range, or `-high` for a ceiling only - so a catch-up run
  can be aimed at a single job or a small block of jobs (handy for testing),
  not only "this number and up". An invoice outside the range, or one whose
  job number can't be read, is skipped. The range is still not saved; it
  guards that one run. Routine polling is unchanged.

## 1.0.38
- The app icon is now blue, and it is finally used *everywhere*. Dialogs were
  showing CustomTkinter's own blue logo, not ours: `apply_icon()` was only
  called on the main window, and `CTkToplevel` stamps the library's icon on
  itself shortly after construction. Every window now applies the InvoiceM8
  icon, twice, so ours is the one that survives.
- New "Catch up…" button in the header: a one-off sweep of invoice mail
  already in the mailbox, for use after the app has been off or on a new
  mailbox. It asks how far back to look and, optionally, a job number to
  stop at - an invoice for a Service-system job below that number, or one
  whose job number can't be read, is skipped rather than filed against a
  job that is probably closed. The job floor is not saved; it guards that
  one run. Routine polling is unchanged.

## 1.0.37
- Added a test suite (`run_tests.bat`, or `python -m unittest discover -s
  tests`). 28 tests, standard library only, no dependency to install. It
  covers the parts that fail silently: document classification, credit
  detection, reference extraction, supplier identification, the duplicate
  guard, credit-to-job linking and the routing gates. `release.py` now refuses
  to publish if they fail.
- Writing it immediately found two real bugs:
  - **Held invoices were invisible.** An invoice held for low confidence went
    only to the pending queue, but the Error Log - the one screen with a Retry
    button - reads the error log, so it could never be actioned despite the
    message saying to retry there. Held invoices are now recorded there too.
  - **`repair_pending_status` had been lost** in an earlier refactor while
    still being called, so replaying queued invoices raised AttributeError.
  - Retrying also dropped `doc_type` and the reference candidates, so a
    retried credit note would have been re-filed as an invoice.

## 1.0.36
- **Not every attachment is an invoice.** Documents are now classified before
  anything is filed: statements of account, quotes, remittance advices and
  delivery dockets are skipped with a line in the Activity Log instead of
  being attached to a job. A document that merely mentions a quote or PO but
  is clearly a tax invoice is still treated as an invoice, and anything
  unrecognisable defaults to invoice - skipping a real one is the worse error.
- **Low-confidence readings no longer invent suppliers.** New setting
  "Min confidence to add a supplier" (Settings > Watcher, default 0.4): below
  it, an unrecognised supplier is NOT created - the invoice is held and
  explained in the Error Log. Invoices matching a supplier already on file are
  unaffected, since the name match is its own evidence.
- Fixed a batch of regex patterns whose word boundaries had been written as
  literal control characters, which silently stopped them matching.

## 1.0.35
- **Fixed the wrong party being extracted.** InvoiceM8 files invoices a
  business RECEIVES from its suppliers, but the parser was asking for the
  "customer / bill-to" company - which on an incoming invoice is your own
  business. It now extracts the SUPPLIER: the company on the letterhead, in
  the From details, or whose bank/ABN is given for payment, explicitly
  ignoring the Bill To party.
- The sending email address is now used as a supplier hint (an invoice from
  accounts@acmeplumbing.com.au is from Acme Plumbing), with generic mail hosts
  ignored, and the document letterhead is used as a further fallback.
- Renamed "Customers" to **Suppliers** throughout the interface, which is what
  they actually are. Database column names are unchanged - a schema rename
  would be risk without benefit - and the code notes why.

## 1.0.34
Job matching is the part that has to be right, so it no longer rests on a
single guess.

- **Invoices: every reference on the document is tried against real jobs.** The
  parser now collects all plausible numbers (labelled job number, filename
  digits, any other long number) and ServiceM8 - the system of record - decides
  which is a real job, checking the job number, the internal number and the
  purchase order number. The first that matches wins, and the matched number is
  recorded. If none match, the error lists every number tried.
- **Credit notes no longer need a job number.** Credits quote the invoice they
  are crediting, not a job, so requiring one failed them outright. Every upload
  now records the job it was filed against, and a credit is linked by matching
  the invoice number it quotes to an invoice already processed.
- If a credit quotes nothing recognisable, it falls back to the customer's most
  recent invoice and says so as a WARNING - it is a guess and should be checked,
  not buried in the routine log. With no history at all the credit is held with
  an explanation rather than filed against a guess.
- The AI prompt now asks for candidate references and states that a credit
  carries the original invoice number rather than a job number.
- New in-app guide "How invoices are matched to jobs".

## 1.0.33
- **New suppliers are added automatically instead of prompting.** A modal per
  unknown supplier does not scale once the AI is reading real mail, so an
  unrecognised name is now added silently and routed straight away. Defaults:
  Service system upload ON, Accounting system upload OFF (a deliberate,
  per-customer decision), PDF only.
- **Customers tab shows which ones are new.** Auto-added suppliers appear as
  "* NEW" in yellow until reviewed, with a sort selector (Name A-Z / Z-A,
  Newest or Oldest added, New-first), a "Show new only" switch and a counter.
  Opening a customer and clicking Save is what marks it reviewed.
- An invoice whose customer name cannot be read at all is still held rather
  than guessed - it lands in the Error Log to retry once the customer exists.
- New in-app guide explaining all of the above (the "?" beside Customers).

## 1.0.32
- **No Azure setup for customers.** InvoiceM8 now ships with its own
  Application (client) ID, so a new site just adds an email account and clicks
  Sign in. (Public-client IDs are not secrets - the sign-in still happens
  against the user's own Microsoft account and there is no client secret.) A
  site that needs its own app registration can still override it in Settings.
- **Monitor up to 10 mailboxes.** Settings gains an "Email accounts" section
  with "+ Add an email account". Each row has its own address, backend
  (graph / imap / com), folder and - importantly - its own credentials: a
  Graph sign-in token or an IMAP app password, which cannot be shared between
  accounts. Rows can be mixed, disabled without deleting, tested individually,
  and removed.
- Every mailbox is scanned on the same schedule and shares the customer
  database and duplicate checks, so the same invoice arriving at two addresses
  is still uploaded once. A mailbox that fails is logged against its address
  and does not stop the others being scanned.
- Help updated: a new "Email accounts" guide, the Graph guide rewritten around
  the no-Azure path (with the manual app registration kept as an optional
  appendix), and the Client ID field marked optional.

## 1.0.31
- **Fixed Google Gemini failing with 404.** Google retired `gemini-1.5-flash`,
  so every AI parse fell back to regex. The app now asks the Gemini API which
  models the key can actually use and retries automatically, preferring a
  current flash model - so a future rename cannot break it either. If nothing
  works, the error lists the models the key does have.
- **App icon.** InvoiceM8 now has its own icon, used for both the .exe and the
  window/taskbar so they match. Regenerate with `python make_icon.py`.

## 1.0.30
Efficiency pass over the whole ingest path, plus a full code-annotation sweep.
See `plan.md` for the findings this implements.

- **Emails already processed are no longer re-downloaded.** The mail backends
  now receive the set of handled message ids and skip those *before* touching
  their attachments. An invoice sitting in the inbox used to have its files
  re-fetched and rewritten on every single poll.
- **Only wanted attachment types are downloaded.** The watcher passes the union
  of every customer's enabled file types to the mailbox, so signature images
  and other junk never reach the disk. Per-customer filtering still happens in
  the router.
- **IMAP now fetches headers first.** It used to download every full message -
  attachments included - for up to 500 search hits just to read the Date
  header, then throw most of them away. Headers come back in a few batched
  round trips; full bodies are fetched only for messages that survive the date
  and dedupe filters.
- **Graph folds attachments into the list query** via `$expand`, removing one
  HTTP round trip per message, and now follows `@odata.nextLink` - after a long
  outage the back-check was silently truncated at the newest 100 messages.
- **OAuth tokens are cached until they expire.** Xero, MYOB and QuickBooks
  refreshed the token on *every* request; combined with rebuilding the provider
  per attachment, a three-file email cost three refreshes per platform. Both
  are fixed: providers are now built once per email.
- **Connection reuse**: every provider and the Graph backend now use a
  `requests.Session` instead of a fresh TLS handshake per call.
- **Reading a setting no longer scans the settings table.** Checking whether a
  key existed re-read every row; opening the Settings window did that dozens of
  times.
- **Customer alias lookup is indexed** rather than loading every customer and
  parsing their alias JSON on each miss.
- **Settings connection tests run off the UI thread.** "Test mailbox" performed
  a full mailbox fetch *including attachment downloads* on the Tk thread and
  froze the window; it is now a headers-only probe that writes nothing.
- **New "Test AI" button** sends a small synthetic invoice through the
  configured AI provider and reports what it extracted - proving the key,
  endpoint, model name and JSON-mode support in one click.
- **Bounded growth**: cached attachments are pruned after a configurable
  retention (default 30 days, new Settings field), keeping anything a queued
  retry still needs; the diagnostic log file now rotates; and the activity log
  is trimmed to its newest 20,000 rows.
- **Fixed: queued invoices were never replayed.** `add_pending` wrote an empty
  status, so the "add this customer, then route their waiting invoices" step
  never found anything to route.
- Smaller wins: attachment hashing reads in chunks instead of loading whole
  files; Xero and ServiceM8 stream uploads rather than buffering them; MYOB
  rejects oversized attachments with a clear message; the watcher-running
  border animation ticks at 200 ms instead of 80 ms and pauses while minimised;
  the activity log repaints in blocks rather than row by row; text extraction
  skips formats it cannot read.
- Every function, method and class in the codebase now carries a purpose
  docstring (148 were missing).

## 1.0.30
- Fixed every successful upload being written to the Activity Log twice. The
  router wrote the row itself and also emitted the event, and the GUI persists
  emitted events - so one upload produced two identical "uploaded" lines. The
  upload itself only ever happened once; only the log was doubled.

## 1.0.29
- Reference parsing now understands how suppliers actually label these fields
  instead of one fixed phrasing. Invoice side: Invoice No/Number/#/ID, Tax
  Invoice, Inv, Bill No, Document No, Statement No, Our Ref, Your Ref,
  Reference. Job side: Job (No/Number/Ref), Work Order, W/O, Service
  Order/Call, Ticket, Order No, Purchase Order/PO - matched most-specific
  first, so an explicit "Job No" beats a generic "Reference".
- **Credit detection now keys on the word "credit"**, which is the one label
  that stays consistent across suppliers - while ignoring ordinary invoice
  wording like "credit card", "credit terms" and "credit limit" so a normal
  invoice is never misfiled as a credit.
- The AI prompt was given the same vocabulary, so both the model and the
  regex fallback read invoices the same way.

## 1.0.28
- **Fixed queued invoices never uploading after you add the customer.** New
  pending rows were written with an empty status, but the replay looked for
  status 'pending_new_customer', so the invoice sat in the queue forever and
  nothing was logged. Rows already stuck this way are repaired automatically
  and reported in the Activity Log.
- Regex fallback parsing fixed: "Invoice" was being read as "inv" + "oice"
  (giving ref=oice), and "Job Reference: 10160" captured the word "Reference"
  instead of 10160. Label words are now skipped and only values containing a
  digit are accepted.

## 1.0.27
- New-customer prompt: the window was too small, so the Add / Skip buttons
  were cut off and the form could not be scrolled. It is now larger,
  resizable, adapts to the screen height, scrolls, and pins the buttons to the
  bottom where they can never be pushed off.
- It no longer offers a meaningless "Enable None / Disabled upload" toggle -
  the accounting row only appears when an accounting system is selected.

## 1.0.26
- **Fixed "InefficientFilter: The restriction or sort order is too complex for
  this operation"** from Microsoft Graph. Graph refuses to filter on
  hasAttachments while sorting by date. The mailbox query now tries
  progressively simpler server-side queries and applies the remaining
  narrowing (attachments, unread, date) locally, so a rejected filter can
  never stop invoices being found.
- Graph scan report says which server query actually succeeded.

## 1.0.25
- **Fixed "that code didn't work" at the sign-in page.** A device code is only
  redeemable at the verification URI belonging to the authority that issued
  it, but 1.0.19 hardcoded microsoft.com/devicelogin - so a code issued
  against /consumers (or any specific tenant) was correctly rejected there.
  The app now opens the URI MSAL returns, prefers the one-click
  verification_uri_complete when offered, and prints the exact URL and code in
  the copyable status box.

## 1.0.24
- The sign-in window now prints exactly what it is about to use - Client ID
  (masked, with a length/format check), tenant, authority and scopes - so a
  blank or mistyped Client ID is obvious instead of being guessed at from
  Microsoft's error pages.
- Sign-in now REFUSES to start when the Client ID is empty or not a 36-char
  GUID. Sending Microsoft a malformed client_id produced wildly misleading
  results, including a sign-in page that ended up putting the password in the
  URL. The app no longer lets that request be made.
- The same configuration summary is written to invoicem8.log.

## 1.0.23
- **Fixed silent credential loss.** If the local encryption key in Windows
  Credential Manager is lost or regenerated, every saved API key, client ID
  and password becomes unrecoverable - and previously they just read back as
  EMPTY while the fields still showed masked dots, so the app looked
  configured but failed with confusing errors from the remote service.
  Settings now shows a red warning naming exactly which credentials must be
  re-entered.
- The Graph "no Client ID" error now says when the value is saved but
  undecryptable, instead of implying the app registration is wrong.
- A newly generated master key is read straight back to confirm Credential
  Manager actually persisted it; if it did not, the app falls back to a local
  key file so secrets survive a restart rather than being orphaned each launch.

## 1.0.22
- Sign-in window gains a "Copy status text" button, and every sign-in outcome
  is written to %LOCALAPPDATA%\InvoiceM8\logs\invoicem8.log - so the exact
  Microsoft error can be reported without taking a screenshot.
- Fixed the sign-in result being dropped: the outcome was pushed to the UI
  from the worker thread, which is not reliably thread-safe and could leave
  the window stuck on "Waiting..." even after Microsoft had answered. The Tk
  thread now polls for the result instead.

## 1.0.21
- Microsoft sign-in now opens its own window instead of reporting through the
  Settings status box: the code is shown in large text with Copy and Open
  buttons, and the real outcome (SUCCESS with the account name, or FAILED with
  Microsoft's exact error) appears in a selectable box you can copy from.
- The window states plainly that the browser finishing on Microsoft's "This is
  not the right page" is that page's normal behaviour, and that this window -
  not the browser - shows whether sign-in actually worked.

## 1.0.20
- Graph setup guide now includes the step that was missing: personal Microsoft
  accounts require a redirect URI registered under Authentication > Add a
  platform > "Mobile and desktop applications" >
  https://login.microsoftonline.com/common/oauth2/nativeclient. Without it,
  device-code sign-in fails with "invalid_request: The provided request must
  include a 'redirect_uri' input parameter", even though device-code flow
  normally needs no redirect URI.
- The sign-in error message now detects that exact failure and tells you the
  fix instead of just echoing Microsoft's wording.

## 1.0.19
- Fixed Microsoft sign-in failing with "invalid_request: The provided request
  must include a 'redirect_uri' input parameter". The app was opening MSAL's
  verification_uri, which for personal Microsoft accounts resolves to an
  endpoint that rejects a plain visit; it now always opens
  microsoft.com/devicelogin.
- The sign-in code is shown in large text in the status box AND copied to your
  clipboard, with numbered instructions, instead of relying on the browser.
- Sign-in no longer raises in the background if you close Settings while it is
  still waiting.

## 1.0.18
- Graph guide: the "no directory" step now covers the exact wording the portal
  shows ("The ability to create applications outside of a directory has been
  deprecated") and warns that the M365 Developer Program route it offers needs
  a PAID Visual Studio Professional/Enterprise subscription, leaving the free
  Azure account as the practical option. Also notes that business Microsoft
  365 accounts already have a directory, so customer sites skip this entirely.

## 1.0.17
- Graph setup guide rewritten against the CURRENT portal: sign-in is at
  entra.microsoft.com (not portal.azure.com), and the account-type option is
  now labelled "Any Entra ID Tenant + Personal Microsoft accounts". Adds the
  "Allow public client flows" step that device-code sign-in requires, and
  tells you to check entra.microsoft.com first before assuming you need to
  sign up for Azure.

## 1.0.16
- **New IMAP backend** (Settings > Email > Backend = `imap`). Works with
  Gmail, Fastmail, Yahoo, iCloud, Zoho and most business/cPanel mail hosts
  using just a server name and an app password - no Azure, no app
  registration, no subscription. A provider preset fills in the server and
  port for you. Mail is read with BODY.PEEK so your messages are never marked
  as read, and only emails with attachments are queued.
- **Corrected Graph guidance.** Microsoft now restricts creating a new Entra
  tenant to paid customers, so the advice added in 1.0.15 (create a free
  directory) no longer works on its own: a personal Microsoft account has to
  sign up for a free Azure account first, which creates the directory. The
  guide now says so, and points at IMAP as the easier route where possible.
- "Test Outlook" is now "Test mailbox", and the section is titled "Email",
  since it covers three backends. COM and Graph are unchanged.

## 1.0.15
- Graph setup guide and the Client ID help now cover the "AADSTS16000 /
  Interaction required - your live.com account does not exist in tenant
  Microsoft Services" wall that personal Microsoft accounts hit at
  portal.azure.com, with the exact steps to create your own free Entra
  directory so App registrations becomes available.

## 1.0.14
- **Microsoft Graph sign-in rebuilt around the device-code flow.** It now
  needs only an Application (client) ID - no client secret, no tenant ID, no
  redirect URI. Click "Sign in to Microsoft" in Settings > Outlook, enter the
  short code at microsoft.com/devicelogin, and that's it. The sign-in is
  cached (encrypted) and refreshes itself.
  This matters because the new Outlook for Windows has no COM interface,
  classic Outlook needs a paid Microsoft 365 subscription, and Microsoft
  disabled app-password/IMAP access for personal Outlook accounts in
  September 2024 - Graph OAuth2 is the only remaining option for those users.
- The COM backend is unchanged and still the default for sites running classic
  desktop Outlook.
- Setup guide for the Graph backend rewritten as an exact click-path, and the
  COM guide now spells out when COM cannot work.
- Graph errors are reported properly (folder not found, HTTP status) instead
  of a bare exception, and the scan report notes that the filter requires an
  attachment.

## 1.0.13
- Settings: the action buttons (Save, Test service, Test accounting, Test
  Outlook, Authorise OAuth, Setup guide) and the status box are now pinned in
  a fixed footer instead of living at the bottom of the scrolling form, so
  they are always reachable no matter how long the form gets.
- The status area is a scrollable, selectable text box - long diagnostics wrap
  and can be copied. This also removes a resize feedback loop introduced in
  1.0.12 that could stop the Settings window scrolling.
- Settings window height adapts to the screen (and has a minimum size), so the
  footer is on-screen on smaller laptop displays.

## 1.0.12
- Settings status line now word-wraps and grows with the window, so long
  diagnostics (Test Outlook, connection errors) are fully readable instead of
  being cut off at the edge.
- Mailbox matching is much less brittle: the configured address is matched
  against account SMTP addresses, account display names and data-file names.
  When it still doesn't match, the message now LISTS the accounts Outlook
  actually has on that PC so you can paste the right one in.
- Scan report now includes how many items the folder holds in total, and says
  outright when the folder is empty or has nothing recent - which usually
  means the wrong mailbox is selected.

## 1.0.11
- Outlook COM: if `Outlook.Application` can't be reached (which is always the
  case with the NEW Outlook for Windows Store app - it has no COM interface)
  the error now says exactly that and points at the fix, instead of a raw
  "Invalid class string" COM code.
- Watcher: "no new invoice emails" now reports what was actually scanned -
  which account and folder, how many emails were in the window, how many were
  skipped as already-read, and how many had no matching attachment.
- Attachment file names are now preserved (each message gets its own cache
  folder) and passed to the parser. A file like `testco10160.pdf` alone is
  enough to identify customer "testco" and job 10160.
- COM first scan looks back 14 days instead of the entire mailbox.
- "Only process unread emails" now defaults to OFF - duplicates are prevented
  by message-id, so the read flag was an unnecessary way to miss invoices.
- Test Outlook reports the same scan detail and honours the unread toggle.

## 1.0.10
- ServiceM8: authenticate with the `X-API-Key` header (the documented method
  for Private Application keys) instead of HTTP Basic auth. Basic auth made
  ServiceM8 reply "HTTP 401: Invalid username or password" on Test service.

## 1.0.9
- Update dialog: the "What's new" box now word-wraps (it was breaking
  mid-word) and uses bullet points.

## 1.0.8
- Update dialog: wrapped changelog bullets are joined back into one entry
  each, so "What's new" reads as a clean list instead of a spurious extra
  dash on every continuation line.

## 1.0.7
- Fixed the auto-update relaunch: the freshly-installed exe could fail to
  start with a "parent process has different executable" security error
  because it inherited PyInstaller's onefile environment variables. The
  updater now hands it a clean environment.
- Disabled UPX compression on the build - UPX-packed executables trip
  antivirus heuristics (AVG/Defender flagging the download).
- Release notes are now taken from CHANGELOG.md, so the in-app "What's new"
  shows the real change list instead of "First release."

## 1.0.6
- Activity Log: "Clear" is now two buttons - **Clear filter** (resets the
  search box, also on Esc) and **Clear log** (permanently deletes the stored
  activity history, with a confirmation). The error log is unaffected.

## 1.0.5
- In-app **About / Changelog** window (click the InvoiceM8 wordmark in the
  header, or Settings > Updates > About / Changelog). Shows the version, a
  GitHub link, and this changelog, bundled so it works offline.

## 1.0.4
- Clean shutdown: close is now idempotent and exception-safe, cancels its
  scheduled callbacks, stops the watcher (bounded), then closes the database
  and the window in order. A late write from a worker thread on exit can no
  longer raise.

## 1.0.3
- The self-updater's swap step now runs in its **own visible console window**
  with an InvoiceM8 banner, the repo URL, a clear "not a background/malware
  process" note, step-by-step status, and a visible countdown that holds the
  window open long enough to read before it closes.
- Build/release scripts show the same banner.

## 1.0.2
- Version wiring: `version.py` is the single source of truth for the app
  version (shown in the title bar and Settings > Updates).

## 1.0.1
- Self-update system: frozen builds check GitHub Releases on launch (and via
  Settings > Updates > Check for updates now), download the new build, and
  swap it in. Auto-check is toggleable; running from source never prompts.

## 1.0.0
- First build. Outlook watcher (COM + Microsoft Graph), AI invoice parsing
  (OpenAI / Gemini / Anthropic / any OpenAI-compatible endpoint, all plain
  REST), local SQLite customer database with per-customer routing toggles.
- Pluggable Service systems (ServiceM8 wired; simPRO, AroFlo, Tradify, Fergus,
  Jobber, ServiceTitan, Housecall Pro as previews) and Accounting systems
  (Xero, MYOB, QuickBooks Online wired; Reckon, Sage, FreshBooks as previews),
  each selectable from Settings.
- Credit-note detection and routing; per-email and per-document duplicate
  guards so nothing is uploaded twice.
- Encrypted credential storage (Fernet key in Windows Credential Manager).
- Searchable activity log and a separate error log with manual retry.
- Per-field help popups and per-provider setup guides.
- "Run on Windows startup" toggle.
- Dark UI matching the RamBo desktop app.
