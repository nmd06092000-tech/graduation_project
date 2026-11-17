import re

from flask import Blueprint, make_response, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user
from sqlalchemy.exc import IntegrityError

from app.constants import MSG_EMAIL_NO_AT, MSG_PASSWORD_MISMATCH, MSG_RESET_LINK_INVALID, MSG_RESET_MAIL_SENT
from app.extensions import db
from app.models import User
from app.services import ServiceError, ValidationError, auth_service

auth_bp = Blueprint('auth', __name__)

# ロールごとの設定（ログイン画面・ID入力欄・ログイン後の遷移先）
LOGIN_SETTINGS = {
    User.ROLE_CITIZEN: {'template': 'auth/login_citizen.html', 'id_field': 'email', 'home': 'citizen.mypage'},
    User.ROLE_STAFF: {'template': 'auth/login_staff.html', 'id_field': 'staff_id', 'home': 'staff.dashboard'},
    User.ROLE_ADMIN: {'template': 'auth/login_admin.html', 'id_field': 'admin_id', 'home': 'admin.portal'},
}


def _handle_login(role):
    setting = LOGIN_SETTINGS[role]

    # 同じロールでログイン済みならホーム画面へ
    if current_user.is_authenticated and current_user.role == role:
        return redirect(url_for(setting['home']))

    if request.method == 'POST':
        remember = request.form.get('remember') == 'on'
        try:
            user = auth_service.authenticate(
                role, request.form.get(setting['id_field'], ''), request.form.get('password', ''))
        except ValidationError as e:
            # 入力エラー・ロック中（メッセージごとに色分け）
            for category, message in e.messages:
                flash(message, category)
            return render_template(setting['template'])
        except ServiceError as e:
            # ID またはパスワード違い・利用停止
            flash(str(e), 'danger')
            return render_template(setting['template'])

        # 前のログインの残り（報告の下書き・CSRF トークンなど）を引き継がないよう、セッションを作り直す
        session.clear()
        login_user(user, remember=remember)
        return redirect(url_for(setting['home']))

    return render_template(setting['template'])


# 市民ログイン画面
@auth_bp.route('/login/citizen', methods=['GET', 'POST'])
def login_citizen():
    return _handle_login(User.ROLE_CITIZEN)


# 行政職員ログイン画面
@auth_bp.route('/login/staff', methods=['GET', 'POST'])
def login_staff():
    return _handle_login(User.ROLE_STAFF)


# 管理者ログイン画面
@auth_bp.route('/login/admin', methods=['GET', 'POST'])
def login_admin():
    return _handle_login(User.ROLE_ADMIN)


# ログアウト（CSRF 対策のため POST のみ。GET は 405）
@auth_bp.route('/logout', methods=['POST'])
def logout():
    # 次の人に下書き・CSRF トークンなどを引き継がないよう、セッションを空にする。
    # logout_user より先に clear する：logout_user が付ける「保持 Cookie を消す」印をあとから消さないため
    session.clear()
    logout_user()
    return redirect(url_for('public.index'))


