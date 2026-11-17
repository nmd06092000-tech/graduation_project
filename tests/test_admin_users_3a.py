# GĐ3a のルート（結合）テスト：/admin/users（一覧・絞り込み）、削除・ロック解除・再設定コード発行、ポータル、
# 権限・CSRF・IDOR・一度だけ表示するコード（セッション）・古いセッションの失効。テスト DB が必要
import html as html_lib
import logging
import re
from datetime import timedelta

import pytest
from flask import g

from app.constants import (
    MSG_ACCOUNT_DELETED, MSG_ACCOUNT_TARGET_NOT_FOUND, MSG_ACCOUNT_UNLOCKED, MSG_CANNOT_DELETE_SELF, MSG_CSRF_EXPIRED,
    MSG_ID_TAKEN, MSG_LOGIN_FAILED, MSG_RESET_CODE_GONE, MSG_RESET_CODE_INVALID, MSG_RESET_CODE_NOT_ALLOWED,
)
from app.extensions import db
from app.models import Department, PasswordResetToken, User
from app.services import auth_service, user_service
from tests.conftest import get_csrf_token, lock_account, make_account, soft_delete

NEW_PW = 'NewPass-9876'
LIST_URL = '/admin/users'


def _req(client, method, path, **kwargs):
    """リクエストを送る。fixture の app_context に残った g のキャッシュ（ログインユーザー・CSRF）は毎回捨てる"""
    g.pop('_login_user', None)
    g.pop('csrf_token', None)
    return getattr(client, method)(path, **kwargs)


def _get(client, path):
    return _req(client, 'get', path)


def _post(client, path, **data):
    return _req(client, 'post', path, data=data)


def _text(response):
    return html_lib.unescape(response.get_data(as_text=True))


def _flashes(client):
    """セッションに積まれた flash を (カテゴリ, メッセージ) のリストで返す（画面を表示する前に読む）"""
    with client.session_transaction() as session:
        return [tuple(item) for item in session.get('_flashes', [])]


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _post_form_count(html):
    return len(re.findall(r'<form\b[^>]*method\s*=\s*["\']post["\']', html, flags=re.IGNORECASE))


def _shown_login_ids(html):
    return re.findall(r'<span class="admin-users-login-id">([^<]*)</span>', html)


def _issued_code(html):
    match = re.search(r'id="issued-code-value">([^<]+)<', html)
    return match.group(1) if match else None


@pytest.fixture
def login(login):
    """conftest の login に、g のキャッシュを捨てる処理を足したもの（別クライアントのログインが「ログイン済み」にならないように）"""
    def _login(*args, **kwargs):
        g.pop('_login_user', None)
        g.pop('csrf_token', None)
        return login(*args, **kwargs)
    return _login


@pytest.fixture
def world(app, admin):
    """管理者 admin01（ログインする本人）・別の管理者・職員 2 人・市民 1 人"""
    roads, parks = Department(name='道路管理課'), Department(name='公園課')
    db.session.add_all([roads, parks])
    db.session.commit()
    return {
        'admin': admin,
        'admin2': make_account('admin02', User.ROLE_ADMIN, name='高橋 管理'),
        'staff': make_account('staff_a', User.ROLE_STAFF, name='山田 道路', department=roads),
        'staff2': make_account('staff%b', User.ROLE_STAFF, name='田中 公園', department=parks),
        'citizen': make_account('taro@example.com', User.ROLE_CITIZEN, name='市民 太郎'),
        'roads': roads, 'parks': parks,
    }


@pytest.fixture
def admin_client(app, client, login, world):
    """管理者 admin01 でログイン済みのクライアント"""
    assert login(client, User.ROLE_ADMIN, 'admin01').status_code == 302
    return client


# ---------------------------------------------------------------------------
# 権限：未ログイン・市民・職員（4 ルート）
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ('get', '/admin/users'),
    ('post', '/admin/users/{id}/delete'),
    ('post', '/admin/users/{id}/unlock'),
    ('post', '/admin/users/{id}/reset-code'),
]


@pytest.mark.parametrize('method, path', ENDPOINTS)
def test_anonymous_is_redirected_to_admin_login_and_nothing_changes(app, client, world, method, path):
    # Arrange
    lock_account(world['staff'])

    # Act
    response = _req(client, method, path.format(id=world['staff'].id))

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login/admin')
    fresh = _fresh(world['staff'].id)
    assert fresh.del_flag is False and fresh.locked_until is not None
    assert PasswordResetToken.query.count() == 0


@pytest.mark.parametrize('role, login_id', [(User.ROLE_CITIZEN, 'taro@example.com'), (User.ROLE_STAFF, 'staff_a')])
@pytest.mark.parametrize('method, path', ENDPOINTS)
def test_citizen_and_staff_get_403_and_nothing_changes(app, client, login, world, role, login_id, method, path):
    # Arrange: 市民・職員が管理者の画面・操作を直接叩く
    login(client, role, login_id)
    lock_account(world['staff2'])

    # Act
    response = _req(client, method, path.format(id=world['staff2'].id))

    # Assert
    assert response.status_code == 403
    fresh = _fresh(world['staff2'].id)
    assert fresh.del_flag is False and fresh.locked_until is not None
    assert PasswordResetToken.query.count() == 0
    with client.session_transaction() as session:
        assert 'reset_code' not in session


