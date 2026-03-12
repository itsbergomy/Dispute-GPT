"""
Dispute letter generation service.
Extracted from dispute_ui.py — handles prompt packs and GPT letter generation.
"""

import os
import tempfile
from openai import OpenAI
from dotenv import load_dotenv
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.utils import ImageReader
from PyPDF2 import PdfMerger
from PIL import Image

load_dotenv()

openai_client = OpenAI()

# ─── Prompt Packs ───

PACKS = {
    "default": [
        "Write a letter {action} for {entity} regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}",
        "I need a letter {action} for {entity} about an issue regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}"
    ],
    "arbitration": [
        "Draft an arbitration demand under 15 U.S.C. 1681e(b) to {entity}, account {account_number}, stating {issue}. I will {action}.",
        "Compose a formal arbitration request for {entity}, referencing {issue}, account {account_number}, and demanding {action}."
    ],
    "consumer_law": [
        "Write a letter to {entity} invoking the Fair Credit Billing Act regarding {issue} on account {account_number}. I request {action}.",
        "Craft a demand under the Fair Debt Collection Practices Act for {entity}, account {account_number}, disputing {issue} and seeking {action}."
    ],
    "ACDV_response": [
        "Compose a formal demand letter {action} to {entity} about the disputed credit file. File/Account No: {account_number}, Dispute Date: {dispute_date}. Invoke *Cushman v. Trans Union Corp.*, 115 F.3d 220 (3d Cir. 1997), and demand immediate production of the full Automated Consumer Dispute Verification (ACDV) record, including **Method of Verification**, **Submission Procedure**, and **FCRA Compliance Policies**, with delivery required within {days} business days or outline available FCRA remedies.",
        "Compose a formal demand letter {action} to {entity} regarding flawed reinvestigation procedures. File/Account No: {account_number}, Dispute Date: {dispute_date}. Invoke *Giliespie v. Equifax Info. Servs.*, 484 F.3d 938, which held CRAs liable for unreasonable investigation processes, and demand documented proof of each step of your reinvestigation protocol—including source contact logs, verification methodologies, and internal quality-control policies—with delivery required within {days} business days or outline available FCRA remedies for failure to comply.",
        "Write a formal demand letter to {entity} demanding immediate production of the full Automated Consumer Dispute Verification (ACDV) record related to the {account_name} and {account_number} and previous {dispute_date}."
    ]
}

# Preamble injected before every prompt so GPT uses real client data
CLIENT_CONTEXT_PREAMBLE = (
    "Write this dispute letter on behalf of the client below. "
    "Use their REAL name and address in the letter header, body, and signature. "
    "Do NOT use placeholder text like [YOUR NAME], [ADDRESS], or {{CLIENT_NAME}}.\n\n"
    "Client: {client_full_name}\n"
    "Address: {client_address}\n"
    "{client_address_line2_section}"
    "City/State/ZIP: {client_city_state_zip}\n"
    "Date: {today_date}\n\n"
    "Recipient: {entity}\n"
    "{recipient_address_section}"
    "---\n\n"
)

PACK_INFO = [
    {"key": "default", "name": "📝 Default Pack", "description": "Your go-to dispute templates — clean, direct, and effective for first-round disputes."},
    {"key": "arbitration", "name": "⚖️ Arbitration Pack", "description": "Heavy hitters. Arbitration demands under 15 U.S.C. §1681e(b) — for when bureaus won't budge."},
    {"key": "consumer_law", "name": "🛡️ Consumer Law Pack", "description": "Cite the FCBA, FDCPA, and more — full statutory firepower for stubborn creditors."},
    {"key": "ACDV_response", "name": "🔍 ACDV Enforcement Pack", "description": "Demand the full ACDV record — method of verification, submission procedure, and FCRA compliance docs. Make them prove it."}
]


def generate_letter(prompt, model="gpt-4o"):
    """
    Generate a dispute letter using GPT.

    Args:
        prompt: The filled-in prompt template.
        model: OpenAI model to use.

    Returns:
        The generated letter text.
    """
    response = openai_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are DisputeGPT, a bot that creates credit dispute letters. Use your knowledge of UCC, CFPB regulations, and USC to write compelling letters that address inaccuracies and potential infringements by creditors."
            },
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content


