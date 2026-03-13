"""
Client Portal blueprint — public, no-auth read-only view for business clients.
Accessed via unique token link shared by the business user.
"""

from datetime import datetime
from flask import Blueprint, render_template, jsonify, abort

from models import (
    db, ClientPortalToken, Client, DisputePipeline,
    DisputeAccount, ClientDisputeLetter
)

portal_bp = Blueprint('portal', __name__)


def _get_client_from_token(token):
    """Look up and validate a portal token. Returns (client, portal_token) or aborts 404."""
    pt = ClientPortalToken.query.filter_by(token=token, is_active=True).first()
    if not pt:
        abort(404)
    if pt.expires_at and pt.expires_at < datetime.utcnow():
        abort(404)
    client = Client.query.get(pt.client_id)
    if not client:
        abort(404)
    return client, pt


@portal_bp.route('/portal/<token>')
def client_portal(token):
    """Main portal view — read-only dispute status for the client."""
    client, pt = _get_client_from_token(token)

    # Get latest pipeline
    pipeline = DisputePipeline.query.filter_by(client_id=client.id).order_by(
        DisputePipeline.created_at.desc()
    ).first()

    # Group accounts by round
    rounds_data = {}
    if pipeline:
        accounts = DisputeAccount.query.filter_by(pipeline_id=pipeline.id).order_by(
            DisputeAccount.round_number, DisputeAccount.created_at
        ).all()
        for acct in accounts:
            rn = acct.round_number or 1
            if rn not in rounds_data:
                rounds_data[rn] = {
                    'round_number': rn,
                    'accounts': [],
                    'total': 0,
                    'sent': 0,
                    'delivered': 0,
                    'removed': 0,
                }
            rd = rounds_data[rn]
            rd['total'] += 1
            if acct.mailed_at:
                rd['sent'] += 1
            if acct.letter and acct.letter.delivery_status == 'delivered':
                rd['delivered'] += 1
            if acct.outcome == 'removed':
                rd['removed'] += 1

            rd['accounts'].append({
                'account_name': acct.account_name,
                'bureau': acct.bureau,
                'outcome': acct.outcome,
                'delivery_status': acct.letter.delivery_status if acct.letter else None,
                'mailed_at': acct.mailed_at,
            })

    return render_template('client_portal.html',
                           client=client,
                           pipeline=pipeline,
                           rounds=sorted(rounds_data.values(), key=lambda r: r['round_number']))


@portal_bp.route('/portal/<token>/tracking')
def portal_tracking(token):
    """AJAX endpoint — tracking data for the portal."""
    client, pt = _get_client_from_token(token)

    pipeline = DisputePipeline.query.filter_by(client_id=client.id).order_by(
        DisputePipeline.created_at.desc()
    ).first()

    if not pipeline:
        return jsonify({'rounds': {}})

    accounts = DisputeAccount.query.filter_by(pipeline_id=pipeline.id).order_by(
        DisputeAccount.round_number, DisputeAccount.created_at
    ).all()

    rounds = {}
    for acct in accounts:
        rn = acct.round_number or 1
        if rn not in rounds:
            rounds[rn] = []
        ltr = acct.letter
        rounds[rn].append({
            'account_name': acct.account_name,
            'bureau': acct.bureau,
            'outcome': acct.outcome,
            'delivery_status': ltr.delivery_status if ltr else None,
            'mailed_at': ltr.mailed_at.isoformat() if ltr and ltr.mailed_at else None,
        })

    return jsonify({'rounds': {str(k): v for k, v in sorted(rounds.items())}})
