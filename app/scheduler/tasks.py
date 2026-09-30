# Modified 2026-09-30: reliability and security improvements; see NOTICE.
import atexit
import os
from pathlib import Path

from flask_apscheduler import APScheduler
from app import db
from app.models import MiAccount
from app.services import BusyError, submit_sync
from app.time_utils import local_now


def acquire_leader(path):
    handle = open(path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def sync_steps(app):
    with app.app_context():
        hour = local_now().hour
        ids = [a.id for a in MiAccount.query.filter(MiAccount.is_active.is_(True),
                MiAccount.sync_start_hour <= hour, MiAccount.sync_end_hour >= hour).all()]
        for ident in ids:
            try:
                account = db.session.get(MiAccount, ident)
                if account:
                    submit_sync(account, 'scheduled')
            except BusyError:
                db.session.rollback()
            except Exception:
                db.session.rollback()
                app.logger.error('定时任务提交失败，账号编号 %s。', ident)


def configure_scheduler(app):
    scheduler = APScheduler()
    app.config['SCHEDULER_TIMEZONE'] = 'Asia/Shanghai'
    scheduler.init_app(app)
    scheduler.add_job(id='sync_steps', func=sync_steps, args=[app], trigger='cron',
                      minute=0, second=0, max_instances=1, coalesce=True, misfire_grace_time=60)
    app.extensions['motion_scheduler'] = scheduler
    app.extensions['scheduler_leader'] = False
    if app.config['SCHEDULER_ENABLED']:
        handle = acquire_leader(Path(app.instance_path) / 'scheduler.lock')
        if handle:
            app.extensions['scheduler_lock'] = handle
            scheduler.start()
            app.extensions['scheduler_leader'] = True
            def shutdown():
                if scheduler.running:
                    scheduler.shutdown(wait=False)
                handle.close()
            atexit.register(shutdown)
