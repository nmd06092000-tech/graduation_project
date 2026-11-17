# GĐ2b のルート（結合）テスト：市民のパスワード再設定（メール）・職員/管理者の再設定コード・市民のパスワード変更・
# 管理者のアカウント登録。ログイン・権限・CSRF・redirect/flash・DB・古いセッションの失効を確認する。メールはモックで受ける
import html as html_lib
import re

import pytest
from flask import g

from app.constants import (
    MSG_CSRF_EXPIRED, MSG_CURRENT_PASSWORD_WRONG, MSG_DEPARTMENT_INVALID, MSG_DEPARTMENT_REQUIRED, MSG_EMAIL_NO_AT,
    MSG_EMAIL_REQUIRED, MSG_ID_HAS_AT, MSG_ID_TAKEN, MSG_LOGIN_LOCKED, MSG_NAME_REQUIRED, MSG_PASSWORD_CHANGED,
    MSG_PASSWORD_MISMATCH, MSG_PASSWORD_PUBLIC, MSG_PASSWORD_SAME_AS_CURRENT, MSG_PASSWORD_TOO_SHORT,
    MSG_RESET_CODE_INVALID, MSG_RESET_CODE_REQUIRED, MSG_RESET_LINK_INVALID, MSG_RESET_MAIL_SENT, MSG_ROLE_REQUIRED,
)
from app.extensions import db
from app.models import PasswordResetToken, User
from app.services import auth_service
from app.utils.mail import MailError
from tests.conftest import _make_user, get_csrf_token, sent_reset_token

NEW_PW = 'NewPass-9876'


def _flashes(client):
    """セッションに積まれた flash を (カテゴリ, メッセージ) のリストで返す（画面を表示する前に読む）"""
    with client.session_transaction() as session:
        return [tuple(item) for item in session.get('_flashes', [])]


def _status(client, path):
    """path を GET したステータス。fixture の app_context に残った g のキャッシュ（ログインユーザー・CSRF）を捨ててから送る"""
    g.pop('_login_user', None)
    g.pop('csrf_token', None)
    return client.get(path).status_code


def _html(client, path):
    g.pop('_login_user', None)
    g.pop('csrf_token', None)
    return client.get(path).get_data(as_text=True)


@pytest.fixture
def login(login):
    """conftest の login に、g に残ったログイン中ユーザーのキャッシュを捨てる処理を足したもの
    （fixture の app_context が続くため、前のログインが残っていると別のクライアントのログインが「ログイン済み」扱いになる）"""
    def _login(*args, **kwargs):
        g.pop('_login_user', None)
        g.pop('csrf_token', None)
        return login(*args, **kwargs)
    return _login


def _text(response):
    """応答の HTML 本文（&#39; などの文字参照は元の文字に戻す）"""
    return html_lib.unescape(response.get_data(as_text=True))


def _logged_in(response):
    """ログイン POST が成功したか（失敗はログイン画面へ redirect される）"""
    return response.status_code == 302 and '/login/' not in response.headers['Location']


