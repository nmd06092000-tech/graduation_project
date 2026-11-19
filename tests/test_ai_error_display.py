# 職員画面の AI 判定エラー表示（詳細画面の赤いブロック・AI分析画面の失敗行）と緊急度アコーディオンのテスト
import re
from decimal import Decimal

import pytest

from app.constants import (
    AI_ERROR_GENERIC_MESSAGE, AI_ERROR_KIND_LABELS, AI_ERROR_UNKNOWN_MESSAGE, AiAnalysisStatus, ReportStatus, Urgency,
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

# 最新の判定が失敗：種類ラベル・本文（接頭辞 [kind] なし）・日時が返る
def test_get_for_staff_ai_failed_has_error_info(make_report):
    report = make_report()
    failure = _failed(report, '[server] Gemini サーバーエラー (503)')

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True
    assert detail['ai_error_type'] == AI_ERROR_KIND_LABELS['server']
    assert detail['ai_error_message'] == 'Gemini サーバーエラー (503)'
    assert '[' not in detail['ai_error_message']
    assert detail['ai_error_date'] and str(failure.created_at.year) in detail['ai_error_date']


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


# 過去に失敗しても、最新が成功ならエラーは出さない（逆に最新が失敗なら出す）
def test_get_for_staff_error_follows_latest_analysis(make_report, road):
    report = make_report()
    _failed(report, '[timeout] タイムアウト')
    _succeeded(report, road)
    assert report_service.get_for_staff(report.id)['ai_failed'] is False

    _failed(report, '[network] 接続できません')
    assert report_service.get_for_staff(report.id)['ai_failed'] is True


# config / auth は詳細を出さず共通文にする（設定変数名・キーを含めない）
@pytest.mark.parametrize('kind', ['config', 'auth'])
def test_get_for_staff_hides_detail_for_config_and_auth(make_report, kind):
    report = make_report()
    _failed(report, f'[{kind}] GEMINI_API_KEY が未設定です')

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_error_type'] == AI_ERROR_KIND_LABELS[kind]
    assert detail['ai_error_message'] == AI_ERROR_GENERIC_MESSAGE
    assert 'GEMINI' not in detail['ai_error_message']


# config / auth 以外でも、メッセージ中の設定変数名（括弧書き）は取り除く
def test_get_for_staff_strips_config_variable_name(make_report):
    report = make_report()
    _failed(report, '[invalid_response] 出力が上限に達しました（GEMINI_MAX_OUTPUT_TOKENS を確認）')

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_error_type'] == AI_ERROR_KIND_LABELS['invalid_response']
    assert detail['ai_error_message'] == '出力が上限に達しました'
    assert 'GEMINI' not in detail['ai_error_message']


# 括弧書きでない変数名が残る場合は共通文に置き換える
def test_get_for_staff_replaces_message_when_config_name_remains(make_report):
    report = make_report()
    _failed(report, '[client] GEMINI_MODEL の値が不正です')

    message = report_service.get_for_staff(report.id)['ai_error_message']

    assert 'GEMINI' not in message


# 変数名ではない大文字語（SAFETY / HTTP コード）は残す
@pytest.mark.parametrize('kind, raw', [
    ('server', 'Gemini サーバーエラー (503)'),
    ('blocked', '入力がブロックされました (SAFETY)'),
])
def test_get_for_staff_keeps_plain_messages(make_report, kind, raw):
    report = make_report()
    _failed(report, f'[{kind}] {raw}')

    assert report_service.get_for_staff(report.id)['ai_error_message'] == raw


# 接頭辞なし（旧データ）・NULL・空・接頭辞のみ・未知の kind
@pytest.mark.parametrize('raw, label, message', [
    ('古い形式のエラー本文', AI_ERROR_KIND_LABELS['unknown'], '古い形式のエラー本文'),
    (None, AI_ERROR_KIND_LABELS['unknown'], AI_ERROR_UNKNOWN_MESSAGE),
    ('', AI_ERROR_KIND_LABELS['unknown'], AI_ERROR_UNKNOWN_MESSAGE),
    ('[server]', AI_ERROR_KIND_LABELS['server'], AI_ERROR_UNKNOWN_MESSAGE),
    ('[weird] 何か', AI_ERROR_KIND_LABELS['unknown'], '何か'),
])
def test_get_for_staff_error_message_fallbacks(make_report, raw, label, message):
    report = make_report()
    _failed(report, raw)

    detail = report_service.get_for_staff(report.id)

    assert detail['ai_failed'] is True
    assert (detail['ai_error_type'], detail['ai_error_message']) == (label, message)


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


# 受け入れ基準1：失敗時は赤いブロック（エラー内容・メッセージ・発生日時）
def test_detail_shows_error_block_when_ai_failed(staff_client, make_report):
    report = make_report()
    _failed(report, '[invalid_response] 出力が上限に達しました（GEMINI_MAX_OUTPUT_TOKENS を確認）')

    response = staff_client.get(f'/staff/reports/{report.id}')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'alert alert-danger' in html
    assert 'エラー内容：応答形式エラー' in html
    assert '出力が上限に達しました' in html
    assert '発生日時：' in html
    # 接頭辞・設定変数名は画面に出さない
    assert '[invalid_response]' not in html
    assert 'GEMINI_MAX_OUTPUT_TOKENS' not in html


@pytest.mark.parametrize('kind', ['config', 'auth'])
def test_detail_error_block_does_not_leak_config_name(staff_client, make_report, kind):
    report = make_report()
    _failed(report, f'[{kind}] GEMINI_API_KEY が未設定です')

    html = staff_client.get(f'/staff/reports/{report.id}').get_data(as_text=True)

    assert AI_ERROR_GENERIC_MESSAGE in html
    assert 'GEMINI_API_KEY' not in html


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
