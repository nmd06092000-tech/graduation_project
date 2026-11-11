import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user
from sqlalchemy.exc import IntegrityError

from app.constants import MSG_EMAIL_NO_AT, MSG_PASSWORD_MISMATCH
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


# パスワード再設定画面
# TODO: 現状は入力チェックのみで、パスワードは保存されない。
#       本人確認（メールのトークン）付きの流れに作り直す予定（README 参照）
@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        email = request.form.get('email', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        # 1. パスワード文字数チェック（8文字未満はエラー）
        if len(new_password) < 8:
            return render_template('auth/reset_password.html', error='新しいパスワードは8文字以上で入力してください。')

        # 2. パスワード一致チェック
        if new_password != confirm_password:
            return render_template('auth/reset_password.html', error='パスワードが一致しません。')

        # 再設定完了画面へリダイレクト
        return redirect(url_for('auth.reset_password_complete'))

    return render_template('auth/reset_password.html')


# パスワード再設定完了画面
@auth_bp.route('/reset-password/complete')
def reset_password_complete():
    return render_template('auth/reset_password_complete.html')


# 行政職員 パスワード再設定画面
@auth_bp.route('/login/staff/reset-password', methods=['GET', 'POST'])
def reset_password_staff():
    if request.method == 'POST':
        staff_id = request.form.get('staff_id', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        # 1. 職員IDの @ チェック
        if '@' in staff_id:
            return render_template('auth/reset_password_staff.html', error='職員IDに @ は使用できません。')

        # 2. パスワード文字数チェック（8文字未満はエラー）
        if len(new_password) < 8:
            return render_template('auth/reset_password_staff.html', error='新しいパスワードは8文字以上で入力してください。')

        # 3. パスワード一致チェック
        if new_password != confirm_password:
            return render_template('auth/reset_password_staff.html', error='新しいパスワードと確認用パスワードが一致しません。')

        # 再設定完了画面へリダイレクト
        return redirect(url_for('auth.reset_password_staff_complete'))

    return render_template('auth/reset_password_staff.html')


# 行政職員 パスワード再設定完了画面
@auth_bp.route('/login/staff/reset-password/complete')
def reset_password_staff_complete():
    return render_template('auth/reset_password_staff_complete.html')


# 管理者 パスワード再設定画面
@auth_bp.route('/login/admin/reset-password', methods=['GET', 'POST'])
def reset_password_admin():
    if request.method == 'POST':
        admin_id = request.form.get('admin_id', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        # 1. 管理者IDの @ チェック
        if '@' in admin_id:
            return render_template('auth/reset_password_admin.html', error='管理者IDに @ は使用できません。')

        # 2. パスワード文字数チェック（8文字未満はエラー）
        if len(new_password) < 8:
            return render_template('auth/reset_password_admin.html', error='新しいパスワードは8文字以上で入力してください。')

        # 3. パスワード一致チェック
        if new_password != confirm_password:
            return render_template('auth/reset_password_admin.html', error='パスワードが一致しません。')

        # 再設定完了画面へリダイレクト
        return redirect(url_for('auth.reset_password_admin_complete'))

    return render_template('auth/reset_password_admin.html')


# 管理者 パスワード再設定完了画面
@auth_bp.route('/login/admin/reset-password/complete')
def reset_password_admin_complete():
    return render_template('auth/reset_password_admin_complete.html')
