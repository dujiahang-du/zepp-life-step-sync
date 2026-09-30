# Modified 2026-09-30: reliability and security improvements; see NOTICE.
import re
import time
from collections import defaultdict, deque
from threading import Lock
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import User

auth_bp = Blueprint('auth', __name__)
attempt_lock = Lock()


def throttle():
    # 本机单进程部署的登录限流；不信任客户端提供的转发 IP。
    with attempt_lock:
        attempts = current_app.extensions.setdefault('login_attempts', defaultdict(deque))
        now = time.monotonic()
        for key in list(attempts):
            while attempts[key] and attempts[key][0] < now - 300:
                attempts[key].popleft()
            if not attempts[key]:
                del attempts[key]
        key = request.remote_addr or 'local'
        if len(attempts) > 2000 or len(attempts[key]) >= 10:
            abort(429)
        attempts[key].append(now)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    errors, values = {}, {}
    if request.method == 'POST':
        throttle()
        values['username'] = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=values['username']).first()
        if not values['username'] or not password or len(password) > 128 or user is None or not user.check_password(password):
            errors['form'] = '管理账号或密码不正确，请重新输入。运动账号请在登录后添加。'
        else:
            session.clear()
            login_user(user)
            session.permanent = True
            return redirect(url_for('main.index'))
    return render_template('auth/login.html', errors=errors, values=values), 422 if errors else 200


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if not current_app.config['ALLOW_REGISTRATION']:
        abort(403)
    errors, values = {}, {}
    if request.method == 'POST':
        throttle()
        values = {k: request.form.get(k, '').strip() for k in ('username', 'email')}
        password = request.form.get('password', '')
        if not re.fullmatch(r'[\w.-]{3,40}', values['username']):
            errors['username'] = '使用 3–40 个文字、数字、下划线、点或短横线。'
        if len(values['email']) > 120 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
            errors['email'] = '请输入有效邮箱。'
        if not 10 <= len(password) <= 128:
            errors['password'] = '密码长度应为 10–128 个字符。'
        if password != request.form.get('confirm_password', ''):
            errors['confirm_password'] = '两次输入的密码不一致。'
        if User.query.filter_by(username=values['username']).first():
            errors['username'] = '此用户名已注册。'
        if User.query.filter_by(email=values['email']).first():
            errors['email'] = '此邮箱已注册。'
        if not errors:
            user = User(**values)
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                errors['form'] = '用户名或邮箱已被注册，请更换后重试。'
            else:
                flash('管理账号已创建，请登录。', 'success')
                return redirect(url_for('auth.login'))
    return render_template('auth/register.html', errors=errors, values=values), 422 if errors else 200


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('auth.login'))
