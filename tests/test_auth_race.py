# ログインの原子的なロック（GĐ2a 修正後）のテスト：パスワード確認中に別リクエストがロックした場合
# 「別のリクエスト」は別の DB 接続（db.engine.begin()）で locked_until を更新して再現する
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import update

from app.constants import MSG_ACCOUNT_DISABLED, MSG_LOGIN_FAILED, MSG_LOGIN_LOCKED
from app.extensions import db
from app.models import User
from app.services import ServiceError, ValidationError, auth_service
from tests.conftest import _make_user

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
CITIZEN_ID = 'taro@example.com'


def _lock_message(minutes):
    return MSG_LOGIN_LOCKED.format(minutes=minutes)


def _set_columns(user_id, **values):
    """別接続で users の列を直接更新する（別リクエストの動きの再現）"""
    with db.engine.begin() as other_connection:
        other_connection.execute(update(User.__table__).where(User.__table__.c.id == user_id).values(**values))


def _lock_during_password_check(user_id, locked_until):
    """User.check_password を差し替え、確認の最中に別リクエストが locked_until を入れる状態にする"""
    original = User.check_password

    def _check(self, password):
        _set_columns(user_id, locked_until=locked_until)
        return original(self, password)
    return mock.patch.object(User, 'check_password', autospec=True, side_effect=_check)


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@pytest.fixture
def user(app):
    return _make_user(CITIZEN_ID, '山田 太郎', User.ROLE_CITIZEN)


def test_correct_password_rejected_when_other_request_locks_during_check(user, fake_now):
    # Arrange
    fake_now(T0)
    user_id = user.id
    locked_until = T0 + timedelta(minutes=10)

    # Act
    with _lock_during_password_check(user_id, locked_until):
        with pytest.raises(ValidationError) as excinfo:
            auth_service.authenticate(User.ROLE_CITIZEN, CITIZEN_ID, 'password123')

    # Assert: ロック中の警告（残り 10 分）。最終ログインは更新されず、失敗回数もロックの期限もそのまま
    assert excinfo.value.messages == [('warning', _lock_message(10))]
    fresh = _fresh(user_id)
    assert fresh.last_login_at is None
    assert fresh.failed_login_count == 0
    assert _utc(fresh.locked_until) == locked_until


def test_wrong_password_does_not_count_when_other_request_locks_during_check(user, fake_now):
    # Arrange: すでに 2 回失敗している
    fake_now(T0)
    for _ in range(2):
        with pytest.raises(ServiceError):
            auth_service.authenticate(User.ROLE_CITIZEN, CITIZEN_ID, 'wrong-password')
    user_id = user.id

    # Act: 3 回目の確認中に別リクエストがロックする
    with _lock_during_password_check(user_id, T0 + timedelta(minutes=10)):
        with pytest.raises(ValidationError) as excinfo:
            auth_service.authenticate(User.ROLE_CITIZEN, CITIZEN_ID, 'wrong-password')

    # Assert: ロック中のメッセージ。回数は加算されず（2 のまま）、期限も延びない
    assert excinfo.value.messages == [('warning', _lock_message(10))]
    fresh = _fresh(user_id)
    assert fresh.failed_login_count == 2
    assert _utc(fresh.locked_until) == T0 + timedelta(minutes=10)


def test_lock_race_remaining_minutes_is_at_least_one(user, fake_now):
    # Arrange: 別リクエストが入れた期限まで 10 秒しかない
    fake_now(T0)

    # Act
    with _lock_during_password_check(user.id, T0 + timedelta(seconds=10)):
        with pytest.raises(ValidationError) as excinfo:
            auth_service.authenticate(User.ROLE_CITIZEN, CITIZEN_ID, 'password123')

    # Assert: 切り上げて最低 1 分
    assert excinfo.value.messages == [('warning', _lock_message(1))]


def test_lock_race_does_not_log_in_via_route(client, user, fake_now):
    # Arrange
    fake_now(T0)

    # Act: 画面から。確認中にロックされる
    with _lock_during_password_check(user.id, T0 + timedelta(minutes=10)):
        response = client.post('/login/citizen', data={'email': CITIZEN_ID, 'password': 'password123'})

    # Assert: ログインされず（リダイレクトなし）、ロックの文言が出て、保護された画面には入れない
    assert response.status_code == 200
    assert _lock_message(10) in response.get_data(as_text=True)
    assert client.get('/citizen/mypage').status_code == 302


def test_record_failure_returns_locked_error_when_update_matches_no_row(user, fake_now):
    # Arrange: 読み出し時点ではロックなしだが、DB はすでにロック済み（失敗 3 回）
    now = fake_now(T0)
    user_id = user.id
    _set_columns(user_id, locked_until=T0 + timedelta(minutes=7), failed_login_count=3)

    # Act
    error = auth_service._record_failure(user, now, 'メールアドレス')

    # Assert: 例外を「返す」（投げない）。回数は 3 のまま
    assert isinstance(error, ValidationError)
    assert error.messages == [('warning', _lock_message(7))]
    assert _fresh(user_id).failed_login_count == 3


def test_record_success_raises_locked_error_and_keeps_state_when_locked(user, fake_now):
    # Arrange
    now = fake_now(T0)
    user_id = user.id
    _set_columns(user_id, locked_until=T0 + timedelta(minutes=3), failed_login_count=2)

    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service._record_success(user, now)

    # Assert
    assert excinfo.value.messages == [('warning', _lock_message(3))]
    fresh = _fresh(user_id)
    assert fresh.last_login_at is None and fresh.failed_login_count == 2


def test_record_success_resets_count_and_sets_last_login_when_lock_expired(user, fake_now):
    # Arrange: 失敗 3 回、ロックの期限は過去（期限切れ＝ロックされていない扱い）
    now = fake_now(T0)
    user.failed_login_count = 3
    user.locked_until = T0 - timedelta(minutes=1)
    db.session.commit()

    # Act
    auth_service._record_success(user, now)

    # Assert
    fresh = _fresh(user.id)
    assert fresh.failed_login_count == 0
    assert fresh.locked_until is None
    assert _utc(fresh.last_login_at) == T0


def test_disabled_account_correct_password_shows_disabled_and_no_last_login(user, fake_now):
    # Arrange
    fake_now(T0)
    user.is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, CITIZEN_ID, 'password123')

    # Assert
    assert str(excinfo.value) == MSG_ACCOUNT_DISABLED
    db.session.refresh(user)
    assert user.last_login_at is None


def test_public_test_password_login_counts_as_failure(app, fake_now):
    # Arrange: 実際のパスワードは別。公開パスワードは運用者の設定では禁止だが、ログインでは通常の失敗として数える
    fake_now(T0)
    account = _make_user(CITIZEN_ID, '山田 太郎', User.ROLE_CITIZEN)
    account.set_password('Real-secret-1')
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate(User.ROLE_CITIZEN, CITIZEN_ID, 'password123')

    # Assert: 入力エラー（MSG_PASSWORD_PUBLIC）ではなく「ログイン失敗」で、回数に数える
    assert str(excinfo.value) == MSG_LOGIN_FAILED.format(label=auth_service.LOGIN_ID_LABELS[User.ROLE_CITIZEN])
    db.session.refresh(account)
    assert account.failed_login_count == 1
