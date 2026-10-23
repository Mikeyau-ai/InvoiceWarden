# InvoiceWarden - Efficiency & Annotation Review

Findings from a full read of the ~6,000-line source tree (2026-08-28).

## Status: implemented in 1.0.30

Every numbered item below is done except the three explicitly marked
"noted only" (2.5, 2.6 and the `_save_state` bullet in section 7), which the
review itself concluded needed no change. Verified by three smoke-test suites
covering the database layer, the token cache, the router, the Graph fetch and
the watcher wiring, plus a launch check.

Two things surfaced during implementation that were **not** in the original
review:

* **Fixed - queued invoices were never replayed.** `add_pending` named every
  column in its INSERT, so an omitted `status` was written as `''` rather than
  taking the column default. `list_pending("pending_new_customer")` therefore
  never matched anything, and the "add this customer, now route their waiting
  invoices" step silently did nothing. Fixed in `core/database.py`; the new
  cache sweep also depends on that status to know which files to protect.
* **Not changed - one email yields one document reference.** The duplicate
  guard treats customer + reference + doc type as the document identity, and
  the parser extracts a single reference per email, so an email carrying two
  *different* invoice PDFs uploads only the first (the second is logged as a
  duplicate). This is the existing documented rule, not a regression, but it
  is a real limitation. Changing it means either parsing per attachment or
  including the file name in the identity - a design call, so it is left
  alone.

The sections below are kept as written, as the record of why each change was
made.

---

Each item lists **where**, **why it costs**, and the **proposed fix**. Severity:

* **P1** - wasted network/disk/CPU on the hot path (every poll, every invoice)
* **P2** - noticeable but bounded, or only felt as the DB/mailbox grows
* **P3** - polish, dead code, annotation gaps

---

## 1. Ingest path: work done that is then thrown away  (P1)

The mailbox -> parse -> route pipeline currently downloads and writes files
before it knows whether it needs them. On a 5-minute poll this repeats forever.

### 1.1 Attachments are saved to disk for emails already processed
`core/watcher.py:117-128`, `integrations/email_outlook.py:213-221` / `:352`,
`integrations/email_imap.py:143-153`

`backend.fetch()` saves every attachment of every in-window message, and only
*afterwards* does `_poll` call `db.is_email_processed(msg.message_id)` and skip
it. An invoice email that stays in the inbox is re-downloaded and re-written on
every poll for as long as it is inside the lookback window.

**Fix:** hand the backend a `seen_ids: set[str]` (or a `should_process(id)`
callback) built once per poll from `processed_emails`, and have each backend
skip the message before touching its attachments. For COM/Graph the message id
is known before the attachment call; for IMAP see 1.2.

**Verify:** poll twice with an unread invoice present - the second poll writes
no files and logs it as skipped.

### 1.2 IMAP downloads the entire RFC822 message (attachments included) just to read its Date
`integrations/email_imap.py:141-151`

The loop does `conn.fetch(uid, "(BODY.PEEK[])")` for up to 500 UIDs, parses the
whole MIME tree, and *then* discards anything older than `floor`. Since the
IMAP `SINCE` search is day-granular and deliberately widened by a day, a large
fraction of those full-body downloads are thrown away immediately.

**Fix:** two-pass fetch. First fetch only the headers for the whole batch in one
round trip (`BODY.PEEK[HEADER.FIELDS (DATE MESSAGE-ID SUBJECT FROM)]` over a UID
range); drop anything outside the window or already in `processed_emails`; only
then fetch `BODY.PEEK[]` for the survivors.

**Verify:** instrument byte counts before/after against a folder with a few
large-attachment mails outside the window.

### 1.3 Every attachment type is downloaded, then most are discarded
`core/watcher.py:116` (`allowed_ext = set()`), `core/router.py:87-96`

The watcher deliberately passes an empty `allowed_ext`, so signature images,
`.zip`, `.ics` and everything else lands in the attachment cache; the router
then skips them per customer. Nothing ever deletes them.

**Fix:** pass the union of `file_types` across all customers (falling back to
`config.DEFAULT_FILE_TYPES` when the table is empty) as a cheap prefilter.
Per-customer filtering stays in the router - this only removes files no customer
could ever want.

### 1.4 Graph makes an extra HTTP round trip per message
`integrations/email_outlook.py:352`, `:377-397`