def _password_of(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _issue_code(login_id):
    return auth_service.issue_reset_code(login_id)[0]


def _reset_staff(client, login_id, code, password=NEW_PW, confirm=None, path='/login/staff/reset-password', **extra):
    return client.post(path, data={'login_id': login_id, 'reset_code': code, 'new_password': password,
                                   'confirm_password': password if confirm is None else confirm, **extra})


# ---------------------------------------------------------------------------
# 市民 パスワード再設定 1 つ目：/reset-password（メールアドレス）
# ---------------------------------------------------------------------------

def test_reset_password_page_asks_only_for_email(client):
    # Act
    response = client.get('/reset-password')
    html = response.get_data(as_text=True)

    # Assert
    assert response.status_code == 200
    assert 'name="email"' in html and 'name="csrf_token"' in html
    assert 'name="new_password"' not in html and 'name="confirm_password"' not in html


def test_reset_password_citizen_with_multi_address_login_id_is_neutral_and_token_removed(app, client):
    # Arrange: 複数アドレス形式の login_id を持つ市民（DB へ直接作成）。送信は InvalidRecipient で失敗する（モックなし）
    odd = _make_user('a@x.com,b@y.com', '変な 市民', User.ROLE_CITIZEN)

    # Act
    response = client.post('/reset-password', data={'email': 'a@x.com,b@y.com'})

    # Assert: 500 にならず中立メッセージ、作成したトークンは消える
    assert response.status_code == 302
    assert _flashes(client) == [('success', MSG_RESET_MAIL_SENT)]
    assert PasswordResetToken.query.filter_by(user_id=odd.id).count() == 0


def test_reset_password_registered_email_sends_mail_and_shows_neutral_message(client, citizen, outbox):
    # Act
    response = client.post('/reset-password', data={'email': 'taro@example.com'})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'] == '/reset-password'
    assert _flashes(client) == [('success', MSG_RESET_MAIL_SENT)]
    outbox.assert_called_once()
    assert outbox.call_args.args[0] == 'taro@example.com'
    assert PasswordResetToken.query.filter_by(user_id=citizen.id).count() == 1


def test_reset_password_unknown_email_looks_identical_and_sends_nothing(client, citizen, outbox):
    # Act
    known = client.post('/reset-password', data={'email': 'taro@example.com'})
    known_flashes = _flashes(client)
    unknown_client = client.application.test_client()
    unknown = unknown_client.post('/reset-password', data={'email': 'nobody@example.com'})

    # Assert: ステータス・移動先・メッセージが同じ（登録の有無が分からない）
    assert (unknown.status_code, unknown.headers['Location']) == (known.status_code, known.headers['Location'])
    assert _flashes(unknown_client) == known_flashes == [('success', MSG_RESET_MAIL_SENT)]
    assert outbox.call_count == 1
    assert PasswordResetToken.query.count() == 1


def test_reset_password_throttled_request_looks_identical(client, citizen, clock, outbox):
    # Arrange
    client.post('/reset-password', data={'email': 'taro@example.com'})
    other = client.application.test_client()

    # Act: 60 秒以内の 2 回目
    response = other.post('/reset-password', data={'email': 'taro@example.com'})

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password'
    assert _flashes(other) == [('success', MSG_RESET_MAIL_SENT)]
    assert outbox.call_count == 1


@pytest.mark.parametrize('email, message', [
    ('', MSG_EMAIL_REQUIRED),
    ('   ', MSG_EMAIL_REQUIRED),
    ('taro.example.com', MSG_EMAIL_NO_AT.format(email='taro.example.com')),
])
def test_reset_password_input_error_shows_message(client, citizen, outbox, email, message):
    # Act
    response = client.post('/reset-password', data={'email': email})

    # Assert: 画面を表示し直し、メッセージを出す。メールは送らない
    assert response.status_code == 200
    assert message in _text(response)
    outbox.assert_not_called()


def test_reset_password_mail_failure_still_shows_neutral_message(client, citizen, outbox):
    # Arrange
    outbox.side_effect = MailError('SMTPException')

    # Act
    response = client.post('/reset-password', data={'email': 'taro@example.com'})

    # Assert: 失敗してもエラー画面にならず、同じメッセージ。トークンは残らない
    assert response.status_code == 302
    assert _flashes(client) == [('success', MSG_RESET_MAIL_SENT)]
    assert PasswordResetToken.query.count() == 0


def test_reset_link_uses_public_base_url_not_request_host(client, citizen, outbox):
    # Act: Host や X-Forwarded-Host を偽装して送る
    client.post('/reset-password', data={'email': 'taro@example.com'},
                headers={'Host': 'evil.example', 'X-Forwarded-Host': 'evil.example'})

    # Assert
    body = outbox.call_args.args[2]
    assert 'http://localhost:5000/reset-password/confirm/' in body
    assert 'evil.example' not in body


def test_reset_password_without_csrf_token_is_rejected(csrf_client, citizen, outbox):
    # Act
    response = csrf_client.post('/reset-password', data={'email': 'taro@example.com'},
                                headers={'Referer': 'http://localhost/reset-password'})

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    outbox.assert_not_called()
    assert PasswordResetToken.query.count() == 0


def test_reset_password_with_csrf_token_works(csrf_client, citizen, outbox):
    # Arrange
    token = get_csrf_token(csrf_client, '/reset-password')

    # Act
    response = csrf_client.post('/reset-password', data={'email': 'taro@example.com', 'csrf_token': token})

    # Assert
    assert response.status_code == 302 and _flashes(csrf_client) == [('success', MSG_RESET_MAIL_SENT)]
    outbox.assert_called_once()


# ---------------------------------------------------------------------------
# 市民 パスワード再設定 2 つ目：/reset-password/confirm/<token>
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_link(citizen, clock, outbox):
    """メールで届いたリンクの URL とトークン（市民 taro。service で送り、クライアントのセッションには触れない）"""
    auth_service.request_password_reset('taro@example.com')
    raw = sent_reset_token(outbox)
    return raw, f'/reset-password/confirm/{raw}'


def test_confirm_page_with_valid_token_shows_form_with_privacy_headers(app, reset_link):
    # Arrange
    raw, url = reset_link

    # Act
    response = app.test_client().get(url)
    html = response.get_data(as_text=True)

    # Assert
    assert response.status_code == 200
    assert 'name="new_password"' in html and 'name="confirm_password"' in html
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('token', ['unknown-token', 'A' * 43, '0'])
def test_confirm_page_with_unknown_token_redirects_with_message(client, citizen, token):
    # Act
    response = client.get(f'/reset-password/confirm/{token}')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password'
    assert _flashes(client) == [('danger', MSG_RESET_LINK_INVALID)]
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['Cache-Control'] == 'no-store'


def test_confirm_page_with_expired_token_redirects(client, reset_link, clock):
    # Arrange
    _, url = reset_link
    clock.advance(minutes=30)

    # Act
    response = client.get(url)

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password'
    assert _flashes(client) == [('danger', MSG_RESET_LINK_INVALID)]


def test_confirm_post_sets_new_password_and_user_can_log_in(client, reset_link, citizen, login):
    # Arrange
    raw, url = reset_link

    # Act
    response = client.post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW})

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password/complete'
    assert response.headers['Cache-Control'] == 'no-store'
    assert _password_of(citizen.id).check_password(NEW_PW)
    assert client.get('/reset-password/complete').status_code == 200
    # 新しいパスワードでログインでき、古いパスワードでは入れない
    other = client.application.test_client()
    assert login(other, User.ROLE_CITIZEN, 'taro@example.com', NEW_PW).status_code == 302
    assert not _logged_in(login(client.application.test_client(), User.ROLE_CITIZEN, 'taro@example.com', 'password123'))


