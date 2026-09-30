"""手动与定时执行共享状态、锁和结果记录。"""
import json
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import BoundedSemaphore

from flask import current_app
from sqlalchemy import or_
from app import db
from app.models import MiAccount, StepRecord, SyncJob
from app.security import decrypt, encrypt
from app.time_utils import local_now, local_day_start, utcnow
from app.utils.mi_motion import MiMotion


class BusyError(Exception):
    pass


def configure_executor(app):
    app.extensions['motion_executor'] = ThreadPoolExecutor(max_workers=2, thread_name_prefix='motion')
    app.extensions['motion_capacity'] = BoundedSemaphore(4)


def current_range(account):
    now = local_now()
    rate = 1 if account.sync_end_hour == 0 else min((now.hour + now.minute / 60) / account.sync_end_hour, 1)
    return int(account.min_step * rate), int(account.max_step * rate)


def is_busy(account):
    return bool(account.sync_lock_until and account.sync_lock_until > utcnow())


def next_run(account):
    if not account.is_active:
        return None
    now = local_now()
    candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if candidate.hour < account.sync_start_hour:
        candidate = candidate.replace(hour=account.sync_start_hour)
    elif candidate.hour > account.sync_end_hour:
        candidate = (candidate + timedelta(days=1)).replace(hour=account.sync_start_hour)
    return candidate


def submit_sync(account, source='manual'):
    pending = SyncJob.query.filter_by(account_id=account.id).filter(SyncJob.status.in_(('queued', 'running'))).all()
    for job in pending:
        job_state(job)
    db.session.refresh(account)
    if account.sync_hold:
        raise BusyError('账号已暂停，请先在账号卡片完成重新授权或核对提交结果。')
    app = current_app._get_current_object()
    capacity = app.extensions['motion_capacity']
    acquired = capacity.acquire(timeout=120) if source == 'scheduled' else capacity.acquire(blocking=False)
    if not acquired:
        if source == 'scheduled':
            slot = local_now().strftime('%Y-%m-%d %H')
            changed = MiAccount.query.filter(MiAccount.id == account.id, MiAccount.is_active.is_(True),
                MiAccount.sync_hold.is_(None), or_(MiAccount.last_scheduled_slot.is_(None),
                MiAccount.last_scheduled_slot != slot)).update({'last_scheduled_slot': slot}, synchronize_session=False)
            if changed:
                db.session.add(StepRecord(account_id=account.id, step_count=0, status=False,
                    outcome='skipped', source='scheduled', message='执行队列已满，本时段计划已跳过；未向 Zepp 提交。'))
            db.session.commit()
        raise BusyError('执行队列已满，请稍后重试。')
    handed_off = False
    try:
        now = utcnow()
        if source == 'manual':
            latest = SyncJob.query.filter_by(account_id=account.id).order_by(SyncJob.created_at.desc()).first()
            if latest and latest.created_at > now - timedelta(seconds=30):
                raise BusyError('刚刚已提交任务，请等待至少 30 秒再试。')
        ident = str(uuid.uuid4())
        slot = local_now().strftime('%Y-%m-%d %H')
        query = MiAccount.query.filter(MiAccount.id == account.id, MiAccount.sync_hold.is_(None),
            or_(MiAccount.sync_lock_until.is_(None), MiAccount.sync_lock_until <= now))
        values = {'sync_lock_until': now + timedelta(minutes=5), 'sync_lock_token': ident}
        if source == 'scheduled':
            query = query.filter(MiAccount.is_active.is_(True),
                or_(MiAccount.last_scheduled_slot.is_(None), MiAccount.last_scheduled_slot != slot))
            values['last_scheduled_slot'] = slot
        if query.update(values, synchronize_session=False) != 1:
            db.session.rollback()
            raise BusyError('此账号正在执行，或本时段的计划已提交。')
        job = SyncJob(id=ident, account_id=account.id, source=source)
        db.session.add(job)
        db.session.commit()
        if app.config.get('SYNC_INLINE'):
            handed_off = True
            execute_sync(app, ident)
        else:
            try:
                app.extensions['motion_executor'].submit(execute_sync, app, ident)
                handed_off = True
            except RuntimeError:
                job.status, job.message, job.finished_at = 'failed', '服务正在停止，请稍后重试。', utcnow()
                MiAccount.query.filter_by(id=account.id, sync_lock_token=ident).update({'sync_lock_until': None, 'sync_lock_token': None})
                db.session.commit()
                raise BusyError(job.message)
        return ident
    except Exception:
        if not handed_off:
            capacity.release()
        raise


