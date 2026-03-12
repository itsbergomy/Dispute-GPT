"""
Pipeline engine — the state machine that drives autonomous dispute processing.
Each state has a handler that does one thing and returns the next state.
"""

import os
import re
import json
import logging
from datetime import datetime

from models import (
    db, DisputePipeline, PipelineTask, DisputeAccount,
    Client, ClientReportAnalysis, ClientDisputeLetter, WorkflowSetting
)
from services.pdf_parser import extract_negative_items_from_pdf, compute_pdf_hash
from services.report_analyzer import run_report_analysis
from services.strategy import (
    select_accounts_for_dispute, get_escalation_config, build_dispute_reason
)
from services.letter_generator import (
    PACKS, generate_letter, build_prompt, letter_to_pdf,
    image_to_pdf, merge_dispute_package
)
from services.delivery import mail_letter_via_docupost

logger = logging.getLogger(__name__)

# Wait states — pipeline pauses here until external action
WAIT_STATES = {'review', 'awaiting_response', 'completed', 'failed'}

# Hardcoded bureau dispute addresses
BUREAU_ADDRESSES = {
    'experian': {
        'name': 'Experian',
        'company': 'Experian',
        'address1': 'P.O. Box 4500',
        'city': 'Allen',
        'state': 'TX',
        'zip': '75013',
    },
    'transunion': {
        'name': 'TransUnion LLC',
        'company': 'TransUnion',
        'address1': 'P.O. Box 2000',
        'city': 'Chester',
        'state': 'PA',
        'zip': '19016',
    },
    'equifax': {
        'name': 'Equifax Information Services LLC',
        'company': 'Equifax',
        'address1': 'P.O. Box 740256',
        'city': 'Atlanta',
        'state': 'GA',
        'zip': '30374',
    },
}

# Placeholder patterns that must NOT survive into mailed letters
PLACEHOLDER_RE = re.compile(r'\{[A-Z_]+\}|\[YOUR[ _].*?\]|\[CLIENT.*?\]|\[ADDRESS.*?\]|\[ACCOUNT.*?\]', re.IGNORECASE)


# ─── Helpers ───

def _get_agent_config(pipeline):
    """Extract agent_config from strategy_json, or return empty dict."""
    data = json.loads(pipeline.strategy_json or '{}')
    return data.get('agent_config', {})


def _get_client_context(client, account=None, recipient=None):
    """Build a full context dict for letter personalization."""
    today = datetime.utcnow().strftime('%B %d, %Y')
    ctx = {
        'client_full_name': f"{client.first_name} {client.last_name}",
        'client_first_name': client.first_name or '',
        'client_last_name': client.last_name or '',
        'client_address': client.address_line1 or '',
        'client_address_line2': client.address_line2 or '',
        'client_city': client.city or '',
        'client_state': client.state or '',
        'client_zip': client.zip_code or '',
        'client_city_state_zip': f"{client.city or ''}, {client.state or ''} {client.zip_code or ''}",
        'today_date': today,
        'date': today,
    }
    if account:
        ctx.update({
            'account_name': account.account_name or '',
            'account_number': account.account_number or '',
            'bureau': account.bureau or '',
            'entity': (account.bureau or '').title(),
            'marks': account.status or '',
            'action': (account.issue.split('.')[0] if account.issue else 'investigation and correction'),
            'issue': account.issue or 'Inaccurate reporting',
            'dispute_date': '',
            'days': '30',
        })
    if recipient:
        ctx.update({
            'creditor_name': recipient.get('name', ''),
            'creditor_address': recipient.get('address1', ''),
            'creditor_city_state_zip': f"{recipient.get('city','')}, {recipient.get('state','')} {recipient.get('zip','')}",
            'bureau_name': recipient.get('name', ''),
            'bureau_address': recipient.get('address1', ''),
        })
    return ctx


