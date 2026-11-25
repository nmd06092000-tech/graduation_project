# 職員画面の AI 判定エラー表示（詳細画面の赤いブロック・AI分析画面の失敗行）と緊急度アコーディオンのテスト
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ai
from app.ai.rule_based import MODEL_NAME as RULE_BASED_MODEL_NAME
from app.constants import (
    AI_ERROR_KIND_LABELS, AI_ERROR_KIND_MESSAGES, AiAnalysisStatus, ReportStatus, Urgency,
)
from app.extensions import db
from app.models import AiAnalysis, Report, ReportStatusHistory, User
from app.services import report_service


def _succeeded(report, category, urgency='high'):
    analysis = AiAnalysis(report=report, status=AiAnalysisStatus.SUCCEEDED.value, model_name='rule_based',
                          category_id=category.id, urgency=urgency, department_id=category.default_department_id,
                          confidence=Decimal('0.92'))
    db.session.add(analysis)
    db.session.commit()
    return analysis


def _failed(report, error_message):
    analysis = AiAnalysis(report=report, status=AiAnalysisStatus.FAILED.value, model_name='gemini',
                          error_message=error_message)
    db.session.add(analysis)
    db.session.commit()
    return analysis


# --- Unit: get_for_staff ---

@pytest.fixture
def gemini_on(monkeypatch):
    """Gemini が有効な環境（実際の API は呼ばない）"""
    monkeypatch.setattr(ai, 'gemini_enabled', lambda: True)


@pytest.fixture
def gemini_off(monkeypatch):
    monkeypatch.setattr(ai, 'gemini_enabled', lambda: False)


def _row(report, status, model_name, at, category=None, error_message=None, urgency='high'):
    """日時を指定して AI 判定の行を1件作る（同じ日時なら id の順で新旧が決まる）"""
    values = dict(report=report, status=status, model_name=model_name, created_at=at)
    if status == AiAnalysisStatus.SUCCEEDED.value:
        values.update(category_id=category.id, urgency=urgency, department_id=category.default_department_id,
                      confidence=Decimal('0.92'))
    else:
        values.update(error_message=error_message)
    analysis = AiAnalysis(**values)
    db.session.add(analysis)
    db.session.commit()
    return analysis


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


# 最新の判定が失敗（成功なし）：種類ラベル・固定の案内文・日時が返り、結果欄は空
def test_get_for_staff_ai_failed_has_error_info(make_report):
    report = make_report()
    failure = _failed(report, '[server] Gemini サーバーエラー (503)')

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True
    assert detail['ai_error_type'] == AI_ERROR_KIND_LABELS['server']
    assert detail['ai_error_message'] == AI_ERROR_KIND_MESSAGES['server']
    assert detail['ai_error_date'] and str(failure.created_at.year) in detail['ai_error_date']
    assert detail['ai_department'] is None and detail['ai_confidence'] is None and detail['ai_is_keyword'] is False


# 最新の判定が成功：エラー情報は出さない
def test_get_for_staff_ai_succeeded_has_no_error_info(make_report, road):
    report = make_report()
    _succeeded(report, road)

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is False
    assert detail['ai_error_type'] is None and detail['ai_error_message'] is None and detail['ai_error_date'] is None


# 判定なし：エラー情報は出さない
def test_get_for_staff_without_ai_has_no_error_info(make_report):
    report = make_report()

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is False
    assert detail['ai_error_type'] is None and detail['ai_error_message'] is None and detail['ai_error_date'] is None


# Gemini 失敗 → キーワード判定が成功（同じ日時）：エラー枠とキーワード判定の結果が両方出る
def test_get_for_staff_gemini_failed_with_keyword_result(make_report, road, gemini_on):
    report = make_report()
    _row(report, 'failed', 'gemini-test', NOW, error_message='[quota] x')
    _row(report, 'succeeded', RULE_BASED_MODEL_NAME, NOW, category=road)

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True and detail['ai_error_message'] == AI_ERROR_KIND_MESSAGES['quota']
    assert detail['ai_is_keyword'] is True
    assert detail['ai_department'] == '道路管理課' and detail['ai_confidence'] == '92%'
    assert detail['ai_urgency_code'] == 'high'


# 失敗のあとに Gemini が成功：エラーは出さず、キーワード判定の注記も出さない
def test_get_for_staff_gemini_succeeded_after_failure_has_no_error(make_report, road, gemini_on):
    report = make_report()
    _row(report, 'failed', 'gemini-test', NOW - timedelta(hours=1), error_message='[timeout] x')
    _row(report, 'succeeded', 'gemini-test', NOW, category=road)

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is False and detail['ai_is_keyword'] is False
    assert detail['ai_department'] == '道路管理課'