def test_confirm_post_link_cannot_be_used_twice(client, reset_link, citizen):
    # Arrange
    raw, url = reset_link
    client.post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW})
    second_client = client.application.test_client()

    # Act
    response = second_client.post(url, data={'new_password': 'Another-pw-1', 'confirm_password': 'Another-pw-1'})

    # Assert: リンク無効 → 1 つ目の画面へ
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password'
    assert _flashes(second_client) == [('danger', MSG_RESET_LINK_INVALID)]
    assert _password_of(citizen.id).check_password(NEW_PW)
    # 使用済みのリンクを開き直しても同じ
    assert second_client.get(url).status_code == 302


@pytest.mark.parametrize('new, confirm, message', [
    (NEW_PW, 'different-pw-1', MSG_PASSWORD_MISMATCH),
    ('short', 'short', MSG_PASSWORD_TOO_SHORT.format(length=5)),
    ('', '', None),
    ('password123', 'password123', MSG_PASSWORD_PUBLIC),
])
def test_confirm_post_input_error_keeps_link_usable(client, reset_link, citizen, new, confirm, message):
    # Arrange
    raw, url = reset_link

    # Act
    response = client.post(url, data={'new_password': new, 'confirm_password': confirm})

    # Assert: 同じリンクの画面へ戻る。リンクは使い切られず、パスワードも変わらない
    assert response.status_code == 302 and response.headers['Location'] == url
    flashes = _flashes(client)
    assert flashes and all(category == 'warning' for category, _ in flashes)
    if message:
        assert ('warning', message) in flashes
    assert _password_of(citizen.id).check_password('password123')
    assert client.get(url).status_code == 200
    retry = client.post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW})
    assert retry.headers['Location'] == '/reset-password/complete'


def test_confirm_post_unlocks_account(client, reset_link, citizen, clock, login):
    # Arrange: 5 回失敗してロック中
    other = client.application.test_client()
    for _ in range(5):
        login(other, User.ROLE_CITIZEN, 'taro@example.com', 'wrong-password')
    assert _password_of(citizen.id).locked_until is not None
    raw, url = reset_link

    # Act
    client.post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW})

    # Assert
    user = _password_of(citizen.id)
    assert user.locked_until is None and user.failed_login_count == 0
    assert _logged_in(login(client.application.test_client(), User.ROLE_CITIZEN, 'taro@example.com', NEW_PW))


def test_confirm_post_logs_out_existing_session(app, citizen, reset_link, login):
    # Arrange: 別の端末で既にログイン中
    old_device = app.test_client()
    login(old_device, User.ROLE_CITIZEN, 'taro@example.com')
    assert _status(old_device, '/citizen/mypage') == 200
    raw, url = reset_link

    # Act
    app.test_client().post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW})

    # Assert: 古いセッションはログイン画面へ戻される
    assert _status(old_device, '/citizen/mypage') == 302


def test_confirm_post_without_csrf_token_is_rejected(csrf_client, reset_link, citizen):
    # Arrange
    raw, url = reset_link

    # Act
    response = csrf_client.post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW},
                                headers={'Referer': f'http://localhost{url}'})

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert _password_of(citizen.id).check_password('password123')
    assert auth_service.is_reset_token_valid(raw) is True


def test_confirm_form_with_csrf_token_works(csrf_client, reset_link, citizen):
    # Arrange
    raw, url = reset_link
    token = get_csrf_token(csrf_client, url)

    # Act
    response = csrf_client.post(url, data={'new_password': NEW_PW, 'confirm_password': NEW_PW, 'csrf_token': token})

    # Assert
    assert response.headers['Location'] == '/reset-password/complete'
    assert _password_of(citizen.id).check_password(NEW_PW)


def test_confirm_page_rejects_staff_reset_code_as_token(client, staff):
    # Arrange
    code = _issue_code('staff01')

    # Act
    response = client.get(f'/reset-password/confirm/{code}')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/reset-password'


# ---------------------------------------------------------------------------
# 職員・管理者 パスワード再設定：/login/staff/reset-password、/login/admin/reset-password
# ---------------------------------------------------------------------------

STAFF_PAGE = '/login/staff/reset-password'
ADMIN_PAGE = '/login/admin/reset-password'


