# Modified 2026-09-30: reliability and security improvements; see NOTICE.
"""Zepp Life 协议适配。参考 TonyJiangWJ/mimotion（Apache-2.0）。"""
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from app.time_utils import local_now
from app.validation import normalize_account


class MotionError(Exception):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class MiMotion:
    def __init__(self, user, password, tokens=None, session=None):
        self.user = normalize_account(user)
        self.password = password
        self.tokens = dict(tokens or {})
        self.is_phone = self.user.startswith('+86')
        self.session = session or requests.Session()
        self.device_id = self.tokens.get('device_id') or str(uuid.uuid4())
        self.device_lookup_fallback = False
        self.outcome = 'failed'

    @staticmethod
    def get_beijing_time():
        return local_now()

    @staticmethod
    def format_now():
        return local_now().strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def get_time():
        return str(round(local_now().timestamp() * 1000))

    @staticmethod
    def get_access_token(location):
        return parse_qs(urlparse(location).query).get('access', [None])[0]

    def _request(self, method, url, **kwargs):
        stage = {
            '/v2/registrations/tokens': '账号认证',
            '/v2/client/login': '客户端授权',
            '/huami.health.getUserInfo.json': '令牌校验',
            '/v1/device/binds.json': '设备查询',
            '/v1/data/band_data.json': '步数提交',
        }.get(urlparse(url).path, '服务请求')
        try:
            response = self.session.request(method, url, timeout=(5, 15), **kwargs)
        except requests.Timeout as exc:
            raise MotionError('Zepp 服务响应超时，请稍后重试。') from exc
        except requests.RequestException as exc:
            raise MotionError('无法连接 Zepp 服务，请检查网络后重试。') from exc
        if response.status_code not in (200, 303):
            # 不记录账号、请求体、令牌、响应体或带查询参数的 URL。
            logging.getLogger(__name__).warning('Zepp request rejected: stage=%s status=%s', stage, response.status_code)
            if response.status_code == 429:
                raise MotionError('Zepp 请求过于频繁，请稍后再试。', status_code=429)
            if response.status_code == 400:
                raise MotionError(f'Zepp 在「{stage}」阶段拒绝了请求（HTTP 400）。请先在 Zepp Life App 确认账号能正常登录；若仍失败，请反馈此阶段名称以检查接口兼容性。', status_code=400)
            raise MotionError(f'Zepp 在「{stage}」阶段返回 HTTP {response.status_code}，请稍后重试。', status_code=response.status_code)
        return response

    @staticmethod
    def _json(response):
        try:
            result = response.json()
        except (ValueError, TypeError) as exc:
            raise MotionError('Zepp 返回了无法识别的数据，请稍后重试。') from exc
        if not isinstance(result, dict):
            raise MotionError('Zepp 返回的数据格式异常。')
        return result

    def login(self, *, allow_password=False):
        app_token = self.tokens.get('app_token')
        if app_token and self.tokens.get('user_id'):
            params = {'r': str(uuid.uuid4()), 'userid': self.tokens['user_id'],
                      'appid': '428135909242707968', 'channel': 'Normal', 'country': 'CN',
                      'cv': '50818_6.14.0', 'device': 'android_31', 'device_type': 'android_phone',
                      'lang': 'zh_CN', 'timezone': 'Asia/Shanghai', 'v': '2.0'}
            headers = {'User-Agent': 'MiFit6.14.0 (M2007J1SC; Android 12; Density/2.75)',
                       'country': 'CN', 'appplatform': 'android_phone', 'hm-privacy-diagnostics': 'false',
                       'hm-privacy-ceip': 'true', 'x-request-id': str(uuid.uuid4()),
                       'timezone': 'Asia/Shanghai', 'channel': 'Normal', 'cv': '50818_6.14.0',
                       'appname': 'com.xiaomi.hm.health', 'v': '2.0', 'apptoken': app_token,
                       'lang': 'zh_CN', 'clientid': '428135909242707968'}
            try:
                response = self._json(self._request('GET', 'https://api-mifit-cn3.zepp.com/huami.health.getUserInfo.json',
                    params=params, headers=headers))
            except MotionError as exc:
                if exc.status_code not in (401, 403):
                    raise
            else:
                if response.get('message') == 'success':
                    return self.tokens.get('login_token'), self.tokens['user_id']
            self.tokens = {}
        if not allow_password:
            self.outcome = 'requires_auth'
            raise MotionError('授权已失效或尚未建立，已暂停同步。请手动重新授权；重新登录可能使手机端退出。')
        values = {'emailOrPhone': self.user, 'password': self.password, 'state': 'REDIRECTION',
                  'client_id': 'HuaMi', 'country_code': 'CN', 'token': 'access',
                  'redirect_uri': 'https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html'}
        padder = padding.PKCS7(128).padder()
        padded = padder.update(urlencode(values).encode()) + padder.finalize()
        # 固定密钥与 IV 属于上游传输协议；本地凭据使用独立随机密钥和认证加密。
        cipher = Cipher(algorithms.AES(b'xeNtBVqzDc6tuNTh'), modes.CBC(b'MAAAYAAAAAAAAABg')).encryptor()
        payload = cipher.update(padded) + cipher.finalize()
        headers = {'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                   'user-agent': 'MiFit6.14.0 (M2007J1SC; Android 12; Density/2.75)',
                   'app_name': 'com.xiaomi.hm.health', 'appname': 'com.xiaomi.hm.health',
                   'appplatform': 'android_phone', 'x-hm-ekv': '1', 'hm-privacy-ceip': 'false'}
        response = self._request('POST', 'https://api-user.zepp.com/v2/registrations/tokens',
                                 data=payload, headers=headers, allow_redirects=False)
        code = self.get_access_token(response.headers.get('Location', ''))
        if response.status_code != 303 or not code:
            raise MotionError('Zepp 未完成认证。请检查手机号／邮箱与密码，或先在 Zepp Life App 登录确认账号状态。')
        data = {'app_name': 'com.xiaomi.hm.health', 'app_version': '6.14.0', 'code': code,
                'country_code': 'CN', 'device_id': self.device_id, 'device_model': 'phone' if self.is_phone else 'android_phone',
                'grant_type': 'access_token', 'third_name': 'huami_phone' if self.is_phone else 'email'}
        if not self.is_phone:
            data.update({'allow_registration=': 'false', 'lang': 'zh_CN', 'os_version': '1.5.0',
                         'source': 'com.xiaomi.hm.health:6.14.0:50818',
                         'dn': 'account.zepp.com,api-user.zepp.com,api-mifit.zepp.com,api-watch.zepp.com,app-analytics.zepp.com,api-analytics.huami.com,auth.zepp.com'})
        # 授权接口接收普通表单，不能沿用密文登录请求的 x-hm-ekv 标记。
        grant_headers = {'app_name': 'com.xiaomi.hm.health', 'x-request-id': str(uuid.uuid4()),
                         'accept-language': 'zh-CN', 'appname': 'com.xiaomi.hm.health',
                         'cv': '50818_6.14.0', 'v': '2.0', 'appplatform': 'android_phone',
                         'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'}
        result = self._json(self._request('POST', 'https://account.huami.com/v2/client/login', data=data, headers=grant_headers))
        info = result.get('token_info') or {}
        if result.get('result') != 'ok' or not all(info.get(k) for k in ('login_token', 'app_token', 'user_id')):
            raise MotionError('Zepp 授权失败或响应字段缺失，请在 App 确认账号状态后重试。')
        self.tokens = {k: info[k] for k in ('login_token', 'app_token', 'user_id')}
        self.tokens['device_id'] = self.device_id
        return info['login_token'], info['user_id']

    def get_app_token(self, login_token=None):
        if not self.tokens.get('app_token'):
            self.login()
        return self.tokens['app_token']

    def validate_credentials(self):
        self.login(allow_password=True)
        return self.tokens

    def _bound_device(self):
        self.device_lookup_fallback = False
        if self.tokens.get('bound_device_id'):
            return self.tokens['bound_device_id']
        try:
            result = self._json(self._request('GET', 'https://api-mifit-cn.huami.com/v1/device/binds.json',
                params={'userid': self.tokens['user_id']}, headers={'apptoken': self.tokens['app_token'],
                    'User-Agent': 'MiFit6.14.0 (M2007J1SC; Android 12; Density/2.75)'}))
        except MotionError as exc:
            if exc.status_code is not None and not 500 <= exc.status_code < 600:
                raise
            # 设备查询是辅助步骤；沿用上游的默认设备兼容路径，不重试提交接口。
            logging.getLogger(__name__).warning('Zepp device lookup unavailable; using default device parameters')
            result = {}
        items = result.get('items') or []
        if not isinstance(items, list):
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('deviceType') == 0 or any(word in str(item.get('productName', '')).lower() for word in ('band', 'watch', '手环', '手表')):
                ident = item.get('deviceId') or item.get('mac')
                if ident and re.fullmatch(r'[A-Za-z0-9:-]{1,64}', str(ident)):
                    self.tokens['bound_device_id'] = str(ident).replace(':', '').upper()
                    return self.tokens['bound_device_id']
        self.device_lookup_fallback = True
        return 'DA932FFFFE8816E7'

    def sync_step(self, step_count):
        self.outcome = 'failed'
        submitting = False
        if not isinstance(step_count, int) or not 0 <= step_count <= 98800:
            return '步数必须为 0–98800 的整数。', False
        try:
            self.login()
            device = self._bound_device()
            payload = Path(__file__).with_name('band_payload.txt').read_text(encoding='utf-8')
            payload = re.sub(r'(date%22%3A%22)[^%]+', lambda m: m[1] + local_now().strftime('%Y-%m-%d'), payload, count=1)
            payload = re.sub(r'(ttl%5C%22%3A)\d+', lambda m: m[1] + str(step_count), payload, count=1)
            payload = payload.replace('DA932FFFFE8816E7', device)
            body = urlencode({'userid': self.tokens['user_id'], 'last_sync_data_time': '1597306380',
                              'device_type': '0', 'last_deviceid': device}) + '&data_json=' + payload
            submitting = True
            response = self._json(self._request('POST', 'https://api-mifit-cn.huami.com/v1/data/band_data.json',
                params={'t': self.get_time(), 'r': str(uuid.uuid4())}, data=body,
                headers={'apptoken': self.tokens['app_token'], 'Content-Type': 'application/x-www-form-urlencoded'}))
            if response.get('message') == 'success':
                self.outcome = 'success'
                note = '未获取到绑定设备，已使用默认设备参数。' if self.device_lookup_fallback else ''
                return f'Zepp 已接受 {step_count:,} 步；{note}微信／支付宝展示请在对应 App 核对。', True
            if not response.get('message'):
                raise MotionError('Zepp 提交响应缺少结果字段。')
            return 'Zepp 未接受本次提交，请检查账号、绑定设备与 App 状态后重试。', False
        except MotionError as exc:
            if exc.status_code in (401, 403):
                self.tokens = {}
                self.outcome = 'requires_auth'
                return '授权已失效，已暂停同步。请手动重新授权；重新登录可能使手机端退出。', False
            if submitting and (exc.status_code is None or exc.status_code >= 500):
                self.outcome = 'unknown'
                return '提交结果待确认，已暂停同步。请先在 Zepp Life 核对步数，再确认恢复。' + str(exc), False
            return str(exc), False
        finally:
            self.close()

    def close(self):
        self.session.close()
