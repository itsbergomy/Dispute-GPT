"""
Pipeline API blueprint — endpoints for autonomous dispute pipeline control.
"""

import os
import json
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (
    db, DisputePipeline, DisputeAccount, BureauResponse,
    Client, ClientDisputeLetter, WorkflowSetting
)
from services.pipeline_engine import (
    create_pipeline, advance_pipeline, get_pipeline_status,
    approve_pipeline_letters, _get_agent_config
)

pipeline_bp = Blueprint('pipeline', __name__)

# Valid prompt packs
VALID_PACKS = {'default', 'consumer_law', 'ACDV_response', 'arbitration'}


def _run_pipeline_bg(pipeline_id):
    """Run pipeline advancement in a background thread using the CURRENT app."""
    import logging
    from flask import current_app
    logger = logging.getLogger(__name__)

    # Grab the real app object from the current request context
    # so the thread reuses the same SQLAlchemy engine / connection pool.
    app = current_app._get_current_object()

    def _run():
        try:
            with app.app_context():
                # Small delay to let the request's commit finish
                import time; time.sleep(0.5)
                logger.info(f"[BG Thread] Advancing pipeline {pipeline_id}")
                advance_pipeline(pipeline_id)
                logger.info(f"[BG Thread] Pipeline {pipeline_id} advanced OK")
        except Exception:
            logger.exception(f"[BG Thread] Pipeline {pipeline_id} failed")
            # Mark pipeline as failed so the UI shows an error
            try:
                with app.app_context():
                    pipe = DisputePipeline.query.get(pipeline_id)
                    if pipe and pipe.state not in ('completed', 'failed'):
                        pipe.state = 'failed'
                        pipe.error_message = 'Background processing error — check server logs'
                        db.session.commit()
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _advance(pipeline_id):
    """Launch pipeline in a background thread (Huey-free dev mode)."""
    _run_pipeline_bg(pipeline_id)


def _validate_config(config):
    """Validate agent config dict. Returns (cleaned_config, error_string)."""
    if not isinstance(config, dict):
        return None, 'config must be a dict'

    mode = config.get('mode', 'supervised')
    if mode not in ('supervised', 'full_auto'):
        return None, 'mode must be "supervised" or "full_auto"'

    max_rounds = config.get('max_rounds', 3)
    if not isinstance(max_rounds, int) or max_rounds < 1 or max_rounds > 5:
        return None, 'max_rounds must be 1-5'

    round_packs = config.get('round_packs', [])
    if round_packs:
        if not isinstance(round_packs, list) or len(round_packs) > max_rounds:
            return None, f'round_packs must be a list of up to {max_rounds} items'
        for pack in round_packs:
            if pack not in VALID_PACKS:
                return None, f'Invalid pack: {pack}. Valid: {", ".join(VALID_PACKS)}'

    send_to = config.get('send_to', 'bureaus')
    if send_to not in ('bureaus', 'creditors'):
        return None, 'send_to must be "bureaus" or "creditors"'

    creditor_addresses = config.get('creditor_addresses', [])
    if send_to == 'creditors':
        if not creditor_addresses:
            return None, 'creditor_addresses required when send_to is "creditors"'
        for i, cred in enumerate(creditor_addresses):
            for field in ('name', 'address1', 'city', 'state', 'zip'):
                if not cred.get(field, '').strip():
                    return None, f'Creditor {i+1} missing required field: {field}'

    # Optional custom letter override
    custom_letter_id = config.get('custom_letter_id')
    if custom_letter_id is not None:
        if not isinstance(custom_letter_id, int):
            return None, 'custom_letter_id must be an integer'
        from models import CustomLetter
        from flask_login import current_user
        cl = CustomLetter.query.get(custom_letter_id)
        if not cl or cl.user_id != current_user.id:
            return None, 'Custom letter not found or not yours'

    cleaned = {
        'mode': mode,
        'max_rounds': max_rounds,
        'round_packs': round_packs,
        'send_to': send_to,
        'creditor_addresses': creditor_addresses if send_to == 'creditors' else [],
    }
    if custom_letter_id is not None:
        cleaned['custom_letter_id'] = custom_letter_id

    return cleaned, None