@pytest.mark.parametrize('method, path', ENDPOINTS[1:])
def test_admin_session_of_deleted_admin_cannot_operate(app, client, login, world, method, path):
    # Arrange: admin01 でログイン済み → 別の管理者に削除された
    login(client, User.ROLE_ADMIN, 'admin01')
    user_service.delete_account(world['admin'].id, actor_id=world['admin2'].id)

    # Act
    response = _req(client, method, path.format(id=world['staff'].id))

    # Assert: 古いセッションは次のリクエストで無効（ログイン画面へ）。操作は実行されない
    assert response.status_code == 302 and response.headers['Location'].endswith('/login/admin')
    assert _fresh(world['staff'].id).del_flag is False
    assert PasswordResetToken.query.count() == 0


@pytest.mark.parametrize('path', ['/admin/users/{id}/delete', '/admin/users/{id}/unlock', '/admin/users/{id}/reset-code'])
def test_get_on_post_only_routes_is_405_and_changes_nothing(admin_client, world, path):
    # Act
    response = _get(admin_client, path.format(id=world['staff'].id))

    # Assert
    assert response.status_code == 405
    assert _fresh(world['staff'].id).del_flag is False


@pytest.mark.parametrize('path', ['/admin/users/abc/delete', '/admin/users/-1/delete', '/admin/users/1.5/unlock',
                                  '/admin/users/x/reset-code'])
def test_non_numeric_or_negative_id_in_url_is_404(admin_client, world, path):
    # Act / Assert: URL の型（int）に合わない ID はルートにならない
    assert _post(admin_client, path).status_code == 404


# ---------------------------------------------------------------------------
# CSRF（3 つの POST）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('action', ['delete', 'unlock', 'reset-code'])
def test_post_without_csrf_token_is_rejected_and_changes_nothing(csrf_client, csrf_login, world, action):
    # Arrange
    csrf_login(csrf_client, User.ROLE_ADMIN, 'admin01')
    lock_account(world['staff'])

    # Act
    response = _req(csrf_client, 'post', f"/admin/users/{world['staff'].id}/{action}",
                    headers={'Referer': 'http://localhost/admin/users'})

    # Assert: 一覧へ戻って警告。何も実行されない
    assert response.status_code == 302 and response.headers['Location'] == '/admin/users'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    fresh = _fresh(world['staff'].id)
    assert fresh.del_flag is False and fresh.locked_until is not None
    assert PasswordResetToken.query.count() == 0
    with csrf_client.session_transaction() as session:
        assert 'reset_code' not in session


def test_post_with_invalid_csrf_token_is_rejected(csrf_client, csrf_login, world):
    # Arrange
    csrf_login(csrf_client, User.ROLE_ADMIN, 'admin01')

    # Act
    response = _post(csrf_client, f"/admin/users/{world['staff'].id}/delete", csrf_token='forged-token')

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert _fresh(world['staff'].id).del_flag is False


@pytest.mark.parametrize('action', ['delete', 'unlock', 'reset-code'])
def test_post_with_valid_csrf_token_is_accepted(csrf_client, csrf_login, world, action):
    # Arrange
    csrf_login(csrf_client, User.ROLE_ADMIN, 'admin01')
    token = get_csrf_token(csrf_client, LIST_URL)

    # Act
    response = _post(csrf_client, f"/admin/users/{world['staff'].id}/{action}", csrf_token=token)

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert ('warning', MSG_CSRF_EXPIRED) not in _flashes(csrf_client)
    assert _fresh(world['staff'].id).del_flag is (action == 'delete')


def test_rendered_list_forms_all_carry_csrf_token_and_filters(csrf_client, csrf_login, world):
    # Arrange
    csrf_login(csrf_client, User.ROLE_ADMIN, 'admin01')
    lock_account(world['staff'])

    # Act
    html = _text(_get(csrf_client, '/admin/users?role=staff&q=山'))

    # Assert: 削除モーダルを含むすべての POST form に csrf_token と絞り込み条件の hidden がある
    forms = re.findall(r'(<form\b[^>]*method="post"[^>]*>)(.*?)</form>', html, flags=re.DOTALL)
    assert forms
    for tag, body in forms:
        assert re.search(r'name="csrf_token"\s+value="[^"]+"', body), tag
        if 'logout' not in tag:
            assert 'name="role" value="staff"' in body and 'name="q" value="山"' in body, tag


# ---------------------------------------------------------------------------
# 一覧 GET /admin/users
# ---------------------------------------------------------------------------

def test_list_shows_staff_and_admin_columns_without_citizen(admin_client, world):
    # Act
    response = _get(admin_client, LIST_URL)
    html = _text(response)

    # Assert: 氏名・ログインID・役割・所属部署・状態の列。市民は出ない
    assert response.status_code == 200
    for header in ('氏名', 'ログインID', '役割', '所属部署', '状態'):
        assert f'<th scope="col">{header}</th>' in html
    assert sorted(_shown_login_ids(html)) == ['admin01', 'admin02', 'staff%b', 'staff_a']
    assert '山田 道路' in html and '道路管理課' in html and '公園課' in html
    assert 'taro@example.com' not in html and '市民 太郎' not in html
    assert '4件表示（全4件）' in html


def test_list_has_new_staff_registration_button(admin_client):
    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert
    assert 'href="/admin/users/register"' in html and '新規職員アカウント登録' in html


