import time

from flask import Blueprint, current_app, flash, make_response, render_template, request, redirect, session, url_for
from flask_login import current_user

from app.constants import (
    MSG_ACCOUNT_DELETED, MSG_ACCOUNT_UNLOCKED, MSG_CATEGORY_CREATED, MSG_CATEGORY_HIDDEN, MSG_CATEGORY_RESTORED,
    MSG_CATEGORY_UPDATED, MSG_DEPARTMENT_CREATED, MSG_DEPARTMENT_HIDDEN, MSG_DEPARTMENT_RESTORED,
    MSG_DEPARTMENT_UPDATED, MSG_RESET_CODE_GONE, MSG_ROLE_REQUIRED,
)
from app.models import User
from app.services import ServiceError, ValidationError, master_service, user_service
from app.utils.decorators import role_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# 入力エラーで画面に戻すとき、入力内容（パスワードを除く）を次の表示まで保持するセッションのキー
USER_FORM_KEY = 'user_form'
USER_FORM_FIELDS = ('staff_name', 'login_id', 'department', 'role')
# マスタ管理（部署・カテゴリの追加フォーム）の入力内容のキー。セッションは Cookie に入るので保存する長さを制限する
DEPARTMENT_FORM_KEY = 'department_form'
CATEGORY_FORM_KEY = 'category_form'
MASTER_FORM_VALUE_MAX = 100

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
    return render_template('admin/portal.html', recent_users=recent_users, user_total=user_service.count_accounts(),
                           master=master_service.portal_summary())


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
        # 非表示の部署に所属する職員も絞り込めるよう、非表示の部署も含める
        departments=user_service.list_departments(include_deleted=True),
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


# ---------------------------------------------------------------- マスタ管理（部署・カテゴリ。3b）
# どのフォームも data-nav="replace" なので、成功・失敗とも一覧へ戻す（PRG）。「非表示も表示」の状態は hidden の show_deleted=1 で保つ

# 部署の一覧画面（非表示の部署は show_deleted=1 のときだけ出す）
@admin_bp.route('/departments')
@role_required(User.ROLE_ADMIN)
def department_list():
    show_deleted = _show_deleted(request.args)
    form = {'name': '', **(session.pop(DEPARTMENT_FORM_KEY, None) or {})}
    return render_template(
        'admin/departments.html', departments=master_service.list_department_rows(include_deleted=show_deleted),
        show_deleted=show_deleted, hidden_count=master_service.count_hidden()['departments'], form=form)


