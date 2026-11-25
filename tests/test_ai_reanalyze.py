# 職員のAI分析画面「AIモデル再実行」（全体・報告1件）のテスト（service の再実行・対象の選び方と、route の権限・フラッシュ・PRG）
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from app import ai
from app.ai import AIError
from app.constants import (
    MSG_AI_RERUN_BATCH_DONE, MSG_AI_RERUN_CANCELLED, MSG_AI_RERUN_DONE, MSG_AI_RERUN_FAILED, MSG_AI_RERUN_FALLBACK,
    MSG_AI_RERUN_NO_TARGET, MSG_AI_RERUN_REMAINING, MSG_CSRF_EXPIRED, Urgency,
)
from app.extensions import db
from app.models import AiAnalysis, CitizenNotification, Report, StaffNotification, User
from app.services import ServiceError, report_service
from tests.conftest import get_csrf_token

BULK_URL = '/staff/ai-analysis/reanalyze'


def _single_url(report_id):
    return f'/staff/reports/{report_id}/reanalyze'


class _FailingClassifier:
    model_name = 'gemini-test'

    def classify(self, text, photos=(), *, choices):
        raise AIError('Gemini API エラー (429)', kind='quota')


@pytest.fixture
def gemini_down(monkeypatch):
    """Gemini が有効なのに必ず失敗する状態（失敗の行を残して rule_based へ切り替わる）"""
    monkeypatch.setattr(ai, 'gemini_enabled', lambda: True)
    monkeypatch.setattr(ai, 'get_classifier', lambda: _FailingClassifier())


@pytest.fixture
def ai_down(monkeypatch):
    """Gemini もキーワード判定も失敗する状態（outcome = failed）"""
    def failing_classify(*args, **kwargs):
        raise AIError('テスト用の失敗', kind='timeout')
    monkeypatch.setattr(report_service, '_classify', failing_classify)


def _seed(report, status, model_name, age_seconds, category=None, now=None):
    """AI 判定の行を age_seconds 秒前の日時で1件作る（同じ now を渡せば同じ日時の行になる）"""
    now = now or datetime.now(timezone.utc)
    values = dict(report=report, status=status, model_name=model_name, trigger_type='initial',
                  created_at=now - timedelta(seconds=age_seconds))
    if status == 'succeeded':
        values.update(category_id=category.id, urgency='medium', confidence=Decimal('0.90'))
    else:
        values.update(error_message='[quota] テスト用の失敗')
    analysis = AiAnalysis(**values)
    db.session.add(analysis)
    db.session.commit()
    return analysis


def _manual_rows():
    db.session.expire_all()
    return db.session.query(AiAnalysis).filter_by(trigger_type='manual').order_by(AiAnalysis.id).all()


# --- Unit: reanalyze_report ---

# 受け入れ基準2・3：manual の行を追加し、未設定の項目だけ埋める（職員の設定値は上書きしない）。通知は作らない
def test_reanalyze_report_adds_manual_row_and_fills_blank_fields_only(make_report, road):
    blank = make_report(urgency=None)
    decided = make_report(urgency='low')

    result = report_service.reanalyze_report(blank.id)
    report_service.reanalyze_report(decided.id)

    [first, second] = _manual_rows()
    analysis_of_blank = first
    assert result['outcome'] == report_service.RERUN_SUCCEEDED and result['report_id'] == blank.id
    assert result['urgency'] == Urgency.label_of(analysis_of_blank.urgency)
    assert analysis_of_blank.status == 'succeeded' and analysis_of_blank.trigger_type == 'manual'
    assert blank.category_id == analysis_of_blank.category_id is not None
    assert blank.urgency == analysis_of_blank.urgency and blank.department_id == analysis_of_blank.department_id
    assert second.report_id == decided.id and decided.urgency == 'low'
    assert db.session.query(CitizenNotification).count() == 0
    assert db.session.query(StaffNotification).count() == 0