`_download_attachments` is a separate `GET /messages/{id}/attachments` for each
message. Graph supports `$expand=attachments` on the list call.

**Fix:** add `$expand=attachments` to the (already fallback-laddered) query and
read the bytes from the list response; keep the per-message call as the fallback
if the expand is rejected (it is another candidate for `InefficientFilter`).
Also skip writing a file whose destination already exists at the same size.

### 1.5 Provider objects are rebuilt (and re-authenticated) per attachment
`core/router.py:89-98` -> `_dispatch` -> `build_service_provider` /
`build_accounting_provider`

`_dispatch` is called once per attachment, and each call constructs fresh
provider instances. Because `_headers()` on the OAuth providers performs a live
token refresh (see 3.1), a three-attachment email costs three refresh POSTs per
platform.

**Fix:** resolve the target providers once in `route()` (they depend only on
settings + the customer's toggles, not on the file) and pass the list into
`_dispatch`.

### 1.6 `_file_hash` reads whole files into memory
`core/router.py:56-61`

`hashlib.sha256(path.read_bytes())` - fine for a 200 KB invoice, not for a
scanned 50 MB PDF.

**Fix:** chunked read (`while chunk := fh.read(1 << 20)`).

---

## 2. Database access patterns  (P1/P2)

### 2.1 `Settings.get()` can trigger a full-table scan per call  (P1)
`core/settings_store.py:83-88`, `:115-116`

When a value reads back empty, `get()` calls `_explicitly_set()`, which is
`db.all_settings()` - `SELECT * FROM settings`, every row, building a dict - to
answer "does this key exist?". `Settings.get` is called from `_row()` per form
field, from `Provider.configured()` per setting field, from `build_backend`,
from `label_for(...)` in list loops, and so on. Rendering the Settings tab does
this dozens of times.

**Fix (either):**
* have `Database.get_setting` return `(value, encrypted, found)` so existence
  comes from the row itself and `_explicitly_set` disappears; or
* cache the settings table in `Settings` and invalidate on `set()`/`update()`
  (single process, one writer - safe).

The first is smaller and removes the query entirely. Prefer it.

**Verify:** count `SELECT * FROM settings` executions while opening the Settings
window - should drop from dozens to zero.

### 2.2 `clear_activity_log` fetches every row to count them
`core/database.py:245-249`

`len(self._query("SELECT id FROM activity_log"))` materialises the whole table
purely for a number.

**Fix:** `SELECT COUNT(*)`, or read `cursor.rowcount` off the `DELETE` (which
`_exec` already returns).

### 2.3 Activity-log search scans the table six times with `LIKE '%%'`
`core/database.py:235-243`, called by `gui/logs_tab.py:76`

With an empty search box (the default, and the state on every refresh) the query
still evaluates six `LIKE '%%'` predicates against every row. There is no index
on `activity_log`, and the table grows by one row per emitted event forever
(`gui/app.py:154`).

**Fix:** short-circuit to `SELECT * FROM activity_log ORDER BY id DESC LIMIT ?`
when `term` is empty. For the non-empty case, an FTS5 virtual table is only
worth it if the log is expected to reach six figures - otherwise the LIMIT 500
descending-PK scan is fine.

### 2.4 `find_customer_by_name` loads and JSON-parses every customer on an alias miss
`core/database.py:181-193`

The canonical-name lookup is a single indexed query, but any miss falls through
to `list_customers()` + `json.loads` per row - on every invoice, and for every
unknown customer (exactly the case that then hits the pending queue).

**Fix:** either an `aliases` lookup table (`customer_id, alias COLLATE NOCASE`,
indexed, rewritten on upsert), or an in-memory alias map on `Database` rebuilt
on `upsert_customer`/`delete_customer`. The table is cleaner and makes the alias
a real queryable thing.

### 2.5 Every write commits individually
`core/database.py:140-146`

`_exec` commits after each statement. In WAL mode that is a durability point per
row; the router writes several rows per attachment (activity + document record),
and `emit_event` writes one per log line.

**Fix:** low priority at this workload. If logging ever feels sluggish, add a
`transaction()` context manager and wrap the per-message route in it.

### 2.6 Indexes
`processed_emails.message_id` is `UNIQUE` (indexed) and `processed_documents`
is covered by the two indexes at `core/database.py:99-101`. Only `activity_log`
lacks one - handled by 2.3. No other action.

---

## 3. Network / provider efficiency  (P1/P2)

### 3.1 Every request refreshes the OAuth token
`integrations/accounting/xero.py:58-74`, `integrations/accounting/myob.py:76-102`,
`integrations/accounting/quickbooks.py:61-71`

`_headers()` calls `_access_token()`, which POSTs to the token endpoint. So
`test_connection()` is two round trips, each `upload_invoice` is two-to-four,
and combined with 1.5 this multiplies by the attachment count. Xero and QBO also
*rotate* the refresh token on every refresh, so each redundant call additionally
writes an encrypted setting to the DB.

**Fix:** cache `(access_token, expires_at)` on the provider instance and reuse
it until ~60s before expiry (the token responses carry `expires_in`). With 1.5
this drops to one refresh per platform per poll.

### 3.2 No connection reuse anywhere
Every provider module and `integrations/email_outlook.py` calls
`requests.get/post/put` at module level, so each call is a fresh TCP+TLS
handshake to the same host.

**Fix:** one `requests.Session` per provider instance (and one for the Graph
backend), used for all of its calls. Nearly free to do, meaningful on the
multi-call upload paths.

### 3.3 Graph result set is capped at 100 with no pagination
`integrations/email_outlook.py:308-315`

`$top=100` with no `@odata.nextLink` follow-up. After a long outage the
back-check silently sees only the newest 100 messages.

**Fix:** follow `@odata.nextLink` up to a sane page budget. This is a
correctness gap as much as an efficiency one.

### 3.4 Whole-file reads before upload
`myob.py:160-161` (read + base64 into memory), `xero.py:146-148`,
`quickbooks.py:98-102`, `servicem8.py:94-98`

**Fix:** pass the open file handle to `requests` (`data=fh`) where the API takes
a raw body - Xero and ServiceM8 both do. MYOB needs base64 inside JSON, so leave
it, but bound it with a size check and a clear error above ~20 MB.

---

## 4. GUI responsiveness  (P2)

### 4.1 The glow animation redraws 12.5x/second, forever
`gui/app.py:276-283`

An 80 ms `after` loop reconfiguring a border colour runs the whole time the
watcher is on - i.e. all day, on a background utility.

**Fix:** raise the interval to ~150-200 ms with matching phase resolution
(visually near-identical), and skip the `configure` entirely when the window is
withdrawn or iconified (`self.state() != "normal"`).

### 4.2 Connection tests block the UI thread
`gui/settings_tab.py:451-473`

`_test_service`, `_test_accounting` and especially `_test_outlook` run network
I/O inline. `_test_outlook` performs a *full mailbox fetch including attachment
downloads* on the Tk main thread - the window is frozen for its duration.

**Fix:** run each on a worker thread and post the result back via
`self._app.after(0, ...)`; `App.check_updates_now` (`gui/app.py:184-201`) already
demonstrates the pattern. For `_test_outlook`, add a "headers only / don't save
attachments" mode to `fetch` so the test is cheap.

### 4.3 Log repaint inserts one textbox line at a time
`gui/logs_tab.py:79-83`

Up to 500 tagged `insert` calls per refresh.

**Fix:** group consecutive same-level rows and insert each run as a single
string. Minor, but refresh runs on every search and after every retry.

### 4.4 Redundant per-row settings lookups
`gui/customers_tab.py:57-62`

`svc_label` and `acct_label` are computed correctly at `:52-53`, then
`label_for(self._app.settings.get(...))` is called *again inside the loop* for
each customer. Use the hoisted values. (Cheap alone; compounds with 2.1.)

---

## 5. Unbounded growth  (P2)

### 5.1 The attachment cache is never pruned
`config.py:21`; written by all three backends, read by the router and by
error-retry. Nothing deletes it. Every processed invoice stays on disk forever,
and with 1.1 and 1.3 unfixed it also accumulates duplicates and files no
customer wants.

**Fix:** a retention sweep on startup and after each poll - delete cache
sub-folders older than N days (default 30), keeping anything still referenced by
an unresolved `error_log.payload`. Expose the retention in the Watcher section.

### 5.2 The diagnostic log file grows without bound
`main.py:25-32` - plain `logging.FileHandler`.

**Fix:** `RotatingFileHandler(..., maxBytes=2_000_000, backupCount=3)`.

### 5.3 `activity_log` grows without bound
One row per emitted event (`gui/app.py:152-156`), and the tab only ever reads
the newest 500. "Clear log" is manual.

**Fix:** trim on startup to the newest N rows (e.g. 20,000), or age out beyond
90 days - a single `DELETE ... WHERE id < (SELECT ...)`.

---

## 6. Annotation pass  (P3, but explicitly in scope)

House rule: every function, method and class carries a one-line purpose
docstring, and non-obvious blocks get an explanatory comment. An AST sweep finds
**148 undocumented functions/classes**. Module docstrings are uniformly good;
the gap is almost entirely at method level.

| File | Undocumented |
|---|---|
| `core/database.py` | 21 - most public query/mutation methods (`list_customers`, `add_activity`, `search_activity`, `add_error`, `list_errors`, `add_pending`, `is_email_processed`, `last_seen_email_time`, ...) |
| `gui/settings_tab.py` | 16 - incl. `_save`, `_test_service`, `_test_accounting`, `_test_outlook`, `_oauth`, `_row`, `_dropdown` |
| `integrations/*` providers | 30+ - every provider **class** (`MyobProvider`, `XeroProvider`, `QuickBooksProvider`, `ServiceM8Provider`, and all 10 stub classes) plus their `test_connection` / `upload_invoice` / `_headers` / `_access_token` overrides |
| `gui/customers_tab.py` | 8 - `_build_form`, `_entry`, `_new`, `_load`, `_collect`, `_save`, `_delete` |
| `gui/app.py` | 8 - `_build_header`, `_build_tabs`, `_toggle_watcher`, `_scan_now`, `refresh_logs` |
| `core/updater.py` | 7 - `_state`, `_save_state`, `is_frozen`, `set_enabled`, `_is_newer`, `size_mb` |
| `core/watcher.py` | 4 - `running`, `start`, `_run` |
| remainder | `provider_base.py` (9 - the abstract contract methods, which most need it), `email_outlook.py` (5 - incl. all three `fetch` implementations), `settings_store.py` (5), `crypto.py`, `parser_ai.py`, dialogs |

**Approach:** one pass per package (`core/`, `integrations/`, `gui/`), docstrings
only - no behavioural edits mixed in, so the diff stays reviewable. The three
`fetch` implementations and the `Provider` contract methods deserve two or three
lines each (what they return, what they raise); the rest are one-liners.

Re-run the AST sweep after each pass to confirm the count reaches zero:

```bash
python -c "import ast,pathlib;print(sum(1 for p in pathlib.Path('.').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and not ast.get_docstring(n)))"
```

---

## 7. Smaller items  (P3)

* `gui/logs_tab.py:12` - `FONT_UI` imported and never used.
* `core/database.py:28` + `gui/customers_tab.py:153` - `myob_enabled` is a
  retained column nothing reads; routing uses the two generic toggles. Keep the
  column (migration cost) but drop it from `upsert_customer`'s write set, or
  mark it vestigial in the schema comment.
* `integrations/email_outlook.py:343-346` - `datetime.fromisoformat` is computed
  in the local-filter pass and again at `:349` when building the message. Parse
  once.
* `core/database.py:133-134` - the two `PRAGMA` statements execute outside
  `self._lock`. Harmless today (no other thread exists at construction), but
  move them inside the `with self._lock:` block for consistency.
* `core/parser_ai.py:252` - `extract_text` runs over *every* attachment
  including `.png`/`.jpg`, which always return `""`. Skip extensions the
  extractor cannot handle rather than calling into it.
* `core/updater.py:39-53` - `_save_state` re-reads and re-parses the JSON file
  on each write. Two keys, called rarely; noted only.

---

## Suggested order

1. **1.1 + 1.3** - stop re-downloading, and stop saving files nobody wants.
   Biggest saving, contained blast radius.
2. **2.1** - remove the settings full-table scan; everything else gets cheaper.
3. **3.1 + 1.5** - token caching plus one provider instance per email.
4. **4.2** - un-freeze the Settings window.
5. **1.2** - the IMAP two-pass fetch (largest single change; do it alone).
6. **5.1 + 5.2 + 5.3** - retention for cache, log file, activity table.
7. **6** - annotation pass, one package at a time.
8. **3.3** - Graph pagination (correctness; schedule with a version bump).
9. **7** - cleanup sweep.

Each numbered step is independently shippable and should carry its own
`version.py` patch bump per the release process.