# 最新が失敗でも、以前の成功があれば結果は残す（「未判定」にしない）
def test_get_for_staff_latest_failed_keeps_previous_result(make_report, road, gemini_on):
    report = make_report()
    _row(report, 'succeeded', 'gemini-test', NOW - timedelta(hours=2), category=road, urgency='low')
    _row(report, 'failed', 'gemini-test', NOW - timedelta(hours=1), error_message='[network] x')

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True and detail['ai_error_type'] == AI_ERROR_KIND_LABELS['network']
    assert detail['ai_department'] == '道路管理課' and detail['ai_urgency_code'] == 'low'
    assert detail['ai_is_keyword'] is False


# Gemini 無効（rule_based のみ）：従来どおり、最新の失敗が成功より新しいときだけエラー
def test_get_for_staff_rule_based_only_follows_latest_analysis(make_report, road, gemini_off):
    report = make_report()
    _row(report, 'failed', RULE_BASED_MODEL_NAME, NOW - timedelta(hours=2), error_message='[timeout] x')
    _row(report, 'succeeded', RULE_BASED_MODEL_NAME, NOW - timedelta(hours=1), category=road)
    assert report_service.get_for_staff(report.id)['ai_failed'] is False

    _row(report, 'failed', RULE_BASED_MODEL_NAME, NOW, error_message='[network] x')
    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True and detail['ai_is_keyword'] is True
    assert detail['ai_department'] == '道路管理課'


# 種類ごとに固定の案内文（技術的な詳細・設定変数名は出さない）。config と auth は同じ文
@pytest.mark.parametrize('kind', list(AI_ERROR_KIND_MESSAGES))
def test_get_for_staff_error_message_is_fixed_per_kind(make_report, kind):
    report = make_report()
    _failed(report, f'[{kind}] GEMINI_API_KEY が未設定です (503)')

    detail = report_service.get_for_staff(report.id)

    assert (detail['ai_error_type'], detail['ai_error_message']) == (
        AI_ERROR_KIND_LABELS[kind], AI_ERROR_KIND_MESSAGES[kind])
    assert 'GEMINI' not in detail['ai_error_message'] and '503' not in detail['ai_error_message']


# 接頭辞なし（旧データ）・NULL・空・未知の kind は「不明なエラー」の固定文
@pytest.mark.parametrize('raw', ['古い形式のエラー本文', None, '', '[weird] 何か'])
def test_get_for_staff_error_message_unknown_fallback(make_report, raw):
    report = make_report()
    _failed(report, raw)

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True
    assert (detail['ai_error_type'], detail['ai_error_message']) == (
        AI_ERROR_KIND_LABELS['unknown'], AI_ERROR_KIND_MESSAGES['unknown'])


# --- Unit: list_ai_analyses ---

def test_list_ai_analyses_status_code(make_report, road):
    ok_report = make_report(title='成功の報告')
    ng_report = make_report(title='失敗の報告')
    _succeeded(ok_report, road)
    _failed(ng_report, '[server] x')

    codes = {log['title']: log['status_code'] for log in report_service.list_ai_analyses()}

    assert codes == {'成功の報告': 'succeeded', '失敗の報告': 'failed'}


# --- Integration ---

@pytest.fixture
def staff_client(app, login, staff):
    client = app.test_client()
    login(client, User.ROLE_STAFF, staff.login_id)
    return client


def _form_html(html):
    """ステータス更新フォーム（<form ... /status ...> から次の </form> まで）を返す"""
    start = html.index('/status"')
    start = html.rfind('<form', 0, start)
    return html[start:html.index('</form>', start)]


# 受け入れ基準1：失敗時は赤いブロック（エラー内容・固定の案内文・発生日時）。技術的な詳細・設定変数名は出さない
@pytest.mark.parametrize('kind, label', [('invalid_response', '応答形式エラー'), ('config', '設定エラー'), ('auth', '認証エラー')])
def test_detail_shows_error_block_when_ai_failed(staff_client, make_report, kind, label):
    report = make_report()
    _failed(report, f'[{kind}] GEMINI_API_KEY が未設定です（GEMINI_MAX_OUTPUT_TOKENS を確認）')

    response = staff_client.get(f'/staff/reports/{report.id}')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'alert alert-danger' in html
    assert f'エラー内容：{label}' in html
    assert AI_ERROR_KIND_MESSAGES[kind] in html
    assert '発生日時：' in html
    assert f'[{kind}]' not in html and 'GEMINI_API_KEY' not in html and 'GEMINI_MAX_OUTPUT_TOKENS' not in html


# キーワード判定の結果のときだけ「※キーワード判定の結果です」を出す（Gemini 失敗でもエラー枠と併記）
def test_detail_shows_keyword_note_only_for_rule_based_result(staff_client, make_report, road, gemini_on):
    keyword = make_report(title='キーワード判定')
    _row(keyword, 'failed', 'gemini-test', NOW, error_message='[quota] x')
    _row(keyword, 'succeeded', RULE_BASED_MODEL_NAME, NOW, category=road)
    by_gemini = make_report(title='Gemini判定')
    _row(by_gemini, 'succeeded', 'gemini-test', NOW, category=road)

    keyword_html = staff_client.get(f'/staff/reports/{keyword.id}').get_data(as_text=True)
    gemini_html = staff_client.get(f'/staff/reports/{by_gemini.id}').get_data(as_text=True)

    assert '※キーワード判定の結果です' in keyword_html and 'alert alert-danger' in keyword_html
    assert '道路管理課' in keyword_html and '未判定' not in keyword_html
    assert '※キーワード判定の結果です' not in gemini_html and 'alert alert-danger' not in gemini_html