def test_list_hides_deleted_accounts_and_updates_counts(admin_client, world):
    # Arrange
    soft_delete(world['staff2'], by=world['admin'])

    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert
    assert 'staff%b' not in _shown_login_ids(html) and '田中 公園' not in html
    assert '3件表示（全3件）' in html


def test_list_orders_newest_account_first(admin_client, world, clock):
    # Arrange: 作成日時を明示
    for index, key in enumerate(['admin', 'admin2', 'staff', 'staff2']):
        world[key].created_at = clock.now + timedelta(days=index)
    db.session.commit()

    # Act
    ids = _shown_login_ids(_text(_get(admin_client, LIST_URL)))

    # Assert
    assert ids == ['staff%b', 'staff_a', 'admin02', 'admin01']


def test_list_state_badges_active_locked_and_disabled(admin_client, world):
    # Arrange: staff_a はロック中、staff%b は利用停止（is_active=False の旧データ）
    lock_account(world['staff'])
    world['staff2'].is_active = False
    db.session.commit()
    until = user_service.account_row(_fresh(world['staff'].id))['locked_until_text']

    # Act
    html = _text(_get(admin_client, LIST_URL))
    rows = re.findall(r'<tr>(.*?)</tr>', html, flags=re.DOTALL)
    row_of = {login_id: next(row for row in rows if f'>{login_id}<' in row) for login_id in ('staff_a', 'staff%b', 'admin02')}

    # Assert
    assert 'ロック中' in row_of['staff_a'] and f'〜{until}' in row_of['staff_a'] and '有効' not in row_of['staff_a']
    assert '利用停止' in row_of['staff%b'] and '有効' not in row_of['staff%b']
    assert '有効' in row_of['admin02'] and 'ロック中' not in row_of['admin02']


def test_list_unlock_button_only_for_locked_accounts(admin_client, world, clock):
    # Arrange: 期限切れのロックは対象外
    lock_account(world['staff'])
    world['staff2'].locked_until = clock.now - timedelta(minutes=1)
    db.session.commit()

    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert
    assert html.count('/unlock"') == 1
    assert f"/admin/users/{world['staff'].id}/unlock" in html
    assert f"/admin/users/{world['staff2'].id}/unlock" not in html


def test_list_post_forms_count_is_fixed_two_plus_per_row_plus_locked(admin_client, world):
    # Arrange: 4 行・ロック 1 件
    lock_account(world['staff'])

    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert: 削除モーダル 1 + ログアウト 1 + 再設定コード 4 + ロック解除 1
    assert _post_form_count(html) == 2 + 4 + 1
    assert html.count('/reset-code"') == 4 and html.count('action="/logout"') == 1


def test_list_marks_self_and_disables_own_delete_button(admin_client, world):
    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert: 自分には「あなた」の表示があり、削除の送信先（data-delete-url）が出ない。ほかの行には出る
    assert 'あなた' in html
    assert f"data-delete-url=\"/admin/users/{world['admin'].id}/delete\"" not in html
    for key in ('admin2', 'staff', 'staff2'):
        assert f"data-delete-url=\"/admin/users/{world[key].id}/delete\"" in html


def test_list_delete_modal_is_shared_and_names_target_via_data_attributes(admin_client, world):
    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert: モーダルは 1 つ。名前と ID は行の data 属性から入る
    assert html.count('id="userDeleteModal"') == 1
    assert 'data-user-name="山田 道路"' in html and 'data-user-login-id="staff_a"' in html
    assert 'アカウント削除確認' in html and 'id="user-delete-name"' in html and 'id="user-delete-login-id"' in html


def test_list_escapes_html_in_names_and_ids(admin_client, world):
    # Arrange
    make_account('x&y', User.ROLE_STAFF, name='<script>alert(1)</script>')

    # Act
    raw = _get(admin_client, LIST_URL).get_data(as_text=True)

    # Assert: エスケープされ、タグとして出ない
    assert '<script>alert(1)</script>' not in raw
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in raw
    assert 'x&amp;y' in raw


def test_list_search_by_name_or_id_partially_ignoring_case(admin_client, world):
    # Act
    by_name = _shown_login_ids(_text(_get(admin_client, '/admin/users?q=山田')))
    by_id = _shown_login_ids(_text(_get(admin_client, '/admin/users?q=ADMIN0')))

    # Assert
    assert by_name == ['staff_a']
    assert sorted(by_id) == ['admin01', 'admin02']


@pytest.mark.parametrize('query, expected', [('%25', ['staff%b']), ('_', ['staff_a']), ('%5C', [])])
def test_list_search_wildcards_are_literal(admin_client, world, query, expected):
    # Act
    ids = _shown_login_ids(_text(_get(admin_client, f'/admin/users?q={query}')))

    # Assert
    assert ids == expected


def test_list_filters_by_role_and_department_and_echoes_selection(admin_client, world):
    # Act
    html = _text(_get(admin_client, f"/admin/users?role=staff&department={world['roads'].id}&q=%E5%B1%B1"))

    # Assert: 結果と、フォームに戻る選択値
    assert _shown_login_ids(html) == ['staff_a']
    assert '<option value="staff" selected>' in html
    assert f"<option value=\"{world['roads'].id}\" selected>" in html
    assert 'value="山"' in html
    assert '1件表示（全4件）' in html


def test_list_ignores_invalid_filter_values(admin_client, world):
    # Act
    response = _get(admin_client, '/admin/users?role=citizen&department=abc&q=%20%20')

    # Assert: エラーにせず全件
    assert response.status_code == 200
    assert len(_shown_login_ids(_text(response))) == 4


