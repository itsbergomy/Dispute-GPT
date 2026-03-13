"""
PDF parsing and negative item extraction service.
Extracted from dispute_ui.py — all credit report PDF processing lives here.
"""

import re
import json
import hashlib
import base64
import pdfplumber
import fitz
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI()


def compute_pdf_hash(file_path):
    """Compute SHA-256 hash of a PDF file for deduplication."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while True:
            data = f.read(8192)
            if not data:
                break
            sha256.update(data)
    return sha256.hexdigest()


def pdf_to_base64_images(pdf_path, max_pages=5):
    """Convert PDF pages to base64-encoded PNG images for vision analysis."""
    images = []
    try:
        doc = fitz.open(pdf_path)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(dpi=150)
            image_bytes = pix.tobytes("png")
            b64_image = base64.b64encode(image_bytes).decode("utf-8")
            images.append(f"data:image/png;base64,{b64_image}")
    except Exception as e:
        raise ValueError(f"Failed to open PDF: {e}")
    return images


def detect_bureau(full_text):
    """Detect which credit bureau generated this report from header text."""
    header = full_text[:1000].lower()
    if 'experian' in header:
        return 'experian'
    elif 'transunion' in header:
        return 'transunion'
    elif 'equifax' in header:
        return 'equifax'
    return 'unknown'


def vision_filter_accounts(negative_items, file_path, max_pages=5):
    """Use GPT-4o Vision to validate which accounts are truly negative."""
    images = pdf_to_base64_images(file_path, max_pages=max_pages)

    accounts_summary = [
        {
            "account_name": acct["account_name"],
            "account_number": acct["account_number"],
            "status": acct["status"],
            "payment_history": acct.get("raw_payment_lines", [])
        }
        for acct in negative_items
    ]

    vision_prompt = f"""
We have extracted these accounts from a credit report PDF:

{json.dumps(accounts_summary, indent=2)}

IMPORTANT DEFINITIONS:
- A "late bucket" is any entry in the payment-history grid showing:
    • "30" (30 days past due)
    • "60" (60 days past due)
    • "90" (90 days past due)
    • "120" (120 days past due)
    • the words "Charge-off" (or "CO" when used to mean charge-off)
    • "C" (collection)

- A "clean" history line is one showing only:
    • a check-mark ✓
    • a dash "–"

- "CLS" means "closed in good standing" and is normally positive but IF you see any late bucket (30/60/90/120/CO/C) in the same grid, YOU MUST treat that whole account as negative.

TASK:
For each account above, look at both:
  1. Its status text (e.g. "Paid, Closed/Never Late", "Current", "Collection Account")
  2. Its payment-history grid (using the definitions above)

Mark an account as "skip" ONLY IF:
  • The status is positive (e.g. "Paid", "Never Late", "Closed", "Current"),
  • and its payment history grid shows **only** clean buckets (✓, or –),
  • and you see no late buckets (30, 60, 90, 120, CO, C).

Otherwise mark it as "keep".

RETURN ONLY valid JSON in this format:

