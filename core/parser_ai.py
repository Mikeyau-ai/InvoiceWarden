"""AI-assisted extraction of customer + job reference from an invoice.

Text is gathered from the email body and any PDF/DOCX attachment, then handed
to an LLM with a strict JSON-output prompt. A regex fallback runs if no AI key
is configured or the call fails, so the watcher still functions.

Every provider is called over its plain REST API with ``requests`` - no vendor
SDKs. Supported out of the box:

  * ``openai``            - OpenAI / ChatGPT  (api.openai.com)
  * ``gemini``            - Google Gemini     (generativelanguage.googleapis.com)
  * ``anthropic``         - Anthropic Claude  (api.anthropic.com)
  * ``openai_compatible`` - any OpenAI-compatible ``/chat/completions`` endpoint:
                            Azure OpenAI, OpenRouter, Groq, Together, Mistral,
                            DeepSeek, or a local server (Ollama, LM Studio,
                            llama.cpp, vLLM). Set the base URL in Settings.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_PROMPT = """You extract structured data from invoices a business has RECEIVED from its
suppliers (accounts payable), so it can file them against the right job.
Return ONLY minified JSON with these keys:
  customer_name  - the SUPPLIER: the business that ISSUED this invoice and is
                   owed the money. This is an INCOMING invoice, so it is the
                   company on the letterhead / in the "From" details / whose
                   bank or ABN is given for payment. Do NOT return the "Bill
                   To" / "To" / "Deliver To" party - that is the recipient
                   reading this, not the supplier.
  job_number     - job / work order number if present, else ""
  invoice_ref    - the invoice / credit note number, else ""
  amount_total   - total incl. tax as a number string, else ""
  invoice_date   - ISO date if present, else ""
  doc_type       - what KIND of document this is, one of:
                     "invoice"    - a supplier invoice/bill asking for payment
                     "credit"     - a credit of any kind (see below)
                     "statement"  - a statement of account listing several
                                    invoices; NOT itself a payable document
                     "quote"      - a quotation or estimate
                     "remittance" - a remittance/payment advice
                     "delivery"   - a delivery docket or packing slip
                     "other"      - anything else (catalogue, terms, letter)
                   Only "invoice" and "credit" get filed. If the document is
                   genuinely an invoice, say invoice even when it also mentions
                   a quote or purchase order number.
                   "credit" applies if this is a CREDIT of any kind (credit note,
                   credit memo, adjustment note, credit invoice, refund),
                   otherwise "invoice". The word "credit" is the deciding
                   signal - but ignore ordinary payment wording such as
                   "credit card", "credit terms" or "credit limit", which
                   appears on normal invoices.
                   NOTE: a CREDIT NOTE usually has NO job number - it quotes
                   the ORIGINAL INVOICE it is crediting. For a credit, put that
                   original invoice number in job_candidates so it can be
                   traced back to the right job.
  job_candidates - ARRAY of every reference number on the document that could
                   plausibly be the job number, best guess first. Include the
                   job number, any purchase order number, and any other long
                   number you are unsure about. Getting the JOB number right
                   matters more than anything else here - when in doubt list
                   the extra candidate rather than omitting it.
  confidence     - 0..1 float, your confidence in customer_name
Do not include commentary. If unknown, use "".

Suppliers label these fields inconsistently. Treat all of these as the
INVOICE reference: "Invoice No/Number/#/ID", "Tax Invoice", "Inv", "Bill No",
"Document No", "Statement No", "Our Ref", "Your Ref", "Reference".
Treat all of these as the JOB number: "Job", "Job No/Number/Ref", "Work
Order", "W/O", "Service Order", "Service Call", "Ticket", "Order No",
"Purchase Order"/"PO". If both a job number and a purchase order appear,
prefer the one labelled as a job.

The ATTACHMENT FILE NAMES are a strong hint: invoice PDFs are often named
like "Customer10160.pdf", "INV-1042_Customer.pdf" or "10160 Testco.pdf" - a
run of letters is usually the customer and a run of 3+ digits is usually the
job number or invoice number. Use them when the text is sparse or scanned.

