from flask import Blueprint, render_template, request, redirect, url_for

from app.models import User
from app.utils.decorators import role_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# 管理者ポータル
@admin_bp.route('/portal')
@role_required(User.ROLE_ADMIN)
def portal():
    return render_template('admin/portal.html')


# 新規職員アカウント登録画面
# TODO: 現状は保存しない。user_service で入力チェック・アカウント作成を行う（README 参照）
@admin_bp.route('/users/register', methods=['GET', 'POST'])
@role_required(User.ROLE_ADMIN)
def user_register():
    if request.method == 'POST':
        return redirect(url_for('admin.user_register_complete'))
    return render_template('admin/user_form.html')


# 新規職員アカウント登録完了画面
@admin_bp.route('/users/register/complete')
@role_required(User.ROLE_ADMIN)
def user_register_complete():
    return render_template('admin/user_register_complete.html')
