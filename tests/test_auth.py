# ログイン・ログアウト・ロール別アクセス制御のテスト（GĐ2a：auth_service.authenticate、セッションの作り直し、市民登録のメールチェック）
import html as html_lib
import re
import pytest

from app.constants import (
    MSG_EMAIL_NO_AT, MSG_ID_HAS_AT, MSG_ID_OR_PASSWORD_REQUIRED, MSG_ID_REQUIRED, MSG_LOGIN_FAILED,
    MSG_PASSWORD_REQUIRED, MSG_PASSWORD_TOO_SHORT,
)
from app.extensions import db
from app.models import User
from app.services import ServiceError, ValidationError, auth_service
from tests.conftest import _make_user

LOGIN_IDS = {User.ROLE_CITIZEN: 'taro@example.com', User.ROLE_STAFF: 'staff01', User.ROLE_ADMIN: 'admin01'}
ID_FIELDS = {User.ROLE_CITIZEN: 'email', User.ROLE_STAFF: 'staff_id', User.ROLE_ADMIN: 'admin_id'}
LABELS = {User.ROLE_CITIZEN: 'メールアドレス', User.ROLE_STAFF: '職員ID', User.ROLE_ADMIN: '管理者ID'}
HOME_URLS = {User.ROLE_CITIZEN: '/citizen/mypage', User.ROLE_STAFF: '/staff/dashboard', User.ROLE_ADMIN: '/admin/portal'}
ALL_ROLES = [User.ROLE_CITIZEN, User.ROLE_STAFF, User.ROLE_ADMIN]


def _alerts(page):
    """画面の alert を [(カテゴリ, メッセージ)] で返す"""
    return [(category, html_lib.unescape(text.strip()))
            for category, text in re.findall(r'class="alert alert-(\w+)[^"]*"[^>]*>\s*<i[^>]*></i>([^<]*)', page)]


def _messages(excinfo):
    return excinfo.value.messages


# ---------------------------------------------------------------------------
# authenticate：成功・ID の正規化
# ---------------------------------------------------------------------------

def test_login_id_labels_per_role():
    # Assert: メッセージの {label} に入る名前
    assert auth_service.LOGIN_ID_LABELS == {
        User.ROLE_CITIZEN: 'メールアドレス', User.ROLE_STAFF: '職員ID', User.ROLE_ADMIN: '管理者ID'}


@pytest.mark.parametrize('role', ALL_ROLES)
def test_authenticate_returns_user_and_records_last_login(app, role):
    # Arrange
    account = _make_user(LOGIN_IDS[role], 'テスト 太郎', role)
    assert account.last_login_at is None

    # Act
    result = auth_service.authenticate(role, LOGIN_IDS[role], 'password123')

    # Assert
    assert result.id == account.id
    db.session.refresh(account)
    assert account.last_login_at is not None


def test_authenticate_citizen_trims_and_lowercases_email(citizen):
    # Act: 前後の空白と大文字は無視する（メールアドレスは小文字で保存）
    result = auth_service.authenticate(User.ROLE_CITIZEN, '  Taro@Example.COM ', 'password123')

    # Assert
    assert result.id == citizen.id


def test_authenticate_staff_trims_id_but_keeps_case(staff):
    # Act / Assert: 職員 ID は空白だけ除く
    assert auth_service.authenticate(User.ROLE_STAFF, ' staff01 ', 'password123').id == staff.id
    with pytest.raises(ServiceError):
        auth_service.authenticate(User.ROLE_STAFF, 'STAFF01', 'password123')


def test_authenticate_rejects_account_of_other_role(staff):
    # Act / Assert: 職員の ID を市民・管理者のログインに入れても入れない（ロールが違う）
    for role in (User.ROLE_CITIZEN, User.ROLE_ADMIN):
        with pytest.raises(ServiceError) as excinfo:
            auth_service.authenticate(role, staff.login_id, 'password123')
        assert str(excinfo.value) == MSG_LOGIN_FAILED.format(label=LABELS[role])