def _sanitize_letter(text, context):
    """Replace any remaining placeholders in generated letter text with real values."""
    # First pass: replace known placeholder patterns with context values
    replacements = {
        '{CLIENT_NAME}': context.get('client_full_name', ''),
        '{CLIENT_FULL_NAME}': context.get('client_full_name', ''),
        '{CLIENT_ADDRESS}': context.get('client_address', ''),
        '{CLIENT_CITY_STATE_ZIP}': context.get('client_city_state_zip', ''),
        '{ACCOUNT_NAME}': context.get('account_name', ''),
        '{ACCOUNT_NUMBER}': context.get('account_number', ''),
        '{BUREAU}': context.get('bureau', ''),
        '{ENTITY}': context.get('entity', ''),
        '{DATE}': context.get('today_date', ''),
        '{TODAY_DATE}': context.get('today_date', ''),
        '[YOUR NAME]': context.get('client_full_name', ''),
        '[YOUR ADDRESS]': context.get('client_address', ''),
        '[CLIENT NAME]': context.get('client_full_name', ''),
        '[CLIENT ADDRESS]': context.get('client_address', ''),
        '[ACCOUNT NAME]': context.get('account_name', ''),
        '[ACCOUNT NUMBER]': context.get('account_number', ''),
        '[ADDRESS]': context.get('client_address', ''),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)

    # Second pass: catch any remaining {UPPERCASE} or [BRACKET] placeholders
    remaining = PLACEHOLDER_RE.findall(text)
    if remaining:
        logger.warning(f"Unfilled placeholders found in letter: {remaining}")
        # Remove them rather than mail with placeholders
        text = PLACEHOLDER_RE.sub('', text)

    return text