def build_prompt(template_pack, template_index, context):
    """
    Build a filled prompt from a template pack, prepended with client context.

    Args:
        template_pack: Key from PACKS dict (e.g., 'default', 'arbitration').
        template_index: Index of the template within the pack.
        context: Dict with keys: entity, account_name, account_number, marks, action, issue,
                 and optionally client_full_name, client_address, client_city_state_zip,
                 today_date, dispute_date, days, etc.

    Returns:
        Filled prompt string with client preamble.
    """
    templates = PACKS.get(template_pack, PACKS['default'])
    idx = min(template_index, len(templates) - 1)

    # Ensure all expected keys have defaults
    ctx = {
        'entity': '',
        'account_name': '',
        'account_number': '',
        'marks': '',
        'action': '',
        'issue': '',
        'dispute_date': '',
        'days': '',
        'client_full_name': '',
        'client_address': '',
        'client_address_line2': '',
        'client_city_state_zip': '',
        'today_date': '',
        'creditor_address': '',
        'creditor_city_state_zip': '',
        'bureau_address': '',
    }
    ctx.update(context)

    # Build optional sections for preamble (only show if data exists)
    addr2 = ctx.get('client_address_line2', '').strip()
    ctx['client_address_line2_section'] = f"{addr2}\n" if addr2 else ''

    recip_addr = ctx.get('creditor_address') or ctx.get('bureau_address', '')
    recip_csz = ctx.get('creditor_city_state_zip', '').strip(', ')
    if recip_addr:
        ctx['recipient_address_section'] = f"Address: {recip_addr}\n{recip_csz}\n" if recip_csz else f"Address: {recip_addr}\n"
    else:
        ctx['recipient_address_section'] = ''

    # Prepend client context preamble + the template body
    preamble = CLIENT_CONTEXT_PREAMBLE.format(**ctx)
    body = templates[idx].format(**ctx)

    return preamble + body


def letter_to_pdf(letter_text, output_path=None):
    """
    Convert letter text to a PDF file.

    Args:
        letter_text: The letter content.
        output_path: Where to save the PDF. If None, uses a temp file.

    Returns:
        Path to the generated PDF.
    """
    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), 'letter.pdf')

    doc = SimpleDocTemplate(
        output_path, pagesize=LETTER,
        leftMargin=inch, rightMargin=inch,
        topMargin=inch, bottomMargin=inch
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='Body', parent=styles['Normal'],
        fontSize=11, leading=14
    ))
    story = []
    for line in letter_text.split('\n'):
        if not line.strip():
            story.append(Spacer(1, 12))
        else:
            story.append(Paragraph(line.strip(), styles['Body']))
    doc.build(story)
    return output_path


def image_to_pdf(image_path, output_path=None, field_type='other'):
    """
    Convert an image file to a single-page PDF with proper scaling.

    Args:
        image_path: Path to the image file.
        output_path: Where to save the PDF. If None, derives from image path.
        field_type: 'ssn_file' allows 130% upscale, others cap at 100%.

    Returns:
        Path to the generated PDF.
    """
    if output_path is None:
        output_path = os.path.splitext(image_path)[0] + '.pdf'

    page_w, page_h = LETTER
    margin = inch
    max_w = page_w - 2 * margin
    max_h = page_h - 2 * margin

    img = Image.open(image_path).convert('RGB')
    orig_w, orig_h = img.size

    raw_scale = min(max_w / orig_w, max_h / orig_h)

    if field_type == 'ssn_file':
        scale = min(raw_scale, 1.3)
    else:
        scale = min(raw_scale, 1.0)

    new_w = int(orig_w * scale)
    new_h = int(orig_h * scale)

    resized = img.resize((new_w, new_h), Image.LANCZOS)
    reader = ImageReader(resized)

    c = pdfcanvas.Canvas(output_path, pagesize=LETTER)
    x = (page_w - new_w) / 2
    y = (page_h - new_h) / 2
    c.drawImage(reader, x, y, width=new_w, height=new_h)
    c.showPage()
    c.save()

    return output_path


def merge_dispute_package(pdf_paths, output_path=None):
    """
    Merge multiple PDFs into a single DisputePackage.pdf.

    Args:
        pdf_paths: List of PDF file paths to merge.
        output_path: Where to save the merged PDF. If None, uses temp dir.

    Returns:
        Path to the merged PDF.
    """
    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), 'DisputePackage.pdf')

    merger = PdfMerger()
    for p in pdf_paths:
        merger.append(p)
    merger.write(output_path)
    merger.close()

    return output_path
