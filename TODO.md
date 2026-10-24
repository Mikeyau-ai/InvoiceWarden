# InvoiceWarden: to do

From the app review on 2026-10-24. Verdict: **not ready to market yet** (listed "Coming soon"; 69/69 tests pass).
InvoiceWarden is bring-your-own-AI: customers enter their own AI key.

- [x] **Updates moved off public GitHub Releases** to the signed Sixth Day Studios update store (1.0.42,
      2026-10-24), so the repo could be made private.
- [ ] **After the 1.0.42 transition**: remove the `--github-transition` code from release.py once installed
      copies are on 1.0.42 or later.
- [ ] **Unsigned exe.** InvoiceWarden-X.Y.Z.exe triggers SmartScreen ("Windows protected your PC"). For a paid
      app that handles business email and invoices, code-sign it (or ship it through the Microsoft Store)
      before selling it.
- [ ] **Help page** on the website (/invoicewarden/help): setup needs the email account, a ServiceM8 API key and
      the customer's own AI key, which is where people will get stuck.
- [ ] **Live Stripe checkout exists** (sixthdaystudios.com/api/buy?p=invoicewarden-monthly / -yearly). The page
      doesn't link to it while "coming soon"; test trial, purchase, licence and cancellation end to end first.
- [ ] **Download link**: once released, point the website's download button at
      https://sixthdaystudios.com/api/updates/invoicewarden/download (it always serves the newest version).