def test_list_with_no_match_shows_empty_message(admin_client):
    # Act
    html = _text(_get(admin_client, '/admin/users?q=zzzz-no-such'))

    # Assert
    assert '該当するアカウントがありません。' in html and '0件表示' in html


@pytest.mark.parametrize('path', ['/admin/users', '/admin/users?role=admin&q=a'])
def test_list_always_sends_no_store_and_no_referrer_headers(admin_client, path):
    # Act
    response = _get(admin_client, path)

    # Assert: コードがない通常の表示でも付く
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'


def test_list_page_is_standalone_without_nav_parent(admin_client):
    # Act
    raw = _get(admin_client, LIST_URL).get_data(as_text=True)

    # Assert: 一覧自体は独立画面（親なし）
    assert 'data-nav-parent=""' in raw


# ---------------------------------------------------------------------------
# 削除 POST /admin/users/<id>/delete
# ---------------------------------------------------------------------------

def test_delete_success_redirects_flashes_and_writes_three_columns(admin_client, world):
    # Act
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/delete")

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert _flashes(admin_client) == [('success', MSG_ACCOUNT_DELETED.format(name='山田 道路', login_id='staff_a'))]
    fresh = _fresh(world['staff'].id)
    assert fresh.del_flag is True and fresh.deleted_at is not None and fresh.deleted_by_id == world['admin'].id


def test_delete_success_message_is_shown_on_list_and_account_disappears(admin_client, world):
    # Act
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")
    html = _text(_get(admin_client, LIST_URL))

    # Assert
    assert MSG_ACCOUNT_DELETED.format(name='山田 道路', login_id='staff_a') in html
    assert 'staff_a' not in _shown_login_ids(html)


def test_delete_account_disappears_from_portal(admin_client, world):
    # Act
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")
    html = _text(_get(admin_client, '/admin/portal'))

    # Assert: ポータルの一覧（氏名 (部署)）から消え、総数も減る（削除した旨の flash には氏名が出るので行の形で調べる）
    assert '山田 道路 (道路管理課)' not in html and '全3件' in html


def test_delete_keeps_filters_in_redirect(admin_client, world):
    # Act
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/delete",
                     role='staff', department=str(world['roads'].id), q='山')

    # Assert
    assert response.headers['Location'] == f"/admin/users?role=staff&department={world['roads'].id}&q=%E5%B1%B1"


def test_delete_drops_invalid_or_empty_filters_from_redirect(admin_client, world):
    # Act: 不正な role・部署、空白だけの q は付けない（hidden を改ざんされても安全）
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/delete",
                     role='citizen', department='abc', q='   ')

    # Assert
    assert response.headers['Location'] == LIST_URL


def test_delete_ignores_open_redirect_attempts_in_filters(admin_client, world):
    # Act: 外部 URL を入れても移動先は常に一覧
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/delete",
                     role='staff', q='http://evil.example/', next='http://evil.example/')

    # Assert
    assert response.headers['Location'].startswith('/admin/users')
    assert 'evil.example' not in response.headers['Location'].split('?')[0]


def test_delete_self_is_refused_with_danger_flash(admin_client, world):
    # Act
    response = _post(admin_client, f"/admin/users/{world['admin'].id}/delete")

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert _flashes(admin_client) == [('danger', MSG_CANNOT_DELETE_SELF)]
    assert _fresh(world['admin'].id).del_flag is False


@pytest.mark.parametrize('kind', ['missing', 'citizen', 'deleted', 'over_bigint', 'huge'])
def test_delete_unknown_citizen_deleted_or_out_of_range_id_is_flash_not_404(admin_client, world, kind):
    # Arrange
    target = {'missing': 999999, 'citizen': world['citizen'].id, 'over_bigint': 2 ** 63, 'huge': 10 ** 30,
              'deleted': soft_delete(make_account('gone', User.ROLE_STAFF), by=world['admin']).id}[kind]

    # Act
    response = _post(admin_client, f'/admin/users/{target}/delete')

    # Assert: IDOR（市民・ほかの種類の ID）でも 404/500 にせず、同じ danger の flash
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert _flashes(admin_client) == [('danger', MSG_ACCOUNT_TARGET_NOT_FOUND)]
    assert _fresh(world['citizen'].id).del_flag is False


def test_delete_twice_second_time_says_not_found(admin_client, world):
    # Arrange
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")
    with admin_client.session_transaction() as session:
        session.pop('_flashes', None)

    # Act
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/delete")

    # Assert
    assert response.status_code == 302
    assert _flashes(admin_client) == [('danger', MSG_ACCOUNT_TARGET_NOT_FOUND)]


@pytest.mark.parametrize('status_target', ['ok', 'self'])
def test_delete_with_replace_history_header_returns_json_redirect(admin_client, world, status_target):
    # Arrange: data-nav="replace" のフォームは fetch で送られ、成功も失敗も移動先を JSON で返す
    target = world['staff'] if status_target == 'ok' else world['admin']

    # Act
    response = _req(admin_client, 'post', f'/admin/users/{target.id}/delete', headers={'X-Replace-History': '1'})

    # Assert
    assert response.status_code == 200
    assert response.get_json() == {'redirect': LIST_URL}


