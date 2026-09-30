import json
from datetime import timedelta
from unittest.mock import patch

from app import db
from app.models import MiAccount, StepRecord, SyncJob, User
from app.security import decrypt, PREFIX
from app.time_utils import utcnow, local_day_start, display_time
from conftest import add_account, csrf, FakeMotion


def test_startup_registers_job_but_does_not_run_in_tests(app):
    scheduler = app.extensions['motion_scheduler']
    assert [job.id for job in scheduler.get_jobs()] == ['sync_steps']
    assert not scheduler.running


def test_guest_pages_and_headers(client):
    for path in ('/', '/login', '/register'):
        result = client.get(path)
        assert result.status_code == 200
        assert 'Content-Security-Policy' in result.headers
        assert result.headers['Cache-Control'] == 'no-store'
    assert client.get('/accounts').status_code == 302
    assert client.get('/api/account/1/stats').status_code == 401


def test_csrf_and_post_only_routes(signed_in):
    assert signed_in.post('/account/add', data={}).status_code == 400
    for path in ('/account/1/delete', '/account/1/sync', '/logout'):
        assert signed_in.get(path).status_code == 405


def test_registration_validation_and_login(app, client):
    token = csrf(client)
    assert client.post('/register', data={'csrf_token': token}).status_code == 422
    values = {'csrf_token': token, 'username': 'new_user', 'email': 'new@example.invalid',
              'password': 'long-password-123', 'confirm_password': 'long-password-123'}
    assert client.post('/register', data=values).status_code == 302
    assert client.post('/register', data=values).status_code == 422
    assert client.post('/login', data={'csrf_token': token, 'username': 'new_user', 'password': 'wrong'}).status_code == 422
    assert client.post('/login', data={'csrf_token': token, 'username': 'new_user', 'password': 'long-password-123'}).status_code == 302
    result = client.get('/')
    assert result.status_code == 200 and '连接你的第一个运动账号' in result.text
    with app.app_context():
        assert User.query.first().password_hash != 'long-password-123'
    with client.session_transaction() as session:
        assert session['csrf_token'] != token
        new_token = session['csrf_token']
    assert client.post('/logout', data={'csrf_token': new_token}).status_code == 302
    assert client.get('/accounts').status_code == 302


def test_auth_throttle(client):
    token = csrf(client)
    for _ in range(10):
        assert client.post('/login', data={'csrf_token': token, 'username': 'none', 'password': 'none'}).status_code == 422
    assert client.post('/login', data={'csrf_token': token}).status_code == 429


def test_add_only_validates_and_encrypts(app, signed_in):
    assert add_account(signed_in).status_code == 302
    assert FakeMotion.calls == [('validate', 'motion@example.invalid')]
    with app.app_context():
        account = MiAccount.query.first()
        assert account.mi_password.startswith(PREFIX)
        assert account.get_password() == 'fixture-password'
        assert 'fixture-token' not in account.token_data
        assert json.loads(decrypt(account.token_data))['app_token'] == 'fixture-token'
        assert StepRecord.query.count() == 0
    assert add_account(signed_in).status_code == 422


def test_invalid_account_and_ranges_never_call_remote(app, signed_in):
    cases = [{'mi_user':'website_username'}, {'min_step':'30000', 'max_step':'100'},
             {'min_step':'-1'}, {'max_step':'999999'}, {'max_step':'abc'},
             {'sync_start_hour':'23', 'sync_end_hour':'2'}, {'sync_end_hour':'25'}, {'mi_password':''}]
    for change in cases:
        assert add_account(signed_in, **change).status_code == 422
    assert FakeMotion.calls == []
    with app.app_context():
        assert MiAccount.query.count() == 0


