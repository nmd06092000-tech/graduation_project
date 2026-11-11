# ログインの連続失敗による一時ロック（GĐ2a）のテスト：auth_service.authenticate と、ログイン画面の表示
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import MSG_ACCOUNT_DISABLED, MSG_ID_REQUIRED, MSG_LOGIN_FAILED, MSG_LOGIN_LOCKED
from app.extensions import db
from app.models import User
from app.services import ServiceError, ValidationError, auth_service
from tests.conftest import _make_user

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
LOGIN_IDS = {User.ROLE_CITIZEN: 'taro@example.com', User.ROLE_STAFF: 'staff01', User.ROLE_ADMIN: 'admin01'}
ID_FIELDS = {User.ROLE_CITIZEN: 'email', User.ROLE_STAFF: 'staff_id', User.ROLE_ADMIN: 'admin_id'}
ALL_ROLES = [User.ROLE_CITIZEN, User.ROLE_STAFF, User.ROLE_ADMIN]


def _failed(role):
    return MSG_LOGIN_FAILED.format(label=auth_service.LOGIN_ID_LABELS[role])


def _wrong(role, login_id, times=1):
    """間違ったパスワードで times 回ログインを試す（毎回 ServiceError か ValidationError になる）"""
    for _ in range(times):
        with pytest.raises((ServiceError, ValidationError)):
            auth_service.authenticate(role, login_id, 'wrong-password')


def _lock_message(minutes):
    return MSG_LOGIN_LOCKED.format(minutes=minutes)


@pytest.fixture
def user(app):
    """既定は市民（ロール別のテストは _make_user で作る）"""
    return _make_user(LOGIN_IDS[User.ROLE_CITIZEN], '山田 太郎', User.ROLE_CITIZEN)


# ---------------------------------------------------------------------------
# 失敗回数とロックの発動
# ---------------------------------------------------------------------------

def test_four_wrong_passwords_do_not_lock(user, fake_now):
    # Arrange
    fake_now(T0)

    # Act: 4 回失敗
    for _ in range(4):
        with pytest.raises(ServiceError) as excinfo:
            auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'wrong-password')
        assert str(excinfo.value) == _failed(User.ROLE_CITIZEN)

    # Assert: 回数は 4、ロックなし。正しいパスワードで入れる
    db.session.refresh(user)
    assert user.failed_login_count == 4
    assert user.locked_until is None
    assert auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123').id == user.id


def test_fifth_wrong_password_locks_and_shows_locked_message(user, fake_now):
    # Arrange
    fake_now(T0)
    _wrong(User.ROLE_CITIZEN, user.login_id, times=4)

    # Act: 5 回目
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'wrong-password')

    # Assert: 5 回目の時点でロックのメッセージ（15 分・warning）。回数は 0 に戻り、期限は now+15 分
    assert excinfo.value.messages == [('warning', _lock_message(15))]
    db.session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until == T0 + timedelta(minutes=15)


def test_correct_password_rejected_while_locked(user, fake_now):
    # Arrange
    fake_now(T0)
    _wrong(User.ROLE_CITIZEN, user.login_id, times=5)

    # Act / Assert: 正しいパスワードでも入れない
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123')
    assert excinfo.value.messages == [('warning', _lock_message(15))]
    db.session.refresh(user)
    assert user.last_login_at is None


def test_more_failures_while_locked_do_not_extend_lock(user, fake_now):
    # Arrange
    fake_now(T0)
    _wrong(User.ROLE_CITIZEN, user.login_id, times=5)
    fake_now(T0 + timedelta(minutes=5))

    # Act: ロック中にさらに失敗
    _wrong(User.ROLE_CITIZEN, user.login_id, times=3)

    # Assert: 期限は最初のまま、回数も増えない
    db.session.refresh(user)
    assert user.locked_until == T0 + timedelta(minutes=15)
    assert user.failed_login_count == 0


@pytest.mark.parametrize('remaining, expected_minutes', [
    (timedelta(minutes=15), 15),
    (timedelta(minutes=10, seconds=1), 11),
    (timedelta(minutes=10), 10),
    (timedelta(seconds=30), 1),
    (timedelta(seconds=1), 1),
])
def test_locked_message_rounds_remaining_minutes_up(user, fake_now, remaining, expected_minutes):
    # Arrange: 期限まで remaining 残っている状態
    user.locked_until = T0 + remaining
    db.session.commit()
    fake_now(T0)

    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123')

    # Assert: 切り上げで表示（最小 1 分）
    assert excinfo.value.messages == [('warning', _lock_message(expected_minutes))]


def test_login_succeeds_after_lock_expires_and_resets_counter(user, fake_now):
    # Arrange
    fake_now(T0)
    _wrong(User.ROLE_CITIZEN, user.login_id, times=5)
    fake_now(T0 + timedelta(minutes=15, seconds=1))

    # Act
    result = auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123')

    # Assert: 入れて、ロックと回数が解除され、最終ログイン日時が入る
    assert result.id == user.id
    db.session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None
    assert user.last_login_at == T0 + timedelta(minutes=15, seconds=1)


def test_wrong_password_after_lock_expires_counts_from_zero(user, fake_now):
    # Arrange
    fake_now(T0)
    _wrong(User.ROLE_CITIZEN, user.login_id, times=5)
    fake_now(T0 + timedelta(minutes=16))

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'wrong-password')

    # Assert: ロック明けは 0 回から数え直し（通常の失敗メッセージ）
    assert str(excinfo.value) == _failed(User.ROLE_CITIZEN)
    db.session.refresh(user)
    assert user.failed_login_count == 1