def test_delete_logs_warning_with_actor_and_target_ids(admin_client, world, caplog):
    # Act
    with caplog.at_level(logging.WARNING):
        _post(admin_client, f"/admin/users/{world['staff'].id}/delete")

    # Assert
    records = [r for r in caplog.records if 'action=delete' in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert f"actor_id={world['admin'].id}" in records[0].getMessage()
    assert f"target_id={world['staff'].id}" in records[0].getMessage()


def test_delete_failure_logs_rejection_without_personal_data(admin_client, world, caplog):
    # Act
    with caplog.at_level(logging.WARNING):
        _post(admin_client, f"/admin/users/{world['admin'].id}/delete")

    # Assert: 成功の記録（アカウントを削除しました）は出ず、拒否の記録だけ。氏名・ログインID は出さない
    messages = [r.getMessage() for r in caplog.records if 'action=delete' in r.getMessage()]
    assert len(messages) == 1 and 'result=rejected' in messages[0]
    assert f"actor_id={world['admin'].id}" in messages[0] and f"target_id={world['admin'].id}" in messages[0]
    assert '削除しました' not in messages[0] and '佐藤' not in messages[0] and 'admin01' not in messages[0]


@pytest.mark.parametrize('action', ['unlock', 'reset-code'])
def test_unlock_and_reset_code_failure_log_rejection(admin_client, world, caplog, action):
    # Act
    with caplog.at_level(logging.WARNING):
        _post(admin_client, f"/admin/users/{world['citizen'].id}/{action}")

    # Assert
    key = action.replace('-', '_')
    messages = [r.getMessage() for r in caplog.records if f'action={key}' in r.getMessage()]
    assert len(messages) == 1 and 'result=rejected' in messages[0]
    assert 'taro@example.com' not in messages[0]


def test_delete_invalidates_staff_session_on_next_request(app, admin_client, login, world):
    # Arrange: 職員が別の端末でログイン中
    staff_client = app.test_client()
    login(staff_client, User.ROLE_STAFF, 'staff_a')
    assert _get(staff_client, '/staff/dashboard').status_code == 200

    # Act
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")

    # Assert: 次のリクエストからログイン画面へ
    response = _get(staff_client, '/staff/dashboard')
    assert response.status_code == 302 and response.headers['Location'].endswith('/login/staff')


def test_delete_invalidates_remember_me_cookie(app, admin_client, world):
    # Arrange: 「ログイン状態を保持する」でログイン。セッション Cookie を捨てても remember cookie で入れる（対照）
    staff_client = app.test_client()
    _post(staff_client, '/login/staff', staff_id='staff_a', password='password123', remember='on')
    staff_client.delete_cookie('session')
    assert _get(staff_client, '/staff/dashboard').status_code == 200

    # Act: 削除してから、再びセッション Cookie を捨てる
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")
    staff_client.delete_cookie('session')

    # Assert: remember cookie だけでは入れない
    response = _get(staff_client, '/staff/dashboard')
    assert response.status_code == 302 and response.headers['Location'].endswith('/login/staff')


@pytest.mark.parametrize('role, field, login_id', [
    (User.ROLE_STAFF, 'staff_id', 'staff_a'), (User.ROLE_ADMIN, 'admin_id', 'admin02')])
def test_deleted_account_cannot_log_in_and_gets_same_message_as_unknown_id(app, admin_client, world, role, field, login_id):
    # Arrange
    target = world['staff'] if role == User.ROLE_STAFF else world['admin2']
    _post(admin_client, f'/admin/users/{target.id}/delete')
    fresh_client = app.test_client()

    # Act: 正しいパスワードと、存在しない ID
    deleted = _post(fresh_client, f'/login/{role}', **{field: login_id, 'password': 'password123'})
    unknown = _post(app.test_client(), f'/login/{role}', **{field: 'no_such_id', 'password': 'password123'})

    # Assert: どちらもログイン画面（200）に同じ文言。ログインされない
    label = auth_service.LOGIN_ID_LABELS[role]
    assert deleted.status_code == 200 and unknown.status_code == 200
    assert MSG_LOGIN_FAILED.format(label=label) in _text(deleted)
    assert MSG_LOGIN_FAILED.format(label=label) in _text(unknown)
    assert _get(fresh_client, '/admin/portal' if role == User.ROLE_ADMIN else '/staff/dashboard').status_code == 302


def test_deleted_account_repeated_login_attempts_never_lock(app, admin_client, world):
    # Arrange
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")
    client = app.test_client()

    # Act: 失敗の上限を超えて試す
    for _ in range(7):
        html = _text(_post(client, '/login/staff', staff_id='staff_a', password='wrong-password'))

    # Assert: ロックの文言は出ず、失敗回数も増えない
    assert MSG_LOGIN_FAILED.format(label='職員ID') in html and 'ロック' not in html
    fresh = _fresh(world['staff'].id)
    assert fresh.failed_login_count == 0 and fresh.locked_until is None


def test_delete_voids_issued_reset_code_so_it_cannot_be_used(app, admin_client, world):
    # Arrange: コードを発行してから削除
    code, _ = auth_service.issue_reset_code('staff_a')
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")

    # Act: 職員用の再設定画面でそのコードを使う
    response = _post(app.test_client(), '/login/staff/reset-password', login_id='staff_a', reset_code=code,
                     new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert
    assert response.status_code == 200 and MSG_RESET_CODE_INVALID in _text(response)
    assert _fresh(world['staff'].id).check_password('password123')


def test_deleted_login_id_stays_reserved_in_register_form(admin_client, world):
    # Arrange
    _post(admin_client, f"/admin/users/{world['staff'].id}/delete")

    # Act
    response = _post(admin_client, '/admin/users/register', role='staff', login_id='staff_a', staff_name='新人',
                     department=str(world['roads'].id), password=NEW_PW, password_confirm=NEW_PW)

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith('/admin/users/register')
    assert ('warning', MSG_ID_TAKEN.format(label='職員ID')) in _flashes(admin_client)
    assert User.query.filter_by(login_id='staff_a').count() == 1


# ---------------------------------------------------------------------------
# ロック解除 POST /admin/users/<id>/unlock
# ---------------------------------------------------------------------------

def test_unlock_success_clears_lock_and_flashes(admin_client, world):
    # Arrange
    lock_account(world['staff'])

    # Act
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/unlock")

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert _flashes(admin_client) == [('success', MSG_ACCOUNT_UNLOCKED.format(name='山田 道路', login_id='staff_a'))]
    fresh = _fresh(world['staff'].id)
    assert fresh.locked_until is None and fresh.failed_login_count == 0


def test_unlock_lets_the_account_log_in_immediately(app, admin_client, world):
    # Arrange
    lock_account(world['staff'])
    locked_try = _post(app.test_client(), '/login/staff', staff_id='staff_a', password='password123')
    assert locked_try.status_code == 200

    # Act
    _post(admin_client, f"/admin/users/{world['staff'].id}/unlock")
    response = _post(app.test_client(), '/login/staff', staff_id='staff_a', password='password123')

    # Assert
    assert response.status_code == 302 and '/login/' not in response.headers['Location']


def test_unlock_on_unlocked_account_still_succeeds(admin_client, world):
    # Act
    response = _post(admin_client, f"/admin/users/{world['staff'].id}/unlock")

    # Assert
    assert response.status_code == 302
    assert _flashes(admin_client)[0][0] == 'success'


def test_unlock_row_loses_button_and_badge_after_unlock(admin_client, world):
    # Arrange
    lock_account(world['staff'])
    assert 'ロック中' in _text(_get(admin_client, LIST_URL))

    # Act
    _post(admin_client, f"/admin/users/{world['staff'].id}/unlock")
    html = _text(_get(admin_client, LIST_URL))

    # Assert
    assert 'ロック中' not in html and '/unlock"' not in html


@pytest.mark.parametrize('kind', ['missing', 'citizen', 'deleted', 'huge'])
def test_unlock_unknown_citizen_deleted_or_huge_id_is_flash_not_404(admin_client, world, kind):
    # Arrange
    target = {'missing': 999999, 'citizen': world['citizen'].id, 'huge': 10 ** 30,
              'deleted': soft_delete(make_account('gone', User.ROLE_STAFF), by=world['admin']).id}[kind]
    lock_account(world['citizen'])

    # Act
    response = _post(admin_client, f'/admin/users/{target}/unlock')

    # Assert: 市民のロックは解除されない
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert _flashes(admin_client) == [('danger', MSG_ACCOUNT_TARGET_NOT_FOUND)]
    assert _fresh(world['citizen'].id).locked_until is not None


def test_unlock_keeps_filters_and_logs_warning(admin_client, world, caplog):
    # Arrange
    lock_account(world['staff'])

    # Act
    with caplog.at_level(logging.WARNING):
        response = _post(admin_client, f"/admin/users/{world['staff'].id}/unlock", role='staff', q='山')

    # Assert
    assert response.headers['Location'] == '/admin/users?role=staff&q=%E5%B1%B1'
    records = [r.getMessage() for r in caplog.records if 'action=unlock' in r.getMessage()]
    assert len(records) == 1
    assert f"actor_id={world['admin'].id}" in records[0] and f"target_id={world['staff'].id}" in records[0]


# ---------------------------------------------------------------------------
# 再設定コード発行 POST /admin/users/<id>/reset-code と、一度だけの表示
# ---------------------------------------------------------------------------

def _issue(client, user, **data):
    return _post(client, f'/admin/users/{user.id}/reset-code', **data)


def test_reset_code_post_stores_code_in_session_and_redirects(admin_client, world):
    # Act
    response = _issue(admin_client, world['staff'])

    # Assert: PRG。発行の知らせは一覧のコード枠で出すので flash は使わない（コードが flash に入らない）
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    with admin_client.session_transaction() as session:
        stored = session['reset_code']
    assert set(stored) == {'name', 'login_id', 'code', 'expires_at_text', 'is_locked', 'issued'}
    assert stored['login_id'] == 'staff_a' and stored['name'] == '山田 道路' and stored['is_locked'] is False
    assert _flashes(admin_client) == []


def test_reset_code_is_shown_once_with_expiry_and_copy_button(admin_client, world):
    # Arrange
    _issue(admin_client, world['staff'])
    with admin_client.session_transaction() as session:
        stored = session['reset_code']

    # Act
    response = _get(admin_client, LIST_URL)
    html = _text(response)

    # Assert
    assert _issued_code(html) == stored['code']
    assert '山田 道路' in html and 'ログインID: staff_a' in html
    assert f"有効期限: {stored['expires_at_text']}" in html
    assert 'id="issued-code-copy"' in html and 'コピー' in html
    assert response.headers['Cache-Control'] == 'no-store' and response.headers['Referrer-Policy'] == 'no-referrer'
    assert '再設定コードを発行しました' in html


def test_reset_code_disappears_on_reload_and_session_is_cleared(admin_client, world):
    # Arrange
    _issue(admin_client, world['staff'])
    _get(admin_client, LIST_URL)

    # Act
    with admin_client.session_transaction() as session:
        assert 'reset_code' not in session
    html = _text(_get(admin_client, LIST_URL))

    # Assert: 再読み込みでは出ず、「時間切れ」の警告も出ない
    assert _issued_code(html) is None and 'issued-code-card' not in html
    assert MSG_RESET_CODE_GONE not in html


def test_reset_code_is_not_visible_to_another_admin_session(app, admin_client, login, world):
    # Arrange
    _issue(admin_client, world['staff'])
    other = app.test_client()
    login(other, User.ROLE_ADMIN, 'admin02')

    # Act
    html = _text(_get(other, LIST_URL))

    # Assert: 発行した管理者のセッションにだけある
    assert _issued_code(html) is None


def test_reset_code_shown_code_can_reset_the_password(app, admin_client, world):
    # Arrange
    _issue(admin_client, world['staff'])
    code = _issued_code(_text(_get(admin_client, LIST_URL)))

    # Act: 画面に出たコード（ハイフン付き）で職員が再設定する
    response = _post(app.test_client(), '/login/staff/reset-password', login_id='staff_a', reset_code=code,
                     new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert
    assert response.status_code == 302
    assert _fresh(world['staff'].id).check_password(NEW_PW)


def test_reset_code_reissue_voids_the_previous_code(admin_client, world):
    # Arrange
    _issue(admin_client, world['staff'])
    first = _issued_code(_text(_get(admin_client, LIST_URL)))

    # Act
    _issue(admin_client, world['staff'])
    second = _issued_code(_text(_get(admin_client, LIST_URL)))

    # Assert
    assert first != second
    assert PasswordResetToken.query.filter_by(user_id=world['staff'].id, used_at=None).count() == 1


@pytest.mark.parametrize('age_seconds, shown', [(0, True), (110, True), (121, False), (500, False), (-1000, False)])
def test_reset_code_expires_after_120_seconds(admin_client, world, age_seconds, shown):
    # Arrange: セッションの発行時刻を過去（または未来）にずらす（dict ごと入れ直す）
    _issue(admin_client, world['staff'])
    with admin_client.session_transaction() as session:
        session['reset_code'] = {**session['reset_code'], 'issued': session['reset_code']['issued'] - age_seconds}

    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert: 期限内はコードを出す。期限切れ・未来の時刻はコードを出さず、本文に警告
    if shown:
        assert _issued_code(html) is not None and MSG_RESET_CODE_GONE not in html
    else:
        assert _issued_code(html) is None and MSG_RESET_CODE_GONE in html
    with admin_client.session_transaction() as session:
        assert 'reset_code' not in session


@pytest.mark.parametrize('broken', ['not-a-dict', {}, {'issued': 'x'}, {'issued': None, 'code': 'A'}, 12345])
def test_reset_code_broken_session_value_does_not_crash_and_shows_nothing(admin_client, broken):
    # Arrange
    with admin_client.session_transaction() as session:
        session['reset_code'] = broken

    # Act
    response = _get(admin_client, LIST_URL)

    # Assert
    assert response.status_code == 200
    assert _issued_code(_text(response)) is None and MSG_RESET_CODE_GONE in _text(response)


def test_reset_code_for_locked_account_is_issued_with_unlock_notice(admin_client, world):
    # Arrange
    lock_account(world['staff'])
    _issue(admin_client, world['staff'])

    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert: 発行され、「ロック解除してから」の案内が出る。ロックは残る
    assert _issued_code(html) is not None
    assert 'コードを使う前に' in html and 'ロック解除' in html
    assert _fresh(world['staff'].id).locked_until is not None


def test_reset_code_for_unlocked_account_has_no_unlock_notice(admin_client, world):
    # Arrange
    _issue(admin_client, world['staff'])

    # Act
    html = _text(_get(admin_client, LIST_URL))

    # Assert
    assert 'コードを使う前に' not in html


@pytest.mark.parametrize('kind', ['missing', 'citizen', 'deleted', 'huge'])
def test_reset_code_unknown_citizen_or_deleted_id_is_flash_and_issues_nothing(admin_client, world, kind):
    # Arrange
    target = {'missing': 999999, 'citizen': world['citizen'].id, 'huge': 10 ** 30,
              'deleted': soft_delete(make_account('gone', User.ROLE_STAFF), by=world['admin']).id}[kind]

    # Act
    response = _post(admin_client, f'/admin/users/{target}/reset-code')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert _flashes(admin_client) == [('danger', MSG_ACCOUNT_TARGET_NOT_FOUND)]
    assert PasswordResetToken.query.count() == 0
    with admin_client.session_transaction() as session:
        assert 'reset_code' not in session


def test_reset_code_for_disabled_account_is_refused(admin_client, world):
    # Arrange
    world['staff'].is_active = False
    db.session.commit()

    # Act
    _issue(admin_client, world['staff'])

    # Assert
    assert _flashes(admin_client) == [('danger', MSG_RESET_CODE_NOT_ALLOWED.format(login_id='staff_a'))]
    with admin_client.session_transaction() as session:
        assert 'reset_code' not in session


def test_reset_code_keeps_filters_in_redirect(admin_client, world):
    # Act
    response = _issue(admin_client, world['staff'], role='staff', department=str(world['roads'].id), q='山')

    # Assert
    assert response.headers['Location'] == f"/admin/users?role=staff&department={world['roads'].id}&q=%E5%B1%B1"


def test_reset_code_never_appears_in_logs(admin_client, world, caplog):
    # Act
    with caplog.at_level(logging.DEBUG):
        _issue(admin_client, world['staff'])
    with admin_client.session_transaction() as session:
        code = session['reset_code']['code']

    # Assert: 発行の記録（action・actor・target）はあるが、コードは平文でも記号なしでも出ない
    messages = [r.getMessage() for r in caplog.records if 'action=reset_code' in r.getMessage()]
    assert len(messages) == 1
    assert f"actor_id={world['admin'].id}" in messages[0] and f"target_id={world['staff'].id}" in messages[0]
    assert code not in caplog.text and code.replace('-', '') not in caplog.text


def test_reset_code_page_text_is_not_in_other_pages(admin_client, world):
    # Arrange
    _issue(admin_client, world['staff'])
    code = _issued_code(_text(_get(admin_client, LIST_URL)))

    # Act / Assert: 画面を閉じたあとはどこにも出ない
    assert code not in _text(_get(admin_client, '/admin/portal'))
    assert code not in _text(_get(admin_client, LIST_URL))


# ---------------------------------------------------------------------------
# ポータル GET /admin/portal
# ---------------------------------------------------------------------------

def test_portal_shows_five_newest_accounts_with_total_and_link(admin_client, world, clock):
    # Arrange: 全部で 7 件（管理者 2 + 職員 2 + 追加 3）。作成日時を明示
    for index in range(3):
        make_account(f'extra{index}', User.ROLE_STAFF, name=f'追加職員{index}', created_at=clock.now + timedelta(days=10 + index))
    for index, key in enumerate(['admin', 'admin2', 'staff', 'staff2']):
        world[key].created_at = clock.now + timedelta(days=index)
    db.session.commit()

    # Act
    html = _text(_get(admin_client, '/admin/portal'))

    # Assert: 新しい 5 件（追加 3 + staff2 + staff）だけ。古い管理者 2 人は出ない
    for name in ('追加職員0', '追加職員1', '追加職員2', '田中 公園', '山田 道路'):
        assert name in html
    assert '佐藤 管理 (管理者)' not in html and '高橋 管理 (管理者)' not in html
    assert '全7件' in html
    assert 'href="/admin/users"' in html and 'すべて表示' in html


def test_portal_excludes_citizens_and_deleted_and_shows_department_or_role(admin_client, world):
    # Arrange
    soft_delete(world['staff2'], by=world['admin'])

    # Act
    html = _text(_get(admin_client, '/admin/portal'))

    # Assert: 職員は部署名、管理者は役割ラベルを括弧で表示
    assert '市民 太郎' not in html and '田中 公園' not in html
    assert '山田 道路 (道路管理課)' in html and '高橋 管理 (管理者)' in html


def test_portal_shows_lock_and_disabled_badges(admin_client, world):
    # Arrange
    lock_account(world['staff'])
    world['staff2'].is_active = False
    db.session.commit()

    # Act
    html = _text(_get(admin_client, '/admin/portal'))

    # Assert
    assert 'ロック中' in html and '利用停止' in html and '有効' in html


def test_portal_keeps_category_department_mapping_card(admin_client):
    # Act
    html = _text(_get(admin_client, '/admin/portal'))

    # Assert
    assert '道路の破損・陥没' in html and '→ 道路維持課' in html
    assert '公園の設備故障' in html and '→ 都市公園課' in html


def test_portal_has_only_logout_post_form(admin_client):
    # Act
    html = _text(_get(admin_client, '/admin/portal'))

    # Assert
    assert _post_form_count(html) == 1 and 'action="/logout"' in html


def test_portal_with_only_the_logged_in_admin_lists_one_account(client, login, admin):
    # Arrange: world を使わないので、アカウントは admin01 だけ
    login(client, User.ROLE_ADMIN, 'admin01')

    # Act
    html = _text(_get(client, '/admin/portal'))

    # Assert
    assert '全1件' in html and '佐藤 管理' in html


# ---------------------------------------------------------------------------
# 登録画面・完了画面の戻り先
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('path', ['/admin/users/register', '/admin/users/register/complete'])
def test_register_screens_return_to_user_list(admin_client, path):
    # Act
    raw = _get(admin_client, path).get_data(as_text=True)

    # Assert: 親画面（nav_parent）と戻るリンクが一覧
    assert 'data-nav-parent="/admin/users"' in raw
    assert 'data-nav-parent="/admin/portal"' not in raw
    assert 'href="/admin/users"' in raw


def test_register_success_still_redirects_to_complete_screen(admin_client, world):
    # Act
    response = _post(admin_client, '/admin/users/register', role='admin', login_id='newadmin', staff_name='新管理',
                     password=NEW_PW, password_confirm=NEW_PW)

    # Assert: 登録のあとは完了画面（その画面から一覧へ戻れる）
    assert response.status_code == 302 and response.headers['Location'].endswith('/admin/users/register/complete')
    assert 'newadmin' in _shown_login_ids(_text(_get(admin_client, LIST_URL)))
