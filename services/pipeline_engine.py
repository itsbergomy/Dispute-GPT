"""
Pipeline engine — the state machine that drives autonomous dispute processing.
Each state has a handler that does one thing and returns the next state.
"""

import os
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


def create_pipeline(client_id, user_id):
    """Create a new dispute pipeline for a client."""
    pipeline = DisputePipeline(
        client_id=client_id,
        user_id=user_id,
        state='intake',
        round_number=1,
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

    return {
        'id': pipeline.id,
        'client_id': pipeline.client_id,
        'state': pipeline.state,
        'round_number': pipeline.round_number,
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
    pdf_path = os.path.join(upload_folder, client.pdf_filename)

    # Extract negative items
    negative_items = extract_negative_items_from_pdf(pdf_path)

    # Store in strategy_json for the next step
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
    """AI picks accounts, bureaus, and templates for this round."""
    strategy_data = json.loads(pipeline.strategy_json or '{}')
    negative_items = strategy_data.get('negative_items', [])
    analysis = strategy_data.get('analysis', {})

    if not negative_items:
        raise ValueError("No negative items found to dispute")

    # Get previously unresolved accounts for re-dispute rounds
    previously_disputed = None
    if pipeline.round_number > 1:
        unresolved = DisputeAccount.query.filter(
            DisputeAccount.pipeline_id == pipeline.id,
            DisputeAccount.outcome.in_(['verified', 'no_response']),
            DisputeAccount.round_number == pipeline.round_number - 1,
        ).all()
        previously_disputed = [
            {'account_name': a.account_name, 'account_number': a.account_number}
            for a in unresolved
        ]

    # AI selects accounts
    decisions = select_accounts_for_dispute(
        negative_items=negative_items,
        analysis_data=analysis,
        round_number=pipeline.round_number,
        previously_disputed=previously_disputed,
    )

    # Get escalation config for this round
    escalation = get_escalation_config(pipeline.round_number)

    # Create DisputeAccount records for each account x bureau
    for decision in decisions:
        for bureau in escalation['bureaus']:
            action, issue = build_dispute_reason(decision, pipeline.round_number)

            account = DisputeAccount(
                pipeline_id=pipeline.id,
                account_name=decision.get('account_name', ''),
                account_number=decision.get('account_number', ''),
                bureau=bureau,
                status=decision.get('reason', ''),
                issue=issue,
                template_pack=escalation['pack'],
                dispute_reason=decision.get('legal_basis', ''),
                escalation_level=escalation['level'],
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

    for account in accounts:
        # Skip CFPB escalation (handled differently)
        if account.bureau == 'cfpb':
            continue

        # Build the prompt context
        context = {
            'entity': account.bureau.title(),
            'account_name': account.account_name,
            'account_number': account.account_number,
            'marks': account.status,
            'action': account.issue.split('.')[0] if account.issue else 'investigation and correction',
            'issue': account.issue or 'Inaccurate reporting',
            'dispute_date': '',
            'days': '30',
        }

        # Build and generate the letter
        prompt = build_prompt(account.template_pack, 0, context)
        letter_text = generate_letter(prompt)

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
    # Check for auto_approve workflow setting
    auto_approve = WorkflowSetting.query.filter_by(
        client_id=pipeline.client_id,
        key='auto_approve',
        enabled=True,
    ).first()

    if auto_approve:
        # Auto-approve all draft letters
        accounts = DisputeAccount.query.filter_by(
            pipeline_id=pipeline.id,
            round_number=pipeline.round_number,
        ).all()

        for account in accounts:
            if account.letter and account.letter.status == 'Draft':
                account.letter.status = 'Approved'

        db.session.commit()
        return 'delivery'

    # Stay in review — human must approve via API
    return 'review'


def approve_pipeline_letters(pipeline_id):
    """Called when a human approves all letters in a pipeline at the review stage."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.state != 'review':
        return False

    accounts = DisputeAccount.query.filter_by(
        pipeline_id=pipeline.id,
        round_number=pipeline.round_number,
    ).all()

    for account in accounts:
        if account.letter and account.letter.status == 'Draft':
            account.letter.status = 'Approved'

    db.session.commit()

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

        # 2. Supporting documents (ID, SSN)
        for attr, field_type in [('id_filename', 'id_file'), ('ssn_filename', 'ssn_file')]:
            filename = getattr(client, attr)
            if not filename:
                continue
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

        # 4. Mail via DocuPost
        # Build bureau address
        bureau_addresses = {
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

        recipient = bureau_addresses.get(account.bureau.lower(), {})
        sender = {
            'name': f"{client.first_name} {client.last_name}",
            'address1': client.address_line1 or '',
            'address2': client.address_line2 or '',
            'city': client.city or '',
            'state': client.state or '',
            'zip': client.zip_code or '',
        }

        result = mail_letter_via_docupost(
            pdf_url=package_path,  # Note: DocuPost needs a public URL
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
    # This handler is only called if manually triggered.
    # The periodic task (check_response_deadlines) handles the 30-day timeout.
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

    # Check if we've hit max rounds
    if pipeline.round_number >= 5:
        return 'completed'  # Exhausted all escalation levels

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
