import io

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import current_user

from app.constants import (
    MSG_AI_LOG_NOT_FOUND, MSG_GROUP_MERGED, MSG_GROUP_STATUS_UPDATED, MSG_MAP_NO_RESULT,
    MSG_NO_DUPLICATES, MSG_STATUS_UPDATED, ReportStatus,
)
from app.models import User
from app.services import ServiceError, report_service, duplicate_service, stats_service, status_service
from app.utils.decorators import role_required

staff_bp = Blueprint('staff', __name__, url_prefix='/staff')


# 5. 行政職員ダッシュボード
@staff_bp.route('/dashboard')
@role_required(User.ROLE_STAFF)
def dashboard():
    return render_template('staff/dashboard.html',
                           status_counts=stats_service.dashboard_summary(),
                           category_stats=stats_service.count_by_category(),
                           urgency_stats=stats_service.count_by_urgency(),
                           priority_reports=stats_service.priority_reports())


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


# 行政職員 対応状況の更新（ステータス変更と担当者メモを対応履歴に記録する）
@staff_bp.route('/reports/<int:report_id>/status', methods=['POST'])
@role_required(User.ROLE_STAFF)
def report_status_update(report_id):
    new_status = request.form.get('status', '')
    try:
        report = status_service.update_report_status(
            report_id, new_status, actor_id=current_user.id, comment=request.form.get('comment', ''))
    except ServiceError as e:
        flash(str(e), 'warning')
        return redirect(url_for('staff.report_detail', report_id=report_id))
    if report is None:
        abort(404)
    flash(MSG_STATUS_UPDATED.format(report_id=report_id, status=ReportStatus.label_of(new_status)), 'success')
    return redirect(url_for('staff.report_detail', report_id=report_id))


# 行政職員 地図表示画面（Google Maps にピンを表示、カテゴリ・緊急度で絞り込み）
@staff_bp.route('/map')
@role_required(User.ROLE_STAFF)
def report_map():
    selected_category = request.args.get('category', 'all')
    selected_urgency = request.args.get('urgency', 'all')
    reports = stats_service.map_points(category=selected_category, urgency=selected_urgency)
    if not reports and (selected_category != 'all' or selected_urgency != 'all'):
        flash(MSG_MAP_NO_RESULT, 'warning')
    return render_template('staff/map.html', reports=reports,
                           filter_options=stats_service.map_filter_options(),
                           selected_category=selected_category, selected_urgency=selected_urgency,
                           google_maps_api_key=current_app.config['GOOGLE_MAPS_API_KEY'],
                           google_maps_map_id=current_app.config['GOOGLE_MAPS_MAP_ID'])


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
    elif search_query and not ai_logs:
        flash(MSG_AI_LOG_NOT_FOUND, 'danger')
    return render_template('staff/ai_analysis.html', ai_logs=ai_logs, search_query=search_query,
                           ai_summary=report_service.ai_summary())


# 行政職員 対応管理・重複確認画面
@staff_bp.route('/duplicates')
@role_required(User.ROLE_STAFF)
def duplicates():
    duplicate_groups = duplicate_service.list_candidate_groups()
    if not duplicate_groups:
        flash(MSG_NO_DUPLICATES, 'success')
    return render_template('staff/duplicates.html', duplicate_groups=duplicate_groups)


# 行政職員 重複グループの代表案件への統合
@staff_bp.route('/duplicates/<int:group_id>/merge', methods=['GET', 'POST'])
@role_required(User.ROLE_STAFF)
def duplicate_merge(group_id):
    if request.method == 'POST':
        primary_report_id = request.form.get('primary_report_id', type=int)
        try:
            primary = duplicate_service.merge_group(group_id, primary_report_id, actor_id=current_user.id)
        except ServiceError as e:
            flash(str(e), 'warning')
            return redirect(url_for('staff.duplicate_merge', group_id=group_id))
        if primary is None:
            abort(404)
        # 統合済みのグループは重複確認画面の一覧から外れるため、一覧に戻ってメッセージを表示する
        flash(MSG_GROUP_MERGED.format(group_id=group_id, report_id=primary.id), 'success')
        return redirect(url_for('staff.duplicates'))
    group = duplicate_service.get_group(group_id)
    if group is None:
        abort(404)
    return render_template('staff/duplicate_merge.html', group=group)


# 行政職員 重複グループの一括対応ステータス変更（status_service でグループ内の報告を1件ずつ遷移させる）
@staff_bp.route('/duplicates/<int:group_id>/status', methods=['GET', 'POST'])
@role_required(User.ROLE_STAFF)
def duplicate_batch_status(group_id):
    if request.method == 'POST':
        new_status = request.form.get('status', '')
        try:
            group = status_service.change_group_status(group_id, new_status, actor_id=current_user.id)
        except ServiceError as e:
            flash(str(e), 'warning')
            return redirect(url_for('staff.duplicate_batch_status', group_id=group_id))
        if group is None:
            abort(404)
        flash(MSG_GROUP_STATUS_UPDATED.format(group_id=group_id, status=ReportStatus.label_of(new_status)),
              'success')
        return redirect(url_for('staff.duplicate_batch_status', group_id=group_id))
    group = duplicate_service.get_group(group_id)
    if group is None:
        abort(404)
    return render_template('staff/duplicate_batch_status.html', group=group)


# 行政職員 統計・レポート画面
@staff_bp.route('/stats')
@role_required(User.ROLE_STAFF)
def stats():
    stats_summary = stats_service.summary()
    category_stats = stats_service.count_by_category()
    return render_template('staff/stats.html', stats_summary=stats_summary, category_stats=category_stats,
                           monthly_trend=stats_service.monthly_trend())


# 行政職員 統計レポートの出力（fmt: pdf / csv）。ファイルとしてダウンロードさせる
@staff_bp.route('/stats/export/<fmt>')
@role_required(User.ROLE_STAFF)
def stats_export(fmt):
    exported = stats_service.export_report(fmt)
    if exported is None:
        abort(404)
    data, mimetype, filename = exported
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True, download_name=filename)