# 受け入れ基準2：存在しない報告は None、取り消しは MSG_AI_RERUN_CANCELLED で行を増やさない
def test_reanalyze_report_missing_returns_none_and_cancelled_is_rejected(make_report, road):
    cancelled = make_report(status='cancelled')

    assert report_service.reanalyze_report(999999) is None
    with pytest.raises(ServiceError) as error:
        report_service.reanalyze_report(cancelled.id)

    assert str(error.value) == MSG_AI_RERUN_CANCELLED
    assert db.session.query(AiAnalysis).count() == 0


# 受け入れ基準4：Gemini 失敗 → rule_based に切り替わったら fallback（失敗の行と成功の行が両方 manual）
def test_reanalyze_report_fallback_when_gemini_fails(make_report, road, gemini_down):
    report = make_report()

    result = report_service.reanalyze_report(report.id)

    assert result['outcome'] == report_service.RERUN_FALLBACK
    rows = _manual_rows()
    assert [(a.status, a.model_name) for a in rows] == [('failed', 'gemini-test'), ('succeeded', 'rule_based')]
    assert report.category_id is not None


# 受け入れ基準4：どの分類器も失敗なら failed。失敗の行だけ残り、報告の値は変わらない
def test_reanalyze_report_failed_when_all_classifiers_fail(make_report, road, ai_down):
    report = make_report(urgency=None)

    result = report_service.reanalyze_report(report.id)

    assert result == {'report_id': report.id, 'outcome': report_service.RERUN_FAILED, 'urgency': None}
    assert [(a.status, a.trigger_type) for a in _manual_rows()] == [('failed', 'manual')]
    assert report.category_id is None and report.urgency is None


# --- Unit: 再実行の対象の選び方 ---

# 受け入れ基準1：対象 = 未完了で、(a) 成功がない (b) Gemini 有効時に Gemini 失敗で rule_based になった。直近60秒は除く
@pytest.mark.parametrize('report_status, gemini, analyses, expected', [
    ('received', False, [], True),                                                    # 判定の行がない
    ('resolved', False, [], False),                                                   # 完了済みは対象外
    ('cancelled', False, [], False),                                                  # 取り消しは対象外
    ('received', True, [('succeeded', 'gemini-test', 3600)], False),                  # Gemini で成功済み
    ('in_progress', False, [('failed', 'gemini-test', 3600)], True),                  # 失敗だけ（成功なし）
    ('received', True, [('failed', 'gemini-test', 3600), ('succeeded', 'rule_based', 3600)], True),   # (b)
    ('received', False, [('failed', 'gemini-test', 3600), ('succeeded', 'rule_based', 3600)], False), # Gemini 無効
    ('received', True, [('succeeded', 'rule_based', 3600)], False),                   # 失敗の行がない rule_based
    ('received', False, [('failed', 'gemini-test', 30)], False),                      # 60秒以内は除く
    ('received', False, [('failed', 'gemini-test', 90)], True),                       # 60秒を過ぎたら対象
], ids=['no-analysis', 'resolved', 'cancelled', 'gemini-succeeded', 'only-failed', 'gemini-fallback',
        'gemini-off', 'rule-based-only', 'cooldown-30s', 'after-cooldown-90s'])
def test_rerun_targets_selection(monkeypatch, make_report, road, report_status, gemini, analyses, expected):
    monkeypatch.setattr(ai, 'gemini_enabled', lambda: gemini)
    report = make_report(status=report_status)
    now = datetime.now(timezone.utc)
    for status, model_name, age in analyses:
        _seed(report, status, model_name, age, category=road, now=now)

    assert report_service.count_ai_rerun_targets() == (1 if expected else 0)


# 受け入れ基準1：判定のない報告（作成が古い順）→ 最後の判定が古い報告の順に実行する
def test_rerun_order_unanalyzed_first_then_oldest_attempt(make_report, road):
    now = datetime.now(timezone.utc)
    newer_attempt = make_report(created_at=now - timedelta(days=5))
    older_attempt = make_report(created_at=now - timedelta(days=4))
    unanalyzed_new = make_report(created_at=now - timedelta(days=1))
    unanalyzed_old = make_report(created_at=now - timedelta(days=2))
    _seed(newer_attempt, 'failed', 'gemini-test', 3600, now=now)
    _seed(older_attempt, 'failed', 'gemini-test', 7200, now=now)

    summary = report_service.reanalyze_pending_reports(limit=10)

    assert [a.report_id for a in _manual_rows()] == [unanalyzed_old.id, unanalyzed_new.id,
                                                      older_attempt.id, newer_attempt.id]
    assert summary['targets'] == 4 and summary['succeeded'] == 4