[
  {{ "account_number": "12345", "action": "keep" }},
  {{ "account_number": "67890", "action": "skip" }},
  ...
]
"""

    vision_inputs = (
        [{"type": "image_url", "image_url": {"url": img, "detail": "high"}} for img in images]
        + [{"type": "text", "text": vision_prompt}]
    )
    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": vision_inputs}],
        temperature=0
    )

    raw = resp.choices[0].message.content or ""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    m = re.search(r"\[\s*\{.*\}\s*\]", raw, re.S)
    if not m:
        m = re.match(r"\[.*\]$", raw, re.S)
    if not m:
        return negative_items

    json_str = m.group(0)
    try:
        decisions = json.loads(json_str)
    except json.JSONDecodeError:
        return negative_items

    keep_set = {d["account_number"] for d in decisions if d.get("action") == "keep"}
    return [acct for acct in negative_items if acct["account_number"] in keep_set]


def _parse_experian(full_text):
    """Parse Experian credit report format using regex."""
    blocks = full_text.split("Account name")
    negative_items = []

    grid_regex = re.compile(r'\b(?:30|60|90|120|C)\b')
    clean_regex = re.compile(
        r'\b(?:open|current|(?:pays|paid)(?:\s+\w+)*\s+as\s+agreed|closed|never\s+late|'
        r'exceptional\s+payment\s+history)\b',
        re.IGNORECASE
    )
    status_regex = re.compile(
        r'\b(charged\s+off|charge-off|repossession|collection(?:\s+account)?|'
        r'past\s+due|delinquent|settlement|written\s+off)\b',
        re.IGNORECASE
    )

    for block in blocks[1:]:
        lines = block.strip().splitlines()
        data = {
            "account_name": None,
            "account_number": None,
            "account_type": None,
            "balance": None,
            "status": None,
            "issue": None
        }

        first = lines[0].strip()
        m = re.match(r"(.+?)\s+Balance", first)
        data["account_name"] = m.group(1).strip() if m else first

        payment_history = []
        in_ph = False
        for line in lines:
            low = line.lower()

            if "payment history" in low:
                in_ph = True
                continue
            if in_ph:
                if not line.strip() or line.lower().startswith(("account name", "account number")):
                    in_ph = False
                else:
                    payment_history.append(line.strip())
                continue

            if "account number" in low and not data["account_number"]:
                mm = re.search(r"account number[:\s-]*(\S+)", line, re.I)
                if mm:
                    data["account_number"] = mm.group(1).strip()

            if "account type" in low and not data["account_type"]:
                mm = re.search(r"account type[:\s]*(.+)", line, re.I)
                if mm:
                    data["account_type"] = mm.group(1).strip()

            if "balance" in low and not data["balance"]:
                mm = re.search(r"balance[:\s-]*\$?([\d,]+)", line, re.I)
                if mm:
                    data["balance"] = f"${mm.group(1).strip()}"

            if "status" in low and not data["status"]:
                mm = re.search(r"status[:\s]*(.+?)(?:\.|$)", line, re.I)
                if mm:
                    data["status"] = mm.group(1).strip()

        status_text = (data["status"] or "").strip()
        grid_text = " ".join(payment_history)

        if clean_regex.search(status_text) and not grid_regex.search(grid_text):
            continue

        grid_issue = bool(grid_regex.search(grid_text))
        status_issue = bool(status_regex.search(status_text))
        acct_issue = "collection" in (data["account_type"] or "").lower()

        if not (grid_issue or status_issue or acct_issue):
            continue

        if grid_issue:
            data["issue"] = "Late payments / Charge-off in payment history"
        elif status_issue:
            data["issue"] = status_text
        else:
            data["issue"] = "Collection account"

        negative_items.append(data)

    return negative_items


def _parse_with_vision_only(file_path):
    """Fallback parser: use GPT-4o Vision to extract accounts from any report format."""
    images = pdf_to_base64_images(file_path, max_pages=8)

    prompt = """Analyze this credit report and extract ALL negative/derogatory accounts.

For each negative account, return:
- account_name: The creditor/company name
- account_number: The account number
- account_type: Type of account (e.g., Collection, Installment, Revolving)
- balance: Current balance with $ sign
- status: Account status text
- issue: Why this account is negative (e.g., "Late payments", "Collection account", "Charge-off")

Skip any accounts that are current, paid as agreed, or in good standing with no late payments.

RETURN ONLY valid JSON array:
[
  {
    "account_name": "...",
    "account_number": "...",
    "account_type": "...",
    "balance": "$...",
    "status": "...",
    "issue": "..."
  }
]

If no negative accounts found, return: []
"""

    vision_inputs = (
        [{"type": "image_url", "image_url": {"url": img, "detail": "high"}} for img in images]
        + [{"type": "text", "text": prompt}]
    )

    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": vision_inputs}],
        temperature=0
    )

    raw = resp.choices[0].message.content or ""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        items = json.loads(raw)
        if isinstance(items, list):
            return items
    except json.JSONDecodeError:
        pass

    return []


def extract_negative_items_from_pdf(file_path):
    """
    Extract negative/derogatory items from a credit report PDF.
    Auto-detects bureau format and uses appropriate parser.
    Falls back to vision-only extraction for unknown formats.
    """
    with pdfplumber.open(file_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    bureau = detect_bureau(full_text)

    if bureau == 'experian':
        items = _parse_experian(full_text)
    else:
        # TransUnion, Equifax, and unknown formats use vision-based extraction
        items = _parse_with_vision_only(file_path)

    # Run vision filter on all results for validation
    items = vision_filter_accounts(items, file_path)

    return items


def extract_pdf_metrics(pdf_path):
    """Extract high-level metrics from a credit report PDF."""
    try:
        items = extract_negative_items_from_pdf(pdf_path)
    except Exception:
        return {'negative_count': 0, 'total_collections': 0}

    negative_count = len(items)
    total_collections = sum(1 for item in items if 'collection' in (item.get('issue') or '').lower())

    return {
        'negative_count': negative_count,
        'total_collections': total_collections
    }
