import json
from unittest.mock import Mock

import pytest
import requests

from app import db
from app.models import MiAccount, StepRecord, SyncJob
from app.security import encrypt
from app.time_utils import utcnow
from app.utils.mi_motion import MiMotion
from app.utils.devices import matching_device, wearable_device
from conftest import add_account
from test_client import response


def listed(items):
    return response(data={'code': 1, 'data': items})


def probe():
    return response(data={'message': 'success'})


@pytest.fixture
def device_session(app, signed_in):
    assert add_account(signed_in).status_code == 302
    session = Mock()
    app.config['MOTION_CLIENT_FACTORY'] = lambda user, password, tokens=None: MiMotion(user, password, tokens, session)
    return session


def device_post(client, action='check', confirmed='on'):
    return client.post('/account/1/device', data={
        'csrf_token': 'fixture-csrf', 'action': action, 'confirmed': confirmed})


def state(app):
    with app.app_context():
        account = db.session.get(MiAccount, 1)
        return account.get_device(), account.sync_hold, account.is_active


def prepare(app, client, session):
    session.request.side_effect = [probe(), listed([])]
    assert device_post(client).status_code == 302
    identity, hold, active = state(app)
    assert identity['state'] == 'prepared' and hold == 'requires_device' and not active
    return identity


def test_binding_persists_before_request_and_requires_matching_readback(app, signed_in, device_session):
    identity = prepare(app, signed_in, device_session)
    def send(method, url, **kwargs):
        if url.endswith('getUserInfo.json'):
            return probe()
        if url.endswith('binds.json'):
            stored, hold, active = state(app)
            assert stored == {**identity, 'state': 'attempted'}
            assert hold == 'requires_device' and not active
            assert kwargs['data']['deviceid'] == identity['id']
            assert kwargs['data']['mac'] == identity['mac']
            return response(data={'code': 1})
        assert url.endswith('lists.json')
        calls = [c for c in device_session.request.call_args_list if c.args[1].endswith('binds.json')]
        return listed([{'deviceid': identity['id'], 'device_type': '0'}] if calls else [])
    device_session.request.side_effect = send
    assert device_post(signed_in, 'bind').status_code == 302
    saved, hold, active = state(app)
    assert saved == {**identity, 'state': 'confirmed'} and hold is None and not active
    with app.app_context():
        account = db.session.get(MiAccount, 1)
        assert identity['id'] not in account.device_data
        assert StepRecord.query.count() == 0 and SyncJob.query.count() == 0
    assert sum(c.args[0] == 'POST' for c in device_session.request.call_args_list) == 1
    assert '微信' in signed_in.get('/account/1/device').get_data(as_text=True)


@pytest.mark.parametrize('failure', ['timeout', 'server', 'rejected', 'wrong_device', 'empty', 'read_timeout'])
def test_uncertain_binding_never_repeats_or_submits(app, signed_in, device_session, failure):
    identity = prepare(app, signed_in, device_session)
    bind = {'timeout': requests.Timeout(), 'server': response(500),
            'rejected': response(data={'code': 0})}.get(failure, response(data={'code': 1}))
    readback = requests.Timeout() if failure == 'read_timeout' else listed(
        [{'deviceid': 'OTHER', 'device_type': '0'}] if failure == 'wrong_device' else [])
    device_session.request.side_effect = [probe(), listed([]), bind, readback]
    assert device_post(signed_in, 'bind').status_code == 422
    saved, hold, active = state(app)
    assert saved == {**identity, 'state': 'attempted'} and hold == 'requires_device' and not active
    device_session.request.side_effect = [probe(), listed([])]
    assert device_post(signed_in, 'bind').status_code == 422
    assert sum(c.args[0] == 'POST' for c in device_session.request.call_args_list) == 1
    assert signed_in.post('/account/1/sync', json={}, headers={'X-CSRF-Token':'fixture-csrf'}).status_code == 409
    # A delayed server result can be confirmed with a query only.
    device_session.request.side_effect = [probe(), listed([{'deviceid':identity['id']}])]
    assert device_post(signed_in).status_code == 302
    assert state(app)[0]['state'] == 'confirmed'


@pytest.mark.parametrize('data', [{}, {'code':0,'data':[]}, {'code':True,'data':[]}, {'code':1,'data':{}}, {'code':1,'data':[None]}])
def test_bad_device_lists_never_allow_binding(app, signed_in, device_session, data):
    device_session.request.side_effect = [probe(), response(data=data)]
    assert device_post(signed_in, 'bind').status_code == 422
    assert state(app)[0] == {}
    assert all(c.args[0] == 'GET' for c in device_session.request.call_args_list)


def test_get_and_missing_confirmation_do_not_touch_zepp(app, signed_in, device_session):
    assert signed_in.get('/account/1/device').status_code == 200
    assert device_post(signed_in, 'bind', '').status_code == 422
    assert signed_in.post('/account/1/device', data={'action':'bind','confirmed':'on'}).status_code == 400
    device_session.request.assert_not_called()


