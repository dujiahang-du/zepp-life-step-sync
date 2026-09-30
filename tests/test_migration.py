import sqlite3
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from app import create_app, db
from app.models import MiAccount


def legacy_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE mi_account (id INTEGER PRIMARY KEY, user_id INTEGER, mi_user VARCHAR(120) NOT NULL,
            mi_password VARCHAR(120) NOT NULL, min_step INTEGER, max_step INTEGER, is_active BOOLEAN,
            sync_start_hour INTEGER, sync_end_hour INTEGER, created_at DATETIME, updated_at DATETIME)''')
        connection.execute("INSERT INTO mi_account (id,user_id,mi_user,mi_password,min_step,max_step,is_active,sync_start_hour,sync_end_hour) VALUES (1,1,'legacy@example.invalid','legacy-secret',18000,25000,1,8,22)")


def test_migration_preserves_data_and_encrypts_idempotently(tmp_path):
    path=tmp_path/'legacy.db'
    legacy_database(path)
    key=Fernet.generate_key().decode()
    config={'TESTING':True,'INSTANCE_PATH':str(tmp_path/'instance'),'SQLALCHEMY_DATABASE_URI':'sqlite:///'+path.as_posix(),'ENCRYPTION_KEY':key}
    for _ in range(2):
        app=create_app(config)
        with app.app_context():
            account=db.session.get(MiAccount,1)
            assert account.mi_user=='legacy@example.invalid'
            assert account.get_password()=='legacy-secret'
            assert account.min_step==18000 and account.is_active
            assert account.token_data is None
            db.session.remove()
            db.engine.dispose()
        app.extensions['motion_executor'].shutdown()
    with sqlite3.connect(path) as connection:
        value=connection.execute('SELECT mi_password FROM mi_account').fetchone()[0]
        assert value.startswith('enc:v1:') and 'legacy-secret' not in value


def test_wrong_key_fails_closed(tmp_path):
    path=tmp_path/'legacy.db'
    legacy_database(path)
    config={'TESTING':True,'INSTANCE_PATH':str(tmp_path/'instance'),'SQLALCHEMY_DATABASE_URI':'sqlite:///'+path.as_posix()}
    app=create_app(config)
    app.extensions['motion_executor'].shutdown()
    with pytest.raises(ValueError,match='密钥'):
        create_app(config)


def test_non_test_upgrade_backs_up_legacy_database(tmp_path):
    path=tmp_path/'legacy.db'
    legacy_database(path)
    app=create_app({'INSTANCE_PATH':str(tmp_path/'instance'),'SQLALCHEMY_DATABASE_URI':'sqlite:///'+path.as_posix(),'SCHEDULER_ENABLED':False})
    backups=list((tmp_path/'instance/backups').glob('*.db'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute('SELECT mi_password FROM mi_account').fetchone()[0]=='legacy-secret'
    assert (tmp_path/'instance/secrets.json').exists()
    app.extensions['motion_executor'].shutdown()


def test_leader_lock_excludes_second_scheduler(tmp_path):
    from app.scheduler.tasks import acquire_leader
    path=tmp_path/'scheduler.lock'
    first=acquire_leader(path)
    assert first is not None
    assert acquire_leader(path) is None
    first.close()
    second=acquire_leader(path)
    assert second is not None
    second.close()


def test_duplicate_legacy_accounts_stop_before_mutation(tmp_path):
    path = tmp_path / 'duplicates.db'
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO mi_account (id,user_id,mi_user,mi_password) VALUES (2,1,'legacy@example.invalid','another-secret')")
    with pytest.raises(RuntimeError, match='重复'):
        create_app({'TESTING': True, 'INSTANCE_PATH': str(tmp_path/'instance'),
                    'SQLALCHEMY_DATABASE_URI': 'sqlite:///'+path.as_posix()})
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT mi_password FROM mi_account WHERE id=1').fetchone()[0] == 'legacy-secret'
        assert 'token_data' not in [row[1] for row in connection.execute('PRAGMA table_info(mi_account)')]


def test_running_scheduler_executes_a_real_background_job(tmp_path):
    from threading import Event
    from datetime import datetime, timezone
    signal = Event()
    app = create_app({'TESTING': True, 'INSTANCE_PATH': str(tmp_path/'running'),
                      'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'SCHEDULER_ENABLED': True})
    scheduler = app.extensions['motion_scheduler']
    try:
        assert scheduler.running and app.extensions['scheduler_leader']
        scheduler.add_job(id='acceptance_tick', func=signal.set, trigger='interval', seconds=60,
                          next_run_time=datetime.now(timezone.utc))
        assert signal.wait(5), '后台调度没有执行'
    finally:
        scheduler.shutdown()
        app.extensions['scheduler_lock'].close()
        app.extensions['motion_executor'].shutdown()
