# ダッシュボード「本日の優先対応案件」の上限 10 件・全件ボタン・マップ高さ（クラス）のテスト
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.constants import ReportStatus, Urgency
from app.models import User
from app.services import stats_service

BASE_TIME = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
MAPS_TEST_VALUE = 'TEST-MAPS-0001'
EMPTY_TEXT = '優先対応が必要な案件はありません。'
COMPONENTS_CSS = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'css' / 'components.css'


def _make_high(make_report, count, **values):
    """緊急度「高」・未対応の報告を count 件作る（title は「高01」から、古い順に created_at を進める）"""
    return [make_report(title=f'高{i:02d}', created_at=BASE_TIME + timedelta(minutes=i), **values)
            for i in range(1, count + 1)]


def _dashboard_html(app, client, login, staff, api_key=''):
    app.config['GOOGLE_MAPS_API_KEY'] = api_key
    login(client, User.ROLE_STAFF, staff.login_id)
    response = client.get('/staff/dashboard')
    assert response.status_code == 200
    return response.get_data(as_text=True)


# --- service ---

def test_priority_reports_limited_to_10_oldest_first(make_report):
    # Arrange: 高・未対応が 12 件
    _make_high(make_report, 12)

    # Act
    rows = stats_service.priority_reports()

    # Assert: 上限 10 件で古い順（11・12 件目は出ない）
    assert stats_service.PRIORITY_LIMIT == 10
    assert [r['title'] for r in rows] == [f'高{i:02d}' for i in range(1, 11)]


def test_count_priority_reports_zero(app):
    assert stats_service.count_priority_reports() == 0


def test_count_priority_reports_exactly_10(make_report):
    _make_high(make_report, 10)

    assert stats_service.count_priority_reports() == 10


def test_count_priority_reports_counts_beyond_limit(make_report):
    _make_high(make_report, 11)

    assert stats_service.count_priority_reports() == 11
    assert len(stats_service.priority_reports()) == 10


def test_count_priority_reports_excludes_other_urgency_and_closed(make_report):
    # Arrange: 数えるのは高・未完了の 2 件（保留中も未完了）。中・低・未判定・完了・取り消し・対象外は除外
    make_report(title='高未対応')
    make_report(title='高保留', status=ReportStatus.ON_HOLD.value)
    make_report(urgency=Urgency.MEDIUM.value)
    make_report(urgency=Urgency.LOW.value)
    make_report(urgency=None)
    make_report(status=ReportStatus.RESOLVED.value)
    make_report(status=ReportStatus.CANCELLED.value)
    make_report(status=ReportStatus.NOT_APPLICABLE.value)

    # Assert: 件数と一覧が同じ条件
    assert stats_service.count_priority_reports() == 2
    assert {r['title'] for r in stats_service.priority_reports()} == {'高未対応', '高保留'}


# --- route ---

def test_dashboard_shows_all_reports_button_when_over_10(app, client, login, staff, make_report):
    # Arrange
    _make_high(make_report, 11)

    # Act
    html = _dashboard_html(app, client, login, staff)

    # Assert: 確認ボタンは 10 行、全件ボタンは報告一覧へ（全 11 件）
    assert len(re.findall(r'class="btn btn-sm btn-outline-primary px-3 py-1">確認</a>', html)) == 10
    button = re.search(r'<a href="/staff/reports" class="btn[^"]*">([^<]*)</a>', html)
    assert button and '全 11 件' in button.group(1)
    assert '高10' in html and '高11' not in html


def test_dashboard_no_all_reports_button_when_exactly_10(app, client, login, staff, make_report):
    _make_high(make_report, 10)

    html = _dashboard_html(app, client, login, staff)

    assert len(re.findall(r'>確認</a>', html)) == 10
    assert '報告一覧で全件を見る' not in html
    assert not re.search(r'<a href="/staff/reports" class="btn', html)


def test_dashboard_empty_priority_shows_message_without_button(app, client, login, staff):
    html = _dashboard_html(app, client, login, staff)

    assert EMPTY_TEXT in html
    assert '報告一覧で全件を見る' not in html
    assert '>確認</a>' not in html


def test_dashboard_map_has_dashboard_map_class_without_fixed_height(app, client, login, staff):
    html = _dashboard_html(app, client, login, staff, MAPS_TEST_VALUE)

    tag = re.search(r'<div id="reportMap"[^>]*>', html)
    assert tag
    assert 'dashboard-map' in tag.group(0)
    # min-height は inline で指定、固定 height は禁止（min-height を除く）
    assert 'min-height: 300px' in tag.group(0)
    assert not re.search(r'(?<!min-)height:\s*300px', tag.group(0))
    assert not re.search(r'(?<!min-)height:\s*300px', html)


def test_dashboard_placeholder_has_dashboard_map_class_without_key(app, client, login, staff):
    html = _dashboard_html(app, client, login, staff, '')

    placeholder = re.search(r'<div class="[^"]*dashboard-map-placeholder[^"]*"[^>]*>', html)
    assert placeholder
    assert re.search(r'(^|\s|")dashboard-map(\s|")', placeholder.group(0))
    assert 'min-height: 300px' in placeholder.group(0)
    assert not re.search(r'(?<!min-)height:\s*300px', placeholder.group(0))
    assert 'id="reportMap"' not in html


# --- 静的 CSS ---

def test_components_css_defines_dashboard_map_min_height():
    css = COMPONENTS_CSS.read_text(encoding='utf-8')

    block = re.search(r'\.dashboard-map\s*\{([^}]*)\}', css)
    assert block
    assert re.search(r'min-height:\s*300px', block.group(1))