def test_non_wearable_does_not_trigger_binding(app, signed_in, device_session):
    device_session.request.side_effect = [probe(), listed([{'device_type':'1','deviceid':'SCALE'}])]
    assert device_post(signed_in, 'bind').status_code == 422
    assert state(app)[0]['state'] == 'unsupported'
    assert all(c.args[0] == 'GET' for c in device_session.request.call_args_list)


def test_wearable_selection_skips_scale():
    assert wearable_device([{'device_type':'1','deviceid':'SCALE'}, {'deviceType':0,'deviceId':'BAND'}]) == 'BAND'
    assert wearable_device([{'device_type':'1','deviceid':'SCALE','productName':'band'}]) is None
    assert wearable_device([{'deviceType':False,'deviceId':'SCALE'}]) is None
    assert matching_device([{'mac':'aa-bb-cc-dd-ee-ff'}], {'mac':'AA:BB:CC:DD:EE:FF'}) == 'AABBCCDDEEFF'


def test_expired_auth_preserves_device_identity(app, signed_in, device_session):
    identity = prepare(app, signed_in, device_session)
    device_session.request.side_effect = [response(401)]
    assert device_post(signed_in).status_code == 422
    assert state(app) == (identity, 'requires_auth', False)
    with app.app_context():
        assert db.session.get(MiAccount,1).token_data is None
    assert not any(c.args[0] == 'POST' for c in device_session.request.call_args_list)


def test_missing_device_pauses_sync_and_renders_recovery(app, signed_in, device_session):
    device_session.request.side_effect = [probe(), listed([])]
    result = signed_in.post('/account/1/sync',json={},headers={'X-CSRF-Token':'fixture-csrf'})
    assert result.status_code == 202
    with app.app_context():
        assert SyncJob.query.one().status == 'requires_device'
        assert StepRecord.query.one().outcome == 'requires_device'
    assert state(app)[1:] == ('requires_device',False)
    html = signed_in.get('/accounts').get_data(as_text=True)
    assert '查询或绑定设备' in html and '/account/1/device' in html


def test_lock_and_account_ownership_block_device_mutations(app, signed_in, device_session):
    from datetime import timedelta
    with app.app_context():
        account = db.session.get(MiAccount,1)
        account.sync_lock_until = utcnow()+timedelta(minutes=5)
        account.sync_lock_token = 'other-task'
        db.session.commit()
    assert device_post(signed_in).status_code == 422
    device_session.request.assert_not_called()
    with app.app_context():
        account = db.session.get(MiAccount,1)
        assert account.sync_lock_token == 'other-task'
        account.user_id = 999
        db.session.commit()
    assert signed_in.get('/account/1/device').status_code == 404
    assert device_post(signed_in).status_code == 404


def test_sync_ignores_cached_device_and_requires_virtual_identity_match():
    session = Mock()
    session.request.side_effect = [probe(), listed([{'device_type':'0','deviceid':'OTHER'}])]
    client = MiMotion('test@example.invalid','fixture',tokens={'app_token':'fixture','user_id':'uid','bound_device_id':'OLD'},session=session)
    client.virtual_device = {'id':'WANTED','state':'confirmed'}
    assert client.sync_step(100)[1] is False
    assert client.outcome == 'requires_device'
    assert session.request.call_count == 2


def test_reauthorization_keeps_attempted_identity(app, signed_in, device_session):
    from conftest import FakeMotion
    identity = prepare(app, signed_in, device_session)
    identity['state'] = 'attempted'
    with app.app_context():
        account = db.session.get(MiAccount,1)
        account.device_data = encrypt(json.dumps(identity))
        account.sync_hold = 'requires_auth'
        account.token_data = None
        db.session.commit()
    app.config['MOTION_CLIENT_FACTORY'] = FakeMotion
    result = signed_in.post('/account/1/recover',data={'csrf_token':'fixture-csrf','action':'authorize','confirmed':'on'})
    assert result.status_code == 302
    assert state(app)[0] == identity


def test_device_check_cannot_bypass_stale_submission_recovery(app, signed_in, device_session):
    from datetime import timedelta
    with app.app_context():
        account = db.session.get(MiAccount,1)
        account.sync_lock_until = utcnow()-timedelta(minutes=1)
        account.sync_lock_token = 'stale-submit'
        db.session.add(SyncJob(id='stale-submit',account_id=1,status='running',
            created_at=utcnow()-timedelta(minutes=6)))
        db.session.commit()
    assert device_post(signed_in).status_code == 422
    device_session.request.assert_not_called()
    assert state(app)[1:] == ('unknown',False)
    with app.app_context():
        assert db.session.get(SyncJob,'stale-submit').status == 'unknown'
