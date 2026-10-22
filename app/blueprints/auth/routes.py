from datetime import datetime, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User

auth_bp = Blueprint('auth', __name__)

# ロールごとの設定（ログイン画面・ID入力欄・ログイン後の遷移先）
LOGIN_SETTINGS = {
    User.ROLE_CITIZEN: {'template': 'auth/login_citizen.html', 'id_field': 'email', 'label': 'メールアドレス', 'home': 'citizen.mypage'},
    User.ROLE_STAFF: {'template': 'auth/login_staff.html', 'id_field': 'staff_id', 'label': '職員ID', 'home': 'staff.dashboard'},
    User.ROLE_ADMIN: {'template': 'auth/login_admin.html', 'id_field': 'admin_id', 'label': '管理者ID', 'home': 'admin.portal'},
}


def _handle_login(role):
    setting = LOGIN_SETTINGS[role]

    # 同じロールでログイン済みならホーム画面へ
    if current_user.is_authenticated and current_user.role == role:
        return redirect(url_for(setting['home']))

    if request.method == 'POST':
        login_id = request.form.get(setting['id_field'], '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        # 未入力チェック（両方未入力の場合は1つのメッセージにまとめる）
        errors = []
        if not login_id and not password:
            errors.append(f"{setting['label']}またはパスワードを入力してください")
        else:
            if not login_id:
                errors.append(f"{setting['label']}を入力してください。")
            if not password:
                errors.append('パスワードを入力してください。')
            elif len(password) < 8:
                # パスワード文字数チェック（8文字未満はエラー）
                errors.append(f'このテキストは8文字以上で指定してください（現在は{len(password)}文字です）。')
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template(setting['template'])

        if role == User.ROLE_CITIZEN:
            # メールアドレスは大文字・小文字を区別しない
            login_id = login_id.lower()
        elif '@' in login_id:
            # 職員・管理者IDに @ は使用不可（既存仕様）
            flash(f"{setting['label']}に @ は使用できません", 'danger')
            return render_template(setting['template'])

        user = User.query.filter_by(login_id=login_id, role=role).first()
        if user is None or not user.check_password(password):
            flash(f"{setting['label']}またはパスワードが正しくありません", 'danger')
            return render_template(setting['template'])

        if not user.is_active:
            flash('このアカウントは利用停止されています', 'danger')
            return render_template(setting['template'])

        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
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


# ログアウト
@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('public.index'))


# 市民新規登録画面
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        # ログイン時と同じく、メールアドレスは小文字で保存する
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        error = None
        if not name or not email or not password or not confirm_password:
            error = 'お名前、メールアドレス、パスワードのいずれかが未入力です。'
        elif len(name) > 100:
            error = 'お名前は100文字以内で入力してください。'
        elif len(password) < 8:
            error = 'パスワードは8文字以上で入力してください。'
        elif password != confirm_password:
            error = 'パスワードが一致しません。'
        elif User.query.filter_by(login_id=email).first():
            error = 'このメールアドレスは既に登録されています。'

        if error:
            flash(error, 'danger')
            return render_template('auth/register.html', name=name, email=email)

        user = User(login_id=email, name=name, role=User.ROLE_CITIZEN)
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # 同時登録などで一意制約に違反した場合
            db.session.rollback()
            flash('このメールアドレスは既に登録されています。', 'danger')
            return render_template('auth/register.html', name=name, email=email)

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
