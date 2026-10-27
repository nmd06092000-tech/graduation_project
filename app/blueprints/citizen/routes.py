from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import current_user

from app.models import User
from app.services import report_service
from app.utils.decorators import role_required

citizen_bp = Blueprint('citizen', __name__, url_prefix='/citizen')


# 4. 市民マイページ
@citizen_bp.route('/mypage')
@role_required(User.ROLE_CITIZEN)
def mypage():
    return render_template('citizen/mypage.html')


# 市民新規報告画面
@citizen_bp.route('/report', methods=['GET', 'POST'])
@role_required(User.ROLE_CITIZEN)
def report_new():
    if request.method == 'POST':
        # 位置情報の確認画面で保存した住所は、送信後は使わないので消す
        session.pop('report_address', None)
        return redirect(url_for('citizen.report_complete'))
    # 位置情報の確認画面から戻った場合は、推定した住所を発生場所の初期値にする
    return render_template('citizen/report_new.html', address=session.get('report_address', ''))


# 写真撮影画面
# TODO: 現状は撮影画面のプレビューのみ。カメラ撮影（report_form.js）ができたら写真を報告に添付する
@citizen_bp.route('/report/camera')
@role_required(User.ROLE_CITIZEN)
def report_camera():
    return render_template('citizen/report_camera.html')


# 撮影写真の確認画面
@citizen_bp.route('/report/photo')
@role_required(User.ROLE_CITIZEN)
def report_photo():
    return render_template('citizen/report_photo.html')


# 位置情報の確認画面（推定した住所をセッションに保存し、報告画面の初期値にする）
@citizen_bp.route('/report/location')
@role_required(User.ROLE_CITIZEN)
def report_location():
    address = report_service.estimate_address()
    session['report_address'] = address
    return render_template('citizen/report_location.html', address=address)


# 市民新規報告完了画面
# TODO: Report モデルができたら /citizen/reports/<id>/complete にして、作成した報告を表示する
@citizen_bp.route('/report/complete')
@role_required(User.ROLE_CITIZEN)
def report_complete():
    return render_template('citizen/report_complete.html')


# 市民報告一覧画面
@citizen_bp.route('/reports')
@role_required(User.ROLE_CITIZEN)
def report_list():
    reports = report_service.list_by_citizen(current_user.id)
    return render_template('citizen/report_list.html', reports=reports)


# 市民プロフィール設定画面
@citizen_bp.route('/profile')
@role_required(User.ROLE_CITIZEN)
def profile():
    return render_template('citizen/profile.html')


# 市民アイコン設定画面
# TODO: 現状は画像を保存しない。storage_service と User のアイコン列ができたら保存する
@citizen_bp.route('/profile/icon', methods=['GET', 'POST'])
@role_required(User.ROLE_CITIZEN)
def profile_icon():
    if request.method == 'POST':
        flash('プロフィールアイコンを更新しました。', 'success')
        return redirect(url_for('citizen.profile'))
    return render_template('citizen/profile_icon.html')
