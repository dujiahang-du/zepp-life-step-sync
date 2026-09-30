# Modified 2026-09-30: reliability and security improvements; see NOTICE.
from flask import Blueprint, abort, render_template, request
from flask_login import current_user
from app import db
from sqlalchemy import or_
from app.models import MiAccount, StepRecord
from app.stats import statistics, trend_points
from app.time_utils import local_day_start, utcnow

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if not current_user.is_authenticated:
        return render_template('index.html')
    account_id = request.args.get('account', type=int)
    stats = statistics(current_user.id, account_id)
    if stats is None:
        abort(404)
    accounts = stats['accounts']
    today = StepRecord.query.join(MiAccount).filter(MiAccount.user_id == current_user.id,
        StepRecord.created_at >= local_day_start(), StepRecord.created_at <= utcnow())
    total = today.filter(or_(StepRecord.outcome.is_(None), StepRecord.outcome.in_(('success', 'failed')))).count()
    success = today.filter(StepRecord.status.is_(True)).count()
    recent = StepRecord.query.join(MiAccount).filter(MiAccount.user_id == current_user.id).order_by(StepRecord.created_at.desc(), StepRecord.id.desc()).limit(8).all()
    return render_template('dashboard.html', accounts=accounts, total_accounts=len(accounts),
        active_accounts=sum(a.is_active for a in accounts), success_syncs=success, total_syncs=total,
        recent_records=recent, stats=stats, trend=trend_points(stats))