def test_authenticate_wrong_password_message_uses_role_label(staff):
    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate(User.ROLE_STAFF, 'staff01', 'wrong-password')

    # Assert
    assert str(excinfo.value) == '職員IDまたはパスワードが正しくありません'


# ---------------------------------------------------------------------------
# authenticate：入力エラー（メッセージとカテゴリは Excel どおり）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('role', ALL_ROLES)
def test_authenticate_both_empty_is_danger(role):
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(role, '', '')

    # Assert: 全て未入力は赤
    assert _messages(excinfo) == [('danger', MSG_ID_OR_PASSWORD_REQUIRED.format(label=LABELS[role]))]


def test_authenticate_whitespace_id_and_empty_password_is_both_empty():
    # Act: ID が空白だけ・パスワード空 → 全て未入力と同じ扱い
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_STAFF, '   ', '')

    # Assert
    assert _messages(excinfo) == [('danger', MSG_ID_OR_PASSWORD_REQUIRED.format(label='職員ID'))]


def test_authenticate_none_values_are_treated_as_empty():
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, None, None)

    # Assert
    assert _messages(excinfo)[0][0] == 'danger'


def test_authenticate_id_required_is_warning():
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_ADMIN, '', 'password123')

    # Assert
    assert _messages(excinfo) == [('warning', MSG_ID_REQUIRED.format(label='管理者ID'))]


def test_authenticate_password_required_is_warning():
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', '')

    # Assert
    assert _messages(excinfo) == [('warning', MSG_PASSWORD_REQUIRED)]


def test_authenticate_short_password_is_warning_with_length():
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', 'abc')

    # Assert
    assert _messages(excinfo) == [('warning', MSG_PASSWORD_TOO_SHORT.format(length=3))]


@pytest.mark.parametrize('role', [User.ROLE_STAFF, User.ROLE_ADMIN])
def test_authenticate_id_with_at_is_warning_for_staff_and_admin(role):
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(role, 'someone@example.com', 'password123')

    # Assert
    assert _messages(excinfo) == [('warning', MSG_ID_HAS_AT.format(label=LABELS[role]))]


def test_authenticate_collects_id_and_password_errors_together():
    # Act: ID 空 + パスワードが短い
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_STAFF, '', 'abc')

    # Assert: 両方が warning で並ぶ
    assert _messages(excinfo) == [
        ('warning', MSG_ID_REQUIRED.format(label='職員ID')),
        ('warning', MSG_PASSWORD_TOO_SHORT.format(length=3)),
    ]


# ---------------------------------------------------------------------------
# ログイン画面（route）：メッセージのカテゴリ（色）
# ---------------------------------------------------------------------------

def test_login_page_wrong_password_is_danger_alert(client, citizen):
    # Act
    response = client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'wrong-password'})

    # Assert
    assert response.status_code == 200
    assert ('danger', MSG_LOGIN_FAILED.format(label='メールアドレス')) in _alerts(response.get_data(as_text=True))


def test_login_page_input_errors_are_warning_alerts(client):
    # Act
    response = client.post('/login/staff', data={'staff_id': 'a@b', 'password': 'abc'})

    # Assert: 複数メッセージがそれぞれ warning で出る
    alerts = _alerts(response.get_data(as_text=True))
    assert ('warning', MSG_ID_HAS_AT.format(label='職員ID')) in alerts
    assert ('warning', MSG_PASSWORD_TOO_SHORT.format(length=3)) in alerts


def test_login_page_both_empty_is_danger_alert(client):
    # Act
    response = client.post('/login/admin', data={'admin_id': '', 'password': ''})

    # Assert
    assert ('danger', MSG_ID_OR_PASSWORD_REQUIRED.format(label='管理者ID')) in _alerts(response.get_data(as_text=True))


