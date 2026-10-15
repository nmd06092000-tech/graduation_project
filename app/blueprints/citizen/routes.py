from flask import Blueprint, render_template, request, redirect, url_for, flash
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
        flash('困りごとの報告を送信しました。AIによる分類・緊急度判定を開始します。', 'success')
        return redirect(url_for('citizen.mypage'))
    return render_template('citizen/report_new.html')


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