--- EMAIL FROM (usually the supplier's own domain) ---
{sender}
--- EMAIL SUBJECT ---
{subject}
--- ATTACHMENT FILE NAMES ---
{filenames}
--- EMAIL BODY ---
{body}
--- ATTACHMENT TEXT ---
{attachment}
"""


@dataclass
class ParseResult:
    """Normalised extraction output."""

    customer_name: str = ""
    job_number: str = ""
    invoice_ref: str = ""
    amount_total: str = ""
    invoice_date: str = ""
    doc_type: str = "invoice"        # "invoice" | "credit"
    #: Every plausible job/reference number found, best guess first. The job
    #: number is the field that actually has to be right, so the service
    #: provider verifies these against real jobs instead of trusting one guess.
    job_candidates: list = field(default_factory=list)
    confidence: float = 0.0
    source: str = "regex"

    def as_dict(self) -> dict:
        """Plain-dict form, used for the retry payload snapshot."""
        return asdict(self)


# --------------------------------------------------------------------------
# Attachment text extraction
# --------------------------------------------------------------------------
#: Extensions :func:`extract_text` can actually read. Anything else (images,
#: archives) would only ever return an empty string, so it is never opened.
_TEXT_EXTRACTABLE = {"pdf", "docx", "csv", "txt"}


def extract_text(path: Path, limit: int = 12000) -> str:
    """Best-effort plain-text extraction from a single attachment."""
    ext = path.suffix.lower().lstrip(".")
    try:
        if ext == "pdf":
            from pdfminer.high_level import extract_text as pdf_extract
            return (pdf_extract(str(path)) or "")[:limit]
        if ext == "docx":
            import docx
            doc = docx.Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)[:limit]
        if ext in ("csv", "txt"):
            return path.read_text(errors="ignore")[:limit]
    except Exception as exc:
        log.warning("Text extraction failed for %s: %s", path.name, exc)
    return ""


# --------------------------------------------------------------------------
# AI providers (all plain REST)
# --------------------------------------------------------------------------
#: provider_key -> UI/behaviour metadata. `key_setting` is the settings key
#: holding that provider's API key; `needs_key` is False for local servers.
AI_PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI (ChatGPT)",
        "key_setting": "ai.openai_api_key",
        "default_model": "gpt-4o-mini",
        "needs_key": True,
        "needs_base_url": False,
    },
    "gemini": {
        "label": "Google Gemini",
        "key_setting": "ai.gemini_api_key",
        "default_model": "",   # resolved from the API - ids get retired
        "needs_key": True,
        "needs_base_url": False,
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "key_setting": "ai.anthropic_api_key",
        "default_model": "claude-sonnet-5",
        "needs_key": True,
        "needs_base_url": False,
    },
    "openai_compatible": {
        "label": "OpenAI-compatible (Azure / OpenRouter / Groq / Ollama / ...)",
        "key_setting": "ai.compat_api_key",
        "default_model": "",
        "needs_key": False,
        "needs_base_url": True,
    },
}

_HTTP_TIMEOUT = 45


def _post(url: str, headers: dict, payload: dict) -> dict:
    """POST JSON, raise for HTTP errors, return the decoded body."""
    resp = requests.post(url, headers={"Content-Type": "application/json", **headers},
                         json=payload, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _call_openai_chat(api_key: str, model: str, prompt: str,
                      base_url: str = "https://api.openai.com/v1") -> str:
    """OpenAI /chat/completions - also used for every OpenAI-compatible host."""
    base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body = {
        "model": model or "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    try:
        data = _post(f"{base_url}/chat/completions", headers, body)
    except requests.HTTPError as exc:
        # Some compatible servers reject response_format - retry without it.
        if exc.response is not None and exc.response.status_code == 400:
            body.pop("response_format", None)
            data = _post(f"{base_url}/chat/completions", headers, body)
        else:
            raise
    return data["choices"][0]["message"]["content"] or ""


_GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta"

#: Tried in order when no model is configured. Google retires model ids without
#: warning (gemini-1.5-flash started returning 404), so never rely on one name.
_GEMINI_CANDIDATES = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
)

#: For a FREE Gemini key ("ai.gemini_plan" = "free"): the Flash-Lite models have a far bigger
#: free daily allowance (hundreds of requests, against ~20 for the newest Flash models, as of
#: 2026-10) and read invoices well. Google changes these limits without notice.
_GEMINI_FREE_CANDIDATES = (
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
)

#: Cache of the model id discovered from the API (per plan), so we ask at most once.
_gemini_resolved: dict[bool, str] = {}


def gemini_free(settings) -> bool:
    """True when the user said their Gemini key is on Google's free tier."""
    return (settings.get("ai.gemini_plan", "") or "").strip() == "free"


def gemini_available_models(api_key: str) -> list[str]:
    """Model ids this key can actually call generateContent on.

    Asking the API beats hardcoding: it stays correct when Google renames or
    retires models, and it turns a bare 404 into an actionable list.
    """
    resp = requests.get(f"{_GEMINI_ROOT}/models",
                        headers={"x-goog-api-key": api_key}, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    out = []
    for m in resp.json().get("models", []):
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            out.append((m.get("name") or "").removeprefix("models/"))
    return [m for m in out if m]


def _pick_gemini_model(api_key: str, free: bool = False) -> str:
    """Best available model: a preferred candidate, else any flash, else any.

    A free key prefers Flash-Lite (see _GEMINI_FREE_CANDIDATES); otherwise the full Flash.
    """
    if _gemini_resolved.get(free):
        return _gemini_resolved[free]
    models = gemini_available_models(api_key)
    chosen = ""
    for want in (_GEMINI_FREE_CANDIDATES if free else ()) + _GEMINI_CANDIDATES:
        if want in models:
            chosen = want
            break
    else:
        lite = [m for m in models if "flash-lite" in m] if free else []
        flash = [m for m in models if "flash" in m and "thinking" not in m]
        chosen = (lite or flash or models or [""])[0]
    if chosen:
        _gemini_resolved[free] = chosen
        log.info("Gemini model resolved to %s%s", chosen, " (free key)" if free else "")
    return chosen


def _gemini_generate(api_key: str, model: str, prompt: str) -> str:
    """One generateContent call against a specific model id."""
    data = _post(f"{_GEMINI_ROOT}/models/{model}:generateContent",
                 {"x-goog-api-key": api_key}, {
                     "contents": [{"parts": [{"text": prompt}]}],
                     "generationConfig": {"response_mime_type": "application/json",
                                          "temperature": 0},
                 })
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _call_gemini(api_key: str, model: str, prompt: str, free: bool = False) -> str:
    """Google Generative Language API, resilient to retired model ids.

    A configured model is tried first; on 404 (retired/renamed/not available to
    this key) we ask the API what it does offer and retry once.
    """
    chosen = (model or "").strip()
    if chosen:
        try:
            return _gemini_generate(api_key, chosen, prompt)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            log.warning("Gemini model %s returned 404; discovering a current one.",
                        chosen)

    fallback = _pick_gemini_model(api_key, free)
    if not fallback:
        raise RuntimeError(
            "This Gemini API key has no models that support generateContent. "
            "Check the key at aistudio.google.com.")
    try:
        return _gemini_generate(api_key, fallback, prompt)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise RuntimeError(
                f"Gemini rejected both '{chosen or '(default)'}' and the "
                f"discovered '{fallback}'. Models this key can use: "
                f"{', '.join(gemini_available_models(api_key)[:12]) or '(none)'}"
            ) from exc
        raise


def _call_anthropic(api_key: str, model: str, prompt: str) -> str:
    """Anthropic Messages API."""
    data = _post("https://api.anthropic.com/v1/messages", {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }, {
        "model": model or "claude-sonnet-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    })
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


def _coerce_json(text: str) -> dict:
    """Pull the first JSON object out of a model response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------
# Regex fallback
# --------------------------------------------------------------------------
def _from_filename(names: str) -> tuple[str, str]:
    """Pull (customer, number) from a filename like 'testco10160.pdf' or
    '10160 Testco.pdf' / 'INV-1042_Acme.pdf'. Returns ('', '') if nothing fits."""
    for raw in names.splitlines():
        stem = re.sub(r"\.(pdf|docx?|csv|xlsx?|png|jpe?g)$", "", raw.strip(), flags=re.I)
        stem = re.sub(r"\b(inv(oice)?|invoice|bill)\b", " ", stem, flags=re.I)
        letters = re.search(r"[A-Za-z][A-Za-z&' ]{1,}[A-Za-z]", stem)
        digits = re.search(r"\d{3,}", stem)
        if letters or digits:
            name = re.sub(r"[_\-]+", " ", letters.group(0)).strip() if letters else ""
            return name, (digits.group(0) if digits else "")
    return "", ""


#: Phrases where "credit" is ordinary invoice wording, not a credit note -
#: e.g. "payment by credit card", "credit terms 30 days", "credit limit".
_CREDIT_FALSE_FRIENDS = re.compile(
    r"\bcredit\s*(?:card|limit|terms?|account|application|check|rating|"
    r"control|hold|balance|facility|score|union|available)\b", re.I)

#: Unambiguous credit-document wording.
_CREDIT_STRONG = re.compile(
    r"\bcredit\s*(?:note|memo|invoice|adjustment|advice)\b"
    r"|\badjustment\s*note\b"
    r"|\bcredit\s*not[ei]\b"
    r"|\brefund\b"
    r"|\bRCTI\s*credit\b", re.I)


#: Document kinds that are NOT payable and must not be filed against a job.
NON_INVOICE_KINDS = {"statement", "quote", "remittance", "delivery", "other"}

#: Ordered strongest-first: the first pattern to hit decides the kind. Credit
#: is checked separately (see _looks_like_credit) because it is the one label
#: suppliers are consistent about.
_KIND_PATTERNS = (
    ("statement", r"statement\s+of\s+account|monthly\s+statement"
                  r"|brought\s+forward|ageing\s+summary"),
    ("remittance", r"remittance\s+advice|payment\s+advice"
                   r"|remittance"),
    ("quote", r"quotation|quote\s*(?:no|number|#)|estimate\s*(?:no|#)"),
    ("delivery", r"delivery\s+(?:docket|note)|packing\s+(?:slip|list)"
                 r"|consignment\s+note"),
)

#: Wording that means the document really is a payable invoice, which wins over
#: an incidental mention of a quote or delivery number.
_IS_INVOICE = re.compile(r"tax\s*invoice|invoice\s*(?:no|number|#|date)"
                         r"|amount\s+due|please\s+pay", re.I)


def _normalise_kind(value) -> str:
    """Coerce a model's doc_type answer to one of the known kinds."""
    text = str(value or "").strip().lower()
    if text.startswith("credit"):
        return "credit"
    for kind in ("statement", "remittance", "quote", "delivery", "other"):
        if text.startswith(kind):
            return kind
    return "invoice"


def classify_document(blob: str) -> str:
    """Best-effort document kind from its wording.

    Suppliers send statements, quotes, remittances and delivery dockets to the
    same mailbox as invoices. Filing those against a job is wrong, so identify
    them and skip rather than assume every attachment is payable.
    """
    if _looks_like_credit(blob):
        return "credit"
    if _IS_INVOICE.search(blob):
        return "invoice"
    for kind, pattern in _KIND_PATTERNS:
        if re.search(pattern, blob, re.I):
            return kind
    # Default to invoice: skipping a real invoice is worse than filing an
    # unclassifiable one, which a human will see on the job anyway.
    return "invoice"


def _looks_like_credit(blob: str) -> bool:
    """True when the document is a credit rather than an invoice.

    "Credit" is the one label that stays consistent across suppliers, so treat
    it as the deciding signal - but only after removing the phrases where
    "credit" is just payment wording on a normal invoice.
    """
    if _CREDIT_STRONG.search(blob):
        return True
    # Plain "credit" still counts, once the false friends are stripped out.
    cleaned = _CREDIT_FALSE_FRIENDS.sub(" ", blob)
    return bool(re.search(r"\bcredits?\b", cleaned, re.I))


def _reference_candidates(blob: str, filenames: str, *first) -> list[str]:
    """Every number that might be the job, best guess first.

    The parser cannot know which number on an invoice is the JOB number - a
    document often carries an invoice number, a PO and a job number, and
    suppliers label them inconsistently. So collect them all and let the
    service system say which one is a real job.

    Order: explicitly labelled values, then digits in the filename, then any
    other 3+ digit run in the document.
    """
    out: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip(" -/:#")
        if value and any(c.isdigit() for c in value) and value not in out:
            out.append(value)

    for value in first:                      # labelled job / invoice / filename
        add(value)
    for m in re.finditer(r"\d{3,}", filenames):
        add(m.group(0))
    for m in re.finditer(r"[A-Za-z]{0,4}[-/]?\d{3,}", blob):
        add(m.group(0))
    return out[:12]                          # bounded: each costs a lookup


def _supplier_from_sender(sender: str) -> str:
    """Best-guess supplier name from the sending email address.

    An incoming invoice almost always arrives from the supplier's own domain,
    which is a far better signal than anything labelled on the page.
    """
    match = re.search(r"@([A-Za-z0-9.-]+)", sender or "")
    if not match:
        return ""
    host = match.group(1).lower()
    # Drop the public suffix and any generic mail host.
    parts = [p for p in host.split(".")
             if p not in {"com", "net", "org", "au", "nz", "co", "uk", "www"}]
    name = parts[0] if parts else ""
    if name in {"gmail", "outlook", "hotmail", "yahoo", "bigpond", "live",
                "icloud", "me", "msn", "optusnet", "iinet"}:
        return ""            # a personal mailbox tells us nothing
    return name


def _letterhead(attachment: str) -> str:
    """First substantial line of the document - usually the supplier name."""
    for line in (attachment or "").splitlines():
        line = line.strip(" 	|-")
        if len(line) < 3 or len(line) > 60:
            continue
        if not any(c.isalpha() for c in line):
            continue
        if re.search(r"tax\s*invoice|invoice|statement|credit\s*note|abn|acn",
                     line, re.I):
            continue
        return line
    return ""


def _regex_fallback(subject: str, body: str, attachment: str,
                    filenames: str = "", sender: str = "") -> ParseResult:
    """Cheap heuristic extraction when AI is unavailable."""
    blob = f"{subject}\n{body}\n{attachment}\n{filenames}"
    # \b anchors stop "Invoice" itself being consumed as "inv" + "oice", and
    # the label words are skipped so "Job Reference: 10160" yields 10160 rather
    # than the word "Reference".
    # Trade invoices label these fields inconsistently, so match a broad
    # vocabulary rather than one phrasing. Ordered most- to least-specific:
    # an explicit "Job No" should win over a generic "Reference".
    _SEP = r"[\s.:#=\-]{0,4}"
    # A reference always contains a digit; allow an alpha prefix (INV-1042),
    # slashes and hyphens (WO/2026-14).
    _VALUE = r"([A-Za-z]{0,5}[-/ ]?\d[A-Za-z0-9\-/]*)"

    _JOB_LABELS = [
        r"job\s*(?:no|number|num|id|ref(?:erence)?|#)",
        r"\bjob\b",
        r"work\s*order\s*(?:no|number|num|#)?",
        r"\bw[/.]?o\b\s*(?:no|number|#)?",
        r"service\s*(?:order|call|ticket|job)\s*(?:no|number|#)?",
        r"ticket\s*(?:no|number|#)?",
        r"(?:purchase\s*order|\bp[./]?o\b)\s*(?:no|number|num|#)?",
        r"order\s*(?:no|number|num|#)",
        r"site\s*ref(?:erence)?\s*(?:no|number|#)?",
    ]
    _INV_LABELS = [
        r"tax\s*invoice\s*(?:no|number|num|id|#)?",
        r"credit\s*note\s*(?:no|number|num|#)?",
        r"invoice\s*(?:no|number|num|id|#)",
        r"\binvoice\b",
        r"\binv\b\s*(?:no|number|#)?",
        r"bill\s*(?:no|number|num|#)",
        r"(?:document|doc)\s*(?:no|number|num|#)",
        r"statement\s*(?:no|number|#)",
        r"(?:our|your|customer|client)\s*ref(?:erence)?\s*(?:no|number|#)?",
        r"\bref(?:erence)?\b\s*(?:no|number|#)?",
    ]

    def _find_labelled(labels: list[str]) -> str:
        """First value whose label matches, trying labels in priority order."""
        for label in labels:
            for m in re.finditer(label + _SEP + _VALUE, blob, re.I):
                value = m.group(1).strip(" -/")
                if any(ch.isdigit() for ch in value):
                    return value
        return ""

    job_number = _find_labelled(_JOB_LABELS)
    invoice_ref = _find_labelled(_INV_LABELS)
    # Deliberately NOT "bill to"/"to": on an incoming invoice that party is us.
    cust = re.search(r"(?:from|supplier|vendor|issued\s*by|remit\s*to|pay\s*to)"
                     r"\s*[:\-]\s*(.+)", blob, re.I)
    kind = classify_document(blob)

    fn_name, fn_num = _from_filename(filenames)
    candidates = _reference_candidates(blob, filenames, job_number, invoice_ref, fn_num)
    customer = ((cust.group(1).splitlines()[0].strip() if cust else "")
                or _supplier_from_sender(sender)
                or _letterhead(attachment)
                or fn_name)
    job_number = job_number or fn_num
    return ParseResult(
        customer_name=customer,
        job_number=job_number,
        invoice_ref=invoice_ref,
        doc_type=kind,
        job_candidates=candidates,
        confidence=0.5 if (customer and job_number) else (0.2 if customer else 0.0),
        source="regex+filename" if (fn_name or fn_num) else "regex",
    )


def _ai_config(settings) -> tuple[str, dict, str, str, str]:
    """Resolve the configured AI provider into (key_name, meta, model, api_key, base_url)."""
    provider = settings.get("ai.provider", "gemini")
    meta = AI_PROVIDERS.get(provider, AI_PROVIDERS["gemini"])
    return (provider, meta,
            settings.get("ai.model", ""),
            settings.get(meta["key_setting"]),
            settings.get("ai.compat_base_url"))


def _call_provider(provider: str, api_key: str, model: str, prompt: str,
                   base_url: str = "", free: bool = False) -> str:
    """Send one prompt to whichever provider is configured; return raw text."""
    if provider == "openai":
        return _call_openai_chat(api_key, model, prompt)
    if provider == "openai_compatible":
        return _call_openai_chat(api_key, model, prompt, base_url)
    if provider == "anthropic":
        return _call_anthropic(api_key, model, prompt)
    return _call_gemini(api_key, model, prompt, free)


# A miniature invoice used by the Settings "Test AI" button. Exercising the
# real prompt means the test proves the whole path - key, endpoint, model name
# and JSON-mode support - not merely that the host answers.
_TEST_SUBJECT = "Invoice INV-1042 for Acme Pty Ltd"
_TEST_BODY = ("Bill to: Acme Pty Ltd\n"
              "Job #10160\n"
              "Invoice number: INV-1042\n"
              "Total incl GST: $250.00\n")


def test_ai_provider(settings) -> tuple[bool, str]:
    """Round-trip a small synthetic invoice through the configured AI provider.

    Returns ``(ok, detail)`` for direct display in the Settings status box.
    """
    provider, meta, model, api_key, base_url = _ai_config(settings)
    label = meta["label"]
    shown_model = model or meta["default_model"] or "server default"

    if meta["needs_key"] and not api_key:
        return False, (f"{label}: no API key saved. Enter the key above and "
                       f"click Save settings first.")
    if meta["needs_base_url"] and not base_url:
        return False, (f"{label}: no base URL set. It must point at an "
                       f"OpenAI-compatible endpoint ending in /v1.")

    prompt = _PROMPT.format(sender="accounts@acme.com.au", subject=_TEST_SUBJECT,
                            body=_TEST_BODY, attachment="",
                            filenames="INV-1042_Acme.pdf")
    try:
        raw = _call_provider(provider, api_key, model, prompt, base_url,
                             free=gemini_free(settings))
    except Exception as exc:
        return False, f"{label} ({shown_model}) failed: {exc}"

    data = _coerce_json(raw)
    if not data:
        return False, (f"{label} ({shown_model}) replied, but not with usable "
                       f"JSON: {raw[:200]!r}")
    got = str(data.get("customer_name", "")).strip()
    if "acme" not in got.lower():
        return True, (f"{label} ({shown_model}) works, but extracted customer "
                      f"'{got or '(blank)'}' from the test invoice instead of "
                      f"'Acme Pty Ltd' - a weaker model may misread real ones.")
    return True, (f"{label} ({shown_model}) OK - test invoice parsed as "
                  f"customer '{got}', job "
                  f"'{str(data.get('job_number', '')).strip() or '-'}', ref "
                  f"'{str(data.get('invoice_ref', '')).strip() or '-'}'.")


class InvoiceParser:
    """Chooses a provider based on settings and produces a :class:`ParseResult`."""

    def __init__(self, settings) -> None:
        """Bind the parser to the settings store holding the AI config."""
        self._settings = settings

    def parse(self, subject: str, body: str, attachments: list[Path],
              sender: str = "") -> ParseResult:
        """Run extraction over one email + its attachments."""
        # Only bother extracting from formats the extractor actually handles -
        # images and unknown types always come back empty.
        attachment_text = "\n\n".join(
            extract_text(p) for p in attachments
            if p.suffix.lower().lstrip(".") in _TEXT_EXTRACTABLE)[:12000]
        filenames = "\n".join(p.name for p in attachments)
        provider, meta, model, key, base_url = _ai_config(self._settings)

        # Local / compatible servers may not need a key; everyone else does.
        if key or not meta["needs_key"]:
            prompt = _PROMPT.format(subject=subject, body=body[:6000],
                                    attachment=attachment_text,
                                    filenames=filenames, sender=sender)
            try:
                raw = _call_provider(provider, key, model, prompt, base_url,
                                     free=gemini_free(self._settings))
                data = _coerce_json(raw)
                if data:
                    return ParseResult(
                        customer_name=str(data.get("customer_name", "")).strip(),
                        job_number=str(data.get("job_number", "")).strip(),
                        invoice_ref=str(data.get("invoice_ref", "")).strip(),
                        amount_total=str(data.get("amount_total", "")).strip(),
                        invoice_date=str(data.get("invoice_date", "")).strip(),
                        job_candidates=[str(v).strip() for v in
                                        (data.get("job_candidates") or [])
                                        if str(v).strip()],
                        doc_type=_normalise_kind(data.get("doc_type")),
                        confidence=float(data.get("confidence", 0) or 0),
                        source=provider,
                    )
            except Exception as exc:
                log.error("AI parse failed (%s); using regex fallback: %s", provider, exc)

        return _regex_fallback(subject, body, attachment_text, filenames, sender)
