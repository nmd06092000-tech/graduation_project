from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.models import User
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
