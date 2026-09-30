# Modified 2026-09-30: reliability and security improvements; see NOTICE.
import json
import uuid
from datetime import timedelta
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from app import db
from app.models import MiAccount, StepRecord, SyncJob
from app.security import encrypt
from app.services import BusyError, is_busy, job_state, submit_sync
from app.stats import statistics, trend_points
from app.time_utils import local_day_start, utcnow
from app.utils.mi_motion import MiMotion, MotionError
from app.validation import account_settings, normalize_account

account_bp = Blueprint('account', __name__)


def owned_account(ident):
    account = MiAccount.query.filter_by(id=ident, user_id=current_user.id).first()
    if not account:
        abort(404)
    return account


def client_factory():
    return current_app.config.get('MOTION_CLIENT_FACTORY', MiMotion)


def unlocked_query(account):
    return MiAccount.query.filter(MiAccount.id == account.id,
        or_(MiAccount.sync_lock_until.is_(None), MiAccount.sync_lock_until <= utcnow()))


def check_credentials(user, password):
    client = client_factory()(user, password)
    try:
        return client.validate_credentials()
    finally:
        client.close()


def form_values(account=None):
    defaults = {'mi_user': '', 'min_step': 18000, 'max_step': 25000,
                'sync_start_hour': 8, 'sync_end_hour': 22, 'is_active': True}
    if account:
        defaults.update({k: getattr(account, k) for k in defaults})
    if request.method == 'POST':
        defaults.update({k: request.form.get(k, '') for k in defaults if k != 'is_active'})
        defaults['is_active'] = request.form.get('is_active') == 'on'
    return defaults


@account_bp.route('/accounts')
@login_required
def list_accounts():
    accounts = current_user.accounts.order_by(MiAccount.created_at.desc()).all()
    latest = db.session.query(StepRecord.account_id, db.func.max(StepRecord.id).label('record_id')).group_by(StepRecord.account_id).subquery()
    records = StepRecord.query.join(latest, StepRecord.id == latest.c.record_id).join(MiAccount).filter(MiAccount.user_id == current_user.id).all()
    by_id = {r.account_id: r for r in records}
    for account in accounts:
        account.latest_record = by_id.get(account.id)
        account.pending_job = SyncJob.query.filter_by(account_id=account.id).filter(SyncJob.status.in_(['queued', 'running'])).order_by(SyncJob.created_at.desc()).first()
        if account.pending_job:
            job_state(account.pending_job)
            if account.pending_job.status not in ('queued', 'running'):
                account.pending_job = None
                account.latest_record = account.step_records.order_by(StepRecord.id.desc()).first()
    return render_template('account/list.html', accounts=accounts)


@account_bp.route('/account/add', methods=['GET', 'POST'])
@login_required
def add_account():
    values, errors = form_values(), {}
    if request.method == 'POST':
        settings, errors = account_settings(request.form)
        try:
            normalized = normalize_account(values['mi_user'])
        except ValueError as exc:
            errors['mi_user'] = str(exc)
            normalized = ''
        password = request.form.get('mi_password', '')
        if not 1 <= len(password) <= 128:
            errors['mi_password'] = '请输入运动账号密码，最长 128 个字符。'
        if request.form.get('confirm_login') != 'on':
            errors['form'] = '请确认已了解登录可能使手机端退出，再验证连接。'
        if normalized and MiAccount.query.filter_by(user_id=current_user.id, mi_user=normalized).first():
            errors['mi_user'] = '此运动账号已经添加，请在账号管理中编辑。'
        if current_user.accounts.count() >= 50:
            errors['form'] = '最多管理 50 个运动账号。'
        if not errors:
            try:
                tokens = check_credentials(normalized, password)
            except (MotionError, ValueError) as exc:
                errors['form'] = str(exc)
            except Exception:
                current_app.logger.error('账号验证发生非预期错误；未输出凭据。')
                errors['form'] = '验证暂时失败，请稍后重试。'
            else:
                account = MiAccount(user_id=current_user.id, mi_user=normalized, is_active=values['is_active'], **settings)
                account.set_password(password)
                account.token_data = encrypt(json.dumps(tokens))
                db.session.add(account)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    errors['mi_user'] = '此运动账号已经添加，请返回账号管理查看。'
                else:
                    flash('账号连接成功并已保存。本次验证未提交步数。', 'success')
                    return redirect(url_for('account.list_accounts'))
    return render_template('account/add.html', account=None, values=values, errors=errors), 422 if errors else 200


