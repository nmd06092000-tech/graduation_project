# 行政職員ダッシュボード「地域の問題マップ」（Google Maps・map.js 共用）の結合テスト
import json
import re

import pytest
from flask import g

from app.constants import ReportStatus, Urgency
from app.models import User

# テスト用の Google Maps キー（本物ではない。外部へは一切アクセスしない）
MAPS_TEST_VALUE = 'TEST-MAPS-0001'
FALLBACK_TEXT = '［GISマップ表示エリア：ピン色で緊急度を区別］'
MAP_REPORTS_PATTERN = re.compile(r'<script id="mapReports" type="application/json">(.*?)</script>', re.DOTALL)


def _dashboard_html(app, client, login, staff, api_key):
    """キーを指定して職員でログインし、ダッシュボードの HTML を返す（.env の値に左右されないよう必ず上書きする）"""
    app.config['GOOGLE_MAPS_API_KEY'] = api_key
    login(client, User.ROLE_STAFF, staff.login_id)
    response = client.get('/staff/dashboard')
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _map_reports(html):
    """mapReports の script タグの中身を JSON として読む"""
    match = MAP_REPORTS_PATTERN.search(html)
    assert match, 'mapReports の script タグがありません'
    return json.loads(match.group(1))


def test_dashboard_with_key_renders_google_map(app, client, login, staff):
    # Act
    html = _dashboard_html(app, client, login, staff, MAPS_TEST_VALUE)

    # Assert: 地図の枠・報告データ・map.js・Google Maps API の読み込みがある
    assert 'id="reportMap"' in html
    assert 'id="mapReports"' in html
    assert 'js/map.js' in html
    assert 'callback=initReportMap' in html
    assert f'https://maps.googleapis.com/maps/api/js?key={MAPS_TEST_VALUE}&' in html
    assert 'language=ja' in html and 'region=JP' in html
    assert '住所検索 出典：国土地理院' in html
    # Assert: 代替表示は出ない
    assert FALLBACK_TEXT not in html


def test_dashboard_map_has_detail_url_and_map_id(app, client, login, staff):
    # Arrange
    app.config['GOOGLE_MAPS_MAP_ID'] = 'TEST_MAP_ID'

    # Act
    html = _dashboard_html(app, client, login, staff, MAPS_TEST_VALUE)

    # Assert: map.js が使う data 属性（マップ ID・詳細画面の URL のひな形）
    tag = re.search(r'<div id="reportMap"[^>]*>', html)
    assert tag
    assert 'data-map-id="TEST_MAP_ID"' in tag.group(0)
    assert 'data-detail-url="/staff/reports/0"' in tag.group(0)


def test_dashboard_map_reports_only_open_reports(app, client, login, staff, make_report):
    # Arrange: 未対応・対応中（表示対象）と、対応完了・取り消し（対象外）
    opened = make_report(title='未対応の穴', urgency=Urgency.HIGH.value, latitude=35.906, longitude=139.624)
    working = make_report(title='対応中の街灯', urgency=Urgency.LOW.value,
                          status=ReportStatus.IN_PROGRESS.value)
    resolved = make_report(title='完了した落書き', status=ReportStatus.RESOLVED.value)
    cancelled = make_report(title='取り消した報告', status=ReportStatus.CANCELLED.value)

    # Act
    points = _map_reports(_dashboard_html(app, client, login, staff, MAPS_TEST_VALUE))

    # Assert: 未完了の報告だけが入る
    ids = {p['id'] for p in points}
    assert ids == {opened.id, working.id}
    assert resolved.id not in ids and cancelled.id not in ids
    # Assert: ピン表示に必要な項目（緯度・経度・緊急度コード）
    point = next(p for p in points if p['id'] == opened.id)
    assert point['title'] == '未対応の穴'
    assert point['latitude'] == pytest.approx(35.906)
    assert point['longitude'] == pytest.approx(139.624)
    assert point['urgency_code'] == Urgency.HIGH.value
    assert next(p for p in points if p['id'] == working.id)['urgency_code'] == Urgency.LOW.value


