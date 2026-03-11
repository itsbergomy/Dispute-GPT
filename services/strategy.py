"""
AI-driven dispute strategy engine.
Decides which accounts to dispute, with what templates, to which bureaus.
"""

import json
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI()

# Escalation ladder: maps round number to strategy parameters
ESCALATION_MAP = {
    1: {'pack': 'default',       'bureaus': ['experian'],                          'level': 1},
    2: {'pack': 'consumer_law',  'bureaus': ['experian', 'transunion', 'equifax'], 'level': 2},
    3: {'pack': 'ACDV_response', 'bureaus': ['experian', 'transunion', 'equifax'], 'level': 3},
    4: {'pack': 'arbitration',   'bureaus': ['experian', 'transunion', 'equifax'], 'level': 4},
    5: {'pack': 'cfpb',          'bureaus': ['cfpb'],                              'level': 5},
}


def get_escalation_config(round_number):
    """Get the escalation config for a given round number."""
    return ESCALATION_MAP.get(round_number, ESCALATION_MAP[5])


def select_accounts_for_dispute(negative_items, analysis_data=None, round_number=1,
                                 previously_disputed=None):
    """
    Use AI to select the best accounts to dispute this round.

    Args:
        negative_items: List of negative account dicts from PDF extraction.
        analysis_data: Optional report analysis dict with inaccurate/incomplete accounts.
        round_number: Current dispute round (1-based).
        previously_disputed: List of account dicts from prior rounds that were
                             verified or got no response (for re-dispute).

    Returns:
        List of dicts: [{"account_name", "account_number", "reason", "legal_basis"}]
    """
    if round_number > 1 and previously_disputed:
        # Re-dispute: only accounts that came back verified or no_response
        return [
            {
                "account_name": acct.get("account_name", ""),
                "account_number": acct.get("account_number", ""),
                "reason": f"Previous dispute was verified/no response. Escalating to round {round_number}.",
                "legal_basis": _get_legal_basis_for_round(round_number),
            }
            for acct in previously_disputed
        ]

    # Round 1: Use AI to pick the best accounts
    inaccurate = []
    incomplete = []
    if analysis_data:
        inaccurate = analysis_data.get("inaccurate_accounts", [])
        incomplete = analysis_data.get("incomplete_accounts", [])

    prompt = f"""You are an expert credit repair strategist. Given the following negative accounts
extracted from a credit report, select the accounts most likely to be successfully
removed via dispute.

NEGATIVE ACCOUNTS:
{json.dumps(negative_items, indent=2)}

ACCOUNTS FLAGGED AS INACCURATE (from analysis):
{json.dumps(inaccurate, indent=2)}

ACCOUNTS WITH INCOMPLETE INFORMATION:
{json.dumps(incomplete, indent=2)}

PRIORITIZE:
1. Accounts flagged as inaccurate reporting (payment bucket errors, wrong dates)
2. Collection accounts with highest balances
3. Accounts with incomplete information (missing fields)
4. Charge-offs and late payments with strongest dispute basis

For each selected account, provide:
- account_name: The creditor name
- account_number: The account number
- reason: Why this account should be disputed (specific, actionable)
- legal_basis: The legal statute or consumer protection law to cite

Select ALL accounts that have a reasonable basis for dispute. Do not skip any
account that has inaccurate data or incomplete fields.

RETURN ONLY valid JSON array:
[
  {{
    "account_name": "...",
    "account_number": "...",
    "reason": "...",
    "legal_basis": "..."
  }}
]
"""

    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )

        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        decisions = json.loads(raw)
        if isinstance(decisions, list):
            return decisions
    except Exception:
        pass

    # Fallback: dispute all negative items
    return [
        {
            "account_name": item.get("account_name", ""),
            "account_number": item.get("account_number", ""),
            "reason": item.get("issue", "Inaccurate reporting"),
            "legal_basis": "15 U.S.C. § 1681e(b) - FCRA accuracy requirement",
        }
        for item in negative_items
    ]


def build_dispute_reason(account_decision, round_number):
    """
    Build the full dispute reason/action text for a specific account.

    Args:
        account_decision: Dict from select_accounts_for_dispute.
        round_number: Current round.

    Returns:
        Tuple of (action_text, issue_text) for template filling.
    """
    escalation = get_escalation_config(round_number)

    if round_number == 1:
        action = f"requesting investigation and removal of inaccurate information"
        issue = account_decision.get("reason", "Inaccurate reporting")
    elif round_number == 2:
        action = f"demanding compliance under the Fair Credit Reporting Act and Fair Debt Collection Practices Act"
        issue = f"{account_decision.get('reason', 'Unverified debt')}. Previous dispute was not properly investigated per 15 U.S.C. § 1681i."
    elif round_number == 3:
        action = f"demanding production of the ACDV record and method of verification"
        issue = f"{account_decision.get('reason', 'Unverified account')}. Invoking Cushman v. Trans Union Corp., 115 F.3d 220."
    elif round_number == 4:
        action = f"invoking arbitration rights under 15 U.S.C. § 1681e(b)"
        issue = f"{account_decision.get('reason', 'Disputed account')}. Formal arbitration demand after repeated failure to correct."
    else:
        action = f"filing formal complaint for failure to investigate and correct"
        issue = f"{account_decision.get('reason', 'Unresolved dispute')}. Exhausted all direct dispute channels."

    return action, issue


def _get_legal_basis_for_round(round_number):
    """Get the primary legal basis for escalation at a given round."""
    bases = {
        1: "15 U.S.C. § 1681e(b) - FCRA accuracy requirement",
        2: "15 U.S.C. § 1681i - Duty to investigate; 15 U.S.C. § 1692g - Debt validation",
        3: "Cushman v. Trans Union Corp., 115 F.3d 220 - ACDV production demand",
        4: "15 U.S.C. § 1681e(b) - Arbitration under FCRA",
        5: "CFPB complaint - 12 U.S.C. § 5531 - Unfair, deceptive, or abusive practices",
    }
    return bases.get(round_number, bases[5])
