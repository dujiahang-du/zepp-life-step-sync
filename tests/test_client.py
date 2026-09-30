import json
from unittest.mock import Mock
from urllib.parse import parse_qs
import pytest
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from app.utils.mi_motion import MiMotion, MotionError
from app.time_utils import local_now


def response(status=200, data=None, location=None):
    value=Mock(status_code=status, headers={'Location':location} if location else {})
    value.json.return_value=data
    return value


def login_responses():
    return [response(303,location='https://example.invalid/?access=token%2Bwith%26symbols'),
            response(data={'result':'ok','token_info':{'login_token':'login-fixture','app_token':'app-fixture','user_id':'uid-fixture'}})]


def test_v2_login_encryption_and_read_only_validation():
    session=Mock()
    session.request.side_effect=login_responses()
    motion=MiMotion('13800000000','fixture-password',session=session)
    assert motion.validate_credentials()['user_id']=='uid-fixture'
    assert session.request.call_count==2
    first=session.request.call_args_list[0]
    assert first.args[1]=='https://api-user.zepp.com/v2/registrations/tokens'
    assert first.kwargs['timeout']==(5,15)
    cipher=Cipher(algorithms.AES(b'xeNtBVqzDc6tuNTh'),modes.CBC(b'MAAAYAAAAAAAAABg')).decryptor()
    padded=cipher.update(first.kwargs['data'])+cipher.finalize()
    unpadder=padding.PKCS7(128).unpadder()
    plain=(unpadder.update(padded)+unpadder.finalize()).decode()
    assert parse_qs(plain)['emailOrPhone']==['+8613800000000']
    assert parse_qs(plain)['password']==['fixture-password']
    assert session.request.call_args_list[1].kwargs['data']['code']=='token+with&symbols'
    assert all('band_data' not in call.args[1] for call in session.request.call_args_list)


def test_sync_payload_date_steps_device_and_token():
    session=Mock()
    session.request.side_effect=login_responses()+[response(data={'items':[{'deviceType':0,'deviceId':'AA:BB:CC:DD:EE:FF'}]}),response(data={'message':'success'})]
    motion=MiMotion('mail@example.invalid','fixture-password',session=session)
    message,ok=motion.sync_step(12345)
    assert ok and '12,345' in message
    call=session.request.call_args_list[-1]
    data=parse_qs(call.kwargs['data'])
    body=json.loads(data['data_json'][0])[0]
    assert body['date']==local_now().strftime('%Y-%m-%d')
    assert json.loads(body['summary'])['stp']['ttl']==12345
    assert body['data'][0]['did']=='AABBCCDDEEFF'
    assert data['last_deviceid']==['AABBCCDDEEFF']
    assert call.kwargs['headers']['apptoken']=='app-fixture'
    assert all(c.kwargs['timeout']==(5,15) for c in session.request.call_args_list)
    session.close.assert_called_once()


def test_valid_cached_token_avoids_password_login():
    session=Mock()
    session.request.return_value=response(data={'message':'success'})
    motion=MiMotion('mail@example.invalid','unused',tokens={'app_token':'cached','login_token':'login','user_id':'uid'},session=session)
    assert motion.validate_credentials()['app_token']=='cached'
    assert session.request.call_count==1
    assert session.request.call_args.args[0]=='GET'


def test_expired_token_falls_back_to_v2_login():
    session=Mock()
    session.request.side_effect=[response(data={'message':'invalid_token'})]+login_responses()
    motion=MiMotion('mail@example.invalid','fixture',tokens={'app_token':'expired','user_id':'uid'},session=session)
    assert motion.validate_credentials()['app_token']=='app-fixture'


@pytest.mark.parametrize('error',[requests.Timeout(), requests.ConnectionError('secret-url'), ValueError('malformed')])
def test_network_errors_do_not_expose_request_details(error):
    session=Mock()
    if isinstance(error,ValueError):
        bad=response(); bad.json.side_effect=error
        session.request.side_effect=[login_responses()[0],bad]
    else:
        session.request.side_effect=error
    result,ok=MiMotion('mail@example.invalid','fixture-secret',session=session).sync_step(100)
    assert not ok and 'fixture-secret' not in result and 'secret-url' not in result


def test_auth_failure_and_rate_limit_are_actionable():
    for status,location in [(303,'https://example.invalid/?error=INVALID'),(429,None)]:
        session=Mock();session.request.return_value=response(status,location=location)
        with pytest.raises(MotionError):
            MiMotion('mail@example.invalid','fixture',session=session).validate_credentials()


def test_invalid_steps_never_send_network():
    session=Mock()
    assert MiMotion('mail@example.invalid','fixture',session=session).sync_step(-1)[1] is False
    session.request.assert_not_called()