def test_pages_edit_and_password_retention(app, signed_in):
    add_account(signed_in)
    for path in ('/', '/accounts', '/account/add', '/account/1/edit', '/account/1/records'):
        result = signed_in.get(path)
        assert result.status_code == 200
        assert 'fixture-password' not in result.text and 'fixture-token' not in result.text
        assert 'cdn.bootcdn' not in result.text
    values = {'csrf_token':'fixture-csrf', 'min_step':'1000','max_step':'2000',
              'sync_start_hour':'9','sync_end_hour':'20'}
    assert signed_in.post('/account/1/edit', data=values).status_code == 302
    with app.app_context():
        account = db.session.get(MiAccount, 1)
        assert account.min_step == 1000 and account.sync_end_hour == 20
        assert not account.is_active and account.get_password() == 'fixture-password'
    assert signed_in.post('/account/1/toggle',data={'csrf_token':'fixture-csrf'}).status_code == 302
    with app.app_context():
        assert db.session.get(MiAccount,1).is_active


def test_manual_sync_records_and_duplicate_guard(app, signed_in):
    add_account(signed_in)
    result = signed_in.post('/account/1/sync',json={}, headers={'X-CSRF-Token':'fixture-csrf'})
    assert result.status_code == 202
    state = signed_in.get(result.json['status_url'])
    assert state.json['status'] == 'success'
    with app.app_context():
        assert StepRecord.query.count() == 1
        assert StepRecord.query.first().source == 'manual'
        assert db.session.get(MiAccount,1).sync_lock_token is None
    assert signed_in.post('/account/1/sync', json={}, headers={'X-CSRF-Token':'fixture-csrf'}).status_code == 409


def test_failed_sync_records_and_releases_lock(app, signed_in):
    add_account(signed_in)
    FakeMotion.result = ('远端响应超时，请稍后重试。', False)
    response = signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    assert signed_in.get(response.json['status_url']).json['status'] == 'failed'
    with app.app_context():
        assert not StepRecord.query.first().status
        assert db.session.get(MiAccount,1).sync_lock_token is None


def test_cross_user_cannot_access_or_mutate(app, signed_in):
    add_account(signed_in)
    response = signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    with app.app_context():
        other = User(username='other_user',email='other@example.invalid')
        other.set_password('other-test-password')
        db.session.add(other)
        db.session.commit()
        ident = other.id
    with signed_in.session_transaction() as session:
        session['_user_id'] = str(ident)
    for path in ('/account/1/edit','/account/1/records','/api/account/1/stats','/?account=1',response.json['status_url']):
        assert signed_in.get(path).status_code == 404
    for action in ('edit','delete','sync','toggle','recover'):
        assert signed_in.post('/account/1/'+action,data={'csrf_token':'fixture-csrf'}).status_code == 404


def test_delete_cascades_records_and_jobs(app, signed_in):
    add_account(signed_in)
    signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    assert signed_in.post('/account/1/delete',data={'csrf_token':'fixture-csrf'}).status_code == 302
    with app.app_context():
        assert MiAccount.query.count() == StepRecord.query.count() == SyncJob.query.count() == 0


def test_stats_daily_order_and_last_success(app, signed_in):
    add_account(signed_in)
    with app.app_context():
        for when, step, ok in ((local_day_start(1)+timedelta(hours=1),1000,True),
                              (local_day_start(1)+timedelta(hours=2),0,False),
                              (local_day_start(1)+timedelta(hours=3),2000,True)):
            db.session.add(StepRecord(account_id=1,created_at=when,step_count=step,status=ok,message='fixture'))
        db.session.commit()
    result = signed_in.get('/api/account/1/stats').json
    assert result['steps'][-2] == 2000 and result['steps'][-1] is None
    assert result['success_rate'][-2] == 66.7 and result['success_rate'][-1] is None
    assert len(result['dates']) == 7


def test_record_filters_and_pagination(app, signed_in):
    add_account(signed_in)
    with app.app_context():
        for i in range(25):
            db.session.add(StepRecord(account_id=1,created_at=utcnow()-timedelta(minutes=i+1),step_count=i,status=bool(i%2),message='fixture-row'))
        db.session.commit()
    assert signed_in.get('/account/1/records').text.count('fixture-row') == 20
    assert signed_in.get('/account/1/records?page=2').text.count('fixture-row') == 5
    assert signed_in.get('/account/1/records?status=success').text.count('fixture-row') == 12


