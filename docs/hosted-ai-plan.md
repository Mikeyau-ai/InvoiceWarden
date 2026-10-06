# Plan: Sixth Day Studios AI ("AI included")

Status: **planned, not built** (agreed with Mikey 2026-10-06). Today customers bring their own AI
key; the setup wizard and Settings offer a free Gemini key, a paid one, another AI service, or
"Request access" to this.

## Why

Getting an AI key is the one fiddly setup step. With this option a subscriber needs no key at all.
It's offered by request first, so we can see how many people want it before switching it on for
everyone.

## The one rule: our key never leaves our server

A key shipped inside the app can be dug out of the exe and abused, running up our bill. So:

1. **The key lives only on the website's server**, as a Cloudflare Worker secret
   (`GEMINI_API_KEY`), on a **paid** Google project, so Google doesn't use the invoices.
2. **The app sends the invoice text to us**, not to Google:
   `POST https://sixthdaystudios.com/api/ai/parse` with
   `{token, subject, body, attachment_text, filenames, sender}`.
   `token` is the app's signed InvoiceWarden licence token (the same Ed25519 licence system
   RamWarden uses), so we know it's a genuine, active copy.
3. **The server checks before spending anything**, refusing with a clear reason if:
   - the token's signature, product (`invoicewarden`) or expiry is wrong;
   - the licence isn't active (trial ended, subscription cancelled or refunded);
   - this licence has used its **monthly allowance** (start at 2,000 invoices; a D1 counter per
     licence per month);
   - the request is over a size limit (say 60 KB of text);
   - AI access hasn't been switched on for this licence (`licences.ai_enabled`, set from the dev
     page when we approve a request).
4. **The server writes the AI instructions itself** (the same prompt as `core/parser_ai.py`) and
   returns only the parsed fields (`customer_name, job_number, invoice_ref, amount_total,
   invoice_date, confidence`). It can't be used as a general chatbot: callers can't send their own
   instructions, and nothing else comes back.
5. **Backstops:** a monthly budget cap and alerts on the Google Cloud project; a global kill switch
   (a Worker variable) and a per-licence switch on the dev page; Guardian posts a notice if daily
   usage jumps.

Worst case: one paying customer uses up their own allowance, not a stranger draining our account.

## Privacy (has to be said plainly)

Invoices **pass through our server** on their way to the AI. We don't store them: only a count per
licence per month (and errors without invoice content). Before switching this on:

- **Privacy policy:** add a section saying exactly that (what passes through, that it isn't stored,
  that Google, as our AI provider on a paid project, doesn't use it for training).
- **Product page:** the "Your data stays with you" line becomes "...unless you choose Sixth Day
  Studios AI, where invoices pass through our server (not stored)".
- **App:** the choice already carries that note in the wizard; Settings needs the same wording.

## App side

- New AI provider `sixthday` in `AI_PROVIDERS` (`needs_key: False`), shown only when the licence
  says AI is enabled; it calls `/api/ai/parse` with the licence token.
- On "allowance used" or "not enabled", fall back to the regex parser and show one line on the
  Activity page ("AI allowance reached for this month: using basic matching until <date>").

## Cost

Gemini Flash-Lite on a paid key is a fraction of a cent per invoice. At 2,000 invoices a month that's
cents per customer, small against A$4.95. If usage grows, the allowance or the price can change.

## Order of work (when it's time)

1. Subscriptions live on Stripe, with InvoiceWarden licence tokens (needed first).
2. D1: `licences.ai_enabled`, an `ai_usage(licence_id, month, count)` table.
3. Worker route `/api/ai/parse` with the checks above, plus a dev-page switch and usage column.
4. Privacy policy and product page wording.
5. App provider + fallback message; tests with the network faked.
6. Try it with one or two customers who asked for access, then decide whether to offer it to all.
