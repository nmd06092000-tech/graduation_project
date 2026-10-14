from flask import Blueprint, render_template

from app.models import User
from app.utils.decorators import role_required

staff_bp = Blueprint('staff', __name__, url_prefix='/staff')


# 5. 行政職員ダッシュボード
@staff_bp.route('/dashboard')
@role_required(User.ROLE_STAFF)
def dashboard():
    return render_template('staff/dashboard.html')