@pytest.mark.parametrize('path', [STAFF_PAGE, ADMIN_PAGE])
def test_reset_code_pages_show_design_form_plus_code_field(client, path):
    # Act
    response = client.get(path)
    html = response.get_data(as_text=True)

    # Assert
    assert response.status_code == 200
    for field in ('login_id', 'reset_code', 'new_password', 'confirm_password', 'csrf_token'):
        assert f'name="{field}"' in html


@pytest.mark.parametrize('role, login_id, path, complete', [
    (User.ROLE_STAFF, 'staff01', STAFF_PAGE, '/login/staff/reset-password/complete'),
    (User.ROLE_ADMIN, 'admin01', ADMIN_PAGE, '/login/admin/reset-password/complete'),
])
def test_reset_with_code_success_redirects_and_user_can_log_in(client, staff, admin, login, role, login_id, path,
                                                               complete):
    # Arrange
    code = _issue_code(login_id)

    # Act
    response = _reset_staff(client, login_id, code, path=path)

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == complete
    assert client.get(complete).status_code == 200
    user = User.query.filter_by(login_id=login_id).one()
    assert _password_of(user.id).check_password(NEW_PW)
    other = client.application.test_client()
    assert _logged_in(login(other, role, login_id, NEW_PW))
    assert not _logged_in(login(client.application.test_client(), role, login_id, 'password123'))


def test_reset_with_code_accepts_lowercase_code_without_hyphen(client, staff):
    # Arrange
    code = _issue_code('staff01')

    # Act
    response = _reset_staff(client, 'staff01', code.replace('-', '').lower())

    # Assert
    assert response.status_code == 302


def test_reset_with_code_wrong_code_shows_generic_message_and_keeps_id_only(client, staff):
    # Arrange
    _issue_code('staff01')

    # Act
    response = _reset_staff(client, 'staff01', 'ZZZZZ-ZZZZZ')
    html = response.get_data(as_text=True)

    # Assert: 一文のメッセージ。入力した ID は残り、コード・パスワードは戻さない
    assert response.status_code == 200
    assert MSG_RESET_CODE_INVALID in html
    assert 'value="staff01"' in html
    assert 'ZZZZZ-ZZZZZ' not in html and NEW_PW not in html
    assert _password_of(staff.id).check_password('password123')


def test_reset_with_code_unknown_id_shows_same_message_as_wrong_code(client, staff):
    # Arrange
    _issue_code('staff01')

    # Act
    unknown = _reset_staff(client, 'nobody', 'ZZZZZ-ZZZZZ').get_data(as_text=True)
    wrong = _reset_staff(client, 'staff01', 'ZZZZZ-ZZZZZ').get_data(as_text=True)

    # Assert
    assert MSG_RESET_CODE_INVALID in unknown and MSG_RESET_CODE_INVALID in wrong


def test_staff_page_cannot_reset_admin_and_admin_page_cannot_reset_staff(client, staff, admin):
    # Arrange
    staff_code = _issue_code('staff01')
    admin_code = _issue_code('admin01')

    # Act: 役割違いの組み合わせ
    admin_via_staff_page = _reset_staff(client, 'admin01', admin_code, path=STAFF_PAGE)
    staff_via_admin_page = _reset_staff(client, 'staff01', staff_code, path=ADMIN_PAGE)

    # Assert
    for response in (admin_via_staff_page, staff_via_admin_page):
        assert response.status_code == 200 and MSG_RESET_CODE_INVALID in response.get_data(as_text=True)
    assert _password_of(staff.id).check_password('password123')
    assert _password_of(admin.id).check_password('password123')


def test_reset_with_code_citizen_id_is_rejected(client, citizen):
    # Act
    response = _reset_staff(client, 'taro@example.com', 'ABCDE-FGHJK')

    # Assert: ID に @ は使えない
    assert response.status_code == 200
    assert MSG_ID_HAS_AT.format(label='職員ID') in _text(response)
    assert _password_of(citizen.id).check_password('password123')


@pytest.mark.parametrize('login_id, code, new, confirm, message', [
    ('', 'ABCDE-FGHJK', NEW_PW, NEW_PW, '職員IDを入力してください。'),
    ('staff01', '', NEW_PW, NEW_PW, MSG_RESET_CODE_REQUIRED),
    ('staff01', 'ABCDE-FGHJK', 'short', 'short', MSG_PASSWORD_TOO_SHORT.format(length=5)),
    ('staff01', 'ABCDE-FGHJK', NEW_PW, 'different-pw-1', MSG_PASSWORD_MISMATCH),
    ('staff01', 'ABCDE-FGHJK', 'password123', 'password123', MSG_PASSWORD_PUBLIC),
])
def test_reset_with_code_input_errors_show_messages(client, staff, login_id, code, new, confirm, message):
    # Arrange
    real = _issue_code('staff01')

    # Act
    response = _reset_staff(client, login_id, code, new, confirm)

    # Assert: メッセージを出して画面を表示し直す。入力ミスではコードを消費しない
    assert response.status_code == 200 and message in _text(response)
    assert _password_of(staff.id).check_password('password123')
    assert _reset_staff(client.application.test_client(), 'staff01', real).status_code == 302


