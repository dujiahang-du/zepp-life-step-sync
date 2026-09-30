# Modified 2026-09-30: reliability and security improvements; see NOTICE.
from app import db
from app.time_utils import utcnow

class MiAccount(db.Model):
    __table_args__ = (db.UniqueConstraint('user_id', 'mi_user', name='uq_owner_motion_account'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    mi_user = db.Column(db.String(120), nullable=False)  # 小米运动账号
    mi_password = db.Column(db.Text, nullable=False)  # 加密后的运动账号密码
    token_data = db.Column(db.Text)
    device_data = db.Column(db.Text)
    sync_hold = db.Column(db.String(16))
    sync_lock_until = db.Column(db.DateTime)
    sync_lock_token = db.Column(db.String(36))
    last_scheduled_slot = db.Column(db.String(16))
    min_step = db.Column(db.Integer, default=18000)  # 最小步数
    max_step = db.Column(db.Integer, default=25000)  # 最大步数
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    sync_start_hour = db.Column(db.Integer, default=8)  # 同步开始时间（小时）
    sync_end_hour = db.Column(db.Integer, default=22)  # 同步结束时间（小时）
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # 关联步数记录
    step_records = db.relationship('StepRecord', backref='account', lazy='dynamic', cascade='all, delete-orphan')
    jobs = db.relationship('SyncJob', backref='account', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, value):
        from app.security import encrypt
        self.mi_password = encrypt(value)

    def get_password(self):
        from app.security import decrypt
        return decrypt(self.mi_password)

    def get_device(self):
        import json
        from app.security import decrypt
        return json.loads(decrypt(self.device_data)) if self.device_data else {}

    def __repr__(self):
        return f'<MiAccount {self.mi_user}>'
