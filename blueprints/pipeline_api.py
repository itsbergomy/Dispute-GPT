"""
Pipeline API blueprint — endpoints for autonomous dispute pipeline control.
"""

import os
import json
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (
    db, DisputePipeline, DisputeAccount, BureauResponse,
    Client, WorkflowSetting
)
from services.pipeline_engine import (
    create_pipeline, advance_pipeline, get_pipeline_status,
    approve_pipeline_letters
)
from services.pdf_parser import pdf_to_base64_images
from tasks.dispute_tasks import advance_pipeline_task

pipeline_bp = Blueprint('pipeline', __name__)


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

    # Create and start the pipeline
    pipeline = create_pipeline(client_id, current_user.id)

    # Enqueue the first step via background task
    advance_pipeline_task(pipeline.id)

    return jsonify({
        'pipeline_id': pipeline.id,
        'state': pipeline.state,
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
    response_type = request.form.get('response_type')  # removed, updated, verified, stall_letter
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

        # Advance the pipeline
        advance_pipeline_task(pipeline.id)

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
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in pipelines
    ])