# 成功・判定なしではブロックを出さない
def test_detail_hides_error_block_when_ai_succeeded_or_missing(staff_client, make_report, road):
    with_ai = make_report(title='成功あり')
    _succeeded(with_ai, road)
    without_ai = make_report(title='判定なし')

    for report in (with_ai, without_ai):
        html = staff_client.get(f'/staff/reports/{report.id}').get_data(as_text=True)
        assert 'エラー内容：' not in html
        assert 'alert alert-danger' not in html


# 受け入れ基準2：緊急度の判定はフォーム内のアコーディオン（既定は折りたたみ）
def test_detail_urgency_accordion_inside_form_collapsed(staff_client, make_report):
    report = make_report()

    html = staff_client.get(f'/staff/reports/{report.id}').get_data(as_text=True)
    form = _form_html(html)

    assert 'id="urgencyAccordion"' in form
    assert 'id="urgency"' in form and 'name="urgency"' in form
    assert 'id="urgency_reason"' in form and 'name="urgency_reason"' in form
    # 既定は折りたたみ（collapse に show が付かず、ボタンは collapsed / aria-expanded=false）
    assert re.search(r'id="urgencyCollapse" class="accordion-collapse collapse"', form)
    assert 'aria-expanded="false"' in form
    assert 'accordion-button collapsed' in form


# 受け入れ基準2：アコーディオン内の緊急度・理由もフォーム送信で保存される
def test_detail_form_post_still_saves_urgency(staff_client, make_report):
    report = make_report(urgency=Urgency.HIGH.value)

    response = staff_client.post(f'/staff/reports/{report.id}/status', data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '現地確認します。',
        'urgency': Urgency.LOW.value, 'urgency_reason': '軽微な破損のため'})

    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/staff/reports/{report.id}')
    db.session.expire_all()
    assert db.session.get(Report, report.id).urgency == Urgency.LOW.value
    history = ReportStatusHistory.query.filter_by(report_id=report.id).order_by(ReportStatusHistory.id.desc()).first()
    assert history.to_urgency == Urgency.LOW.value and history.urgency_reason == '軽微な破損のため'


# 受け入れ基準3：AI分析画面の失敗行は「—」と赤いバッジ、成功行は従来どおり
def test_ai_analysis_page_failed_row(staff_client, make_report, road):
    ok_report = make_report(title='成功の報告')
    ng_report = make_report(title='失敗の報告')
    _succeeded(ok_report, road)
    _failed(ng_report, '[server] x')

    response = staff_client.get('/staff/ai-analysis')
    html = response.get_data(as_text=True)
    rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL)
    ng_row = next(r for r in rows if '失敗の報告' in r)
    ok_row = next(r for r in rows if '成功の報告' in r)

    assert response.status_code == 200
    # 失敗行：分類・部署・緊急度・信頼度の4箇所が「—」、判定ステータスは赤
    assert ng_row.count('>—<') >= 4
    assert 'badge bg-danger">判定失敗' in ng_row
    assert 'badge bg-secondary' not in ng_row and 'progress-bar' not in ng_row
    # 成功行：従来表示（分類・部署・緊急度・信頼度）
    assert '道路' in ok_row and '道路管理課' in ok_row and '92%' in ok_row
    assert 'badge bg-danger">高 (即時対応)' in ok_row
    assert 'badge bg-info text-dark">判定完了・自動分類済み' in ok_row


# 受け入れ基準4：市民マイページは 200 のまま。職員向けのAI通知（ダッシュボードの未読表示）は出さない
# （A20261118-123834 で県民のお知らせベルをヘッダーに追加したため、旧「bi-bell が無い」の期待を更新）
def test_citizen_mypage_renders_with_citizen_bell_but_no_staff_notice(client, login, citizen):
    login(client, User.ROLE_CITIZEN, citizen.login_id)

    response = client.get('/citizen/mypage')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.count('data-citizen-bell') == 1
    assert '（未読' not in html


# 権限：未ログインはログインへ、市民は職員画面に入れない
@pytest.mark.parametrize('path', ['/staff/reports/{id}', '/staff/ai-analysis'])
def test_staff_ai_pages_reject_anonymous_and_citizen(client, login, citizen, make_report, path):
    report = make_report()
    url = path.format(id=report.id)

    anonymous = client.get(url)
    assert anonymous.status_code == 302 and '/login' in anonymous.headers['Location']

    login(client, User.ROLE_CITIZEN, citizen.login_id)
    citizen_response = client.get(url)
    assert citizen_response.status_code in (302, 403)
    if citizen_response.status_code == 302:
        assert '/staff/' not in citizen_response.headers['Location']
    assert 'エラー内容' not in citizen_response.get_data(as_text=True)