def test_dashboard_map_reports_empty_list_when_no_reports(app, client, login, staff):
    # Act
    html = _dashboard_html(app, client, login, staff, MAPS_TEST_VALUE)

    # Assert: 報告が 0 件でも空の配列が入り、地図の枠は表示される
    assert _map_reports(html) == []
    assert 'id="reportMap"' in html


def test_dashboard_map_reports_escapes_script_in_title(app, client, login, staff, make_report):
    # Arrange: script タグを閉じようとするタイトル
    payload = '</script><script>alert(1)</script>'
    report = make_report(title=payload)

    # Act
    html = _dashboard_html(app, client, login, staff, MAPS_TEST_VALUE)

    # Assert: 生の script は HTML のどこにも出ない（tojson が < をエスケープする）
    assert '<script>alert(1)</script>' not in html
    assert 'alert(1)' in html
    # Assert: mapReports の script タグは壊れず、JSON として読むと元のタイトルに戻る
    points = _map_reports(html)
    assert [p['title'] for p in points if p['id'] == report.id] == [payload]
    raw = MAP_REPORTS_PATTERN.search(html).group(1)
    assert '<' not in raw


def test_dashboard_without_key_shows_fallback(app, client, login, staff, make_report):
    # Arrange
    make_report()

    # Act
    html = _dashboard_html(app, client, login, staff, '')

    # Assert: 地図も Google Maps の読み込みも無く、代替表示が出る
    assert 'id="reportMap"' not in html
    assert 'id="mapReports"' not in html
    assert 'maps.googleapis.com' not in html
    assert 'js/map.js' not in html
    assert 'initReportMap' not in html
    assert FALLBACK_TEXT in html


@pytest.mark.parametrize('api_key', [MAPS_TEST_VALUE, ''])
def test_dashboard_keeps_map_link_and_category_chart(app, client, login, staff, api_key):
    # Act
    html = _dashboard_html(app, client, login, staff, api_key)

    # Assert: キーの有無に関係なく「地図表示で見る」リンクとカテゴリのグラフがある
    assert re.search(r'<a href="/staff/map"[^>]*>地図表示で見る →</a>', html)
    assert '地域の問題マップ' in html
    assert '<canvas id="categoryChart"></canvas>' in html
    assert "document.getElementById('categoryChart')" in html


def test_dashboard_map_key_is_urlencoded_in_script_src(app, client, login, staff):
    # Arrange: クエリを壊す文字・属性を閉じる文字を含むキー
    api_key = 'AB C&x=1#frag+"q"<é'

    # Act
    html = _dashboard_html(app, client, login, staff, api_key)

    # Assert: src の中ではパーセントエンコードされ、後ろのパラメータも残る
    src = re.search(r'<script async src="(https://maps\.googleapis\.com/[^"]*)"', html)
    assert src
    assert 'key=AB%20C%26x%3D1%23frag%2B%22q%22%3C%C3%A9&' in src.group(1)
    assert src.group(1).endswith('callback=initReportMap&language=ja&region=JP')
    # Assert: 生のキーはどこにも出ない
    assert api_key not in html
    assert 'C&x=1' not in html


@pytest.mark.parametrize('api_key', [MAPS_TEST_VALUE, ''])
def test_dashboard_forbidden_for_citizen_and_admin(app, client, login, citizen, admin, api_key):
    # Arrange
    app.config['GOOGLE_MAPS_API_KEY'] = api_key

    # Act / Assert: 市民は 403
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    response = client.get('/staff/dashboard')
    assert response.status_code == 403
    assert 'reportMap' not in response.get_data(as_text=True)

    # Act / Assert: 管理者も 403（別のクライアント。同じ app_context に残ったログイン情報は捨てる）
    other = app.test_client()
    g.pop('_login_user', None)
    login(other, User.ROLE_ADMIN, admin.login_id)
    g.pop('_login_user', None)
    assert other.get('/staff/dashboard').status_code == 403


def test_dashboard_anonymous_redirected_to_staff_login(app, client):
    # Arrange
    app.config['GOOGLE_MAPS_API_KEY'] = MAPS_TEST_VALUE

    # Act
    response = client.get('/staff/dashboard')

    # Assert: 職員のログイン画面へ遷移し、地図のデータは返さない
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login/staff')
    assert 'mapReports' not in response.get_data(as_text=True)
