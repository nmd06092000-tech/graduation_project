from flask import Blueprint, render_template

from app.models import User
from app.services import report_service, duplicate_service, stats_service
from app.utils.decorators import role_required

staff_bp = Blueprint('staff', __name__, url_prefix='/staff')


# 5. 行政職員ダッシュボード
@staff_bp.route('/dashboard')
@role_required(User.ROLE_STAFF)
def dashboard():
    return render_template('staff/dashboard.html')


# 行政職員 報告一覧画面
@staff_bp.route('/reports')
@role_required(User.ROLE_STAFF)
def report_list():
    reports = report_service.list_for_staff()
    return render_template('staff/report_list.html', reports=reports)


# 行政職員 地図表示画面
@staff_bp.route('/map')
@role_required(User.ROLE_STAFF)
def report_map():
    reports = stats_service.map_points()
    return render_template('staff/map.html', reports=reports)


# 行政職員 AI分析・自動判定画面
@staff_bp.route('/ai-analysis')
@role_required(User.ROLE_STAFF)
def ai_analysis():
    ai_logs = report_service.list_ai_analyses()
    return render_template('staff/ai_analysis.html', ai_logs=ai_logs)


# 行政職員 対応管理・重複確認画面
@staff_bp.route('/duplicates')
@role_required(User.ROLE_STAFF)
def duplicates():
    duplicate_groups = duplicate_service.list_candidate_groups()
    return render_template('staff/duplicates.html', duplicate_groups=duplicate_groups)


# 行政職員 統計・レポート画面
@staff_bp.route('/stats')
@role_required(User.ROLE_STAFF)
def stats():
    stats_summary = stats_service.summary()
    category_stats = stats_service.count_by_category()
    return render_template('staff/stats.html', stats_summary=stats_summary, category_stats=category_stats)
