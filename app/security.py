"""本地密钥、凭据加密与表单保护。"""
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app, request, session

PREFIX = 'enc:v1:'


def configure_secrets(app):
    if app.config.get('TESTING'):
        if not app.config.get('ENCRYPTION_KEY'):
            app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        if not app.secret_key:
            app.secret_key = secrets.token_hex(32)
        return
    path = Path(app.instance_path) / 'secrets.json'
    if not path.exists():
        # 已加密数据库缺少密钥时必须保留原状，不能生成新密钥掩盖问题。
        from sqlalchemy import text
        from app import db
        from sqlalchemy import inspect
        if inspect(db.engine).has_table('mi_account'):
            encrypted = db.session.execute(text("SELECT COUNT(*) FROM mi_account WHERE mi_password LIKE 'enc:v1:%'")).scalar()
            if encrypted and not app.config.get('ENCRYPTION_KEY'):
                raise RuntimeError('缺少凭据密钥，请恢复 instance/secrets.json 或设置 ENCRYPTION_KEY。')
        values = {'secret_key': secrets.token_hex(32), 'encryption_key': Fernet.generate_key().decode()}
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(values, handle)
        except FileExistsError:
            pass
    values = json.loads(path.read_text(encoding='utf-8'))
    app.config['SECRET_KEY'] = app.config.get('SECRET_KEY') or values['secret_key']
    app.config['ENCRYPTION_KEY'] = app.config.get('ENCRYPTION_KEY') or values['encryption_key']
    Fernet(app.config['ENCRYPTION_KEY'])


def encrypt(value):
    return PREFIX + Fernet(current_app.config['ENCRYPTION_KEY']).encrypt(value.encode()).decode()


def decrypt(value):
    if not value.startswith(PREFIX):
        raise ValueError('凭据尚未迁移，请重新启动应用完成升级。')
    try:
        return Fernet(current_app.config['ENCRYPTION_KEY']).decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise ValueError('凭据密钥不匹配，请恢复原密钥。') from exc


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def protect_csrf():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        expected = session.get('csrf_token', '')
        if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
            abort(400, description='页面已过期，请刷新页面后重新操作。')
