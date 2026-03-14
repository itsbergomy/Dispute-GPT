"""
CFPB Consumer Complaint Database search — uses the official CFPB API
with no_aggs=true and streaming to keep responses fast.
"""

import logging
import json
import requests

logger = logging.getLogger(__name__)

CFPB_API_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"

# Company response values that count as "wins"
WIN_RESPONSES = {
    'Closed with monetary relief',
    'Closed with non-monetary relief',
}
MONETARY_RESPONSES = {'Closed with monetary relief'}
NON_MONETARY_RESPONSES = {'Closed with non-monetary relief'}


def search_complaints(company_name, limit=25, offset=0, has_narrative=None,
                      response_filter=None):
    """
    Search the CFPB complaint database by company name.

    response_filter: None | 'wins' | 'monetary' | 'non_monetary'
      Applied client-side after fetching results.
    """
    if not company_name or not company_name.strip():
        return {'total': 0, 'complaints': [], 'error': 'Company name required'}

    # When filtering for wins, fetch more results since we'll filter down
    fetch_limit = limit * 4 if response_filter else limit

    params = {
        'search_term': company_name.strip(),
        'field': 'all',
        'size': min(fetch_limit, 100),
        'frm': offset,
        'sort': 'created_date_desc',
        'no_aggs': 'true',
        'format': 'json',
    }

    if has_narrative:
        params['has_narrative'] = 'true'

    try:
        resp = requests.get(CFPB_API_URL, params=params, stream=True, timeout=30)
        if resp.status_code != 200:
            resp.close()
            logger.warning(f"CFPB API returned {resp.status_code}")
            return {'total': 0, 'complaints': [], 'error': f'API returned {resp.status_code}'}

        # Stream-read chunks — no_aggs keeps responses manageable
        max_bytes = 1_000_000
        chunks = []
        bytes_read = 0
        for chunk in resp.iter_content(chunk_size=32_768, decode_unicode=True):
            if chunk:
                chunks.append(chunk)
                bytes_read += len(chunk.encode('utf-8') if isinstance(chunk, str) else chunk)
                if bytes_read >= max_bytes:
                    break
        resp.close()

        raw = ''.join(chunks) if isinstance(chunks[0], str) else b''.join(chunks).decode('utf-8', errors='replace')

        complaints = []
        total_count = 0

        raw_stripped = raw.strip()

        if raw_stripped.startswith('['):
            complaints, total_count = _parse_array_stream(raw_stripped, fetch_limit)
        elif raw_stripped.startswith('{'):
            complaints, total_count = _parse_es_stream(raw_stripped, fetch_limit)
        else:
            return {'total': 0, 'complaints': [], 'error': 'Unexpected API response format'}

        # Apply response filter client-side
        if response_filter:
            if response_filter == 'wins':
                target = WIN_RESPONSES
            elif response_filter == 'monetary':
                target = MONETARY_RESPONSES
            elif response_filter == 'non_monetary':
                target = NON_MONETARY_RESPONSES
            else:
                target = None

            if target:
                complaints = [c for c in complaints if c.get('company_response') in target]
                total_count = len(complaints)

        # Trim to requested limit
        complaints = complaints[:limit]

        return {'total': total_count, 'complaints': complaints}

    except requests.Timeout:
        return {'total': 0, 'complaints': [], 'error': 'Request timed out — try again'}
    except Exception as e:
        logger.error(f"CFPB search error: {e}")
        return {'total': 0, 'complaints': [], 'error': str(e)}


def _normalize_complaint(src):
    """Map raw CFPB fields to our standard format."""
    return {
        'complaint_id': src.get('complaint_id'),
        'date_received': (src.get('date_received') or '')[:10],
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
    }


def _parse_array_stream(raw, limit):
    """Parse a potentially truncated JSON array by extracting individual objects."""
    complaints = []
    try:
        data = json.loads(raw)
        for item in data[:limit]:
            src = item.get('_source', item)
            complaints.append(_normalize_complaint(src))
        return complaints, len(data)
    except json.JSONDecodeError:
        pass

    # Truncated — find complete top-level objects using brace counting
    i = 1
    while len(complaints) < limit and i < len(raw):
        start = raw.find('{', i)
        if start == -1:
            break

        depth = 0
        end = start
        for j in range(start, len(raw)):
            if raw[j] == '{':
                depth += 1
            elif raw[j] == '}':
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        else:
            break

        if depth != 0:
            break

        try:
            obj = json.loads(raw[start:end])
            src = obj.get('_source', obj)
            complaints.append(_normalize_complaint(src))
        except json.JSONDecodeError:
            pass

        i = end + 1

    return complaints, len(complaints)


def _parse_es_stream(raw, limit):
    """Parse a potentially truncated Elasticsearch-style response."""
    try:
        data = json.loads(raw)
        hits = data.get('hits', {})
        total = hits.get('total', {})
        total_count = total.get('value', 0) if isinstance(total, dict) else (total or 0)
        complaints = []
        for hit in hits.get('hits', [])[:limit]:
            src = hit.get('_source', {})
            complaints.append(_normalize_complaint(src))
        return complaints, total_count
    except json.JSONDecodeError:
        pass

    # Truncated — extract _source objects
    complaints = []
    search_from = 0
    while len(complaints) < limit:
        idx = raw.find('"_source"', search_from)
        if idx == -1:
            break

        brace_start = raw.find('{', idx + 9)
        if brace_start == -1:
            break

        depth = 0
        end = brace_start
        for j in range(brace_start, len(raw)):
            if raw[j] == '{':
                depth += 1
            elif raw[j] == '}':
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        else:
            break

        if depth != 0:
            break

        try:
            src = json.loads(raw[brace_start:end])
            complaints.append(_normalize_complaint(src))
        except json.JSONDecodeError:
            pass

        search_from = end

    # Try to extract total from the truncated response
    total_count = len(complaints)
    try:
        import re
        m = re.search(r'"total"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', raw)
        if m:
            total_count = int(m.group(1))
        else:
            m = re.search(r'"total"\s*:\s*(\d+)', raw)
            if m:
                total_count = int(m.group(1))
    except Exception:
        pass

    return complaints, total_count


def get_complaint_narratives(company_name, limit=10):
    """
    Convenience wrapper — returns only complaints that have narratives.
    """
    return search_complaints(company_name, limit=limit, has_narrative=True)