def test_reset_with_code_locks_after_5_wrong_codes(client, staff, login):
    # Arrange
    code = _issue_code('staff01')

    # Act
    for _ in range(5):
        response = _reset_staff(client, 'staff01', 'ZZZZZ-ZZZZZ')

    # Assert: 5 回目でロックのメッセージ。正しいコードもログインも通らない
    assert MSG_LOGIN_LOCKED.format(minutes=15) in response.get_data(as_text=True)
    locked = _reset_staff(client, 'staff01', code)
    assert locked.status_code == 200 and MSG_LOGIN_LOCKED.format(minutes=15) in locked.get_data(as_text=True)
    assert not _logged_in(login(client.application.test_client(), User.ROLE_STAFF, 'staff01', 'password123'))


def test_reset_with_code_logs_out_existing_session(app, staff, login):
    # Arrange
    old_device = app.test_client()
    login(old_device, User.ROLE_STAFF, 'staff01')
    assert _status(old_device, '/staff/dashboard') == 200
    code = _issue_code('staff01')

    # Act
    _reset_staff(app.test_client(), 'staff01', code)

    # Assert
    assert _status(old_device, '/staff/dashboard') == 302


def test_reset_with_code_reuse_is_rejected(client, staff):
    # Arrange
    code = _issue_code('staff01')
    _reset_staff(client, 'staff01', code)

    # Act
    response = _reset_staff(client.application.test_client(), 'staff01', code, password='Another-pw-1')

    # Assert
    assert response.status_code == 200 and MSG_RESET_CODE_INVALID in response.get_data(as_text=True)
    assert _password_of(staff.id).check_password(NEW_PW)


@pytest.mark.parametrize('path', [STAFF_PAGE, ADMIN_PAGE])
def test_reset_with_code_without_csrf_token_is_rejected(csrf_client, staff, admin, path):
    # Arrange
    login_id = 'staff01' if path == STAFF_PAGE else 'admin01'
    code = _issue_code(login_id)

    # Act
    response = _reset_staff(csrf_client, login_id, code, path=path)

    # Assert
    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    user = User.query.filter_by(login_id=login_id).one()
    assert _password_of(user.id).check_password('password123')


def test_reset_with_code_with_csrf_token_works(csrf_client, staff):
    # Arrange
    code = _issue_code('staff01')
    token = get_csrf_token(csrf_client, STAFF_PAGE)

    # Act
    response = _reset_staff(csrf_client, 'staff01', code, csrf_token=token)

    # Assert
    assert response.status_code == 302 and _password_of(staff.id).check_password(NEW_PW)


# ---------------------------------------------------------------------------
# 市民 パスワード変更：/citizen/password
# ---------------------------------------------------------------------------

@pytest.fixture
def citizen_client(client, citizen, login):
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    return client


def _change(client, current='password123', new=NEW_PW, confirm=None, **headers):
    return client.post('/citizen/password', data={
        'current_password': current, 'new_password': new, 'confirm_password': new if confirm is None else confirm},
        headers=headers)


def test_change_password_requires_login(client, citizen):
    # Act
    get_response = client.get('/citizen/password')
    post_response = _change(client)

    # Assert
    assert get_response.status_code == 302 and '/login/citizen' in get_response.headers['Location']
    assert post_response.status_code == 302 and '/login/citizen' in post_response.headers['Location']
    assert _password_of(citizen.id).check_password('password123')


@pytest.mark.parametrize('role', [User.ROLE_STAFF, User.ROLE_ADMIN])
def test_change_password_forbidden_for_staff_and_admin(client, staff, admin, login, role):
    # Arrange
    login(client, role, 'staff01' if role == User.ROLE_STAFF else 'admin01')

    # Act / Assert
    assert client.get('/citizen/password').status_code == 403
    assert _change(client).status_code == 403
    for user in (staff, admin):
        assert _password_of(user.id).check_password('password123')


def test_change_password_page_has_form_and_logout_modal(citizen_client):
    # Act
    response = citizen_client.get('/citizen/password')
    html = response.get_data(as_text=True)

    # Assert
    assert response.status_code == 200
    for field in ('current_password', 'new_password', 'confirm_password', 'csrf_token'):
        assert f'name="{field}"' in html
    assert 'action="/logout"' in html


def test_profile_password_button_points_to_change_password_page(citizen_client):
    # Act
    html = citizen_client.get('/citizen/profile').get_data(as_text=True)

    # Assert: 「パスワードを変更する」は /citizen/password へ（quên mật khẩu ではない）
    assert re.search(r'href="/citizen/password"[^>]*>パスワードを変更する', html)
    assert not re.search(r'href="[^"]*reset-password[^"]*"[^>]*>パスワードを変更する', html)


def test_change_password_success_redirects_to_profile_and_updates_db(citizen_client, citizen):
    # Act
    response = _change(citizen_client)

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/citizen/profile'
    assert _password_of(citizen.id).check_password(NEW_PW)
    assert ('success', MSG_PASSWORD_CHANGED) in _flashes(citizen_client)