def _validate_pdf_no_placeholders(pdf_path):
    """
    Final safety gate: read PDF text and check for surviving placeholders.
    Returns True if clean, raises ValueError if placeholders found.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            page_text = page.extract_text() or ''
            matches = PLACEHOLDER_RE.findall(page_text)
            if matches:
                raise ValueError(
                    f"Letter PDF contains unfilled placeholders: {matches}. "
                    f"Refusing to mail. File: {pdf_path}"
                )
    except ImportError:
        logger.warning("PyPDF2 not available for placeholder validation")
    return True


def create_pipeline(client_id, user_id, config=None):
    """
    Create a new dispute pipeline for a client.

    config dict (optional):
        mode: "supervised" | "full_auto"
        round_packs: ["default", "consumer_law", "ACDV_response"]
        max_rounds: 3
        send_to: "bureaus" | "creditors"
        creditor_addresses: [{"name":"...", "address1":"...", ...}]
    """
    strategy_json = '{}'
    if config:
        strategy_json = json.dumps({'agent_config': config})

    pipeline = DisputePipeline(
        client_id=client_id,
        user_id=user_id,
        state='intake',
        round_number=1,
        strategy_json=strategy_json,
    )
    db.session.add(pipeline)
    db.session.commit()
    return pipeline


def advance_pipeline(pipeline_id):
    """
    Advance a pipeline to its next state.
    This is the main entry point — called by the task queue or directly.
    """
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline:
        logger.error(f"Pipeline {pipeline_id} not found")
        return

    if pipeline.state in ('completed', 'failed'):
        logger.info(f"Pipeline {pipeline_id} already in terminal state: {pipeline.state}")
        return

    handler = STATE_HANDLERS.get(pipeline.state)
    if not handler:
        logger.error(f"No handler for state: {pipeline.state}")
        return

    # Create a task record for tracking
    task = PipelineTask(
        pipeline_id=pipeline.id,
        task_type=pipeline.state,
        state='running',
    )
    db.session.add(task)
    db.session.commit()

    try:
        next_state = handler(pipeline)
        task.state = 'completed'
        task.completed_at = datetime.utcnow()
        task.output_json = json.dumps({'next_state': next_state})

        pipeline.state = next_state
        pipeline.updated_at = datetime.utcnow()
        db.session.commit()

        logger.info(f"Pipeline {pipeline_id}: {task.task_type} -> {next_state}")

        # If the next state is not a wait state, keep advancing
        if next_state not in WAIT_STATES:
            advance_pipeline(pipeline_id)

    except Exception as e:
        logger.exception(f"Pipeline {pipeline_id} failed at {pipeline.state}")
        task.state = 'failed'
        task.error_message = str(e)
        task.completed_at = datetime.utcnow()

        pipeline.state = 'failed'
        pipeline.error_message = str(e)
        pipeline.updated_at = datetime.utcnow()
        db.session.commit()


def get_pipeline_status(pipeline_id):
    """Get full status of a pipeline for API/dashboard display."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline:
        return None

    tasks = PipelineTask.query.filter_by(pipeline_id=pipeline_id).order_by(PipelineTask.created_at).all()
    accounts = DisputeAccount.query.filter_by(pipeline_id=pipeline_id).order_by(DisputeAccount.created_at).all()
    agent_config = _get_agent_config(pipeline)

    return {
        'id': pipeline.id,
        'client_id': pipeline.client_id,
        'state': pipeline.state,
        'round_number': pipeline.round_number,
        'max_rounds': agent_config.get('max_rounds', 3),
        'mode': agent_config.get('mode', 'supervised'),
        'error_message': pipeline.error_message,
        'created_at': pipeline.created_at.isoformat() if pipeline.created_at else None,
        'updated_at': pipeline.updated_at.isoformat() if pipeline.updated_at else None,
        'tasks': [
            {
                'type': t.task_type,
                'state': t.state,
                'error': t.error_message,
                'created_at': t.created_at.isoformat() if t.created_at else None,
                'completed_at': t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks
        ],
        'accounts': [
            {
                'id': a.id,
                'account_name': a.account_name,
                'account_number': a.account_number,
                'bureau': a.bureau,
                'template_pack': a.template_pack,
                'escalation_level': a.escalation_level,
                'outcome': a.outcome,
                'round_number': a.round_number,
                'mailed_at': a.mailed_at.isoformat() if a.mailed_at else None,
                'letter_id': a.letter_id,
                'letter_status': a.letter.status if a.letter else None,
            }
            for a in accounts
        ],
    }


# ─── State Handlers ───

def handle_intake(pipeline):
    """Validate that the client has a PDF and ID documents uploaded."""
    client = Client.query.get(pipeline.client_id)
    if not client:
        raise ValueError("Client not found")

    if not client.pdf_filename:
        raise ValueError("No credit report PDF uploaded for this client")

    upload_folder = os.environ.get('UPLOAD_FOLDER', 'static/uploads')

    # Check client-specific folder first, then root uploads
    pdf_path = os.path.join(upload_folder, str(client.id), client.pdf_filename)
    if not os.path.exists(pdf_path):
        pdf_path = os.path.join(upload_folder, client.pdf_filename)
    if not os.path.exists(pdf_path):
        raise ValueError(f"PDF file not found: {client.pdf_filename}")

    # Compute and store PDF hash
    pipeline.pdf_hash = compute_pdf_hash(pdf_path)
    db.session.commit()

    return 'analysis'


def handle_analysis(pipeline):
    """Extract negative items and run vision-based report analysis."""
    client = Client.query.get(pipeline.client_id)
    upload_folder = os.environ.get('UPLOAD_FOLDER', 'static/uploads')

    pdf_path = os.path.join(upload_folder, str(client.id), client.pdf_filename)
    if not os.path.exists(pdf_path):
        pdf_path = os.path.join(upload_folder, client.pdf_filename)

    # Extract negative items
    negative_items = extract_negative_items_from_pdf(pdf_path)

    # Preserve agent_config when writing to strategy_json
    strategy_data = json.loads(pipeline.strategy_json or '{}')
    strategy_data['negative_items'] = negative_items

    # Run full report analysis
    try:
        analysis = run_report_analysis(pdf_path)
        strategy_data['analysis'] = analysis

        # Save analysis to database
        analysis_record = ClientReportAnalysis(
            client_id=client.id,
            analysis_json=json.dumps(analysis)
        )
        db.session.add(analysis_record)
    except Exception as e:
        logger.warning(f"Report analysis failed (non-fatal): {e}")
        strategy_data['analysis'] = {}

    pipeline.strategy_json = json.dumps(strategy_data)
    db.session.commit()

    return 'strategy'


def handle_strategy(pipeline):
    """Dispute ALL extracted accounts — every negative item gets a letter."""
    strategy_data = json.loads(pipeline.strategy_json or '{}')
    negative_items = strategy_data.get('negative_items', [])
    analysis = strategy_data.get('analysis', {})
    agent_config = strategy_data.get('agent_config', {})

    if not negative_items:
        raise ValueError("No negative items found to dispute")

    if pipeline.round_number > 1:
        # Re-dispute: only accounts that came back verified or no_response
        unresolved = DisputeAccount.query.filter(
            DisputeAccount.pipeline_id == pipeline.id,
            DisputeAccount.outcome.in_(['verified', 'no_response']),
            DisputeAccount.round_number == pipeline.round_number - 1,
        ).all()
        decisions = [
            {
                'account_name': a.account_name,
                'account_number': a.account_number,
                'reason': f'Previous dispute was verified/no response. Escalating to round {pipeline.round_number}.',
                'legal_basis': '',
            }
            for a in unresolved
        ]
    else:
        # Round 1: dispute EVERY negative item — no AI filtering
        # Use AI only to enrich with legal basis / dispute reason
        decisions = select_accounts_for_dispute(
            negative_items=negative_items,
            analysis_data=analysis,
            round_number=pipeline.round_number,
        )
        # Make sure every negative item is included even if AI skipped it
        ai_keys = {(d.get('account_name',''), d.get('account_number','')) for d in decisions}
        for item in negative_items:
            key = (item.get('account_name', ''), item.get('account_number', ''))
            if key not in ai_keys:
                decisions.append({
                    'account_name': item.get('account_name', ''),
                    'account_number': item.get('account_number', ''),
                    'reason': item.get('reason', 'Inaccurate or unverified reporting'),
                    'legal_basis': 'FCRA Section 611 — right to dispute inaccurate information',
                })

    # Determine pack and targets from agent config or escalation map
    round_packs = agent_config.get('round_packs', [])
    send_to = agent_config.get('send_to', 'bureaus')

    if round_packs and pipeline.round_number <= len(round_packs):
        # User-configured pack for this round
        pack = round_packs[pipeline.round_number - 1]
        level = pipeline.round_number
    else:
        # Fallback to escalation map
        escalation = get_escalation_config(pipeline.round_number)
        pack = escalation['pack']
        level = escalation['level']

    # Determine targets (bureaus or creditors)
    if send_to == 'creditors':
        creditor_addresses = agent_config.get('creditor_addresses', [])
        targets = [c['name'] for c in creditor_addresses] if creditor_addresses else ['experian', 'transunion', 'equifax']
    else:
        # All 3 bureaus for all rounds
        targets = ['experian', 'transunion', 'equifax']

    # Create DisputeAccount records for each account x target
    for decision in decisions:
        for target in targets:
            action, issue = build_dispute_reason(decision, pipeline.round_number)

            account = DisputeAccount(
                pipeline_id=pipeline.id,
                account_name=decision.get('account_name', ''),
                account_number=decision.get('account_number', ''),
                bureau=target,
                status=decision.get('reason', ''),
                issue=issue,
                template_pack=pack,
                dispute_reason=decision.get('legal_basis', ''),
                escalation_level=level,
                round_number=pipeline.round_number,
            )
            db.session.add(account)

    db.session.commit()
    return 'generation'


def handle_generation(pipeline):
    """Generate dispute letters for each DisputeAccount in the current round."""
    accounts = DisputeAccount.query.filter_by(
        pipeline_id=pipeline.id,
        round_number=pipeline.round_number,
        outcome='pending',
    ).all()

    client = Client.query.get(pipeline.client_id)
    agent_config = _get_agent_config(pipeline)
    send_to = agent_config.get('send_to', 'bureaus')
    creditor_addresses = agent_config.get('creditor_addresses', [])

    for account in accounts:
        # Skip CFPB escalation (handled differently)
        if account.bureau == 'cfpb':
            continue

        # Determine recipient for context
        if send_to == 'creditors':
            recipient = next(
                (c for c in creditor_addresses if c['name'] == account.bureau),
                {'name': account.bureau}
            )
        else:
            recipient = BUREAU_ADDRESSES.get(account.bureau.lower(), {'name': account.bureau.title()})

        # Build full context with client + account + recipient details
        context = _get_client_context(client, account, recipient)

        # Build and generate the letter
        prompt = build_prompt(account.template_pack, 0, context)
        letter_text = generate_letter(prompt)

        # Sanitize: replace any remaining placeholders with real data
        letter_text = _sanitize_letter(letter_text, context)

        # Save to database
        letter_record = ClientDisputeLetter(
            client_id=client.id,
            letter_text=letter_text,
            status='Draft',
            template_name=f"{account.template_pack} - {account.bureau}",
        )
        db.session.add(letter_record)
        db.session.flush()  # Get the ID

        account.letter_id = letter_record.id
        db.session.commit()

    return 'review'


def handle_review(pipeline):
    """
    Check if auto-approve is enabled. If so, advance to delivery.
    Otherwise, stay in review and wait for human approval.
    """
    # Check agent config first
    agent_config = _get_agent_config(pipeline)
    if agent_config.get('mode') == 'full_auto':
        # Auto-approve all draft letters
        _approve_all_drafts(pipeline)
        return 'delivery'

    # Fallback: check WorkflowSetting for backward compatibility
    auto_approve = WorkflowSetting.query.filter_by(
        client_id=pipeline.client_id,
        key='auto_approve',
        enabled=True,
    ).first()

    if auto_approve:
        _approve_all_drafts(pipeline)
        return 'delivery'

    # Stay in review — human must approve via API
    return 'review'


def _approve_all_drafts(pipeline):
    """Helper to approve all draft letters for the current round."""
    accounts = DisputeAccount.query.filter_by(
        pipeline_id=pipeline.id,
        round_number=pipeline.round_number,
    ).all()

    for account in accounts:
        if account.letter and account.letter.status == 'Draft':
            account.letter.status = 'Approved'

    db.session.commit()


def approve_pipeline_letters(pipeline_id):
    """Called when a human approves all letters in a pipeline at the review stage."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.state != 'review':
        return False

    _approve_all_drafts(pipeline)

    # Advance to delivery
    pipeline.state = 'delivery'
    pipeline.updated_at = datetime.utcnow()
    db.session.commit()

    advance_pipeline(pipeline_id)
    return True


def handle_delivery(pipeline):
    """Merge PDFs and mail each letter via DocuPost."""
    client = Client.query.get(pipeline.client_id)
    upload_folder = os.environ.get('UPLOAD_FOLDER', 'static/uploads')
    agent_config = _get_agent_config(pipeline)
    send_to = agent_config.get('send_to', 'bureaus')
    creditor_addresses = agent_config.get('creditor_addresses', [])

    accounts = DisputeAccount.query.filter_by(
        pipeline_id=pipeline.id,
        round_number=pipeline.round_number,
        outcome='pending',
    ).all()

    for account in accounts:
        if not account.letter or account.letter.status != 'Approved':
            continue
        if account.bureau == 'cfpb':
            continue

        # Build the PDF package
        pdf_paths = []

        # 1. Letter PDF
        letter_pdf = letter_to_pdf(account.letter.letter_text)
        pdf_paths.append(letter_pdf)

        # 2. Supporting documents (ID, SSN) — check client subfolder first
        client_dir = os.path.join(upload_folder, str(client.id))
        for attr, field_type in [('id_filename', 'id_file'), ('ssn_filename', 'ssn_file')]:
            filename = getattr(client, attr)
            if not filename:
                continue
            doc_path = os.path.join(client_dir, filename)
            if not os.path.exists(doc_path):
                doc_path = os.path.join(upload_folder, filename)
            if not os.path.exists(doc_path):
                continue
            ext = filename.rsplit('.', 1)[-1].lower()
            if ext in ('png', 'jpg', 'jpeg'):
                img_pdf = image_to_pdf(doc_path, field_type=field_type)
                pdf_paths.append(img_pdf)
            elif ext == 'pdf':
                pdf_paths.append(doc_path)

        # 3. Merge
        package_path = merge_dispute_package(pdf_paths)

        # 4. Final placeholder safety check
        try:
            _validate_pdf_no_placeholders(package_path)
        except ValueError as e:
            logger.error(str(e))
            account.letter.status = 'Rejected'
            db.session.commit()
            continue

        # 5. Determine recipient address
        if send_to == 'creditors':
            recipient = next(
                (c for c in creditor_addresses if c['name'] == account.bureau),
                {}
            )
            if not recipient:
                logger.warning(f"No creditor address for {account.bureau}, skipping")
                continue
        else:
            recipient = BUREAU_ADDRESSES.get(account.bureau.lower(), {})
            if not recipient:
                logger.warning(f"Unknown bureau {account.bureau}, skipping")
                continue

        sender = {
            'name': f"{client.first_name} {client.last_name}",
            'address1': client.address_line1 or '',
            'address2': client.address_line2 or '',
            'city': client.city or '',
            'state': client.state or '',
            'zip': client.zip_code or '',
        }

        # 6. Mail via DocuPost
        result = mail_letter_via_docupost(
            pdf_url=package_path,
            recipient=recipient,
            sender=sender,
        )

        if result.get('success'):
            account.mailed_at = datetime.utcnow()
            account.letter.status = 'Sent'
        else:
            logger.warning(f"Mail failed for account {account.account_number}: {result.get('error')}")

        db.session.commit()

    return 'awaiting_response'


def handle_awaiting_response(pipeline):
    """No-op — pipeline waits here until responses are uploaded or timeout occurs."""
    return 'awaiting_response'


def handle_response_received(pipeline):
    """
    Examine outcomes for all accounts in the current round.
    If all removed -> completed.
    If any verified/no_response -> escalate and loop back to strategy.
    """
    accounts = DisputeAccount.query.filter_by(
        pipeline_id=pipeline.id,
        round_number=pipeline.round_number,
    ).all()

    all_resolved = all(a.outcome in ('removed', 'updated') for a in accounts)

    if all_resolved:
        return 'completed'

    # Check max rounds from agent config (default 3)
    agent_config = _get_agent_config(pipeline)
    max_rounds = agent_config.get('max_rounds', 3)

    if pipeline.round_number >= max_rounds:
        return 'completed'  # Exhausted configured rounds

    # Escalate: increment round and loop back to strategy
    pipeline.round_number += 1
    db.session.commit()

    return 'strategy'


# ─── State Handler Registry ───

STATE_HANDLERS = {
    'intake': handle_intake,
    'analysis': handle_analysis,
    'strategy': handle_strategy,
    'generation': handle_generation,
    'review': handle_review,
    'delivery': handle_delivery,
    'awaiting_response': handle_awaiting_response,
    'response_received': handle_response_received,
}
