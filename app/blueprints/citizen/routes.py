from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, send_file
from flask_login import current_user
from werkzeug.exceptions import RequestEntityTooLarge

from app.constants import MSG_ICON_NOT_SAVED, MSG_ICON_UPDATED, MSG_PROFILE_UPDATED, MSG_UPLOAD_TOO_LARGE
from app.models import User
from app.services import ServiceError, ValidationError, report_service, storage_service, user_service
from app.services.report_service import InappropriatePhotoError
from app.utils.decorators import role_required

citizen_bp = Blueprint('citizen', __name__, url_prefix='/citizen')

# 撮影した写真・位置情報・推定住所を、報告画面で送信するまで保持するセッションのキー
DRAFT_KEY = 'report_draft'
# 入力エラーで画面に戻すとき、入力内容を次の表示まで保持するキー（下書きの中・セッション）
DRAFT_FORM_KEY = 'form'
PROFILE_FORM_KEY = 'profile_form'


# 写真の合計サイズが MAX_CONTENT_LENGTH を超えた場合は、同じ画面に戻してメッセージを表示する
@citizen_bp.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    flash(MSG_UPLOAD_TOO_LARGE, 'warning')
    return redirect(request.url)


# 4. 市民マイページ（報告件数のサマリーと最近の報告）
@citizen_bp.route('/mypage')
@role_required(User.ROLE_CITIZEN)
def mypage():
    return render_template('citizen/mypage.html',
                           summary=report_service.citizen_summary(current_user.id),
                           recent_reports=report_service.list_by_citizen(
                               current_user.id, limit=report_service.RECENT_REPORT_LIMIT))


# 市民新規報告画面（送信すると報告を登録し、AI で緊急度を判定して完了画面へ）
@citizen_bp.route('/report', methods=['GET', 'POST'])
@role_required(User.ROLE_CITIZEN)
def report_new():
    draft = session.get(DRAFT_KEY) or {}
    if request.method == 'POST':
        try:
            report = report_service.create_report(
                citizen_id=current_user.id,
                title=request.form.get('title', ''),
                description=request.form.get('description', ''),
                address=request.form.get('address', ''),
                latitude=request.form.get('latitude', ''),
                longitude=request.form.get('longitude', ''),
                photos=request.files.getlist('photos'),
                draft=draft,
            )
        except ValidationError as e:
            for category, message in e.messages:
                flash(message, category)
            # 入力内容を下書きに残して報告画面を表示し直す（履歴に同じ画面を増やさない）
            session[DRAFT_KEY] = {**draft, DRAFT_FORM_KEY: {
                key: request.form.get(key, '') for key in ('title', 'description', 'address', 'latitude', 'longitude')}}
            return redirect(url_for('citizen.report_new'))
        # 撮影した写真は報告に移したので、下書きは消す
        session.pop(DRAFT_KEY, None)
        return redirect(url_for('citizen.report_complete', report_id=report.id))

    # 入力エラーで戻った場合は入力内容を、位置情報の確認画面から戻った場合は推定した住所と座標を初期値にする
    form = draft.pop(DRAFT_FORM_KEY, None)
    if form is not None:
        session[DRAFT_KEY] = draft
    else:
        form = {'address': draft.get('address') or '', 'latitude': draft.get('latitude') or '',
                'longitude': draft.get('longitude') or ''}
    return render_template('citizen/report_new.html', form=form, has_draft_photo=bool(draft.get('photo')))


# 写真撮影画面（撮影した写真を一時保存し、撮影写真の確認画面へ）
@citizen_bp.route('/report/camera', methods=['GET', 'POST'])
@role_required(User.ROLE_CITIZEN)
def report_camera():
    if request.method == 'POST':
        try:
            draft = report_service.capture_photo(
                request.files.get('photo'),
                latitude=request.form.get('latitude'),
                longitude=request.form.get('longitude'),
                previous=session.get(DRAFT_KEY),
            )
        except InappropriatePhotoError as e:
            # 不適切な写真は報告画面に戻す（代替フロー4c-1）
            flash(str(e), 'warning')
            return redirect(url_for('citizen.report_new'))
        except ServiceError as e:
            flash(str(e), 'warning')
            return redirect(url_for('citizen.report_camera'))
        session[DRAFT_KEY] = draft
        return redirect(url_for('citizen.report_photo'))
    return render_template('citizen/report_camera.html')