def test_busy_account_cannot_be_edited_or_deleted(app, signed_in):
    add_account(signed_in)
    with app.app_context():
        db.session.get(MiAccount,1).sync_lock_until = utcnow()+timedelta(minutes=1)
        db.session.commit()
    assert signed_in.post('/account/1/delete',data={'csrf_token':'fixture-csrf'}).status_code == 302
    with app.app_context():
        assert MiAccount.query.count() == 1


def test_stale_job_becomes_unknown_once(app, signed_in):
    add_account(signed_in)
    with app.app_context():
        db.session.add(SyncJob(id='stale',account_id=1,status='running',created_at=utcnow()-timedelta(minutes=6)))
        db.session.get(MiAccount,1).sync_lock_token='stale'
        db.session.commit()
    assert signed_in.get('/api/tasks/stale').json['status'] == 'unknown'
    assert signed_in.get('/api/tasks/stale').json['status'] == 'unknown'
    with app.app_context():
        assert StepRecord.query.count() == 1


def test_schedule_honors_window_and_deduplicates_slot(app, signed_in):
    from app.scheduler.tasks import sync_steps
    from app.time_utils import local_now
    add_account(signed_in, sync_start_hour='0',sync_end_hour='23')
    sync_steps(app)
    sync_steps(app)
    with app.app_context():
        assert SyncJob.query.count() == 1
        assert StepRecord.query.first().source == 'scheduled'


def test_step_does_not_decrease_same_day(app, signed_in):
    add_account(signed_in)
    with app.app_context():
        db.session.add(StepRecord(account_id=1,step_count=30000,status=True,created_at=utcnow()-timedelta(minutes=1)))
        db.session.commit()
    response=signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    assert signed_in.get(response.json['status_url']).json['step_count'] == 30000


def test_stale_poll_cannot_overwrite_a_completed_job(app, signed_in):
    from app.services import job_state
    from sqlalchemy import update
    add_account(signed_in)
    with app.app_context():
        job = SyncJob(id='finished-race', account_id=1, status='running', created_at=utcnow()-timedelta(minutes=6))
        db.session.add(job)
        db.session.commit()
        assert job.status == 'running'
        db.session.execute(update(SyncJob).where(SyncJob.id == job.id).values(status='success', message='accepted').execution_options(synchronize_session=False))
        state = job_state(job)
        assert state['status'] == 'success'
        assert StepRecord.query.count() == 0


def test_async_busy_guard_and_capacity_recovery(app, signed_in):
    from threading import Event
    from app.services import submit_sync, BusyError
    started, release = Event(), Event()
    class WaitingMotion(FakeMotion):
        def sync_step(self, steps):
            started.set()
            assert release.wait(5)
            return super().sync_step(steps)
    add_account(signed_in)
    app.config.update(SYNC_INLINE=False, MOTION_CLIENT_FACTORY=WaitingMotion)
    response = signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    try:
        assert response.status_code == 202 and started.wait(3)
        assert signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'}).status_code == 409
        assert signed_in.get(response.json['status_url']).json['status'] == 'running'
    finally:
        release.set()
        app.extensions['motion_executor'].shutdown(wait=True)
    assert signed_in.get(response.json['status_url']).json['status'] == 'success'
    with app.app_context():
        assert StepRecord.query.count() == 1
    capacity = app.extensions['motion_capacity']
    assert all(capacity.acquire(blocking=False) for _ in range(4))
    assert not capacity.acquire(blocking=False)
    for _ in range(4):
        capacity.release()


def test_login_confirmation_required_before_remote(signed_in):
    assert add_account(signed_in, confirm_login='').status_code == 422
    assert FakeMotion.calls == []