# 受け入れ基準1：limit（省略時は AI_RERUN_BATCH_LIMIT）件で打ち切り、残りの件数を返す
def test_reanalyze_pending_respects_batch_limit_and_reports_remaining(app, make_report, road):
    for _ in range(3):
        make_report()

    first = report_service.reanalyze_pending_reports(limit=2)
    app.config['AI_RERUN_BATCH_LIMIT'] = 1
    second = report_service.reanalyze_pending_reports()

    assert (first['targets'], first['executed'], first['succeeded'], first['remaining']) == (3, 2, 2, 1)
    assert (second['targets'], second['executed'], second['remaining']) == (1, 1, 0)
    assert len(_manual_rows()) == 3


# 受け入れ基準1：時間の上限を超えたら打ち切る。ただし1件目は必ず実行する
def test_reanalyze_pending_time_budget_stops_after_first(app, monkeypatch, make_report, road):
    for _ in range(3):
        make_report()
    app.config['AI_RERUN_TIME_BUDGET_SECONDS'] = 20
    ticks = iter(range(0, 1000, 100))
    monkeypatch.setattr(report_service, 'monotonic', lambda: next(ticks))

    summary = report_service.reanalyze_pending_reports(limit=5)

    assert (summary['targets'], summary['executed'], summary['remaining']) == (3, 1, 2)
    assert len(_manual_rows()) == 1


# 想定外のエラーが起きた報告は failed とし、他の報告は続けて実行する
def test_reanalyze_pending_isolates_unexpected_error(monkeypatch, make_report, road):
    broken = make_report(created_at=datetime.now(timezone.utc) - timedelta(days=3))
    make_report()
    make_report()
    original = report_service.analyze_report

    def flaky(report_id, *args, **kwargs):
        if report_id == broken.id:
            raise RuntimeError('boom')
        return original(report_id, *args, **kwargs)
    monkeypatch.setattr(report_service, 'analyze_report', flaky)

    summary = report_service.reanalyze_pending_reports(limit=5)

    assert (summary['executed'], summary['succeeded'], summary['failed']) == (3, 2, 1)
    assert broken.id not in [a.report_id for a in _manual_rows()]


# 受け入れ基準2：「再実行」は報告ごとの最新の行だけ。取り消しの報告には出さない
def test_list_ai_analyses_can_reanalyze_only_on_latest_row_of_open_report(make_report, road):
    now = datetime.now(timezone.utc)
    report = make_report()
    cancelled = make_report(status='cancelled')
    _seed(report, 'failed', 'gemini-test', 7200, now=now)
    _seed(report, 'succeeded', 'rule_based', 3600, category=road, now=now)
    _seed(cancelled, 'succeeded', 'rule_based', 1800, category=road, now=now)

    logs = report_service.list_ai_analyses()

    assert [(log['id'], log['can_reanalyze']) for log in logs] == [
        (cancelled.id, False), (report.id, True), (report.id, False)]


# --- Integration ---

@pytest.fixture
def staff_client(app, login, staff):
    client = app.test_client()
    login(client, User.ROLE_STAFF, staff.login_id)
    return client


def _flashes(client):
    with client.session_transaction() as session:
        return [tuple(item) for item in session.get('_flashes', [])]


def _query(response):
    return parse_qs(urlparse(response.headers['Location']).query)