# 撮影写真の確認画面
@citizen_bp.route('/report/photo')
@role_required(User.ROLE_CITIZEN)
def report_photo():
    if not (session.get(DRAFT_KEY) or {}).get('photo'):
        return redirect(url_for('citizen.report_camera'))
    return render_template('citizen/report_photo.html')


# 撮影した写真（一時保存）の画像。セッションの下書きにある本人の写真だけを返す
@citizen_bp.route('/report/photo/image')
@role_required(User.ROLE_CITIZEN)
def report_photo_image():
    relative = (session.get(DRAFT_KEY) or {}).get('photo')
    if not storage_service.exists(relative):
        abort(404)
    return send_file(storage_service.path_of(relative), mimetype=storage_service.content_type_of(relative),
                     max_age=0)


# 位置情報の確認画面（写真の GPS 情報から AI で住所を推定し、報告画面の初期値にする）
@citizen_bp.route('/report/location')
@role_required(User.ROLE_CITIZEN)
def report_location():
    draft = session.get(DRAFT_KEY) or {}
    if not draft.get('photo'):
        return redirect(url_for('citizen.report_camera'))
    draft, message = report_service.estimate_location(draft)
    session[DRAFT_KEY] = draft
    return render_template('citizen/report_location.html', draft=draft, message=message)


# 市民新規報告完了画面（AI が判定した緊急度を表示する）
@citizen_bp.route('/reports/<int:report_id>/complete')
@role_required(User.ROLE_CITIZEN)
def report_complete(report_id):
    report = report_service.get_complete_info(report_id, current_user.id)
    if report is None:
        abort(404)
    return render_template('citizen/report_complete.html', report=report)


# 市民報告一覧画面
@citizen_bp.route('/reports')
@role_required(User.ROLE_CITIZEN)
def report_list():
    reports = report_service.list_by_citizen(current_user.id)
    return render_template('citizen/report_list.html', reports=reports)


# 市民プロフィール設定画面（お名前・メールアドレスの変更）
@citizen_bp.route('/profile', methods=['GET', 'POST'])
@role_required(User.ROLE_CITIZEN)
def profile():
    if request.method == 'POST':
        try:
            user_service.update_profile(current_user.id, name=request.form.get('name', ''),
                                        email=request.form.get('email', ''))
        except ValidationError as e:
            for category, message in e.messages:
                flash(message, category)
            # 入力内容を残してプロフィール設定画面を表示し直す（履歴に同じ画面を増やさない）
            session[PROFILE_FORM_KEY] = {key: request.form.get(key, '') for key in ('name', 'email')}
            return redirect(url_for('citizen.profile'))
        flash(MSG_PROFILE_UPDATED, 'success')
        return redirect(url_for('citizen.profile'))
    form = session.pop(PROFILE_FORM_KEY, None) or {'name': current_user.name, 'email': current_user.login_id}
    return render_template('citizen/profile.html', form=form)


# 市民アイコン設定画面
@citizen_bp.route('/profile/icon', methods=['GET', 'POST'])
@role_required(User.ROLE_CITIZEN)
def profile_icon():
    if request.method == 'POST':
        try:
            user_service.update_icon(current_user.id, request.files.get('icon'))
        except ServiceError as e:
            flash(str(e), 'warning')
            return redirect(url_for('citizen.profile_icon'))
        flash(MSG_ICON_UPDATED, 'success')
        return redirect(url_for('citizen.profile'))
    return render_template('citizen/profile_icon.html', msg_icon_not_saved=MSG_ICON_NOT_SAVED)


# 本人のアイコン画像
@citizen_bp.route('/profile/icon/image')
@role_required(User.ROLE_CITIZEN)
def profile_icon_image():
    if not storage_service.exists(current_user.icon_path):
        abort(404)
    return send_file(storage_service.path_of(current_user.icon_path),
                     mimetype=storage_service.content_type_of(current_user.icon_path), max_age=0)
