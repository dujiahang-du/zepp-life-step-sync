from app.time_utils import utcnow
from app import db


class SyncJob(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('mi_account.id'), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default='queued')
    source = db.Column(db.String(16), nullable=False, default='manual')
    step_count = db.Column(db.Integer, default=0)
    message = db.Column(db.String(255), default='等待执行')
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    finished_at = db.Column(db.DateTime)
