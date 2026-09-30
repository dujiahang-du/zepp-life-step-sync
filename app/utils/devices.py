"""Zepp 设备协议，参考 sagehere/steps（Apache-2.0），见 NOTICE。"""
import re
import secrets
from app.time_utils import local_now


def device_ident(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9:-]{1,64}', value):
        return value.replace(':', '').replace('-', '').upper()
    return None


def matching_device(devices, identity):
    expected_id, expected_mac = device_ident(identity.get('id')), device_ident(identity.get('mac'))
    for item in devices:
        ident = device_ident(item.get('deviceId') or item.get('deviceid'))
        mac = device_ident(item.get('mac'))
        if (expected_id and ident == expected_id) or (expected_mac and mac == expected_mac):
            return ident or mac
    return None


def wearable_device(devices):
    for item in devices:
        kind = item.get('deviceType', item.get('device_type'))
        name = str(item.get('productName', '')).lower()
        wearable = (type(kind) is int and kind == 0) or kind == '0'
        if kind is None:
            wearable = any(word in name for word in ('band', 'watch', '手环', '手表'))
        if wearable:
            ident = device_ident(item.get('deviceId') or item.get('deviceid') or item.get('mac'))
            if ident:
                return ident
    return None


def new_device_identity():
    octets = bytearray(secrets.token_bytes(6))
    octets[0] = (octets[0] | 2) & 254
    return {'id': secrets.token_hex(8).upper(),
            'mac': ':'.join(f'{value:02X}' for value in octets), 'state': 'prepared'}


class DeviceProtocol:
    def _device_headers(self):
        return {'apptoken': self.tokens['app_token'],
                'User-Agent': 'MiFit6.14.0 (2211133C; Android 15; Density/2.75)'}

    def get_devices(self):
        from app.utils.mi_motion import MotionError
        now = int(local_now().timestamp())
        params = {'t': now, 'callid': now, 'userid': self.tokens['user_id'],
                  'device': 'android_35', 'device_type': 'android_phone',
                  'enableMultiDevice': 'false', 'v': '2.0', 'lang': 'zh_CN',
                  'channel': 'Normal', 'country': 'CN', 'timezone': 'Asia/Shanghai',
                  'cv': '50813_6.14.0'}
        result = self._json(self._request('GET', 'https://api-mifit-cn.huami.com/v1/device/lists.json',
            params=params, headers=self._device_headers()))
        if type(result.get('code')) is not int or result['code'] != 1 or not isinstance(result.get('data'), list):
            raise MotionError('设备查询未返回有效列表，已停止操作；未提交步数。')
        if any(not isinstance(item, dict) for item in result['data']):
            raise MotionError('设备列表格式异常，已停止操作；未提交步数。')
        return result['data']

    def bind_device(self, identity):
        from app.utils.mi_motion import MotionError
        # 调用前必须持久化设备身份及已尝试状态，防止响应丢失后重复绑定。
        if not re.fullmatch(r'[A-F0-9]{16}', identity.get('id', '')) or not re.fullmatch(r'(?:[A-F0-9]{2}:){5}[A-F0-9]{2}', identity.get('mac', '')):
            raise MotionError('本地设备身份无效，未发送绑定请求。')
        data = {'app_time': int(local_now().timestamp()), 'code': '0', 'activeStatus': '0',
                'bind_timezone': '32', 'device_type': '0', 'crcedUserId': '0',
                'userid': self.tokens['user_id'], 'device': 'android_29',
                'deviceid': identity['id'], 'enableMultiDevice': 'true', 'mac': identity['mac'],
                'productVersion': '256', 'brandType': '-1', 'productId': '61', 'device_source': '58',
                'brand': 'XiaoMi', 'fw_version': 'V1.0.0.04', 'hardwareVersion': 'V0.44.131.18',
                'soft_version': '6.13.1', 'sys_model': 'Xiaomi 10 Pro', 'sys_version': 'Android_35',
                'v': '2.0', 'lang': 'zh_CN', 'channel': 'Normal', 'country': 'CN',
                'timezone': 'Asia/Shanghai', 'cv': '50813_6.14.0'}
        result = self._json(self._request('POST', 'https://api-mifit-cn.huami.com/v1/device/binds.json',
            data=data, headers=self._device_headers()))
        if type(result.get('code')) is not int or result['code'] != 1:
            raise MotionError('Zepp 未确认绑定请求，请重新查询设备；不会自动重复绑定。')