@account_bp.route('/account/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_account(id):
    account = owned_account(id)
    values, errors = form_values(account), {}
    if request.method == 'POST':
        settings, errors = account_settings(request.form)
        if is_busy(account):
            errors['form'] = '账号正在执行，请等待任务结束后编辑。'
        password = request.form.get('mi_password', '')
        if len(password) > 128:
            errors['mi_password'] = '密码最长 128 个字符。'
        if password:
            errors['mi_password'] = '请使用重新授权页面更新密码，需先确认手机登录影响。'
        if not errors:
            changes = {**settings, 'is_active': values['is_active'] and not account.sync_hold}
            if unlocked_query(account).filter(MiAccount.sync_hold == account.sync_hold).update(changes, synchronize_session=False) != 1:
                db.session.rollback()
                errors['form'] = '账号正在执行，请任务结束后再保存。'
            else:
                db.session.commit()
                flash('账号设置已保存。', 'success')
                return redirect(url_for('account.list_accounts'))
    return render_template('account/edit.html', account=account, values=values, errors=errors), 422 if errors else 200


@account_bp.route('/account/<int:id>/toggle', methods=['POST'])
@login_required
def toggle_account(id):
    account = owned_account(id)
    if account.sync_hold:
        flash('请先重新授权或核对结果，再启用自动同步。', 'warning')
        return redirect(url_for('account.list_accounts'))
    if unlocked_query(account).filter(MiAccount.sync_hold.is_(None)).update({'is_active': ~MiAccount.is_active}, synchronize_session=False) != 1:
        db.session.rollback()
        flash('任务正在执行，请结束后再调整自动同步状态。', 'warning')
    else:
        db.session.commit()
        db.session.refresh(account)
        flash('已启用自动同步。' if account.is_active else '已暂停自动同步，仍可手动执行。', 'success')
    return redirect(url_for('account.list_accounts'))


@account_bp.route('/account/<int:id>/delete', methods=['POST'])
@login_required
def delete_account(id):
    account = owned_account(id)
    if unlocked_query(account).update({'sync_lock_token': 'deleting', 'sync_lock_until': utcnow() + timedelta(minutes=1)}, synchronize_session=False) != 1:
        db.session.rollback()
        flash('账号正在执行，暂时不能删除。', 'warning')
        return redirect(url_for('account.list_accounts'))
    db.session.delete(account)
    db.session.commit()
    flash('账号及其同步记录已删除。', 'success')
    return redirect(url_for('account.list_accounts'))


@account_bp.route('/account/<int:id>/sync', methods=['POST'])
@login_required
def sync_account(id):
    account = owned_account(id)
    try:
        ident = submit_sync(account)
    except BusyError as exc:
        if request.is_json:
            return jsonify(error=str(exc)), 409
        flash(str(exc), 'warning')
        return redirect(url_for('account.list_accounts'))
    if request.is_json:
        return jsonify(id=ident, status_url=url_for('account.task_status', id=ident)), 202
    flash('任务已提交，可在账号卡片查看执行进度。', 'info')
    return redirect(url_for('account.list_accounts'))


@account_bp.route('/api/tasks/<string:id>')
@login_required
def task_status(id):
    job = SyncJob.query.join(MiAccount).filter(SyncJob.id == id, MiAccount.user_id == current_user.id).first()
    if not job:
        abort(404)
    return jsonify(job_state(job))


@account_bp.route('/account/<int:id>/recover', methods=['GET', 'POST'])
@login_required
def recover_account(id):
    account = owned_account(id)
    errors = {}
    if request.method == 'POST':
        action = request.form.get('action')
        if request.form.get('confirmed') != 'on':
            errors['form'] = '请阅读说明并勾选确认。'
        elif action not in ('authorize', 'resolve') or (action == 'resolve' and account.sync_hold != 'unknown'):
            abort(400)
        else:
            ident = str(uuid.uuid4())
            if unlocked_query(account).update({'sync_lock_token': ident,
                    'sync_lock_until': utcnow() + timedelta(minutes=5)}, synchronize_session=False) != 1:
                db.session.rollback()
                errors['form'] = '账号正在执行，请结束后重试。'
            else:
                db.session.commit()
                try:
                    changes = {'sync_hold': None, 'is_active': False}
                    if action == 'authorize':
                        password = request.form.get('mi_password') or account.get_password()
                        if len(password) > 128:
                            raise ValueError('密码最长 128 个字符。')
                        tokens = check_credentials(account.mi_user, password)
                        changes.update(mi_password=encrypt(password), token_data=encrypt(json.dumps(tokens)))
                    changes.update(sync_lock_token=None, sync_lock_until=None)
                    if MiAccount.query.filter_by(id=id, sync_lock_token=ident).update(changes, synchronize_session=False) != 1:
                        raise ValueError('处理时间过长，请重新加载账号状态。')
                    db.session.commit()
                    flash('已恢复手动同步。自动同步仍暂停，可在账号菜单中自行启用。', 'success')
                    return redirect(url_for('account.list_accounts'))
                except (MotionError, ValueError) as exc:
                    db.session.rollback()
                    errors['form'] = str(exc)
                except Exception:
                    db.session.rollback()
                    errors['form'] = '处理失败，请稍后重试。'
                finally:
                    MiAccount.query.filter_by(id=id, sync_lock_token=ident).update(
                        {'sync_lock_token': None, 'sync_lock_until': None}, synchronize_session=False)
                    db.session.commit()
    return render_template('account/recover.html', account=account, errors=errors), 422 if errors else 200


@account_bp.route('/account/<int:id>/records')
@login_required
def account_records(id):
    account = owned_account(id)
    days = request.args.get('days', 7, type=int)
    if days not in (7, 30, 90):
        days = 7
    status = request.args.get('status', 'all')
    if status not in ('all', 'success', 'failed', 'requires_auth', 'unknown', 'skipped'):
        status = 'all'
    query = StepRecord.query.filter(StepRecord.account_id == id,
        StepRecord.created_at >= local_day_start(days - 1), StepRecord.created_at <= utcnow())
    if status == 'success':
        query = query.filter(StepRecord.status.is_(True))
    elif status == 'failed':
        query = query.filter(StepRecord.status.is_(False), or_(StepRecord.outcome.is_(None), StepRecord.outcome == 'failed'))
    elif status != 'all':
        query = query.filter(StepRecord.outcome == status)
    page = max(1, request.args.get('page', 1, type=int))
    pagination = query.order_by(StepRecord.created_at.desc(), StepRecord.id.desc()).paginate(page=page, per_page=20, error_out=False)
    stats = statistics(current_user.id, id)
    return render_template('account/records.html', account=account, records=pagination.items,
        pagination=pagination, days=days, status=status, stats=stats, trend=trend_points(stats))


@account_bp.route('/api/account/<int:id>/stats')
@login_required
def account_stats(id):
    owned_account(id)
    stats = statistics(current_user.id, id)
    return jsonify({k: stats[k] for k in ('dates', 'steps', 'success_rate')})