def test_change_password_keeps_current_session_logged_in(citizen_client, citizen):
    # Act
    _change(citizen_client)

    # Assert: 操作したセッションはそのままログイン中
    assert _status(citizen_client, '/citizen/profile') == 200
    assert _status(citizen_client, '/citizen/mypage') == 200


def test_change_password_logs_out_other_sessions(app, citizen_client, citizen, login):
    # Arrange: 別の端末でもログイン中
    other_device = app.test_client()
    login(other_device, User.ROLE_CITIZEN, 'taro@example.com')
    assert _status(other_device, '/citizen/mypage') == 200

    # Act
    _change(citizen_client)

    # Assert: 別の端末は入れず、操作した端末は入れる
    assert _status(other_device, '/citizen/mypage') == 302
    assert _status(citizen_client, '/citizen/mypage') == 200


def test_change_password_invalidates_old_remember_cookie_and_reissues_new_one(app, citizen):
    # Arrange: 「ログイン状態を保持する」でログインした端末（Cookie の値を控える）
    device = app.test_client()
    login_response = device.post('/login/citizen', data={'email': 'taro@example.com', 'password': 'password123',
                                                         'remember': 'on'})
    old_cookie = next(v for v in login_response.headers.getlist('Set-Cookie') if v.startswith('remember_token='))
    old_value = old_cookie.split(';')[0].split('=', 1)[1]

    # Act
    response = _change(device)

    # Assert: 新しい Cookie が発行される。古い Cookie だけを持つ端末は入れない
    new_cookies = [v for v in response.headers.getlist('Set-Cookie') if v.startswith('remember_token=')]
    assert new_cookies and new_cookies[0].split(';')[0].split('=', 1)[1] != old_value
    stale = app.test_client()
    stale.set_cookie('remember_token', old_value)
    assert _status(stale, '/citizen/mypage') == 302


def test_change_password_wrong_current_counts_failure(citizen_client, citizen):
    # Act
    response = _change(citizen_client, current='wrong-password')

    # Assert: 同じ画面へ redirect。パスワードは変わらず、失敗が 1 回数えられる
    assert response.status_code == 302 and response.headers['Location'] == '/citizen/password'
    assert _flashes(citizen_client) == [('danger', MSG_CURRENT_PASSWORD_WRONG)]
    user = _password_of(citizen.id)
    assert user.check_password('password123') and user.failed_login_count == 1


def test_change_password_locks_after_5_wrong_current_passwords(citizen_client, citizen, clock):
    # Act
    for _ in range(5):
        response = _change(citizen_client, current='wrong-password')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/citizen/password'
    assert ('warning', MSG_LOGIN_LOCKED.format(minutes=15)) in _flashes(citizen_client)
    assert _password_of(citizen.id).locked_until is not None


@pytest.mark.parametrize('current, new, confirm, message', [
    ('password123', NEW_PW, 'different-pw-1', MSG_PASSWORD_MISMATCH),
    ('password123', 'short', 'short', MSG_PASSWORD_TOO_SHORT.format(length=5)),
    ('', NEW_PW, NEW_PW, '現在のパスワードを入力してください。'),
    ('password123', 'password123', 'password123', MSG_PASSWORD_PUBLIC),
])
def test_change_password_input_errors_redirect_with_flash(citizen_client, citizen, current, new, confirm, message):
    # Act
    response = _change(citizen_client, current=current, new=new, confirm=confirm)

    # Assert: 入力エラーでも必ず redirect（data-nav="replace" の form）。失敗回数には数えない
    assert response.status_code == 302 and response.headers['Location'] == '/citizen/password'
    assert ('warning', message) in _flashes(citizen_client)
    user = _password_of(citizen.id)
    assert user.check_password('password123') and user.failed_login_count == 0


def test_change_password_same_as_current_is_rejected(citizen_client, citizen):
    # Arrange
    citizen.set_password('Current-pw-1')
    db.session.commit()
    citizen_client.application.test_client()
    other = citizen_client.application.test_client()
    other.post('/login/citizen', data={'email': 'taro@example.com', 'password': 'Current-pw-1'})

    # Act
    response = _change(other, current='Current-pw-1', new='Current-pw-1')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/citizen/password'
    assert ('warning', MSG_PASSWORD_SAME_AS_CURRENT) in _flashes(other)


def test_change_password_replace_history_header_returns_json_redirect(citizen_client):
    # Act: navigation.js の fetch 送信
    error = _change(citizen_client, current='wrong-password', **{'X-Replace-History': '1'})
    ok = _change(citizen_client, **{'X-Replace-History': '1'})

    # Assert
    assert error.get_json() == {'redirect': '/citizen/password'}
    assert ok.get_json() == {'redirect': '/citizen/profile'}


def test_change_password_without_csrf_token_is_rejected(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)

    # Act
    response = _change(csrf_client, **{'Referer': 'http://localhost/citizen/password'})

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/citizen/password'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert _password_of(citizen.id).check_password('password123')


