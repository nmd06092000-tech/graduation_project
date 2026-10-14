from flask import Blueprint, render_template

from app.models import User
from app.utils.decorators import role_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# 管理者ポータル
@admin_bp.route('/portal')
@role_required(User.ROLE_ADMIN)
def portal():
    return render_template('admin/portal.html')
