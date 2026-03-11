"""
Credit report analysis service using GPT-4o Vision.
Extracted from dispute_ui.py — handles full report analysis for clients.
"""

import json
from openai import OpenAI
from dotenv import load_dotenv
from services.pdf_parser import extract_pdf_metrics, pdf_to_base64_images

load_dotenv()

openai_client = OpenAI()


def run_report_analysis(pdf_path):
    """
    Run a full credit report analysis using GPT-4o Vision.

    Args:
        pdf_path: Path to the credit report PDF file.

    Returns:
        dict with analysis results including summary, status, recommendations,
        score_factors, inaccurate_accounts, incomplete_accounts, and numeric_fields.
    """
    metrics = extract_pdf_metrics(pdf_path)
    parsed_negative_count = metrics.get("negative_count", 0)
    parsed_collections_count = metrics.get("total_collections", 0)

    base64_images = pdf_to_base64_images(pdf_path)

    vision_prompt = f"""
You are a senior credit analyst trained in U.S. consumer credit laws, FICO scoring models, and bank underwriting data points.

IMPORTANT:
These numbers were deterministically parsed and MUST be used exactly:
- Negative Accounts: {parsed_negative_count}
- Collection Accounts: {parsed_collections_count}

TASK:
1. Summarize FICO, utilization %, total debt, negative & collection counts, avg/oldest age.
2. Classify as "Needs Repair", "Thin Profile", or "Funding Ready".
3. Provide 3–4 aggressive action steps.
4. List 3–5 score factors.
5. Scan the report images and identify:
    Inaccurate Reporting: any account whose payment-history buckets do not progress correctly (e.g., 30, 30, 60 instead of 30, 60, 90).
    Incomplete Information: any account grid missing required fields (e.g., missing monthly payment, missing account type, etc.).

OUTPUT ONLY valid JSON:
{{
  "summary": "...",
  "status": "...",
  "recommendations": [...],
  "score_factors": [...],
  "inaccurate_accounts": [
    {{
      "account_name": "...",
      "account_number": "...",
      "issue": "payment buckets [30,30,60] do not progress"
    }}
  ],
  "incomplete_accounts": [
    {{
      "account_name": "...",
      "account_number": "...",
      "missing_fields": ["monthly payment"]
    }}
  ],
  "numeric_fields": {{
    "credit_score": int|null,
    "utilization":  int|null,
    "total_debt":   int|null,
    "total_collections": {parsed_collections_count},
    "negative_accounts":  {parsed_negative_count},
    "average_age_years": "...",
    "oldest_account_years": "..."
  }}
}}
"""

    vision_inputs = [
        {"type": "image_url", "image_url": {"url": img, "detail": "high"}}
        for img in base64_images
    ] + [{"type": "text", "text": vision_prompt}]

    resp = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": vision_inputs}],
        temperature=0.3
    )

    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```json"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    analysis = json.loads(raw)

    # Enforce numeric consistency
    num = analysis.get("numeric_fields", {})
    analysis["num_collections"] = parsed_collections_count
    analysis["negative_count"] = parsed_negative_count
    analysis["fico_score"] = num.get("credit_score", "N/A")
    analysis["utilization"] = num.get("utilization", 0)
    analysis["total_debt"] = num.get("total_debt", 0)
    analysis["average_credit_age"] = num.get("average_age_years", "N/A")
    analysis["oldest_account_age"] = num.get("oldest_account_years", "N/A")
    analysis["summary_text"] = analysis.get("summary", "")
    analysis["status_text"] = analysis.get("status", "")
    analysis["recommendations"] = analysis.get("recommendations", [])

    return analysis
