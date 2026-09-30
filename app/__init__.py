# Modified 2026-09-30: reliability and security improvements; see NOTICE.
import logging
import os
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import HTTPException

db = SQLAlchemy()


def create_app(config=None):
    config = dict(config or {})
    instance = config.pop('INSTANCE_PATH', None)
    app = Flask(__name__, instance_path=instance) if instance else Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        ENCRYPTION_KEY=os.environ.get('ENCRYPTION_KEY'),
        SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', 'sqlite:///mimotion.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=64 * 1024,
        SCHEDULER_ENABLED=os.environ.get('SCHEDULER_ENABLED', '1') == '1',
        ALLOW_REGISTRATION=os.environ.get('ALLOW_REGISTRATION', '1') == '1',
    )
    app.config.update(config)
    if app.testing:
        app.config.setdefault('SYNC_INLINE', True)
        if 'SCHEDULER_ENABLED' not in config:
            app.config['SCHEDULER_ENABLED'] = False
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    from app.security import configure_secrets, csrf_token, protect_csrf
    from app.migrations import upgrade
    from app.models import User
    with app.app_context():
        configure_secrets(app)
        upgrade(app)
    if not app.testing:
        log_dir = Path(app.instance_path) / 'logs'
        log_dir.mkdir(exist_ok=True)
        handler = RotatingFileHandler(log_dir / 'mimotion.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = '请先登录管理账号。'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith('/api/') or request.is_json:
            return jsonify(error='登录已过期，请重新登录。', login_url='/login'), 401
        from flask import redirect, url_for, flash
        flash('请先登录管理账号。', 'info')
        return redirect(url_for('auth.login'))

    app.before_request(protect_csrf)
    app.jinja_env.globals['csrf_token'] = csrf_token
    from app.time_utils import display_time
    from app.services import configure_executor, next_run, is_busy, current_range
    app.jinja_env.filters['localtime'] = display_time
    app.jinja_env.filters['number'] = lambda v: f'{v:,}' if v is not None else '—'
    app.jinja_env.globals.update(next_run=next_run, is_busy=is_busy, current_range=current_range)

    @app.context_processor
    def common_context():
        return {'scheduler_running': app.extensions.get('scheduler_leader', False)}

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = {404: '页面或账号不存在。', 403: '你没有权限进行此操作。', 405: '此操作需要从页面按钮提交。',
                   413: '提交内容过大，请缩短后重试。', 429: '操作过于频繁，请稍后重试。'}.get(error.code, error.description)
        if request.path.startswith('/api/') or request.is_json:
            return jsonify(error=message), error.code
        return render_template('error.html', code=error.code, message=message), error.code

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        if request.path.startswith('/api/') or request.is_json:
            return jsonify(error='服务暂时无法完成请求，请稍后重试。'), 500
        return render_template('error.html', code=500, message='服务暂时无法完成请求，请稍后重试。'), 500

    from app.controllers.main import main_bp
    from app.controllers.auth import auth_bp
    from app.controllers.account import account_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    configure_executor(app)
    from app.scheduler.tasks import configure_scheduler
    configure_scheduler(app)
    return app
