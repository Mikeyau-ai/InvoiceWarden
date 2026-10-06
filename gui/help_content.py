"""Help text for the Settings tab.

* :data:`FIELD_HELP`  - one/two lines shown by the little ``?`` beside a row.
* :data:`SETUP_GUIDES` - step-by-step "where do I get these" per provider,
  shown by the "Setup guide" button in each section header and by the
  Help tab.

Nothing here is secret; it is just navigation help for each vendor portal.
Portal names/paths were accurate at build time - vendors move menus around,
so treat the paths as a strong hint rather than gospel.
"""
from __future__ import annotations

FIELD_HELP: dict[str, str] = {
    # -- ServiceM8 --
    "servicem8.api_key":
        "ServiceM8 > top-right menu > Add-ons > 'For Developers' > Create a "
        "Private Application. Copy the API Key it shows. Needs a plan tier that "
        "allows API access.",
    # -- simPRO --
    "simpro.build_url":
        "Your simPRO address, e.g. https://yourcompany.simprosuite.com "
        "(the URL you log in at).",
    "simpro.client_id":
        "simPRO > System > Setup > Utilities > API Access (or 'Integrations'). "
        "Create an OAuth2 client and copy the Client ID.",
    "simpro.client_secret":
        "Shown once when you create the OAuth2 client in simPRO API Access. "
        "Regenerate there if lost.",
    "simpro.company_id":
        "simPRO API is per-company. Find the numeric Company ID under "
        "System > Companies, or call GET /api/v1.0/companies/.",
    # -- AroFlo --
    "aroflo.api_key":
        "AroFlo > Site Administration > Integrations > AroFlo API. API access "
        "must be switched on by AroFlo support first.",
    "aroflo.api_secret":
        "Issued alongside the API Key on the AroFlo API integration page.",
    "aroflo.org_encoded":
        "The 'Organisation Encoded' string on the same AroFlo API page - "
        "identifies your org in signed requests.",
    # -- Tradify --
    "tradify.client_id":
        "Tradify API is partner-access only. Request credentials at "
        "tradify.com/integrations or email api@tradify.com; they issue a "
        "Client ID.",
    "tradify.client_secret":
        "Issued with the Client ID by the Tradify integrations team.",
    "tradify.refresh_token":
        "Filled automatically after you complete 'Authorise OAuth' once.",
    # -- Fergus --
    "fergus.personal_access_token":
        "Fergus > your avatar > Settings > Fergus API (or Developer). Generate "
        "a Personal Access Token. Ask Fergus support to enable API if you "
        "don't see the menu.",
    # -- Jobber --
    "jobber.client_id":
        "developer.getjobber.com > sign in > Apps > New App. Copy the Client ID.",
    "jobber.client_secret":
        "Shown with the Client ID in the Jobber Developer Center app settings.",
    "jobber.redirect_uri":
        "Any URL you control that you also enter in the Jobber app's 'Redirect "
        "URIs'. e.g. http://localhost:8765/callback",
    "jobber.refresh_token":
        "Filled automatically after you complete 'Authorise OAuth' once.",
    # -- ServiceTitan --
    "servicetitan.client_id":
        "developer.servicetitan.io > Applications > create app, then have your "
        "ServiceTitan admin approve it in Settings > Integrations > API "
        "Application Access. Client ID is on the app page.",
    "servicetitan.client_secret":
        "Generated on the ServiceTitan developer app page next to the Client ID.",
    "servicetitan.app_key":
        "The 'ST-App-Key' shown after your admin grants the app access to your "
        "tenant.",
    "servicetitan.tenant_id":
        "Your ServiceTitan account (tenant) ID - shown in the integration "
        "approval screen and in the API docs 'Try it' console.",
    # -- Housecall Pro --
    "housecallpro.api_key":
        "Housecall Pro > Settings > API & Integrations (Max plan) - generate an "
        "API key, or request one from Housecall Pro support.",

    # -- Xero --
    "xero.client_id":
        "developer.xero.com > My Apps > New app (choose 'Web app'). After "
        "creating it, open 'Configuration' - Client id is there.",
    "xero.client_secret":
        "Same Xero app > Configuration > 'Generate a secret'. Copy it "
        "immediately.",
    "xero.redirect_uri":
        "Add a redirect URI in the Xero app config and paste the same value "
        "here, e.g. http://localhost:8765/callback",
    "xero.tenant_id":
        "The organisation's tenant id. After 'Authorise OAuth', call "
        "GET https://api.xero.com/connections - use the 'tenantId'. Leave blank "
        "and re-test to have it hinted in the logs.",
    "xero.refresh_token":
        "Filled automatically after you complete 'Authorise OAuth' once.",
    # -- MYOB --
    "myob.client_id":
        "my.myob.com.au > Developer (developer.myob.com) > Register a new app. "
        "'API Key' = Client ID.",
    "myob.client_secret":
        "Shown with the API Key on the MYOB developer app page.",
    "myob.redirect_uri":
        "Must exactly match the 'Redirect URL' set on the MYOB developer app, "
        "e.g. http://localhost:8765/callback",
    "myob.company_file_id":
        "The company file GUID. Call GET https://api.myob.com/accountright/ "
        "after auth - use the file's 'Id'.",
    "myob.cf_username":
        "The user name you use to sign in to that MYOB company file "
        "(company-file level login, not my.myob).",
    "myob.cf_password":
        "Password for that company-file user. Base64(user:pass) is sent as the "
        "x-myobapi-cftoken header - the app does the encoding.",
    "myob.refresh_token":
        "Filled automatically after you complete 'Authorise OAuth' once.",
    # -- QuickBooks Online --
    "qbo.client_id":
        "developer.intuit.com > Dashboard > your app > 'Keys & credentials'. "
        "Use the Production keys once live.",
    "qbo.client_secret":
        "Same Intuit app > Keys & credentials, next to the Client ID.",
    "qbo.redirect_uri":
        "Add it under the Intuit app > Redirect URIs and paste the same value, "
        "e.g. http://localhost:8765/callback",
    "qbo.realm_id":
        "The company id. Shown when you connect the app (OAuth Playground) or "
        "in the URL of your QuickBooks company. Sometimes called 'Company ID'.",
    "qbo.refresh_token":
        "Filled automatically after you complete 'Authorise OAuth' once.",
    # -- Reckon / Sage / FreshBooks --
    "reckon.client_id":
        "developer.reckon.com > register an app for the Reckon One API.",
    "reckon.client_secret": "Issued with the Client ID on the Reckon developer portal.",
    "reckon.redirect_uri": "Match the value set on the Reckon app, e.g. http://localhost:8765/callback",
    "reckon.book_id": "The Reckon One 'Book' id - in the book's URL or via GET /book.",
    "reckon.refresh_token": "Filled automatically after 'Authorise OAuth'.",
    "sage.client_id": "developer.sage.com > Sage Accounting > create an app for OAuth2.",
    "sage.client_secret": "Issued with the Client ID on the Sage developer portal.",
    "sage.redirect_uri": "Match the value set on the Sage app.",
    "sage.refresh_token": "Filled automatically after 'Authorise OAuth'.",
    "freshbooks.client_id": "my.freshbooks.com > Developer > create an app.",
    "freshbooks.client_secret": "Issued with the Client ID in the FreshBooks app settings.",
    "freshbooks.account_id": "Your FreshBooks account id - the code in your dashboard URL.",
    "freshbooks.refresh_token": "Filled automatically after 'Authorise OAuth'.",

    # -- Outlook --
    "outlook.account":
        "The mailbox to watch, e.g. accounts@yourco.com.au. For the COM "
        "backend it must be an account already added to your Outlook desktop "
        "profile.",
    "outlook.folder":
        "Folder display name to scan. 'Inbox' by default; use the exact name "
        "for a sub-folder or rule target.",
    "outlook.graph_client_id":
        "OPTIONAL - leave blank. InvoiceWarden ships with its own Application "
        "(client) ID, so no Azure setup is needed: just add an email account "
        "and click 'Sign in'. Only fill this in if your organisation requires "
        "its own app registration; see the Setup guide for how to create one.",
    "outlook.graph_tenant":
        "Leave blank (defaults to 'common', which accepts both personal "
        "outlook.com and work/school accounts). Only set this to your Directory "
        "(tenant) ID if you registered the app as single-tenant.",

    # -- IMAP --
    "imap.host":
        "Your provider's IMAP server. Gmail: imap.gmail.com. Fastmail: "
        "imap.fastmail.com. Yahoo: imap.mail.yahoo.com. iCloud: "
        "imap.mail.me.com. Business/cPanel hosts are usually "
        "mail.yourdomain.com - check your host's 'email client settings' page.",
    "imap.port":
        "993 for IMAP over SSL, which is what almost everyone uses. Only change "
        "it if your provider documents a different port.",
    "imap.username":
        "Usually your full email address. Some business hosts use a separate "
        "mailbox username - check your host's client settings page.",
    "imap.password":
        "An APP PASSWORD, not your normal login password. Gmail: turn on "
        "2-Step Verification, then myaccount.google.com/apppasswords. Yahoo, "
        "iCloud and Fastmail have the same feature under account security. "
        "outlook.com no longer issues working app passwords - use the Graph "
        "backend for those accounts.",
    "imap.folder":
        "INBOX by default. For a sub-folder use the full path as the server "
        "names it, e.g. 'INBOX/Invoices'. If the name is wrong, Test mailbox "
        "lists the folders it can see.",

    # -- AI --
    "ai.model":
        "Model name for the chosen provider. Leave blank to use the default. "
        "OpenAI: gpt-4o-mini. Gemini: gemini-1.5-flash / gemini-2.0-flash. "
        "Anthropic: claude-sonnet-5 (or a Haiku model). For an OpenAI-"
        "compatible host use whatever model id that host exposes.",
    "ai.openai_api_key":
        "platform.openai.com > sign in > Dashboard > API keys > 'Create new "
        "secret key'. Add billing credit under Settings > Billing first.",
    "ai.gemini_api_key":
        "aistudio.google.com > 'Get API key' > Create API key. Free tier is "
        "usually enough for invoice parsing volume.",
    "ai.anthropic_api_key":
        "console.anthropic.com > Settings > API keys > Create Key. Add billing "
        "credit first.",
    "ai.compat_api_key":
        "API key for the OpenAI-compatible host: OpenRouter (openrouter.ai/keys), "
        "Groq (console.groq.com/keys), Together, Azure OpenAI, etc. Leave blank "
        "for a local server (Ollama / LM Studio) that needs no key.",
    "ai.compat_base_url":
        "Base URL of the OpenAI-compatible endpoint, ending in /v1. Examples: "
        "https://openrouter.ai/api/v1 , https://api.groq.com/openai/v1 , "
        "http://localhost:11434/v1 (Ollama), http://localhost:1234/v1 (LM Studio).",

    "watcher.poll_minutes": "How often to check the inbox while the watcher runs.",
}

