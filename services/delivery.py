"""
Letter delivery service — DocuPost integration for mailing dispute letters.
Extracted from dispute_ui.py.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

DOCUPOST_API_TOKEN = os.getenv("DOCUPOST_API_TOKEN")
DOCUPOST_SENDLETTER_URL = "https://app.docupost.com/api/1.1/wf/sendletter"


def mail_letter_via_docupost(pdf_url, recipient, sender, mail_options=None):
    """
    Send a letter via DocuPost USPS mailing service.

    Args:
        pdf_url: URL to the publicly hosted merged PDF.
        recipient: Dict with keys: name, company, address1, address2, city, state, zip.
        sender: Dict with keys: name, company, address1, address2, city, state, zip.
        mail_options: Optional dict with keys: mail_class, servicelevel, color,
                      doublesided, return_envelope, description.

    Returns:
        Dict with 'success' bool and 'response' or 'error'.
    """
    if not DOCUPOST_API_TOKEN:
        return {'success': False, 'error': 'DocuPost API token not configured'}

    options = mail_options or {}

    params = {
        'api_token': DOCUPOST_API_TOKEN,
        'pdf': pdf_url,
        # Recipient
        'to_name': recipient.get('name', ''),
        'to_company': recipient.get('company', ''),
        'to_address1': recipient.get('address1', ''),
        'to_address2': recipient.get('address2', ''),
        'to_city': recipient.get('city', ''),
        'to_state': recipient.get('state', ''),
        'to_zip': recipient.get('zip', ''),
        # Sender
        'from_name': sender.get('name', ''),
        'from_company': sender.get('company', ''),
        'from_address1': sender.get('address1', ''),
        'from_address2': sender.get('address2', ''),
        'from_city': sender.get('city', ''),
        'from_state': sender.get('state', ''),
        'from_zip': sender.get('zip', ''),
        # Mail options
        'class': options.get('mail_class', 'usps_first_class'),
        'servicelevel': options.get('servicelevel', ''),
        'color': options.get('color', 'false'),
        'doublesided': options.get('doublesided', 'true'),
        'return_envelope': options.get('return_envelope', 'false'),
        'description': options.get('description', ''),
    }

    try:
        resp = requests.post(DOCUPOST_SENDLETTER_URL, params=params)
        if resp.status_code == 200 and b"<Error>" not in resp.content:
            # Parse the JSON response for tracking info
            result = {'success': True, 'response': resp.text}
            try:
                data = resp.json()
                result['letter_id'] = data.get('letter_id')
                result['cost'] = data.get('cost')
            except (ValueError, KeyError):
                pass  # Response wasn't JSON — still treat as success
            return result
        else:
            return {'success': False, 'error': resp.text}
    except Exception as e:
        return {'success': False, 'error': str(e)}
