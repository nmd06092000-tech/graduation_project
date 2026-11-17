import time

from flask import Blueprint, current_app, flash, make_response, render_template, request, redirect, session, url_for
from flask_login import current_user

from app.constants import (
    MSG_ACCOUNT_DELETED, MSG_ACCOUNT_UNLOCKED, MSG_RESET_CODE_GONE, MSG_ROLE_REQUIRED,
)
from app.models import User
from app.services import ServiceError, ValidationError, user_service
from app.utils.decorators import role_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# 入力エラーで画面に戻すとき、入力内容（パスワードを除く）を次の表示まで保持するセッションのキー
USER_FORM_KEY = 'user_form'
USER_FORM_FIELDS = ('staff_name', 'login_id', 'department', 'role')

# 発行した再設定コードを一覧画面へ渡すセッションのキー（一度表示したら消す）と、表示してよい秒数
RESET_CODE_KEY = 'reset_code'
RESET_CODE_TTL_SECONDS = 120
# ポータルに並べる最新アカウントの件数
PORTAL_RECENT_USERS = 5


# 管理者ポータル
@admin_bp.route('/portal')
@role_required(User.ROLE_ADMIN)
def portal():
    recent_users = [user_service.account_row(user) for user in user_service.list_accounts(limit=PORTAL_RECENT_USERS)]
    return render_template('admin/portal.html', recent_users=recent_users, user_total=user_service.count_accounts())


# ユーザー一覧画面（職員・管理者。役割・部署で絞り込み、氏名/ID で検索。発行直後の再設定コードもここに 1 回だけ出す）
@admin_bp.route('/users')
@role_required(User.ROLE_ADMIN)
def user_list():
    filters = user_service.account_filters(
        request.args.get('role'), request.args.get('department'), request.args.get('q'))
    accounts = user_service.list_accounts(
        role=filters['role'], department_id=filters['department'], q=filters['q'])
    response = make_response(render_template(
        'admin/user_list.html', users=[user_service.account_row(user) for user in accounts],
        departments=user_service.list_departments(),
        filters=filters, user_total=user_service.count_accounts(), issued_code=_pop_issued_code()))
    # コードを含むページは保存させず、外部サイトへの Referer 送信もさせない。
    # same-origin は外部へは Referer を送らないが、自サイトへの POST には送るので、
    # HTTPS での CSRF 検証（Referer 必須）が通る（no-referrer だと CSRFError になる）
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


# アカウント削除（論理削除）。フォームは data-nav="replace" なので、成功・失敗とも一覧へ戻す（絞り込みは保つ）
@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def user_delete(user_id):
    try:
        user = user_service.delete_account(user_id, actor_id=current_user.id)
    except ServiceError as e:
        _log_rejected('delete', user_id)
        flash(str(e), 'danger')
    else:
        current_app.logger.warning('アカウントを削除しました action=delete actor_id=%s target_id=%s',
                                   current_user.id, user.id)
        flash(MSG_ACCOUNT_DELETED.format(name=user.name, login_id=user.login_id), 'success')
    return _redirect_to_user_list()


# ログインの一時ロックを解除する
@admin_bp.route('/users/<int:user_id>/unlock', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def user_unlock(user_id):
    try:
        user = user_service.unlock_account(user_id)
    except ServiceError as e:
        _log_rejected('unlock', user_id)
        flash(str(e), 'danger')
    else:
        current_app.logger.warning('アカウントのロックを解除しました action=unlock actor_id=%s target_id=%s',
                                   current_user.id, user.id)
        flash(MSG_ACCOUNT_UNLOCKED.format(name=user.name, login_id=user.login_id), 'success')
    return _redirect_to_user_list()


# 再設定コードを発行する。コードは一覧画面に 1 回だけ出すため、セッションに入れて一覧へ戻す（ログ・flash には出さない）
@admin_bp.route('/users/<int:user_id>/reset-code', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def user_reset_code(user_id):
    try:
        issued = user_service.issue_reset_code_for_account(user_id)
    except ServiceError as e:
        _log_rejected('reset_code', user_id)
        flash(str(e), 'danger')
    else:
        current_app.logger.warning('再設定コードを発行しました action=reset_code actor_id=%s target_id=%s',
                                   current_user.id, user_id)
        # 発行の知らせは一覧画面のコード枠で出すため、flash は使わない
        session[RESET_CODE_KEY] = {**issued, 'issued': int(time.time())}
    return _redirect_to_user_list()


def _log_rejected(action, target_id):
    """業務エラーで拒否した操作を記録する（氏名・ID・コードは出さない）"""
    current_app.logger.warning('アカウント操作を拒否しました action=%s result=rejected actor_id=%s target_id=%s',
                               action, current_user.id, target_id)


def _redirect_to_user_list():
    """フォームの hidden（role / department / q）を引き継いで一覧へ戻す（無効な値・空の値は付けない）"""
    filters = user_service.account_filters(
        request.form.get('role'), request.form.get('department'), request.form.get('q'))
    return redirect(url_for('admin.user_list', **{key: value for key, value in filters.items() if value}))


def _pop_issued_code():
    """セッションの発行直後の再設定コードを取り出す（取り出したら消える）。表示時間内なら辞書、なければ None

    表示時間（RESET_CODE_TTL_SECONDS）を過ぎた・壊れた値のときは、コードを出さず MSG_RESET_CODE_GONE を警告で出す。
    """
    data = session.pop(RESET_CODE_KEY, None)
    if data is None:
        return None
    try:
        age = time.time() - data['issued']
        if 0 <= age <= RESET_CODE_TTL_SECONDS:
            return {key: data[key] for key in ('name', 'login_id', 'code', 'expires_at_text', 'is_locked')}
    except (KeyError, TypeError):
        pass
    flash(MSG_RESET_CODE_GONE, 'warning')
    return None


# 新規職員アカウント登録画面（職員または管理者のアカウントを作る。職員は所属部署が必須）
@admin_bp.route('/users/register', methods=['GET', 'POST'])
@role_required(User.ROLE_ADMIN)
def user_register():
    if request.method == 'POST':
        role = request.form.get('role', '')
        account = dict(login_id=request.form.get('login_id', ''), name=request.form.get('staff_name', ''),
                       password=request.form.get('password', ''),
                       confirm_password=request.form.get('password_confirm', ''))
        try:
            if role == User.ROLE_STAFF:
                user_service.create_staff(department_id=request.form.get('department', ''), **account)
            elif role == User.ROLE_ADMIN:
                user_service.create_admin(**account)
            else:
                raise ValidationError([('warning', MSG_ROLE_REQUIRED)])
        except ServiceError as e:
            if isinstance(e, ValidationError):
                for category, message in e.messages:
                    flash(message, category)
            else:
                flash(str(e), 'danger')
            # 入力内容（パスワードを除く）を残して画面を表示し直す（履歴に同じ画面を増やさない）
            session[USER_FORM_KEY] = {key: request.form.get(key, '') for key in USER_FORM_FIELDS}
            return redirect(url_for('admin.user_register'))
        return redirect(url_for('admin.user_register_complete'))
    form = session.pop(USER_FORM_KEY, None) or {}
    return render_template('admin/user_form.html', form=form, departments=user_service.list_departments())


# 新規職員アカウント登録完了画面
@admin_bp.route('/users/register/complete')
@role_required(User.ROLE_ADMIN)
def user_register_complete():
    return render_template('admin/user_register_complete.html')
