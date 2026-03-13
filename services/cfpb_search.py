"""
CFPB Consumer Complaint Database search — public API, no auth required.
Used by business plan users to find complaint narratives for dispute support.
"""

import logging
import requests

logger = logging.getLogger(__name__)

CFPB_API_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"


def search_complaints(company_name, limit=25, offset=0, has_narrative=None):
    """
    Search the CFPB complaint database by company name.

    Args:
        company_name: Company to search for (e.g., 'Equifax', 'Capital One')
        limit: Max results to return (default 25, max 100)
        has_narrative: If True, only return complaints with narratives

    Returns:
        Dict with 'total', 'complaints' list, and 'error' if any.
    """
    if not company_name or not company_name.strip():
        return {'total': 0, 'complaints': [], 'error': 'Company name required'}

    params = {
        'search_term': company_name.strip(),
        'field': 'company',
        'size': min(limit, 100),
        'frm': offset,
        'sort': 'created_date_desc',
        'format': 'json',
    }

    if has_narrative:
        params['has_narrative'] = 'true'

    try:
        resp = requests.get(CFPB_API_URL, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"CFPB API returned {resp.status_code}")
            return {'total': 0, 'complaints': [], 'error': f'API returned {resp.status_code}'}

        data = resp.json()
        hits = data.get('hits', {})
        total = hits.get('total', {})
        total_count = total.get('value', 0) if isinstance(total, dict) else total

        complaints = []
        for hit in hits.get('hits', []):
            src = hit.get('_source', {})
            complaints.append({
                'complaint_id': src.get('complaint_id'),
                'date_received': src.get('date_received'),
                'company': src.get('company'),
                'product': src.get('product'),
                'sub_product': src.get('sub_product'),
                'issue': src.get('issue'),
                'sub_issue': src.get('sub_issue'),
                'narrative': src.get('complaint_what_happened', ''),
                'company_response': src.get('company_response'),
                'timely_response': src.get('timely'),
                'consumer_disputed': src.get('consumer_disputed'),
                'state': src.get('state'),
            })

        return {'total': total_count, 'complaints': complaints}

    except requests.Timeout:
        return {'total': 0, 'complaints': [], 'error': 'Request timed out'}
    except Exception as e:
        logger.error(f"CFPB search error: {e}")
        return {'total': 0, 'complaints': [], 'error': str(e)}


def get_complaint_narratives(company_name, limit=10):
    """
    Convenience wrapper — returns only complaints that have narratives.
    Useful for pulling real complaint stories to reference in disputes.
    """
    return search_complaints(company_name, limit=limit, has_narrative=True)