# 受け入れ基準3・4：全体の再実行 → 成功は success、manual 行が増え、search/id を保って一覧へ戻る（PRG）
def test_bulk_rerun_success_flashes_and_keeps_filters(staff_client, make_report, road):
    first = make_report(title='道路の穴')
    make_report(title='道路の亀裂')

    response = staff_client.post(BULK_URL, data={'search': '道路', 'id': str(first.id)})

    assert response.status_code == 302
    assert urlparse(response.headers['Location']).path == '/staff/ai-analysis'
    assert _query(response) == {'search': ['道路'], 'id': [str(first.id)]}
    assert _flashes(staff_client) == [('success', MSG_AI_RERUN_BATCH_DONE.format(succeeded=2, fallback=0, failed=0))]
    assert len(_manual_rows()) == 2


# 受け入れ基準4：対象がなければ info（行は増えず、空の絞り込みは URL に付けない）。X-Replace-History は JSON
def test_bulk_rerun_without_targets_flashes_info(staff_client, make_report, road):
    make_report(status='resolved')

    response = staff_client.post(BULK_URL, data={'search': '', 'id': ''})
    replaced = staff_client.post(BULK_URL, headers={'X-Replace-History': '1'})

    assert response.status_code == 302 and response.headers['Location'].endswith('/staff/ai-analysis')
    assert _flashes(staff_client)[0] == ('info', MSG_AI_RERUN_NO_TARGET)
    assert replaced.status_code == 200 and replaced.get_json() == {'redirect': '/staff/ai-analysis'}
    assert _manual_rows() == []


# 受け入れ基準4：上限で打ち切ったときは結果の flash のあとに MSG_AI_RERUN_REMAINING（info）を足す
def test_bulk_rerun_adds_remaining_info_when_over_limit(app, staff_client, make_report, road):
    app.config['AI_RERUN_BATCH_LIMIT'] = 2
    for _ in range(3):
        make_report()

    staff_client.post(BULK_URL)

    assert _flashes(staff_client) == [
        ('success', MSG_AI_RERUN_BATCH_DONE.format(succeeded=2, fallback=0, failed=0)),
        ('info', MSG_AI_RERUN_REMAINING.format(executed=2, remaining=1))]


# 受け入れ基準4：全件失敗は danger、キーワード判定への切り替えがあれば warning
@pytest.mark.parametrize('fixture_name, category, counts', [
    ('ai_down', 'danger', {'succeeded': 0, 'fallback': 0, 'failed': 1}),
    ('gemini_down', 'warning', {'succeeded': 0, 'fallback': 1, 'failed': 0}),
], ids=['all-failed', 'fallback'])
def test_bulk_rerun_flash_category_by_outcome(request, staff_client, make_report, road, fixture_name, category, counts):
    request.getfixturevalue(fixture_name)
    make_report()

    staff_client.post(BULK_URL)

    assert _flashes(staff_client) == [(category, MSG_AI_RERUN_BATCH_DONE.format(**counts))]


# 受け入れ基準2・4：1件の再実行 → 結果に応じた flash と、search/id を保った PRG
@pytest.mark.parametrize('fixture_name, category', [(None, 'success'), ('gemini_down', 'warning'), ('ai_down', 'danger')],
                         ids=['succeeded', 'fallback', 'failed'])
def test_single_rerun_flash_by_outcome_and_redirect_keeps_filters(request, staff_client, make_report, road,
                                                                  fixture_name, category):
    if fixture_name:
        request.getfixturevalue(fixture_name)
    report = make_report(status='in_progress')

    response = staff_client.post(_single_url(report.id), data={'search': '道路', 'id': str(report.id)})

    assert response.status_code == 302
    assert _query(response) == {'search': ['道路'], 'id': [str(report.id)]}
    [(flash_category, message)] = _flashes(staff_client)
    assert flash_category == category
    if category == 'success':
        urgency = Urgency.label_of(_manual_rows()[0].urgency)
        assert message == MSG_AI_RERUN_DONE.format(report_id=report.id, urgency=urgency)
    else:
        template = MSG_AI_RERUN_FALLBACK if category == 'warning' else MSG_AI_RERUN_FAILED
        assert message == template.format(report_id=report.id)
    assert all(a.report_id == report.id for a in _manual_rows())


