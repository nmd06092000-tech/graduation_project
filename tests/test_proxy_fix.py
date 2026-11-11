# リバースプロキシ（Nginx）対応 ProxyFix のテスト（GĐ2a）：PROXY_FIX_HOPS で有効・無効を切り替える
import pytest
from flask import jsonify, request

from app import create_app
from app.constants import MSG_CSRF_EXPIRED
from app.models import User
from tests.conftest import get_csrf_token
from config import TestingConfig


def _make_app(hops, csrf=False):
    """PROXY_FIX_HOPS を指定したアプリを作る（ProxyFix は create_app 時に決まるので子 config で作り直す）"""
    class ProxyConfig(TestingConfig):
        PROXY_FIX_HOPS = hops
        WTF_CSRF_ENABLED = csrf
        WTF_CSRF_SSL_STRICT = True

    proxy_app = create_app(ProxyConfig)

    @proxy_app.route('/_probe')
    def probe():
        return jsonify(secure=request.is_secure, remote=request.remote_addr, host=request.host)
    return proxy_app


def _probe(proxy_app, **headers):
    return proxy_app.test_client().get('/_probe', headers=headers).get_json()


def test_default_config_does_not_use_proxy_fix():
    # Assert: 既定は 0（ProxyFix なし）
    assert TestingConfig.PROXY_FIX_HOPS == 0


def test_forwarded_proto_makes_request_secure_when_enabled(app):
    # Arrange
    proxy_app = _make_app(1)

    # Act
    data = _probe(proxy_app, **{'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '203.0.113.9'})

    # Assert: https と元のクライアント IP を信頼する
    assert data['secure'] is True
    assert data['remote'] == '203.0.113.9'


def test_forwarded_headers_ignored_when_disabled(app):
    # Arrange
    proxy_app = _make_app(0)

    # Act
    data = _probe(proxy_app, **{'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '203.0.113.9'})

    # Assert: ヘッダは無視される（偽装できない）
    assert data['secure'] is False
    assert data['remote'] != '203.0.113.9'


def test_forwarded_host_is_never_trusted(app):
    # Arrange
    proxy_app = _make_app(1)

    # Act: X-Forwarded-Host は x_host=0 なので使わない
    data = _probe(proxy_app, **{'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'evil.example'})

    # Assert
    assert data['host'] == 'localhost'


def test_only_last_forwarded_for_entry_is_used_with_one_hop(app):
    # Arrange
    proxy_app = _make_app(1)

    # Act: クライアントが偽の X-Forwarded-For を付けても、プロキシが足した最後の値だけを採用する
    data = _probe(proxy_app, **{'X-Forwarded-For': '1.2.3.4, 203.0.113.9'})

    # Assert
    assert data['remote'] == '203.0.113.9'


def test_csrf_ssl_strict_passes_with_https_referer_behind_proxy(app, citizen):
    # Arrange: Nginx の背後（内部は http、X-Forwarded-Proto: https）で SSL strict の CSRF 検証が有効
    proxy_app = _make_app(1, csrf=True)
    client = proxy_app.test_client()
    token = get_csrf_token(client, '/login/citizen')

    # Act: Referer は公開側のオリジン（https://localhost）
    response = client.post('/login/citizen', data={
        'email': citizen.login_id, 'password': 'password123', 'csrf_token': token},
        headers={'X-Forwarded-Proto': 'https', 'Referer': 'https://localhost/login/citizen'})

    # Assert: CSRF エラーにならずログインできる
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/citizen/mypage')


@pytest.mark.parametrize('referer', ['https://evil.example/login/citizen', 'http://localhost/login/citizen', None])
def test_csrf_ssl_strict_rejects_wrong_or_missing_referer_behind_proxy(app, citizen, referer):
    # Arrange
    proxy_app = _make_app(1, csrf=True)
    client = proxy_app.test_client()
    token = get_csrf_token(client, '/login/citizen')
    headers = {'X-Forwarded-Proto': 'https'}
    if referer:
        headers['Referer'] = referer

    # Act
    response = client.post('/login/citizen', data={
        'email': citizen.login_id, 'password': 'password123', 'csrf_token': token}, headers=headers)

    # Assert: https と見なされるので Referer の同一オリジン検査が効き、CSRF エラー（302 で戻す）。ログインしない
    assert response.status_code == 302
    assert not response.headers['Location'].endswith('/citizen/mypage')
    with client.session_transaction() as session:
        assert ('warning', MSG_CSRF_EXPIRED) in [tuple(item) for item in session.get('_flashes', [])]
        assert '_user_id' not in session