@pipeline_bp.route('/pipeline/start', methods=['POST'])
@login_required
def start_pipeline():
    """Start an autonomous dispute pipeline for a client."""
    data = request.get_json()
    client_id = data.get('client_id')

    if not client_id:
        return jsonify({'error': 'client_id is required'}), 400

    client = Client.query.get(client_id)
    if not client or client.business_user_id != current_user.id:
        return jsonify({'error': 'Client not found or unauthorized'}), 404

    if not client.pdf_filename:
        return jsonify({'error': 'No credit report PDF uploaded for this client'}), 400

    # Check for existing active pipeline
    active = DisputePipeline.query.filter(
        DisputePipeline.client_id == client_id,
        DisputePipeline.state.notin_(['completed', 'failed']),
    ).first()

    if active:
        return jsonify({
            'error': 'Client already has an active pipeline',
            'pipeline_id': active.id,
            'state': active.state,
        }), 409

    # Validate agent config
    config = data.get('config')
    if config:
        config, error = _validate_config(config)
        if error:
            return jsonify({'error': error}), 400

    # Create and start the pipeline
    pipeline = create_pipeline(client_id, current_user.id, config=config)

    # Advance in background
    _advance(pipeline.id)

    return jsonify({
        'pipeline_id': pipeline.id,
        'state': pipeline.state,
        'mode': (config or {}).get('mode', 'supervised'),
        'message': 'Pipeline started successfully',
    }), 201


@pipeline_bp.route('/pipeline/<int:pipeline_id>/status', methods=['GET'])
@login_required
def pipeline_status(pipeline_id):
    """Get the current status of a pipeline."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.user_id != current_user.id:
        return jsonify({'error': 'Pipeline not found'}), 404

    status = get_pipeline_status(pipeline_id)
    return jsonify(status)


@pipeline_bp.route('/pipeline/<int:pipeline_id>/config', methods=['GET'])
@login_required
def pipeline_config(pipeline_id):
    """Get the agent config for a pipeline."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.user_id != current_user.id:
        return jsonify({'error': 'Pipeline not found'}), 404

    config = _get_agent_config(pipeline)
    return jsonify(config or {})


@pipeline_bp.route('/pipeline/<int:pipeline_id>/approve', methods=['POST'])
@login_required
def approve_pipeline(pipeline_id):
    """Approve all draft letters in a pipeline at the review stage."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.user_id != current_user.id:
        return jsonify({'error': 'Pipeline not found'}), 404

    if pipeline.state != 'review':
        return jsonify({'error': f'Pipeline is in "{pipeline.state}" state, not "review"'}), 400

    success = approve_pipeline_letters(pipeline_id)
    if success:
        return jsonify({'message': 'Letters approved. Delivery started.'})
    else:
        return jsonify({'error': 'Failed to approve letters'}), 500


@pipeline_bp.route('/pipeline/<int:pipeline_id>/response', methods=['POST'])
@login_required
def upload_response(pipeline_id):
    """Upload a bureau response letter for a specific dispute account."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.user_id != current_user.id:
        return jsonify({'error': 'Pipeline not found'}), 404

    account_id = request.form.get('account_id', type=int)
    response_type = request.form.get('response_type')
    file = request.files.get('response_file')

    if not account_id or not response_type:
        return jsonify({'error': 'account_id and response_type are required'}), 400

    account = DisputeAccount.query.get(account_id)
    if not account or account.pipeline_id != pipeline_id:
        return jsonify({'error': 'Account not found in this pipeline'}), 404

    # Save the response file if provided
    filename = ''
    if file and file.filename:
        upload_folder = os.environ.get('UPLOAD_FOLDER', 'static/uploads')
        filename = secure_filename(f"response_{account_id}_{file.filename}")
        file.save(os.path.join(upload_folder, filename))

    # Create response record
    response = BureauResponse(
        dispute_account_id=account_id,
        filename=filename,
        response_type=response_type,
    )
    db.session.add(response)

    # Update account outcome
    account.outcome = response_type
    account.response_received_at = datetime.utcnow()
    db.session.commit()

    # Check if all accounts in the round have responses
    round_accounts = DisputeAccount.query.filter_by(
        pipeline_id=pipeline_id,
        round_number=pipeline.round_number,
    ).all()

    all_responded = all(a.outcome != 'pending' for a in round_accounts)
    if all_responded and pipeline.state == 'awaiting_response':
        pipeline.state = 'response_received'
        pipeline.updated_at = datetime.utcnow()
        db.session.commit()

        _advance(pipeline.id)

    return jsonify({
        'message': 'Response recorded',
        'account_outcome': response_type,
        'all_responded': all_responded,
    })