def test_change_password_with_csrf_token_works(csrf_client, csrf_login, citizen):
    # Arrange
    csrf_login(csrf_client, User.ROLE_CITIZEN, citizen.login_id)
    token = get_csrf_token(csrf_client, '/citizen/password')

    # Act
    response = csrf_client.post('/citizen/password', data={
        'current_password': 'password123', 'new_password': NEW_PW, 'confirm_password': NEW_PW, 'csrf_token': token})

    # Assert
    assert response.headers['Location'] == '/citizen/profile'
    assert _password_of(citizen.id).check_password(NEW_PW)


# ---------------------------------------------------------------------------
# 管理者 アカウント登録：/admin/users/register
# ---------------------------------------------------------------------------

REGISTER = '/admin/users/register'


@pytest.fixture
def admin_client(client, admin, login):
    login(client, User.ROLE_ADMIN, admin.login_id)
    return client


def _register(client, *, name='田中 次郎', login_id='staff10', department='', role='staff', password='Secret-pw1',
              confirm=None, **headers):
    data = {'staff_name': name, 'login_id': login_id, 'role': role, 'password': password,
            'password_confirm': password if confirm is None else confirm}
    if department != '':
        data['department'] = str(department)
    return client.post(REGISTER, data=data, headers=headers)


def test_register_requires_login(client):
    # Act
    get_response = client.get(REGISTER)
    post_response = _register(client, department=1)

    # Assert
    assert get_response.status_code == 302 and '/login/admin' in get_response.headers['Location']
    assert post_response.status_code == 302 and '/login/admin' in post_response.headers['Location']
    assert User.query.filter_by(login_id='staff10').count() == 0


@pytest.mark.parametrize('role', [User.ROLE_CITIZEN, User.ROLE_STAFF])
def test_register_forbidden_for_citizen_and_staff(client, citizen, staff, department, login, role):
    # Arrange
    login(client, role, citizen.login_id if role == User.ROLE_CITIZEN else staff.login_id)

    # Act / Assert
    assert client.get(REGISTER).status_code == 403
    assert _register(client, department=department.id).status_code == 403
    assert User.query.filter_by(login_id='staff10').count() == 0


def test_register_page_lists_departments_from_db(admin_client, department):
    # Act
    html = admin_client.get(REGISTER).get_data(as_text=True)

    # Assert: value は ID、表示は名前
    assert re.search(rf'<option value="{department.id}"[^>]*>道路管理課</option>', html)
    for field in ('staff_name', 'login_id', 'department', 'role', 'password', 'password_confirm', 'csrf_token'):
        assert f'name="{field}"' in html


def test_register_staff_saves_account_and_redirects_to_complete(admin_client, department):
    # Act
    response = _register(admin_client, department=department.id)

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/admin/users/register/complete'
    user = User.query.filter_by(login_id='staff10').one()
    assert user.role == User.ROLE_STAFF and user.name == '田中 次郎' and user.department_id == department.id
    assert user.check_password('Secret-pw1') and user.is_active
    assert admin_client.get('/admin/users/register/complete').status_code == 200


def test_register_staff_new_account_can_log_in(admin_client, department, login):
    # Arrange
    _register(admin_client, department=department.id)

    # Act
    response = login(admin_client.application.test_client(), User.ROLE_STAFF, 'staff10', 'Secret-pw1')

    # Assert
    assert response.status_code == 302 and response.headers['Location'].endswith('/staff/dashboard')


def test_register_admin_without_department_is_valid(admin_client, login):
    # Act: role=admin のときは department を送らない
    response = _register(admin_client, login_id='root2', role='admin')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/admin/users/register/complete'
    user = User.query.filter_by(login_id='root2').one()
    assert user.role == User.ROLE_ADMIN and user.department_id is None
    assert login(admin_client.application.test_client(), User.ROLE_ADMIN, 'root2', 'Secret-pw1').status_code == 302


def test_register_admin_ignores_posted_department(admin_client, department):
    # Act: 管理者に部署が送られてきても保存しない
    _register(admin_client, login_id='root2', role='admin', department=department.id)

    # Assert
    assert User.query.filter_by(login_id='root2').one().department_id is None


@pytest.mark.parametrize('role', ['', 'citizen', 'superuser', 'STAFF'])
def test_register_rejects_missing_or_forged_role(admin_client, department, role):
    # Act
    response = _register(admin_client, department=department.id, role=role)

    # Assert: 市民など許可していない役割は作れない
    assert response.status_code == 302 and response.headers['Location'] == '/admin/users/register'
    assert ('warning', MSG_ROLE_REQUIRED) in _flashes(admin_client)
    assert User.query.filter_by(login_id='staff10').count() == 0


