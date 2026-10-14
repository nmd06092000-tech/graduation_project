from functools import wraps

from flask import redirect, url_for, flash, abort
from flask_login import current_user

from app.models import User

# 未ログイン時に遷移するログイン画面（ロール別）
LOGIN_VIEWS = {
    User.ROLE_CITIZEN: 'auth.login_citizen',
    User.ROLE_STAFF: 'auth.login_staff',
    User.ROLE_ADMIN: 'auth.login_admin',
}


def role_required(role):
    """ログイン済みかつ指定ロールのユーザーのみアクセスを許可する"""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                flash('ログインしてください', 'warning')
                return redirect(url_for(LOGIN_VIEWS[role]))
            if current_user.role != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
