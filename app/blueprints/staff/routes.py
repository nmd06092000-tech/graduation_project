from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.models import User
from app.services import report_service, duplicate_service, stats_service
from app.utils.decorators import role_required

staff_bp = Blueprint('staff', __name__, url_prefix='/staff')


# 5. 行政職員ダッシュボード
@staff_bp.route('/dashboard')
@role_required(User.ROLE_STAFF)
def dashboard():
    return render_template('staff/dashboard.html')


# 行政職員 報告一覧画面（タイトル検索・ID 指定での絞り込み）
@staff_bp.route('/reports')
@role_required(User.ROLE_STAFF)
def report_list():
    search_query = request.args.get('search', '').strip()
    report_id = request.args.get('id', type=int)
    reports = report_service.list_for_staff(search=search_query, report_id=report_id)
    # ID で絞り込んだときは、検索欄に該当報告のタイトルを表示する
    if report_id and reports:
        search_query = reports[0]['title']
    return render_template('staff/report_list.html', reports=reports, search_query=search_query)


# 行政職員 報告詳細・対応更新画面
@staff_bp.route('/reports/<int:report_id>')
@role_required(User.ROLE_STAFF)
def report_detail(report_id):
    report = report_service.get_for_staff(report_id)
    if report is None:
        abort(404)
    return render_template('staff/report_detail.html', report=report)


# 行政職員 対応状況の更新
# TODO: 現状は保存しない。Report モデルができたら status_service で遷移チェック・対応履歴の記録を行う
@staff_bp.route('/reports/<int:report_id>/status', methods=['POST'])
@role_required(User.ROLE_STAFF)
def report_status_update(report_id):
    if report_service.get_for_staff(report_id) is None:
        abort(404)
    new_status = request.form.get('status', '')
    flash(f'報告 #{report_id} のステータスを「{new_status}」に更新しました！', 'success')
    return redirect(url_for('staff.report_detail', report_id=report_id))


# 行政職員 地図表示画面（カテゴリ・緊急度で絞り込み）
@staff_bp.route('/map')
@role_required(User.ROLE_STAFF)
def report_map():
    selected_category = request.args.get('category', 'all')
    selected_urgency = request.args.get('urgency', 'all')
    reports = stats_service.map_points(category=selected_category, urgency=selected_urgency)
    return render_template('staff/map.html', reports=reports,
                           selected_category=selected_category, selected_urgency=selected_urgency)


# 行政職員 AI分析・自動判定画面（タイトル検索・ID 指定での絞り込み）
@staff_bp.route('/ai-analysis')
@role_required(User.ROLE_STAFF)
def ai_analysis():
    search_query = request.args.get('search', '').strip()
    report_id = request.args.get('id', type=int)
    ai_logs = report_service.list_ai_analyses(search=search_query, report_id=report_id)
    # ID で絞り込んだときは、検索欄に該当ログのタイトルを表示する
    if report_id and ai_logs:
        search_query = ai_logs[0]['title']
    return render_template('staff/ai_analysis.html', ai_logs=ai_logs, search_query=search_query)


# 行政職員 対応管理・重複確認画面
@staff_bp.route('/duplicates')
@role_required(User.ROLE_STAFF)
def duplicates():
    duplicate_groups = duplicate_service.list_candidate_groups()
    return render_template('staff/duplicates.html', duplicate_groups=duplicate_groups)


# 行政職員 重複グループの代表案件への統合
# TODO: 現状は保存しない。Report モデルができたら duplicate_service で duplicate_of_id を設定する
@staff_bp.route('/duplicates/<int:group_id>/merge', methods=['GET', 'POST'])
@role_required(User.ROLE_STAFF)
def duplicate_merge(group_id):
    group = duplicate_service.get_group(group_id)
    if group is None:
        abort(404)
    if request.method == 'POST':
        primary_report_id = request.form.get('primary_report_id', '')
        flash(f'重複グループ #{group_id} を代表案件（#{primary_report_id}）に統合しました！', 'success')
        return redirect(url_for('staff.duplicate_merge', group_id=group_id))
    return render_template('staff/duplicate_merge.html', group=group)


# 行政職員 重複グループの一括対応ステータス変更
# TODO: 現状は保存しない。Report モデルができたら status_service でグループ内の報告を1件ずつ遷移させる
@staff_bp.route('/duplicates/<int:group_id>/status', methods=['GET', 'POST'])
@role_required(User.ROLE_STAFF)
def duplicate_batch_status(group_id):
    group = duplicate_service.get_group(group_id)
    if group is None:
        abort(404)
    if request.method == 'POST':
        new_status = request.form.get('status', '')
        flash(f'重複グループ #{group_id} の対応ステータスを『{new_status}』に一括更新しました！', 'success')
        return redirect(url_for('staff.duplicate_batch_status', group_id=group_id))
    return render_template('staff/duplicate_batch_status.html', group=group)


# 行政職員 統計・レポート画面
@staff_bp.route('/stats')
@role_required(User.ROLE_STAFF)
def stats():
    stats_summary = stats_service.summary()
    category_stats = stats_service.count_by_category()
    return render_template('staff/stats.html', stats_summary=stats_summary, category_stats=category_stats)