@pytest.mark.parametrize('kwargs, message', [
    ({'department': ''}, MSG_DEPARTMENT_REQUIRED),
    ({'department': 'abc'}, MSG_DEPARTMENT_INVALID),
    ({'department': '999999'}, MSG_DEPARTMENT_INVALID),
    ({'login_id': ''}, '職員IDを入力してください。'),
    ({'login_id': 'a@b.example'}, MSG_ID_HAS_AT.format(label='職員ID')),
    ({'name': ''}, MSG_NAME_REQUIRED),
    ({'password': 'short'}, MSG_PASSWORD_TOO_SHORT.format(length=5)),
    ({'confirm': 'different-pw-1'}, MSG_PASSWORD_MISMATCH),
    ({'password': 'password123'}, MSG_PASSWORD_PUBLIC),
])
def test_register_staff_errors_redirect_with_flash(admin_client, department, kwargs, message):
    # Arrange
    values = {'department': department.id, **kwargs}

    # Act
    response = _register(admin_client, **values)

    # Assert: 入力エラーでも必ず redirect（200 で描画しない）。アカウントは作られない
    assert response.status_code == 302 and response.headers['Location'] == '/admin/users/register'
    assert ('warning', message) in _flashes(admin_client)
    assert User.query.filter_by(login_id=values.get('login_id', 'staff10')).count() == 0


def test_register_duplicate_login_id_is_rejected(admin_client, department, staff):
    # Act
    response = _register(admin_client, department=department.id, login_id='staff01')

    # Assert
    assert response.headers['Location'] == '/admin/users/register'
    assert ('warning', MSG_ID_TAKEN.format(label='職員ID')) in _flashes(admin_client)
    assert User.query.filter_by(login_id='staff01').count() == 1


def test_register_error_keeps_input_except_passwords(admin_client, department):
    # Arrange: 名前・ID・部署・役割は正しいが、パスワードが短い
    _register(admin_client, name='田中 次郎', login_id='staff10', department=department.id, password='short')

    # Act
    html = admin_client.get(REGISTER).get_data(as_text=True)

    # Assert: 入力が残り、パスワードは戻さない
    assert 'value="田中 次郎"' in html and 'value="staff10"' in html
    assert re.search(rf'<option value="{department.id}"\s+selected', html)
    assert re.search(r'<option value="staff"\s+selected', html)
    assert 'short' not in html.replace('placeholder', '')
    assert not re.search(r'name="password"[^>]*value="', html)


def test_register_kept_input_is_shown_only_once(admin_client, department):
    # Arrange
    _register(admin_client, login_id='staff10', department=department.id, password='short')
    admin_client.get(REGISTER)

    # Act: 2 回目の表示では消えている
    html = admin_client.get(REGISTER).get_data(as_text=True)

    # Assert
    assert 'value="staff10"' not in html


def test_register_error_does_not_store_password_in_session(admin_client, department):
    # Act
    _register(admin_client, department=department.id, password='Secret-pw1', confirm='different-pw-1')

    # Assert
    with admin_client.session_transaction() as session:
        assert 'Secret-pw1' not in str(dict(session)) and 'different-pw-1' not in str(dict(session))


def test_register_success_does_not_leave_form_in_session(admin_client, department):
    # Act
    _register(admin_client, department=department.id)

    # Assert
    with admin_client.session_transaction() as session:
        assert 'user_form' not in session


def test_register_replace_history_header_returns_json_redirect(admin_client, department):
    # Act
    error = _register(admin_client, department='', **{'X-Replace-History': '1'})
    ok = _register(admin_client, department=department.id, **{'X-Replace-History': '1'})

    # Assert
    assert error.get_json() == {'redirect': '/admin/users/register'}
    assert ok.get_json() == {'redirect': '/admin/users/register/complete'}


def test_register_without_csrf_token_is_rejected(csrf_client, csrf_login, admin, department):
    # Arrange
    csrf_login(csrf_client, User.ROLE_ADMIN, admin.login_id)

    # Act
    response = _register(csrf_client, department=department.id, Referer='http://localhost/admin/users/register')

    # Assert
    assert response.status_code == 302 and response.headers['Location'] == '/admin/users/register'
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    assert User.query.filter_by(login_id='staff10').count() == 0


def test_register_with_csrf_token_works(csrf_client, csrf_login, admin, department):
    # Arrange
    csrf_login(csrf_client, User.ROLE_ADMIN, admin.login_id)
    token = get_csrf_token(csrf_client, REGISTER)

    # Act
    response = csrf_client.post(REGISTER, data={
        'staff_name': '田中', 'login_id': 'staff10', 'department': str(department.id), 'role': 'staff',
        'password': 'Secret-pw1', 'password_confirm': 'Secret-pw1', 'csrf_token': token})

    # Assert
    assert response.headers['Location'] == '/admin/users/register/complete'
    assert User.query.filter_by(login_id='staff10').count() == 1


def test_register_complete_requires_admin(client, citizen, login):
    # Act / Assert
    assert client.get('/admin/users/register/complete').status_code == 302
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    assert client.get('/admin/users/register/complete').status_code == 403


def test_register_does_not_create_citizen_even_with_email_like_id(admin_client, department):
    # Act
    _register(admin_client, login_id='someone@example.com', department=department.id)

    # Assert
    assert User.query.filter_by(login_id='someone@example.com').count() == 0
    assert _make_user('ok-id', '確認', User.ROLE_STAFF)
