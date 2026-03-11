"""
Background task definitions for the autonomous dispute pipeline.
"""

from datetime import datetime, timedelta
from huey import crontab
from tasks.worker import huey


@huey.task()
def advance_pipeline_task(pipeline_id):
    """Run the next step of a dispute pipeline in the background."""
    from config import create_app
    app = create_app()
    with app.app_context():
        from services.pipeline_engine import advance_pipeline
        advance_pipeline(pipeline_id)


@huey.periodic_task(crontab(hour='9', minute='0'))
def check_response_deadlines():
    """
    Daily check at 9 AM: mark any disputes past 30 days with no response
    as 'no_response' and trigger re-dispute logic.
    """
    from config import create_app
    app = create_app()
    with app.app_context():
        from models import db, DisputeAccount, DisputePipeline
        from services.pipeline_engine import advance_pipeline

        cutoff = datetime.utcnow() - timedelta(days=30)

        stale_accounts = DisputeAccount.query.filter(
            DisputeAccount.mailed_at < cutoff,
            DisputeAccount.outcome == 'pending',
            DisputeAccount.mailed_at.isnot(None),
        ).all()

        # Group by pipeline
        pipeline_ids = set()
        for account in stale_accounts:
            account.outcome = 'no_response'
            account.response_received_at = datetime.utcnow()
            pipeline_ids.add(account.pipeline_id)

        db.session.commit()

        # Advance each affected pipeline
        for pid in pipeline_ids:
            pipeline = DisputePipeline.query.get(pid)
            if pipeline and pipeline.state == 'awaiting_response':
                pipeline.state = 'response_received'
                pipeline.updated_at = datetime.utcnow()
                db.session.commit()
                advance_pipeline_task(pid)
