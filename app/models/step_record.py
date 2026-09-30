# Modified 2026-09-30: reliability and security improvements; see NOTICE.
from app import db
from app.time_utils import utcnow

class StepRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('mi_account.id'))
    step_count = db.Column(db.Integer, nullable=False)  # 步数
    status = db.Column(db.Boolean, default=True)  # 是否成功
    message = db.Column(db.String(255))  # 执行结果消息
    source = db.Column(db.String(16), default='manual')
    created_at = db.Column(db.DateTime, default=utcnow)

    def __repr__(self):
        return f'<StepRecord {self.step_count}>'