SETUP_GUIDES: dict[str, str] = {
    "servicem8": (
        "ServiceM8 - Private Application API key\n"
        "1. Sign in to ServiceM8 on the web as an account owner.\n"
        "2. Top-right menu > Add-ons.\n"
        "3. Scroll to 'For Developers' > 'Create a Private Application'.\n"
        "4. Give it a name; tick the scopes you need (at minimum: Job, "
        "Attachment, Client - read/write for attachments).\n"
        "5. Save. Copy the 'API Key' shown.\n"
        "Paste it into the Private App API Key field.\n\n"
        "Job matching: the invoice's job number is matched to a ServiceM8 job "
        "on 'generated_job_id' first, then the internal job number. Make sure "
        "your suppliers quote the ServiceM8 job number on their invoice."
    ),
    "simpro": (
        "simPRO - OAuth2 (client credentials)\n"
        "1. simPRO > System > Setup > Utilities > API Access "
        "(older builds: Setup > Integrations > API).\n"
        "2. Add an OAuth2 client; note the Client ID and Client Secret.\n"
        "3. Build URL is your login URL, e.g. https://acme.simprosuite.com\n"
        "4. Company ID: System > Companies, or GET /api/v1.0/companies/.\n"
        "5. Ensure the client has scope for Jobs and Attachments.\n"
        "Then click 'Authorise OAuth' in Settings."
    ),
    "aroflo": (
        "AroFlo - signed API\n"
        "1. Email AroFlo support / your account manager to enable API access.\n"
        "2. Once enabled: Site Administration > Integrations > AroFlo API.\n"
        "3. Copy API Key, API Secret and the Organisation Encoded value.\n"
        "AroFlo signs each request with an HMAC of these - the app handles it."
    ),
    "tradify": (
        "Tradify - partner OAuth2\n"
        "Tradify does not offer self-serve API keys. Apply at "
        "tradify.com/integrations or email api@tradify.com describing the "
        "integration. They issue a Client ID + Secret and whitelist your "
        "redirect URI. Then use 'Authorise OAuth'."
    ),
    "fergus": (
        "Fergus - Personal Access Token\n"
        "1. If you don't see an API menu, ask Fergus support to turn on API "
        "access for your account.\n"
        "2. Avatar > Settings > Fergus API (or Developer).\n"
        "3. Generate a Personal Access Token and copy it.\n"
    ),
    "jobber": (
        "Jobber - OAuth2\n"
        "1. developer.getjobber.com > sign in with your Jobber login.\n"
        "2. Apps > New App. Set the Redirect URI (e.g. "
        "http://localhost:8765/callback) and request the Jobs + Notes/"
        "Attachments scopes.\n"
        "3. Copy Client ID + Client Secret.\n"
        "4. Put the same redirect URI here, then 'Authorise OAuth'."
    ),
    "servicetitan": (
        "ServiceTitan - OAuth2 + App Key\n"
        "1. developer.servicetitan.io > Applications > create an application.\n"
        "2. In ServiceTitan: Settings > Integrations > API Application Access - "
        "an admin approves your app for the tenant and reveals the ST-App-Key.\n"
        "3. Copy Client ID, Client Secret, App Key, and your Tenant ID.\n"
    ),
    "housecallpro": (
        "Housecall Pro - API key\n"
        "API access is on the Max plan. Settings > API & Integrations to "
        "generate a key, or contact Housecall Pro support / their developer "
        "portal for one."
    ),
    "xero": (
        "Xero - OAuth2\n"
        "1. developer.xero.com > My Apps > New app.\n"
        "2. App type 'Web app'. Company URL and a Redirect URI you control "
        "(e.g. http://localhost:8765/callback).\n"
        "3. Open the app > Configuration: copy Client id, 'Generate a secret'.\n"
        "4. Paste Client ID, Secret and the same Redirect URI here.\n"
        "5. 'Authorise OAuth', sign in, pick the organisation.\n"
        "6. Tenant ID: call GET https://api.xero.com/connections (or re-run "
        "'Test accounting' and read it from the log) and paste it in.\n"
        "Scopes needed: accounting.transactions, accounting.attachments, "
        "accounting.contacts, offline_access."
    ),
    "myob": (
        "MYOB (AccountRight / Business) - OAuth2 + company file\n"
        "1. developer.myob.com > sign in > Register a new app ('Desktop').\n"
        "2. Set the Redirect URL (e.g. http://localhost:8765/callback).\n"
        "3. Copy 'API Key' (= Client ID) and the Client Secret.\n"
        "4. 'Authorise OAuth' here, sign in with my.myob.\n"
        "5. Company File ID: call GET https://api.myob.com/accountright/ - copy "
        "the file's Id (GUID).\n"
        "6. Company File Username/Password: the login for that specific file "
        "(set under Setup > User Access in the file). If the file has no "
        "password, use the file 'Administrator' with a blank password."
    ),
    "qbo": (
        "QuickBooks Online - OAuth2\n"
        "1. developer.intuit.com > Dashboard > Create an app > QuickBooks "
        "Online Accounting.\n"
        "2. Keys & credentials: copy Client ID + Client Secret (Development "
        "keys first, Production once approved).\n"
        "3. Add your Redirect URI (e.g. http://localhost:8765/callback).\n"
        "4. 'Authorise OAuth' here and connect a company.\n"
        "5. Realm ID (Company ID) is shown on connect and in the OAuth "
        "playground; paste it in."
    ),
    "reckon": (
        "Reckon One - OAuth2\n"
        "developer.reckon.com > register an app for the Reckon One API. Copy "
        "Client ID/Secret, set the redirect URI, then 'Authorise OAuth'. Book "
        "ID is in your Reckon One book URL."
    ),
    "sage": (
        "Sage Accounting - OAuth2\n"
        "developer.sage.com > Sage Accounting > create an app. Copy Client "
        "ID/Secret, set the callback URL, then 'Authorise OAuth'."
    ),
    "freshbooks": (
        "FreshBooks - OAuth2\n"
        "my.freshbooks.com > Developer > create an app. Copy Client ID/Secret, "
        "set the redirect URI, then 'Authorise OAuth'. Account ID is the code "
        "in your FreshBooks dashboard URL."
    ),
    "outlook_com": (
        "Outlook - COM backend (CLASSIC desktop Outlook only)\n"
        "No credentials. The app automates the classic Outlook desktop client "
        "already signed in on this PC.\n"
        "- Enter the mailbox address in 'Mailbox / account to monitor' exactly "
        "as it appears in Outlook (or leave blank for the default account). If "
        "it doesn't match, Test Outlook lists the accounts it can see.\n"
        "- Outlook must be running (or allowed to start).\n"
        "- 'Folder name' defaults to Inbox; set a sub-folder name if a rule "
        "sorts invoices there.\n\n"
        "IMPORTANT - when COM will NOT work:\n"
        "- The NEW Outlook for Windows (the Store app) has no COM automation "
        "interface at all.\n"
        "- Classic Outlook requires a paid Microsoft 365 subscription.\n"
        "- If Test Outlook reports only an 'Outlook Data File' with 0 items, "
        "your mail is not in a classic profile.\n"
        "In any of those cases use the 'graph' backend instead."
    ),
    "outlook_graph": (
        "Email - Microsoft Graph (Microsoft 365 and Outlook.com)\n"
        "Use this for the new Outlook for Windows, Microsoft 365 and personal "
        "outlook.com accounts. COM cannot reach those, and Microsoft disabled "
        "app-password/IMAP access for personal accounts on 16 Sept 2024.\n\n"
        "NORMAL SETUP - no Azure, nothing to configure:\n"
        "1. Settings > Email accounts > '+ Add an email account'.\n"
        "2. Type the address, leave Backend on 'graph'.\n"
        "3. Click 'Sign in'. A short code appears; the browser opens "
        "Microsoft's device-login page. Enter the code, sign in as that "
        "mailbox, approve the permission.\n"
        "4. The row then shows 'signed in as ...'. Click Test.\n"
        "InvoiceWarden ships with its own Application (client) ID, so customers "
        "never touch the Azure portal. Each mailbox needs its OWN sign-in - "
        "repeat for every account, up to 10.\n\n"
        "Notes:\n"
        "- The browser may finish on a Microsoft page saying 'This is not the "
        "right page'. That is that redirect target's normal behaviour; the "
        "sign-in window shows the real result.\n"
        "- A work/school tenant may ask an administrator to approve InvoiceWarden "
        "once for the organisation. That is a normal consent prompt.\n"
        "- Sign-ins are stored encrypted on this PC and refresh themselves.\n\n"
        "OPTIONAL - use your own app registration instead:\n"
        "Only needed if your organisation requires it. entra.microsoft.com > "
        "Entra ID > App registrations > New registration; supported account "
        "types 'Any Entra ID Tenant + Personal Microsoft accounts'; "
        "Authentication > 'Allow public client flows' = Yes AND add a platform "
        "'Mobile and desktop applications' with the redirect URI "
        "https://login.microsoftonline.com/common/oauth2/nativeclient; API "
        "permissions > Microsoft Graph > Delegated > Mail.Read. Then paste the "
        "Application (client) ID into the field below."
    ),
    "triage": (
        "What gets filed, and what gets skipped\n"
        "Suppliers send more than invoices to the same mailbox, so every "
        "attachment is classified first. Only INVOICES and CREDIT NOTES are "
        "filed against a job.\n\n"
        "Skipped, with a line in the Activity Log saying so:\n"
        "  statement   - a statement of account listing several invoices\n"
        "  quote       - a quotation or estimate\n"
        "  remittance  - a remittance / payment advice\n"
        "  delivery    - a delivery docket or packing slip\n"
        "A document that mentions a quote or PO but is clearly a tax invoice "
        "is still treated as an invoice. Anything unrecognisable defaults to "
        "invoice: skipping a real invoice is worse than filing an odd one, "
        "which you will see sitting on the job anyway.\n\n"
        "CONFIDENCE\n"
        "Settings > Watcher > 'Min confidence to add a supplier' (default "
        "0.4). If the supplier name is read with less confidence than this AND "
        "the supplier is not already on file, the invoice is HELD rather than "
        "inventing a supplier from a doubtful reading. It appears in the Error "
        "Log; add the supplier in the Suppliers tab and retry.\n"
        "This only gates CREATING a supplier. An invoice matching a supplier "
        "already on file always routes - the name match is its own evidence."
    ),
    "matching": (
        "How invoices are matched to jobs\n"
        "This is the part that has to be right, so it does not rely on a single\n"
        "guess.\n\n"
        "INVOICES\n"
        "Every reference number on the document is collected - the labelled job\n"
        "number first, then the filename digits, then any other long number -\n"
        "and each is checked against real jobs in your service system, against\n"
        "the job number, the internal number and the purchase order number.\n"
        "The first one that matches a real job wins. So an invoice that shows\n"
        "an invoice number, a PO and a job number still lands correctly even if\n"
        "the labels are unusual or missing.\n"
        "If nothing matches, the error names every number that was tried, so it\n"
        "is obvious whether the document lacked a job number or the job does\n"
        "not exist yet.\n\n"
        "CREDIT NOTES\n"
        "Credits usually carry NO job number - they quote the invoice being\n"
        "credited. InvoiceWarden records which job every invoice was filed against,\n"
        "so a credit is linked by:\n"
        "  1. matching the invoice number it quotes to an invoice already\n"
        "     uploaded, and using that job; failing that\n"
        "  2. the most recent invoice for the same customer.\n"
        "Either way the Activity Log says which job it used and why. If the\n"
        "original invoice has not been processed, the credit is held with an\n"
        "explanation rather than filed against a guess.\n\n"
        "Improving matches: add the supplier's other trading names to Aliases,\n"
        "and make sure the original invoice is processed before its credit."
    ),
    "customers": (
        "Customers - how suppliers get added\n"
        "Unrecognised suppliers are added automatically as their first invoice "
        "arrives; you are never interrupted mid-scan. A new supplier gets:\n"
        "  - Service system upload: ON  (this is the point of the tool)\n"
        "  - Accounting system upload: OFF (deliberate decision, per customer)\n"
        "  - File types: PDF only\n"
        "and is marked '* NEW' in yellow until you look at it.\n\n"
        "Reviewing:\n"
        "- Sort the list by Name, Newest/Oldest added, or 'New / unreviewed "
        "first' (the default).\n"
        "- 'Show new only' hides everything you have already checked.\n"
        "- The counter under the controls shows how many are still new.\n"
        "- Opening a supplier, checking the toggles and clicking Save clears "
        "the NEW badge. That is all 'reviewing' means.\n\n"
        "Matching: an invoice matches on the supplier name or any of its "
        "Aliases, so add the supplier's other trading names there and future "
        "invoices match without creating a duplicate.\n\n"
        "If no supplier name can be read at all, the invoice is held instead "
        "of guessed - it appears in the Error Log so it can be retried once "
        "the supplier exists."
    ),
    "email_accounts": (
        "Email accounts - monitoring more than one mailbox\n"
        "InvoiceWarden can watch up to 10 mailboxes. Settings > Email accounts > "
        "'+ Add an email account' adds a row; each row has its own address, "
        "backend and credentials.\n\n"
        "Why credentials are per-account, not shared:\n"
        "- graph: each mailbox needs its own Microsoft sign-in (click 'Sign "
        "in' on that row). One sign-in only ever grants access to that "
        "mailbox.\n"
        "- imap: each mailbox has its own server and app password.\n"
        "- com: each row names a different mailbox in the local Outlook "
        "profile; no credentials needed.\n\n"
        "Rows can be mixed - e.g. two Microsoft 365 mailboxes plus a Gmail "
        "address - and the switch at the left of a row disables it without "
        "deleting its settings.\n\n"
        "Every mailbox is scanned on the same schedule and feeds the same "
        "customer database and duplicate checks, so the same invoice arriving "
        "at two addresses is still only uploaded once. If one mailbox fails, "
        "the others are still scanned and the failure is logged against that "
        "address.\n\n"
        "Use 'Test' on a row to check just that mailbox - it reads headers "
        "only and downloads nothing."
    ),
    "outlook_imap": (
        "Email - IMAP backend (Gmail, Fastmail, Yahoo, iCloud, business hosts)\n"
        "The simplest option when it is available: no Azure, no app "
        "registration, no subscription. You need the server name and an APP "
        "PASSWORD.\n\n"
        "Gmail (most common):\n"
        "1. myaccount.google.com > Security > turn ON 2-Step Verification "
        "(app passwords are unavailable without it).\n"
        "2. Go to myaccount.google.com/apppasswords\n"
        "3. Name it 'InvoiceWarden' and Create. Copy the 16-character password.\n"
        "4. In InvoiceWarden: IMAP server imap.gmail.com, Port 993, Username your "
        "full Gmail address, App password the 16 characters (spaces are fine), "
        "Folder INBOX.\n"
        "5. Save settings, then Test mailbox.\n\n"
        "Other providers - same idea, different server:\n"
        "  Fastmail  imap.fastmail.com    993\n"
        "  Yahoo     imap.mail.yahoo.com  993\n"
        "  iCloud    imap.mail.me.com     993\n"
        "  Zoho      imap.zoho.com        993\n"
        "  Business/cPanel: usually mail.yourdomain.com - see your host's "
        "'email client settings' page.\n\n"
        "IMPORTANT - outlook.com / hotmail.com / live.com:\n"
        "Microsoft turned off app-password (Basic auth) access for personal "
        "Microsoft accounts on 16 September 2024, so IMAP cannot work with "
        "them at all. Either use the 'graph' backend, or add a rule in "
        "Outlook.com that auto-forwards invoice emails to a Gmail address and "
        "point IMAP at that instead.\n\n"
        "Notes:\n"
        "- Mail is read with BODY.PEEK, so InvoiceWarden never marks your messages "
        "as read.\n"
        "- Only emails WITH attachments are queued; plain emails are ignored."
    ),
    "gemini": (
        "Google Gemini API key\n"
        "1. aistudio.google.com > sign in.\n"
        "2. 'Get API key' (left nav) > Create API key > pick / create a "
        "Google Cloud project.\n"
        "3. Copy the key. Model: gemini-1.5-flash or gemini-2.0-flash.\n"
        "The free tier limit is usually well above invoice-parsing volume."
    ),
    "anthropic": (
        "Anthropic (Claude) API key\n"
        "1. console.anthropic.com > sign in.\n"
        "2. Add a payment method / credit under Billing.\n"
        "3. Settings > API keys > Create Key. Copy it now (shown once).\n"
        "Model: claude-sonnet-5 (or a Haiku model for lower cost)."
    ),
}