def test_auth_hold_blocks_sync_toggle_and_edit_until_explicit_recovery(app, signed_in):
    add_account(signed_in)
    class ExpiredMotion(FakeMotion):
        outcome = 'requires_auth'
        result = ('授权已失效', False)
    app.config['MOTION_CLIENT_FACTORY'] = ExpiredMotion
    response = signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    assert signed_in.get(response.json['status_url']).json['status'] == 'requires_auth'
    with app.app_context():
        account = db.session.get(MiAccount, 1)
        assert not account.is_active and account.sync_hold == 'requires_auth'
        assert account.token_data is None
        assert StepRecord.query.first().outcome == 'requires_auth'
    assert signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'}).status_code == 409
    signed_in.post('/account/1/toggle',data={'csrf_token':'fixture-csrf'})
    signed_in.post('/account/1/edit', data={'csrf_token':'fixture-csrf','min_step':'1000','max_step':'2000','sync_start_hour':'0','sync_end_hour':'23','is_active':'on'})
    with app.app_context():
        assert not db.session.get(MiAccount,1).is_active
    FakeMotion.calls.clear()
    assert signed_in.get('/account/1/recover').status_code == 200
    assert signed_in.post('/account/1/recover',data={'csrf_token':'fixture-csrf','action':'authorize'}).status_code == 422
    assert FakeMotion.calls == []
    app.config['MOTION_CLIENT_FACTORY'] = FakeMotion
    assert signed_in.post('/account/1/recover',data={'csrf_token':'fixture-csrf','action':'authorize','confirmed':'on'}).status_code == 302
    assert FakeMotion.calls == [('validate','motion@example.invalid')]
    with app.app_context():
        account = db.session.get(MiAccount,1)
        assert account.sync_hold is None and not account.is_active and account.token_data


def test_unknown_recovery_does_not_login_or_resubmit(app, signed_in):
    add_account(signed_in)
    class UnknownMotion(FakeMotion):
        outcome = 'unknown'
        result = ('结果待确认', False)
    app.config['MOTION_CLIENT_FACTORY'] = UnknownMotion
    response = signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    assert signed_in.get(response.json['status_url']).json['status'] == 'unknown'
    assert signed_in.get('/api/account/1/stats').json['success_rate'][-1] is None
    assert '结果待确认' in signed_in.get('/account/1/records').text
    FakeMotion.calls.clear()
    assert signed_in.post('/account/1/recover',data={'csrf_token':'fixture-csrf','action':'resolve','confirmed':'on'}).status_code == 302
    assert FakeMotion.calls == []
    with app.app_context():
        account = db.session.get(MiAccount,1)
        assert account.sync_hold is None and not account.is_active


def test_queue_full_records_one_skip_per_slot(app, signed_in):
    from unittest.mock import Mock
    from app.scheduler.tasks import sync_steps
    add_account(signed_in,sync_start_hour='0',sync_end_hour='23')
    capacity = Mock()
    capacity.acquire.return_value = False
    app.extensions['motion_capacity'] = capacity
    sync_steps(app)
    sync_steps(app)
    with app.app_context():
        assert SyncJob.query.count() == 0
        assert StepRecord.query.count() == 1
        assert StepRecord.query.first().outcome == 'skipped'
    assert signed_in.get('/api/account/1/stats').json['success_rate'][-1] is None


def test_expired_job_blocks_scheduler_before_another_submission(app, signed_in):
    from app.scheduler.tasks import sync_steps
    add_account(signed_in,sync_start_hour='0',sync_end_hour='23')
    with app.app_context():
        db.session.add(SyncJob(id='stale-schedule',account_id=1,status='running',created_at=utcnow()-timedelta(minutes=6)))
        account = db.session.get(MiAccount,1)
        account.sync_lock_token = 'stale-schedule'
        account.sync_lock_until = utcnow()-timedelta(minutes=1)
        db.session.commit()
    FakeMotion.calls.clear()
    sync_steps(app)
    assert FakeMotion.calls == []
    with app.app_context():
        assert db.session.get(MiAccount,1).sync_hold == 'unknown'
        assert SyncJob.query.count() == 1


def test_busy_account_cannot_reauthorize(app, signed_in):
    add_account(signed_in)
    with app.app_context():
        db.session.get(MiAccount,1).sync_lock_until = utcnow()+timedelta(minutes=1)
        db.session.commit()
    FakeMotion.calls.clear()
    assert signed_in.post('/account/1/recover',data={'csrf_token':'fixture-csrf','action':'authorize','confirmed':'on'}).status_code == 422
    assert FakeMotion.calls == []