@pipeline_bp.route('/pipeline/<int:pipeline_id>/cancel', methods=['POST'])
@login_required
def cancel_pipeline(pipeline_id):
    """Cancel a running pipeline."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.user_id != current_user.id:
        return jsonify({'error': 'Pipeline not found'}), 404

    if pipeline.state in ('completed', 'failed'):
        return jsonify({'error': 'Pipeline already terminated'}), 400

    pipeline.state = 'failed'
    pipeline.error_message = 'Cancelled by user'
    pipeline.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'message': 'Pipeline cancelled'})


@pipeline_bp.route('/pipeline/<int:pipeline_id>/delete', methods=['DELETE'])
@login_required
def delete_pipeline(pipeline_id):
    """Delete a pipeline and all its associated records."""
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline or pipeline.user_id != current_user.id:
        return jsonify({'error': 'Pipeline not found'}), 404

    # Delete associated records (bureau responses link to accounts, not pipeline)
    from models import PipelineTask
    accounts = DisputeAccount.query.filter_by(pipeline_id=pipeline_id).all()
    for acct in accounts:
        BureauResponse.query.filter_by(dispute_account_id=acct.id).delete()
    DisputeAccount.query.filter_by(pipeline_id=pipeline_id).delete()
    PipelineTask.query.filter_by(pipeline_id=pipeline_id).delete()
    db.session.delete(pipeline)
    db.session.commit()

    return jsonify({'message': 'Pipeline deleted'})


@pipeline_bp.route('/pipeline/list', methods=['GET'])
@login_required
def list_pipelines():
    """List all pipelines for the current user."""
    pipelines = DisputePipeline.query.filter_by(user_id=current_user.id).order_by(
        DisputePipeline.created_at.desc()
    ).all()

    return jsonify([
        {
            'id': p.id,
            'client_id': p.client_id,
            'client_name': f"{p.client.first_name} {p.client.last_name}" if p.client else 'Unknown',
            'state': p.state,
            'round_number': p.round_number,
            'mode': _get_agent_config(p).get('mode', 'supervised'),
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in pipelines
    ])


@pipeline_bp.route('/pipeline/letter/<int:letter_id>', methods=['GET'])
@login_required
def get_letter(letter_id):
    """Get the text of a dispute letter for viewing/editing."""
    letter = ClientDisputeLetter.query.get(letter_id)
    if not letter:
        return jsonify({'error': 'Letter not found'}), 404

    # Verify ownership through the client → pipeline chain
    client = Client.query.get(letter.client_id)
    if not client or client.business_user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    return jsonify({
        'id': letter.id,
        'letter_text': letter.letter_text,
        'status': letter.status,
        'template_name': letter.template_name,
        'created_at': letter.created_at.isoformat() if letter.created_at else None,
    })


@pipeline_bp.route('/pipeline/letter/<int:letter_id>', methods=['PUT'])
@login_required
def update_letter(letter_id):
    """Update the text of a draft dispute letter."""
    letter = ClientDisputeLetter.query.get(letter_id)
    if not letter:
        return jsonify({'error': 'Letter not found'}), 404

    client = Client.query.get(letter.client_id)
    if not client or client.business_user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    if letter.status != 'Draft':
        return jsonify({'error': f'Cannot edit a letter with status "{letter.status}"'}), 400

    data = request.get_json()
    new_text = data.get('letter_text')
    if not new_text or not new_text.strip():
        return jsonify({'error': 'letter_text is required'}), 400

    letter.letter_text = new_text.strip()
    db.session.commit()

    return jsonify({'message': 'Letter updated', 'id': letter.id})


@pipeline_bp.route('/pipeline/<int:pipeline_id>/next-round', methods=['POST'])
@login_required
def start_next_round(pipeline_id):
    """
    Advance a pipeline from round_review into the next round.
    User must explicitly trigger this — the pipeline never auto-advances between rounds.
    """
    pipeline = DisputePipeline.query.get(pipeline_id)
    if not pipeline:
        return jsonify({'error': 'Pipeline not found'}), 404
    if pipeline.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    if pipeline.state != 'round_review':
        return jsonify({'error': f'Pipeline is in "{pipeline.state}" state, not "round_review"'}), 400

    agent_config = _get_agent_config(pipeline)
    max_rounds = agent_config.get('max_rounds', 3)

    if pipeline.round_number >= max_rounds:
        return jsonify({'error': f'Already at max rounds ({max_rounds})'}), 400

    # Optionally accept updated round_packs for the next round
    data = request.get_json() or {}
    if 'round_packs' in data:
        new_packs = data['round_packs']
        if isinstance(new_packs, list) and all(p in VALID_PACKS for p in new_packs):
            agent_config['round_packs'] = new_packs
            strategy = json.loads(pipeline.strategy_json or '{}')
            strategy['agent_config'] = agent_config
            pipeline.strategy_json = json.dumps(strategy)

    # Increment round and advance to strategy
    pipeline.round_number += 1
    pipeline.state = 'strategy'
    db.session.commit()

    # Kick off the pipeline in background
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Starting Round {pipeline.round_number} for pipeline {pipeline.id}")

    thread = threading.Thread(
        target=_run_pipeline_bg,
        args=(pipeline.id,),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'message': f'Round {pipeline.round_number} started',
        'pipeline_id': pipeline.id,
        'round_number': pipeline.round_number,
    })