def execute_sync(app, ident):
    with app.app_context():
        client = None
        try:
            job = db.session.get(SyncJob, ident)
            if not job:
                return
            account = db.session.get(MiAccount, job.account_id)
            if not account or account.sync_lock_token != ident:
                return
            job.status, job.message = 'running', '正在连接 Zepp 并提交步数'
            lower, upper = current_range(account)
            # 当天累计步数不应因随机区间回落。
            last_step = db.session.query(db.func.max(StepRecord.step_count)).filter(
                StepRecord.account_id == account.id, StepRecord.status.is_(True),
                StepRecord.created_at >= local_day_start()).scalar() or 0
            job.step_count = max(random.randint(lower, upper), last_step)
            db.session.commit()
            tokens = json.loads(decrypt(account.token_data)) if account.token_data else {}
            factory = app.config.get('MOTION_CLIENT_FACTORY', MiMotion)
            client = factory(account.mi_user, account.get_password(), tokens=tokens)
            client.virtual_device = account.get_device()
            message, ok = client.sync_step(job.step_count)
            outcome = 'success' if ok else getattr(client, 'outcome', 'failed')
            if outcome not in ('success', 'failed', 'requires_auth', 'requires_device', 'unknown'):
                outcome = 'failed'
            # 更新前重新读取，避免过期任务覆盖新任务的锁。
            db.session.expire_all()
            claimed = MiAccount.query.filter_by(id=account.id, sync_lock_token=ident).update(
                {'sync_lock_until': None, 'sync_lock_token': None}, synchronize_session=False)
            if claimed != 1:
                db.session.rollback()
                return
            job.status = outcome
            job.message, job.finished_at = message, utcnow()
            if outcome in ('requires_auth', 'requires_device', 'unknown'):
                account.sync_hold, account.is_active = outcome, False
            if outcome == 'requires_auth':
                account.token_data = None
            elif client.tokens:
                account.token_data = encrypt(json.dumps(client.tokens))
            db.session.add(StepRecord(account_id=account.id, step_count=job.step_count,
                                      status=ok, outcome=outcome, message=message, source=job.source))
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.error('执行任务失败，任务编号 %s；未输出凭据或远端响应。', ident)
            job = db.session.get(SyncJob, ident)
            if job and MiAccount.query.filter_by(id=job.account_id, sync_lock_token=ident).update(
                    {'sync_lock_until': None, 'sync_lock_token': None}, synchronize_session=False) == 1:
                job.status, job.message, job.finished_at = 'unknown', '执行中断，结果待确认。请先核对 Zepp Life 步数，再恢复同步。', utcnow()
                MiAccount.query.filter_by(id=job.account_id).update({'sync_hold': 'unknown', 'is_active': False})
                db.session.add(StepRecord(account_id=job.account_id, step_count=job.step_count or 0,
                                          status=False, outcome='unknown', message=job.message, source=job.source))
                db.session.commit()
        finally:
            try:
                if client:
                    client.close()
            finally:
                app.extensions['motion_capacity'].release()


def job_state(job):
    if job.status in ('queued', 'running') and job.created_at < utcnow() - timedelta(minutes=5):
        message = '任务已超时或服务曾重启，请在 Zepp App 核对结果后重试。'
        changed = SyncJob.query.filter(SyncJob.id == job.id, SyncJob.status.in_(('queued', 'running'))).update(
            {'status': 'unknown', 'message': message, 'finished_at': utcnow()}, synchronize_session=False)
        if changed:
            MiAccount.query.filter_by(id=job.account_id, sync_lock_token=job.id).update({'sync_lock_until': None, 'sync_lock_token': None, 'sync_hold': 'unknown', 'is_active': False})
            db.session.add(StepRecord(account_id=job.account_id, step_count=job.step_count or 0, status=False, outcome='unknown', message=message, source=job.source))
        db.session.commit()
        db.session.refresh(job)
    return {'id': job.id, 'status': job.status, 'message': job.message, 'step_count': job.step_count}