# 市民新規登録画面
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        # ログイン時と同じく、メールアドレスは小文字で保存する
        email = request.form.get('email', '').strip().lower()
        postal_code = request.form.get('postal_code', '').strip()
        address = request.form.get('address', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        error = None
        category = 'danger'
        if not name or not email or not postal_code or not address or not password or not confirm_password:
            error = 'お名前、メールアドレス、郵便番号、住所、パスワードのいずれかが未入力です。'
        elif '@' not in email:
            # 市民のメールアドレスに @ が必須（職員・管理者の ID と重ならないようにする。ブラウザ側の検査だけに頼らない）
            error = MSG_EMAIL_NO_AT.format(email=email)
            category = 'warning'
        elif len(name) > 100:
            error = 'お名前は100文字以内で入力してください。'
        elif not re.fullmatch(r'[0-9]{3}-?[0-9]{4}', postal_code):
            error = '郵便番号を正しく入力してください。（例：330-9301）'
        elif len(address) > 255:
            error = '住所は255文字以内で入力してください。'
        elif len(password) < 8:
            error = 'パスワードは8文字以上で入力してください。'
        elif password != confirm_password:
            error = MSG_PASSWORD_MISMATCH
        elif User.query.filter_by(login_id=email).first():
            error = 'このメールアドレスは既に登録されています。'

        if error:
            flash(error, category)
            return render_template('auth/register.html', name=name, email=email,
                                   postal_code=postal_code, address=address)

        # 郵便番号はハイフンありの形（123-4567）にそろえて保存する
        postal_code = postal_code.replace('-', '')
        postal_code = f'{postal_code[:3]}-{postal_code[3:]}'
        user = User(login_id=email, name=name, role=User.ROLE_CITIZEN,
                    postal_code=postal_code, address=address)
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # 同時登録などで一意制約に違反した場合
            db.session.rollback()
            flash('このメールアドレスは既に登録されています。', 'danger')
            return render_template('auth/register.html', name=name, email=email,
                                   postal_code=postal_code, address=address)

        return redirect(url_for('auth.register_complete'))

    return render_template('auth/register.html')


# 市民新規登録完了画面
@auth_bp.route('/register/complete')
def register_complete():
    return render_template('auth/register_complete.html')


def _flash_errors(error):
    """業務エラーを flash する（ValidationError はメッセージごとの色、それ以外は赤 'danger'）"""
    if isinstance(error, ValidationError):
        for category, message in error.messages:
            flash(message, category)
    else:
        flash(str(error), 'danger')


# 市民 パスワード再設定画面（1 画面：メールアドレスと新しいパスワードを入れると、確認用のリンクをメールで送る）
# 登録の有無が分からないよう、入力が正しければ常に同じメッセージを表示する。パスワードは画面に戻さない
@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        email = request.form.get('email', '')
        try:
            auth_service.request_password_reset(
                email, request.form.get('new_password', ''), request.form.get('confirm_password', ''))
        except ValidationError as e:
            _flash_errors(e)
            return render_template('auth/reset_password.html', email=email)
        flash(MSG_RESET_MAIL_SENT, 'success')
        return redirect(url_for('auth.reset_password'))
    return render_template('auth/reset_password.html', email='')


# 市民 パスワード再設定の確認画面（メールのリンクから開く。GET は表示だけで、「確定する」ボタン（POST）で初めて変更する）
# リンクのトークンが Referer や履歴・キャッシュに残らないよう、応答にヘッダを付ける
@auth_bp.route('/reset-password/confirm/<token>', methods=['GET', 'POST'])
def reset_password_confirm(token):
    if request.method == 'POST':
        try:
            auth_service.confirm_password_reset(token)
        except ServiceError as e:
            flash(str(e), 'danger')
            return _no_leak(redirect(url_for('auth.reset_password')))
        return _no_leak(redirect(url_for('auth.reset_password_complete')))

    if not auth_service.is_reset_token_valid(token):
        flash(MSG_RESET_LINK_INVALID, 'danger')
        return _no_leak(redirect(url_for('auth.reset_password')))
    return _no_leak(make_response(render_template('auth/reset_password_confirm.html', token=token)))


def _no_leak(response):
    """再設定リンクの画面の応答：外部サイトへ Referer を送らせず、キャッシュさせない

    same-origin は外部へは Referer を送らないが、自サイトへの POST には送るので、
    HTTPS での CSRF 検証（Referer 必須）が通る（no-referrer だと CSRFError になる）
    """
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    return response


# パスワード再設定完了画面
@auth_bp.route('/reset-password/complete')
def reset_password_complete():
    return render_template('auth/reset_password_complete.html')


def _handle_reset_with_code(role, template, complete_endpoint):
    """職員・管理者のパスワード再設定画面の共通処理（ID・再設定コード・新しいパスワード）

    エラーはメッセージを flash して画面を表示し直す（入力した ID は残し、コード・パスワードは残さない）。
    """
    login_id = request.form.get('login_id', '')
    if request.method == 'POST':
        try:
            auth_service.reset_password_with_code(
                role, login_id, request.form.get('reset_code', ''),
                request.form.get('new_password', ''), request.form.get('confirm_password', ''))
        except ServiceError as e:
            _flash_errors(e)
            return render_template(template, login_id=login_id)
        return redirect(url_for(complete_endpoint))
    return render_template(template, login_id='')


# 行政職員 パスワード再設定画面（運用者が発行した再設定コードが必要）
@auth_bp.route('/login/staff/reset-password', methods=['GET', 'POST'])
def reset_password_staff():
    return _handle_reset_with_code(User.ROLE_STAFF, 'auth/reset_password_staff.html',
                                   'auth.reset_password_staff_complete')


# 行政職員 パスワード再設定完了画面
@auth_bp.route('/login/staff/reset-password/complete')
def reset_password_staff_complete():
    return render_template('auth/reset_password_staff_complete.html')


# 管理者 パスワード再設定画面（運用者が発行した再設定コードが必要）
@auth_bp.route('/login/admin/reset-password', methods=['GET', 'POST'])
def reset_password_admin():
    return _handle_reset_with_code(User.ROLE_ADMIN, 'auth/reset_password_admin.html',
                                   'auth.reset_password_admin_complete')


# 管理者 パスワード再設定完了画面
@auth_bp.route('/login/admin/reset-password/complete')
def reset_password_admin_complete():
    return render_template('auth/reset_password_admin_complete.html')