# 受け入れ基準2：取り消しの報告を直接 POST しても再実行せず、danger の flash で一覧へ戻る
def test_single_rerun_cancelled_report_flashes_danger_without_row(staff_client, make_report, road):
    report = make_report(status='cancelled')

    response = staff_client.post(_single_url(report.id))

    assert response.status_code == 302 and urlparse(response.headers['Location']).path == '/staff/ai-analysis'
    assert _flashes(staff_client) == [('danger', MSG_AI_RERUN_CANCELLED)]
    assert db.session.query(AiAnalysis).count() == 0


# 受け入れ基準5：存在しない報告は 404（行は増えない）
def test_single_rerun_missing_report_returns_404(staff_client, road):
    response = staff_client.post(_single_url(999999))

    assert response.status_code == 404
    assert db.session.query(AiAnalysis).count() == 0


# 受け入れ基準5：職員以外（未ログイン・市民・管理者）は再実行できない
@pytest.mark.parametrize('role', [None, User.ROLE_CITIZEN, User.ROLE_ADMIN])
@pytest.mark.parametrize('kind', ['bulk', 'single'])
def test_rerun_rejects_non_staff(client, login, citizen, admin, make_report, road, role, kind):
    report = make_report()
    if role:
        login(client, role, citizen.login_id if role == User.ROLE_CITIZEN else admin.login_id)
    url = BULK_URL if kind == 'bulk' else _single_url(report.id)

    response = client.post(url)

    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert '/staff/' not in response.headers['Location']
        if role is None:
            assert '/login' in response.headers['Location']
    assert db.session.query(AiAnalysis).count() == 0


# 受け入れ基準5：GET は 405、CSRF トークンがない POST は拒否して何も実行しない（トークン付きなら通る）
def test_rerun_requires_post_and_csrf_token(csrf_client, csrf_login, staff, make_report, road):
    report = make_report()
    csrf_login(csrf_client, User.ROLE_STAFF, staff.login_id)

    assert csrf_client.get(BULK_URL).status_code == 405
    assert csrf_client.get(_single_url(report.id)).status_code == 405
    # トークンなしは CSRF エラーハンドラが警告の flash を出して戻す（再実行はしない）
    for url in (BULK_URL, _single_url(report.id)):
        response = csrf_client.post(url)
        assert response.status_code == 302
        assert _flashes(csrf_client)[-1] == ('warning', MSG_CSRF_EXPIRED)
    assert db.session.query(AiAnalysis).count() == 0

    token = get_csrf_token(csrf_client, '/staff/ai-analysis')
    assert csrf_client.post(_single_url(report.id), data={'csrf_token': token}).status_code == 302
    assert len(_manual_rows()) == 1


# 受け入れ基準1・2：画面に確認モーダルと全体フォーム、最新の行だけの「再実行」フォーム（取り消しには出さない）
def test_ai_analysis_page_shows_modal_and_row_forms_for_latest_rows_only(staff_client, make_report, road):
    now = datetime.now(timezone.utc)
    report = make_report(title='道路の穴')
    cancelled = make_report(title='取り消した報告', status='cancelled')
    _seed(report, 'failed', 'gemini-test', 7200, now=now)
    _seed(report, 'succeeded', 'rule_based', 3600, category=road, now=now)
    _seed(cancelled, 'succeeded', 'rule_based', 1800, category=road, now=now)

    html = staff_client.get(f'/staff/ai-analysis?search=道路&id={report.id}').get_data(as_text=True)
    unfiltered = staff_client.get('/staff/ai-analysis').get_data(as_text=True)

    assert 'id="reanalyzeAllModal"' in html and html.count(f'action="{BULK_URL}"') == 1
    assert html.count(f'action="{_single_url(report.id)}"') == 1
    assert _single_url(cancelled.id) not in unfiltered
    assert unfiltered.count(f'action="{_single_url(report.id)}"') == 1
    assert 'data-nav="replace"' in html and 'name="csrf_token"' in html
    assert f'name="id" value="{report.id}"' in html and 'name="search" value="道路"' in html
