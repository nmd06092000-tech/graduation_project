# 市民のお知らせ画面（/citizen/notifications）・既読 API・ヘッダーの通知ベルのテスト
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import CitizenNotification, User
from tests.conftest import get_csrf_token, make_account

BASE_TIME = datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)
EMPTY_TEXT = 'お知らせはまだありません。'


@pytest.fixture
def citizen_client(client, login, citizen):
    """市民としてログイン済みのクライアント"""
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    return client


@pytest.fixture
def other(app):
    return make_account('hanako@example.com', User.ROLE_CITIZEN, name='佐藤 花子')


def _html(client, path='/citizen/notifications'):
    return client.get(path).get_data(as_text=True)


def _bell_count(html):
    """ベルの数字（data-bell-count の中身）。ベルがなければ None"""
    match = re.search(r'data-bell-count>([^<]*)<', html)
    return match.group(1) if match else None


def _badge_hidden(html):
    """ベルのバッジが hidden 属性つきか（ベルがなければ None）"""
    match = re.search(r'data-bell-badge( hidden)?>', html)
    return None if match is None else bool(match.group(1))


def _unread_in_db(user_id):
    db.session.expire_all()
    return CitizenNotification.query.filter_by(user_id=user_id, read_at=None).count()


def _add_many(user_id, report_id, count, read_at=None):
    db.session.add_all([
        CitizenNotification(user_id=user_id, report_id=report_id, type='report_received', message=f'お知らせ{i}',
                            read_at=read_at, created_at=BASE_TIME + timedelta(minutes=i)) for i in range(count)])
    db.session.commit()


# ---- アクセス制御（受け入れ基準4） ----

def test_notifications_requires_login(client):
    # Act
    response = client.get('/citizen/notifications')

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login/citizen')


@pytest.mark.parametrize('role, login_id', [(User.ROLE_STAFF, 'staff01'), (User.ROLE_ADMIN, 'admin01')])
def test_notifications_forbidden_for_staff_and_admin(client, login, staff, admin, role, login_id):
    # Arrange
    login(client, role, login_id)

    # Act / Assert
    assert client.get('/citizen/notifications').status_code == 403


def test_notifications_shows_only_own_items(citizen_client, other, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report(), message='自分のお知らせ')
    make_citizen_notification(make_report(citizen_id=other.id), message='他人のお知らせ')

    # Act
    html = _html(citizen_client)

    # Assert
    assert '自分のお知らせ' in html and '他人のお知らせ' not in html
    assert html.count('data-notification-item') == 1


# ---- 一覧の表示（受け入れ基準4） ----