# 部署の追加。入力エラーは入力内容を残して一覧を表示し直す
@admin_bp.route('/departments', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def department_create():
    name = request.form.get('name', '')
    ok = _run_master_action('department_create', None, lambda: master_service.create_department(name),
                            MSG_DEPARTMENT_CREATED)
    if not ok:
        session[DEPARTMENT_FORM_KEY] = {'name': name[:MASTER_FORM_VALUE_MAX]}
    return _redirect_to_master('admin.department_list')


# 部署の名前の変更（エラーは flash だけ。モーダルは開き直さない）
@admin_bp.route('/departments/<int:department_id>/update', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def department_update(department_id):
    name = request.form.get('name', '')
    _run_master_action('department_update', department_id, lambda: master_service.rename_department(department_id, name),
                       MSG_DEPARTMENT_UPDATED)
    return _redirect_to_master('admin.department_list')


# 部署の非表示（新しい報告・職員登録・AI の選択肢から外す）
@admin_bp.route('/departments/<int:department_id>/hide', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def department_hide(department_id):
    _run_master_action('department_hide', department_id,
                       lambda: master_service.hide_department(department_id, actor_id=current_user.id),
                       MSG_DEPARTMENT_HIDDEN)
    return _redirect_to_master('admin.department_list')


# 部署を表示に戻す
@admin_bp.route('/departments/<int:department_id>/restore', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def department_restore(department_id):
    _run_master_action('department_restore', department_id, lambda: master_service.restore_department(department_id),
                       MSG_DEPARTMENT_RESTORED)
    return _redirect_to_master('admin.department_list')


# カテゴリの一覧画面（既定の担当部署の選択肢は表示中の部署だけ）
@admin_bp.route('/categories')
@role_required(User.ROLE_ADMIN)
def category_list():
    show_deleted = _show_deleted(request.args)
    form = {'name': '', 'default_department_id': '', **(session.pop(CATEGORY_FORM_KEY, None) or {})}
    return render_template(
        'admin/categories.html', categories=master_service.list_category_rows(include_deleted=show_deleted),
        departments=user_service.list_departments(), show_deleted=show_deleted,
        hidden_count=master_service.count_hidden()['categories'], form=form)


# カテゴリの追加。入力エラーは入力内容を残して一覧を表示し直す
@admin_bp.route('/categories', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def category_create():
    name = request.form.get('name', '')
    department_id = request.form.get('default_department_id', '')
    ok = _run_master_action('category_create', None, lambda: master_service.create_category(name, department_id),
                            MSG_CATEGORY_CREATED)
    if not ok:
        session[CATEGORY_FORM_KEY] = {'name': name[:MASTER_FORM_VALUE_MAX],
                                      'default_department_id': department_id[:MASTER_FORM_VALUE_MAX]}
    return _redirect_to_master('admin.category_list')


# カテゴリの名前・既定の担当部署の変更（エラーは flash だけ）
@admin_bp.route('/categories/<int:category_id>/update', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def category_update(category_id):
    name = request.form.get('name', '')
    department_id = request.form.get('default_department_id', '')
    _run_master_action('category_update', category_id,
                       lambda: master_service.update_category(category_id, name, department_id), MSG_CATEGORY_UPDATED)
    return _redirect_to_master('admin.category_list')


# カテゴリの非表示（新しい報告・AI の選択肢から外す）
@admin_bp.route('/categories/<int:category_id>/hide', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def category_hide(category_id):
    _run_master_action('category_hide', category_id,
                       lambda: master_service.hide_category(category_id, actor_id=current_user.id),
                       MSG_CATEGORY_HIDDEN)
    return _redirect_to_master('admin.category_list')


# カテゴリを表示に戻す
@admin_bp.route('/categories/<int:category_id>/restore', methods=['POST'])
@role_required(User.ROLE_ADMIN)
def category_restore(category_id):
    _run_master_action('category_restore', category_id, lambda: master_service.restore_category(category_id),
                       MSG_CATEGORY_RESTORED)
    return _redirect_to_master('admin.category_list')


def _show_deleted(values):
    """「非表示も表示」の状態。値が '1' のときだけ True（それ以外は無視）"""
    return values.get('show_deleted') == '1'


def _redirect_to_master(endpoint):
    """マスタ管理の一覧へ戻す（「非表示も表示」の状態を保つ）"""
    return redirect(url_for(endpoint, **({'show_deleted': 1} if _show_deleted(request.form) else {})))


def _run_master_action(action, target_id, call, success_message):
    """マスタ管理の操作を実行して flash する。成功なら True。

    業務エラーは（ValidationError なら 1 件ずつ、それ以外は赤で）flash する。結果は WARNING で記録する（名前は出さない）。
    """
    try:
        row = call()
    except ServiceError as e:
        current_app.logger.warning('マスタ操作を拒否しました action=%s result=rejected actor_id=%s target_id=%s',
                                   action, current_user.id, target_id)
        if isinstance(e, ValidationError):
            for category, message in e.messages:
                flash(message, category)
        else:
            flash(str(e), 'danger')
        return False
    current_app.logger.warning('マスタを変更しました action=%s actor_id=%s target_id=%s',
                               action, current_user.id, row.id)
    flash(success_message.format(name=row.name), 'success')
    return True
