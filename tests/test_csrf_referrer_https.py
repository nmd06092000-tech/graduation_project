# HTTPS 環境の CSRF 検証（WTF_CSRF_SSL_STRICT=True は Referer 必須）と Referrer-Policy の回帰テスト
# 不具合: Referrer-Policy: no-referrer だと POST に Referer が付かず、CSRFError でトップへ戻された。
# same-origin に直したので、自サイトへの POST には Referer が付き、外部サイトへは付かない。
import re

import pytest
from flask import g

from app.constants import MSG_CSRF_EXPIRED
from app.extensions import db
from app.models import Department, PasswordResetToken, User
from tests.conftest import lock_account, make_account
from tests.test_account_routes_2b import NEW_PW, _flashes, _password_of
from tests.test_account_routes_2b import reset_link  # noqa: F401  (fixture)

HTTPS = 'https://localhost'
LIST_URL = '/admin/users'
SAME_ORIGIN_LIST = {'Referer': f'{HTTPS}{LIST_URL}'}


def _drop_cache():
    """fixture の app_context に残る g のキャッシュ（ログインユーザー・CSRF）を捨てる"""
    g.pop('_login_user', None)
    g.pop('csrf_token', None)


@pytest.fixture
def world(app, admin):
    """管理者 admin01（操作する本人）・対象の職員 staff_a"""
    roads = Department(name='道路管理課')
    db.session.add(roads)
    db.session.commit()
    return {'admin': admin, 'staff': make_account('staff_a', User.ROLE_STAFF, name='山田 道路', department=roads)}


@pytest.fixture
def https_admin(app, world, login):
    """HTTPS（https://localhost）で管理者 admin01 にログインし、その後に CSRF（SSL strict）を有効にしたクライアント"""
    client = app.test_client()
    # ログイン画面の検証はこのテストの対象外なので、ログインだけ CSRF 無効で行う
    _drop_cache()
    assert login(client, User.ROLE_ADMIN, 'admin01').status_code == 302
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_SSL_STRICT'] = True
    return client


def _https_get(client, path):
    _drop_cache()
    return client.get(path, base_url=HTTPS)


def _https_post(client, path, headers=None, **data):
    _drop_cache()
    return client.post(path, data=data, headers=headers or {}, base_url=HTTPS)


def _csrf_token(client, path):
    _drop_cache()
    html = client.get(path, base_url=HTTPS).get_data(as_text=True)
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, f'{path} に csrf_token の hidden 入力がありません'
    return match.group(1)


# ---------------------------------------------------------------------------
# 管理者 /admin/users：HTTPS + 同一オリジンの Referer で 3 つの POST が通る
# ---------------------------------------------------------------------------

def test_list_page_sends_same_origin_and_no_store_headers_over_https(https_admin):
    # Act
    response = _https_get(https_admin, LIST_URL)

    # Assert: 外部へは Referer を送らない（same-origin）・キャッシュさせない
    assert response.status_code == 200
    assert response.headers['Referrer-Policy'] == 'same-origin'
    assert response.headers['Cache-Control'] == 'no-store'


def test_delete_succeeds_over_https_with_same_origin_referer(https_admin, world):
    # Arrange
    token = _csrf_token(https_admin, LIST_URL)

    # Act
    response = _https_post(https_admin, f"/admin/users/{world['staff'].id}/delete",
                           headers=SAME_ORIGIN_LIST, csrf_token=token)

    # Assert: CSRF エラーにならず一覧へ戻り、実際に論理削除される
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert ('warning', MSG_CSRF_EXPIRED) not in _flashes(https_admin)
    db.session.expire_all()
    assert db.session.get(User, world['staff'].id).del_flag is True


def test_unlock_succeeds_over_https_with_same_origin_referer(https_admin, world):
    # Arrange
    lock_account(world['staff'])
    token = _csrf_token(https_admin, LIST_URL)

    # Act
    response = _https_post(https_admin, f"/admin/users/{world['staff'].id}/unlock",
                           headers=SAME_ORIGIN_LIST, csrf_token=token)

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert ('warning', MSG_CSRF_EXPIRED) not in _flashes(https_admin)
    db.session.expire_all()
    fresh = db.session.get(User, world['staff'].id)
    assert fresh.locked_until is None and fresh.failed_login_count == 0