def test_success_resets_failed_count(user, fake_now):
    # Arrange
    fake_now(T0)
    _wrong(User.ROLE_CITIZEN, user.login_id, times=3)

    # Act
    auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123')

    # Assert: 成功で 0 に戻るので、その後の 4 回失敗ではまだ入れる
    db.session.refresh(user)
    assert user.failed_login_count == 0
    _wrong(User.ROLE_CITIZEN, user.login_id, times=4)
    assert auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123').id == user.id


def test_unknown_id_is_rejected_without_creating_data(app):
    # Act / Assert: 存在しない ID は何度試しても通常の失敗メッセージ（ロックのメッセージは出ない）、データは作られない
    for _ in range(6):
        with pytest.raises(ServiceError) as excinfo:
            auth_service.authenticate(User.ROLE_STAFF, 'nobody', 'wrong-password')
        assert str(excinfo.value) == _failed(User.ROLE_STAFF)
    assert db.session.query(User).count() == 0


@pytest.mark.parametrize('role', ALL_ROLES)
def test_lockout_applies_to_every_role(app, fake_now, role):
    # Arrange
    fake_now(T0)
    account = _make_user(LOGIN_IDS[role], 'テスト 太郎', role)

    # Act: 4 回はロックなし、5 回目でロック
    _wrong(role, account.login_id, times=4)
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(role, account.login_id, 'wrong-password')

    # Assert: 以後は正しいパスワードでも入れない
    assert excinfo.value.messages == [('warning', _lock_message(15))]
    with pytest.raises(ValidationError):
        auth_service.authenticate(role, account.login_id, 'password123')
    db.session.refresh(account)
    assert account.locked_until == T0 + timedelta(minutes=15)


def test_lock_is_per_account(user, staff, fake_now):
    # Arrange
    fake_now(T0)

    # Act: 市民だけ 5 回失敗
    _wrong(User.ROLE_CITIZEN, user.login_id, times=5)

    # Assert: 職員は影響を受けない
    assert auth_service.authenticate(User.ROLE_STAFF, staff.login_id, 'password123').id == staff.id


# ---------------------------------------------------------------------------
# 入力エラー・利用停止はロックの回数に数えない
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('login_id, password', [
    ('', ''), ('taro@example.com', ''), ('', 'password123'), ('taro@example.com', 'short'),
])
def test_input_errors_do_not_count_as_failures(user, fake_now, login_id, password):
    # Arrange
    fake_now(T0)

    # Act: 入力エラーを 10 回
    for _ in range(10):
        with pytest.raises(ValidationError):
            auth_service.authenticate(User.ROLE_CITIZEN, login_id, password)

    # Assert: 回数 0、ロックなし
    db.session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


def test_staff_id_with_at_is_input_error_not_counted(staff, fake_now):
    # Arrange
    fake_now(T0)

    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.authenticate(User.ROLE_STAFF, 'staff01@x', 'wrong-password')

    # Assert
    assert excinfo.value.messages[0][0] == 'warning'
    db.session.refresh(staff)
    assert staff.failed_login_count == 0


def test_disabled_account_with_correct_password_does_not_count(user, fake_now):
    # Arrange
    fake_now(T0)
    user.is_active = False
    db.session.commit()

    # Act: 正しいパスワードで 10 回
    for _ in range(10):
        with pytest.raises(ServiceError) as excinfo:
            auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'password123')
        assert str(excinfo.value) == MSG_ACCOUNT_DISABLED

    # Assert: 回数は増えず、ロックもされない
    db.session.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


def test_disabled_account_with_wrong_password_shows_login_failed(user, fake_now):
    # Arrange: 利用停止でもパスワード違いは通常の失敗として扱う（利用停止かどうかを漏らさない）
    fake_now(T0)
    user.is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, user.login_id, 'wrong-password')

    # Assert
    assert str(excinfo.value) == _failed(User.ROLE_CITIZEN)


# ---------------------------------------------------------------------------
# ログイン画面（route）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('role', ALL_ROLES)
def test_login_page_shows_locked_message_on_fifth_failure(client, app, fake_now, role):
    # Arrange
    fake_now(T0)
    account = _make_user(LOGIN_IDS[role], 'テスト 太郎', role)

    # Act: 5 回失敗
    for _ in range(4):
        client.post(f'/login/{role}', data={ID_FIELDS[role]: account.login_id, 'password': 'wrong-password'})
    response = client.post(f'/login/{role}', data={ID_FIELDS[role]: account.login_id, 'password': 'wrong-password'})

    # Assert: 200 で同じ画面に、ロックのメッセージ（minutes=15）が出る
    assert response.status_code == 200
    assert _lock_message(15) in response.get_data(as_text=True)
    # 正しいパスワードでもログインできない（リダイレクトされず、セッションなし）
    response = client.post(f'/login/{role}', data={ID_FIELDS[role]: account.login_id, 'password': 'password123'})
    assert response.status_code == 200
    with client.session_transaction() as session:
        assert '_user_id' not in session


def test_login_page_input_error_message_still_shown(client, user, fake_now):
    # Arrange
    fake_now(T0)

    # Act: ID 未入力
    response = client.post('/login/citizen', data={'email': '', 'password': 'password123'})

    # Assert
    assert response.status_code == 200
    assert MSG_ID_REQUIRED.format(label='メールアドレス') in response.get_data(as_text=True)