def test_notifications_page_shows_newest_first_with_unread_highlight(
        citizen_client, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    make_citizen_notification(report, message='古い既読', created_at=BASE_TIME, read_at=BASE_TIME)
    make_citizen_notification(report, message='新しい未読', created_at=BASE_TIME + timedelta(hours=1))

    # Act
    html = _html(citizen_client)

    # Assert: 新しい順、未読だけ is-unread と「未読：」ラベル
    assert html.index('新しい未読') < html.index('古い既読')
    assert html.count('notification-item is-unread') == 1
    assert html.count('data-unread-label') == 1
    assert '2026/11/01 18:00' in html


def test_notifications_page_has_detail_and_read_url_per_item(citizen_client, make_report, make_citizen_notification):
    # Arrange
    n = make_citizen_notification(make_report(), message='1行', detail='詳しい説明')

    # Act
    html = _html(citizen_client)

    # Assert
    assert '詳しい説明' in html
    assert f'data-read-url="/citizen/notifications/{n.id}/read"' in html
    assert f'id="notificationDetail{n.id}"' in html


def test_notifications_page_uses_message_when_detail_missing(citizen_client, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report(), message='詳細のない1行', detail=None)

    # Act
    html = _html(citizen_client)

    # Assert: 1行の文言が一覧と詳細の両方に出る
    assert html.count('詳細のない1行') == 2


def test_notifications_page_shows_type_labels(citizen_client, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    for type_ in ('report_received', 'ai_judged', 'staff_update'):
        make_citizen_notification(report, type=type_)

    # Act
    html = _html(citizen_client)

    # Assert
    assert '報告受付' in html and 'AI判定' in html and '職員から' in html


def test_notifications_page_empty_state(citizen_client):
    # Act
    html = _html(citizen_client)

    # Assert: 空のとき、メッセージが出て「すべて既読にする」は出ない
    assert EMPTY_TEXT in html
    assert 'data-notification-item' not in html
    assert 'data-read-all-form' not in html


def test_notifications_page_has_no_empty_message_when_items_exist(citizen_client, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())

    # Act / Assert
    assert EMPTY_TEXT not in _html(citizen_client)


def test_notifications_page_limits_to_100_items(citizen_client, make_report):
    # Arrange
    report = make_report()
    _add_many(report.citizen_id, report.id, 101)

    # Act
    html = _html(citizen_client)

    # Assert: 一番古い 1 件（お知らせ0）は出ない
    assert html.count('data-notification-item') == 100
    assert 'お知らせ100' in html and '>お知らせ0<' not in html


def test_get_notifications_does_not_mark_anything_read(citizen_client, citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())

    # Act
    _html(citizen_client)

    # Assert: 画面を開いただけでは既読にならない
    assert _unread_in_db(citizen.id) == 1


def test_notifications_page_escapes_html(citizen_client, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report(), message='<script>alert(1)</script>', detail='<img src=x onerror=alert(2)>')

    # Act
    html = _html(citizen_client)

    # Assert: エスケープされ、そのままのタグは出ない
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>alert(1)</script>' not in html
    assert '<img src=x' not in html and '&lt;img src=x onerror=alert(2)&gt;' in html


# ---- 1件を既読にする（受け入れ基準5） ----

def test_mark_read_marks_item_and_returns_unread_count_json(citizen_client, citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    target = make_citizen_notification(report)
    make_citizen_notification(report)

    # Act
    response = citizen_client.post(f'/citizen/notifications/{target.id}/read')

    # Assert
    assert response.status_code == 200
    assert response.get_json() == {'unread_count': 1}
    assert _unread_in_db(citizen.id) == 1
    assert db.session.get(CitizenNotification, target.id).read_at is not None


def test_mark_read_twice_is_ok(citizen_client, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report())
    citizen_client.post(f'/citizen/notifications/{target.id}/read')

    # Act
    response = citizen_client.post(f'/citizen/notifications/{target.id}/read')

    # Assert
    assert response.status_code == 200 and response.get_json() == {'unread_count': 0}


def test_mark_read_of_other_users_notification_is_404_and_stays_unread(
        citizen_client, other, make_report, make_citizen_notification):
    # Arrange
    others = make_citizen_notification(make_report(citizen_id=other.id))

    # Act
    response = citizen_client.post(f'/citizen/notifications/{others.id}/read')

    # Assert: IDOR 不可（他人のものは 404、状態は変わらない）
    assert response.status_code == 404
    assert _unread_in_db(other.id) == 1


@pytest.mark.parametrize('notification_id', [999999, 0, 2 ** 63, 2 ** 70])
def test_mark_read_unknown_or_out_of_range_id_is_404(citizen_client, notification_id):
    # Act / Assert: 範囲外の ID でも 500 にならず 404
    assert citizen_client.post(f'/citizen/notifications/{notification_id}/read').status_code == 404


def test_mark_read_rejects_get(citizen_client, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report())

    # Act / Assert
    assert citizen_client.get(f'/citizen/notifications/{target.id}/read').status_code == 405


def test_mark_read_requires_login(client, citizen, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report())

    # Act
    response = client.post(f'/citizen/notifications/{target.id}/read')

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith('/login/citizen')
    assert _unread_in_db(citizen.id) == 1


def test_mark_read_forbidden_for_staff(client, login, staff, citizen, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report())
    login(client, User.ROLE_STAFF, staff.login_id)

    # Act / Assert
    assert client.post(f'/citizen/notifications/{target.id}/read').status_code == 403
    assert _unread_in_db(citizen.id) == 1


def test_mark_read_with_csrf_token_header_succeeds(csrf_client, csrf_login, citizen, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report())
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)
    token = get_csrf_token(csrf_client, '/citizen/notifications')

    # Act: fetch と同じく X-CSRFToken ヘッダーで送る
    response = csrf_client.post(f'/citizen/notifications/{target.id}/read', headers={'X-CSRFToken': token})

    # Assert
    assert response.status_code == 200 and response.get_json() == {'unread_count': 0}


def test_mark_read_without_csrf_token_is_rejected(csrf_client, csrf_login, citizen, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report())
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = csrf_client.post(f'/citizen/notifications/{target.id}/read')

    # Assert: CSRF エラー（リダイレクト）で、既読にならない
    assert response.status_code == 302
    assert _unread_in_db(citizen.id) == 1


def test_notifications_page_embeds_csrf_token_for_fetch(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    html = _html(csrf_client)

    # Assert: JS が X-CSRFToken に使うトークンが一覧に埋め込まれている
    assert re.search(r'data-notification-list data-csrf-token="[^"]+"', html)


# ---- すべて既読にする（受け入れ基準5） ----

def test_read_all_marks_all_own_and_redirects_without_flash(
        citizen_client, citizen, other, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    make_citizen_notification(report)
    make_citizen_notification(report)
    others = make_citizen_notification(make_report(citizen_id=other.id))

    # Act
    response = citizen_client.post('/citizen/notifications/read')

    # Assert: お知らせ画面へ redirect。自分の分だけ既読。flash は出さない
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/citizen/notifications')
    assert _unread_in_db(citizen.id) == 0
    assert _unread_in_db(other.id) == 1 and others.read_at is None
    with citizen_client.session_transaction() as session:
        assert '_flashes' not in session


def test_read_all_with_replace_history_header_returns_redirect_json(citizen_client, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())

    # Act: data-nav="replace" の form（navigation.js の fetch）
    response = citizen_client.post('/citizen/notifications/read', headers={'X-Replace-History': '1'})

    # Assert
    assert response.status_code == 200
    assert response.get_json()['redirect'].endswith('/citizen/notifications')


def test_read_all_form_is_shown_only_when_unread_exists(citizen_client, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())

    # Act
    html = _html(citizen_client)

    # Assert: 未読があるとき、replace の form と CSRF トークンつきで出る
    form = re.search(r'<form[^>]*data-read-all-form[^>]*>.*?</form>', html, re.S)
    assert form is not None
    assert 'data-nav="replace"' in form.group(0) and 'name="csrf_token"' in form.group(0)
    assert 'すべて既読にする' in form.group(0)
    assert '/citizen/notifications/read' in form.group(0)

    # Act: 全部既読にしたあと
    citizen_client.post('/citizen/notifications/read')

    # Assert: 未読 0 のときは出ない
    assert 'data-read-all-form' not in _html(citizen_client)


def test_read_all_requires_login(client):
    # Act
    response = client.post('/citizen/notifications/read')

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith('/login/citizen')


def test_read_all_forbidden_for_staff(client, login, staff, citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())
    login(client, User.ROLE_STAFF, staff.login_id)

    # Act / Assert
    assert client.post('/citizen/notifications/read').status_code == 403
    assert _unread_in_db(citizen.id) == 1


def test_read_all_without_csrf_token_is_rejected(csrf_client, csrf_login, citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = csrf_client.post('/citizen/notifications/read')

    # Assert
    assert response.status_code == 302
    assert _unread_in_db(citizen.id) == 1


# ---- ヘッダーの通知ベル（受け入れ基準6） ----

@pytest.fixture
def bell_pages(make_report):
    """ベルが付く県民の画面（お知らせ画面を含む 7 画面）"""
    report = make_report()
    return ['/citizen/mypage', '/citizen/reports', '/citizen/profile', '/citizen/profile/icon',
            '/citizen/password', f'/citizen/reports/{report.id}/complete', '/citizen/notifications']


def test_bell_shown_on_all_header_pages_with_unread_count(
        citizen_client, bell_pages, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    for _ in range(3):
        make_citizen_notification(report)

    # Act / Assert
    for path in bell_pages:
        response = citizen_client.get(path)
        html = response.get_data(as_text=True)
        assert response.status_code == 200, path
        assert html.count('data-citizen-bell') == 1, path
        assert 'href="/citizen/notifications"' in html, path
        assert _bell_count(html) == '3' and _badge_hidden(html) is False, path


def test_bell_badge_hidden_when_no_unread(citizen_client, bell_pages):
    # Act / Assert: 0 件でもバッジの要素は出力され（JS が書き換える）、hidden が付く
    for path in bell_pages:
        html = _html(citizen_client, path)
        assert _badge_hidden(html) is True, path


def test_bell_badge_hidden_after_all_read(citizen_client, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report(), read_at=BASE_TIME)

    # Act / Assert: 既読だけなら 0 扱い
    assert _badge_hidden(_html(citizen_client, '/citizen/mypage')) is True


@pytest.mark.parametrize('count, expected', [(99, '99'), (100, '99+'), (101, '99+')])
def test_bell_count_caps_at_99_plus(citizen_client, citizen, make_report, count, expected):
    # Arrange
    report = make_report()
    _add_many(citizen.id, report.id, count)

    # Act
    html = _html(citizen_client, '/citizen/mypage')

    # Assert
    assert _bell_count(html) == expected and _badge_hidden(html) is False


def test_bell_count_counts_only_own_unread(citizen_client, other, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())
    make_citizen_notification(make_report(citizen_id=other.id))
    make_citizen_notification(make_report(citizen_id=other.id))

    # Act / Assert
    assert _bell_count(_html(citizen_client, '/citizen/mypage')) == '1'


def test_bell_count_drops_after_mark_read(citizen_client, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    target = make_citizen_notification(report)
    make_citizen_notification(report)
    assert _bell_count(_html(citizen_client, '/citizen/mypage')) == '2'

    # Act
    citizen_client.post(f'/citizen/notifications/{target.id}/read')

    # Assert
    assert _bell_count(_html(citizen_client, '/citizen/mypage')) == '1'


@pytest.mark.parametrize('path', ['/citizen/report', '/citizen/report/camera'])
def test_bell_not_shown_on_report_flow_pages(citizen_client, path):
    # Act
    response = citizen_client.get(path)

    # Assert: ヘッダーのない報告フローの画面にはベルを出さない
    assert response.status_code == 200
    assert 'data-citizen-bell' not in response.get_data(as_text=True)


def test_bell_not_shown_on_staff_pages(client, login, staff, make_report):
    # Arrange
    report = make_report()
    login(client, User.ROLE_STAFF, staff.login_id)

    # Act / Assert
    for path in ('/staff/dashboard', '/staff/reports', f'/staff/reports/{report.id}'):
        html = _html(client, path)
        assert 'data-citizen-bell' not in html and 'data-bell-badge' not in html, path


def test_bell_not_shown_on_admin_pages(client, login, admin):
    # Arrange
    login(client, User.ROLE_ADMIN, admin.login_id)

    # Act / Assert
    for path in ('/admin/portal', '/admin/users'):
        assert 'data-citizen-bell' not in _html(client, path), path