def test_login_page_disabled_account_is_danger_alert(client, citizen):
    # Arrange
    citizen.is_active = False
    db.session.commit()

    # Act
    response = client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'password123'})

    # Assert
    assert ('danger', 'このアカウントは利用停止されています') in _alerts(response.get_data(as_text=True))


@pytest.mark.parametrize('role', ALL_ROLES)
def test_login_success_redirects_to_role_home(client, app, role):
    # Arrange
    _make_user(LOGIN_IDS[role], 'テスト 太郎', role)

    # Act
    response = client.post(f'/login/{role}', data={ID_FIELDS[role]: LOGIN_IDS[role], 'password': 'password123'})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith(HOME_URLS[role])


# ---------------------------------------------------------------------------
# セッション：ログイン・ログアウトで前の内容を引き継がない
# ---------------------------------------------------------------------------

def test_session_draft_is_cleared_on_login(client, citizen, login):
    # Arrange: ログイン前のセッションに下書きがある
    with client.session_transaction() as session:
        session['report_draft'] = {'title': '前の人の下書き'}

    # Act
    response = login(client, User.ROLE_CITIZEN, citizen.login_id)

    # Assert: ログイン成功でセッションが作り直され、下書きは残らない（ログインはできている）
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert 'report_draft' not in session
        assert session['_user_id'] == str(citizen.id)


def test_session_draft_is_not_cleared_when_login_fails(client, citizen, login):
    # Arrange
    with client.session_transaction() as session:
        session['report_draft'] = {'title': '入力中'}

    # Act: パスワード違い
    login(client, User.ROLE_CITIZEN, citizen.login_id, password='wrong-password')

    # Assert: 失敗ではセッションに触らない
    with client.session_transaction() as session:
        assert session['report_draft'] == {'title': '入力中'}


def test_session_draft_is_cleared_on_logout(client, citizen, login):
    # Arrange
    login(client, User.ROLE_CITIZEN, citizen.login_id)
    with client.session_transaction() as session:
        session['report_draft'] = {'title': '残したくない下書き'}

    # Act
    response = client.post('/logout')

    # Assert
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert 'report_draft' not in session
        assert '_user_id' not in session


def test_logout_deletes_remember_cookie(client, citizen):
    # Arrange: 「ログイン状態を保持する」付きでログイン
    client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'password123', 'remember': 'on'})
    assert client.get_cookie('remember_token') is not None

    # Act
    response = client.post('/logout')

    # Assert: remember_token を消す Set-Cookie が返り、Cookie が残らない
    deleting = [value for value in response.headers.getlist('Set-Cookie') if value.startswith('remember_token=')]
    assert deleting and ('Expires=Thu, 01 Jan 1970' in deleting[0] or 'Max-Age=0' in deleting[0])
    assert client.get_cookie('remember_token') is None
    assert client.get('/citizen/mypage').status_code == 302


# ---------------------------------------------------------------------------
# 市民の新規登録：メールアドレスの '@' をサーバーで検査する
# ---------------------------------------------------------------------------

def _register_form(**values):
    return {
        'name': '山田 花子', 'email': 'hanako@example.com', 'postal_code': '330-9301',
        'address': 'さいたま市浦和区高砂3-15-1', 'password': 'password123', 'confirm_password': 'password123',
        **values,
    }


def test_register_email_without_at_is_warning_and_creates_no_user(client, app):
    # Act
    response = client.post('/register', data=_register_form(email='hanakoexample.com'))

    # Assert: 同じ画面に warning。ユーザーは作られない
    assert response.status_code == 200
    assert ('warning', MSG_EMAIL_NO_AT.format(email='hanakoexample.com')) in _alerts(response.get_data(as_text=True))
    assert User.query.count() == 0


def test_register_email_with_at_creates_lowercase_user(client, app):
    # Act
    response = client.post('/register', data=_register_form(email='Hanako@Example.com'))

    # Assert
    assert response.status_code == 302
    assert User.query.filter_by(login_id='hanako@example.com', role=User.ROLE_CITIZEN).count() == 1