def test_reset_code_issue_succeeds_over_https_with_same_origin_referer(https_admin, world):
    # Arrange
    token = _csrf_token(https_admin, LIST_URL)

    # Act
    response = _https_post(https_admin, f"/admin/users/{world['staff'].id}/reset-code",
                           headers=SAME_ORIGIN_LIST, csrf_token=token)

    # Assert: コードが発行され（トークン 1 件・セッションに 1 回だけ表示用）、一覧へ戻る
    assert response.status_code == 302 and response.headers['Location'] == LIST_URL
    assert ('warning', MSG_CSRF_EXPIRED) not in _flashes(https_admin)
    assert PasswordResetToken.query.filter_by(user_id=world['staff'].id).count() == 1
    with https_admin.session_transaction() as session:
        assert 'reset_code' in session


@pytest.mark.parametrize('action', ['delete', 'unlock', 'reset-code'])
def test_admin_post_without_referer_over_https_is_still_blocked(https_admin, world, action):
    # Arrange: 正しいトークンはあるが Referer が無い（no-referrer だったときの状態）
    lock_account(world['staff'])
    token = _csrf_token(https_admin, LIST_URL)

    # Act
    response = _https_post(https_admin, f"/admin/users/{world['staff'].id}/{action}", csrf_token=token)

    # Assert: 保護は有効のまま。トップへ戻り警告。データは変わらない
    assert response.status_code == 302 and response.headers['Location'] == '/'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(https_admin)
    db.session.expire_all()
    fresh = db.session.get(User, world['staff'].id)
    assert fresh.del_flag is False and fresh.locked_until is not None
    assert PasswordResetToken.query.count() == 0
    with https_admin.session_transaction() as session:
        assert 'reset_code' not in session


@pytest.mark.parametrize('referer', ['https://evil.example/admin/users', 'http://localhost/admin/users'])
def test_admin_post_with_other_origin_referer_over_https_is_blocked(https_admin, world, referer):
    # Arrange
    token = _csrf_token(https_admin, LIST_URL)

    # Act
    response = _https_post(https_admin, f"/admin/users/{world['staff'].id}/delete",
                           headers={'Referer': referer}, csrf_token=token)

    # Assert: 別オリジン・http の Referer は拒否される
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(https_admin)
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(User, world['staff'].id).del_flag is False


# ---------------------------------------------------------------------------
# 市民 /reset-password/confirm/<token>：HTTPS + 同一オリジンの Referer で「確定する」が通る
# ---------------------------------------------------------------------------

def test_confirm_page_sends_same_origin_and_no_store_headers_over_https(app, reset_link):  # noqa: F811
    # Arrange
    raw, url = reset_link

    # Act
    response = app.test_client().get(url, base_url=HTTPS)

    # Assert
    assert response.status_code == 200
    assert response.headers['Referrer-Policy'] == 'same-origin'
    assert response.headers['Cache-Control'] == 'no-store'


def test_confirm_post_succeeds_over_https_with_same_origin_referer(app, reset_link, citizen):  # noqa: F811
    # Arrange: GET した確認画面から本物の csrf_token を取る
    raw, url = reset_link
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_SSL_STRICT'] = True
    client = app.test_client()
    token = _csrf_token(client, url)

    # Act
    response = _https_post(client, url, headers={'Referer': f'{HTTPS}{url}'}, csrf_token=token)

    # Assert: 完了画面へ redirect し、パスワードが変わる
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password/complete'
    assert ('warning', MSG_CSRF_EXPIRED) not in _flashes(client)
    assert _password_of(citizen.id).check_password(NEW_PW)
    assert PasswordResetToken.query.filter_by(user_id=citizen.id).one().used_at is not None


def test_confirm_post_without_referer_over_https_is_still_blocked(app, reset_link, citizen):  # noqa: F811
    # Arrange
    raw, url = reset_link
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_SSL_STRICT'] = True
    client = app.test_client()
    token = _csrf_token(client, url)

    # Act: Referer なし
    response = _https_post(client, url, csrf_token=token)

    # Assert: 保護は有効のまま。トップへ戻り警告。パスワードは変わらず、トークンも未使用
    assert response.status_code == 302 and response.headers['Location'] == '/'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(client)
    assert _password_of(citizen.id).check_password('password123')
    assert PasswordResetToken.query.filter_by(user_id=citizen.id).one().used_at is None
