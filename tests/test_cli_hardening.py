# flask コマンドのアカウント運用の追加テスト（GĐ2a 修正後）：公開パスワード禁止・ID の長さ・DB エラー・
# remove-test-users の無効化済みアカウント処理と ID + 役割の一致
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy.exc import OperationalError

from app.constants import (
    MSG_ACCOUNT_SAVE_FAILED, MSG_ID_TOO_LONG, MSG_NO_ADMIN_LEFT, MSG_PASSWORD_PUBLIC, MSG_PASSWORD_TOO_SHORT,
    TEST_ACCOUNT_PW,
)
from app.extensions import db
from app.models import PasswordResetToken, Report, User
from app.services import ServiceError, ValidationError, auth_service, user_service
from app.services.user_service import (
    ACTION_DELETED, ACTION_DISABLED_PW_RESET, ACTION_SKIPPED_INACTIVE, ACTION_SKIPPED_MISSING,
)
from app.utils.validators import validate_operator_password
from tests.conftest import _make_user

FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


def _run(runner, args, input=None):
    return runner.invoke(args=args, input=input, catch_exceptions=False)


def _find(login_id):
    return User.query.filter_by(login_id=login_id).first()


def _add_report(citizen_id):
    report = Report(citizen_id=citizen_id, title='道路の穴', description='穴があります。',
                    latitude=35.9, longitude=139.6, address='さいたま市', urgency='high')
    db.session.add(report)
    db.session.commit()
    return report


# ---------------------------------------------------------------------------
# 公開パスワード（password123）の禁止
# ---------------------------------------------------------------------------

def test_validate_operator_password_rejects_public_password():
    # Assert
    assert validate_operator_password(TEST_ACCOUNT_PW) == [('warning', MSG_PASSWORD_PUBLIC)]
    assert MSG_PASSWORD_PUBLIC == 'このパスワードは公開されているため使用できません。'


def test_validate_operator_password_accepts_other_and_keeps_length_rule():
    # Assert
    assert validate_operator_password('Secret-pw1') == []
    assert validate_operator_password('short') == [('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]
    # 公開パスワードに似た別の値は許可
    assert validate_operator_password('password1234') == []


def test_create_admin_rejects_public_password_and_saves_nothing(runner, app):
    # Act
    result = _run(runner, ['create-admin', '--login-id', 'root', '--name', '管理者'],
                  input=f'{TEST_ACCOUNT_PW}\n{TEST_ACCOUNT_PW}\n')

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_PASSWORD_PUBLIC}' in result.output
    assert User.query.count() == 0


def test_set_password_rejects_public_password_and_keeps_old(runner, staff):
    # Arrange: 現在のパスワードを別の値にしておく
    staff.set_password('Old-secret-1')
    staff.failed_login_count = 2
    db.session.commit()

    # Act
    result = _run(runner, ['set-password', 'staff01'], input=f'{TEST_ACCOUNT_PW}\n{TEST_ACCOUNT_PW}\n')

    # Assert: 変更されず、失敗回数もそのまま
    assert result.exit_code == 1
    assert f'Error: {MSG_PASSWORD_PUBLIC}' in result.output
    db.session.refresh(staff)
    assert staff.check_password('Old-secret-1')
    assert staff.failed_login_count == 2


def test_set_password_by_operator_raises_validation_error_for_public_password(staff):
    # Act
    with pytest.raises(ValidationError) as excinfo:
        auth_service.set_password_by_operator('staff01', TEST_ACCOUNT_PW)

    # Assert
    assert excinfo.value.messages == [('warning', MSG_PASSWORD_PUBLIC)]


# ---------------------------------------------------------------------------
# create-admin：ID の長さ・予期しない DB エラー
# ---------------------------------------------------------------------------

def test_create_admin_rejects_login_id_over_255_chars(runner, app):
    # Act
    result = _run(runner, ['create-admin', '--login-id', 'a' * 256, '--name', '管理者'], input='Secret-pw1\nSecret-pw1\n')

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_ID_TOO_LONG.format(label="管理者ID")}' in result.output
    assert MSG_ID_TOO_LONG == '{label}は255文字以内で入力してください。'
    assert User.query.count() == 0


def test_create_admin_accepts_login_id_of_exactly_255_chars(runner, app):
    # Act: 境界値
    login_id = 'a' * 255
    result = _run(runner, ['create-admin', '--login-id', login_id, '--name', '管理者'], input='Secret-pw1\nSecret-pw1\n')

    # Assert
    assert result.exit_code == 0
    assert _find(login_id) is not None


def test_create_admin_unexpected_db_error_rolls_back_and_exits_1_without_traceback(runner, app):
    # Arrange: commit が予期しない DB エラーを投げる
    error = OperationalError('INSERT INTO users ...', {}, Exception('connection lost'))

    # Act
    with mock.patch.object(db.session, 'commit', side_effect=error):
        result = _run(runner, ['create-admin', '--login-id', 'root', '--name', '管理者'], input='Secret-pw1\nSecret-pw1\n')

    # Assert: 保存用のメッセージだけ。スタックトレース・DB の詳細は出さない。ロールバック済みで何も残らない
    assert result.exit_code == 1
    assert f'Error: {MSG_ACCOUNT_SAVE_FAILED}' in result.output
    assert MSG_ACCOUNT_SAVE_FAILED == 'アカウントを保存できませんでした。しばらくしてからもう一度お試しください。'
    assert 'Traceback' not in result.output
    assert 'connection lost' not in result.output
    assert _find('root') is None


def test_create_admin_service_raises_service_error_on_unexpected_db_error(app):
    # Arrange
    error = OperationalError('INSERT INTO users ...', {}, Exception('boom'))

    # Act
    with mock.patch.object(db.session, 'commit', side_effect=error):
        with pytest.raises(ServiceError) as excinfo:
            user_service.create_admin(login_id='root', name='管理者', password='Secret-pw1')

    # Assert
    assert str(excinfo.value) == MSG_ACCOUNT_SAVE_FAILED
    assert User.query.count() == 0


# ---------------------------------------------------------------------------
# remove-test-users：無効化済みでも公開パスワードのままなら見逃さない
# ---------------------------------------------------------------------------

def _inactive_taro(password=TEST_ACCOUNT_PW, with_report=True):
    taro = _make_user('taro@example.com', '山田 太郎', User.ROLE_CITIZEN)
    taro.is_active = False
    taro.set_password(password)
    db.session.commit()
    if with_report:
        _add_report(taro.id)
    return taro


def test_remove_test_users_inactive_with_public_password_and_data_resets_password(runner, app):
    # Arrange: 無効化済み・password123 のまま・報告あり。有効な管理者 root がいる
    taro = _inactive_taro()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    db.session.add(PasswordResetToken(user_id=taro.id, token_hash='c' * 64, expires_at=FUTURE))
    db.session.commit()

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: 無効のままパスワードだけ変わり、トークンは消える
    assert result.exit_code == 0
    assert f'taro@example.com: {ACTION_DISABLED_PW_RESET}' in result.output
    assert ACTION_DISABLED_PW_RESET == '無効化済み・パスワード変更'
    taro = _find('taro@example.com')
    assert taro.is_active is False
    assert not taro.check_password('password123')
    assert PasswordResetToken.query.count() == 0
    assert Report.query.count() == 1


def test_remove_test_users_inactive_with_public_password_without_data_is_deleted(runner, app):
    # Arrange
    _inactive_taro(with_report=False)
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert
    assert f'taro@example.com: {ACTION_DELETED}' in result.output
    assert _find('taro@example.com') is None


def test_remove_test_users_inactive_with_changed_password_is_skipped_inactive(runner, app):
    # Arrange: 無効・パスワード変更済み・データあり
    _inactive_taro(password='Changed-pw-1')
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: 何もしない
    assert f'taro@example.com: {ACTION_SKIPPED_INACTIVE}' in result.output
    taro = _find('taro@example.com')
    assert taro.is_active is False and taro.check_password('Changed-pw-1')


def test_remove_test_users_inactive_with_changed_password_and_force_resets_password(runner, app):
    # Arrange: データあり → DISABLED_PW_RESET
    _inactive_taro(password='Changed-pw-1')
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    result = _run(runner, ['remove-test-users', '--yes', '--force'])

    # Assert
    assert f'taro@example.com: {ACTION_DISABLED_PW_RESET}' in result.output
    taro = _find('taro@example.com')
    assert taro.is_active is False and not taro.check_password('Changed-pw-1')


def test_remove_test_users_inactive_with_changed_password_without_data_and_force_is_deleted(app):
    # Arrange: データなし → 削除
    _inactive_taro(password='Changed-pw-1', with_report=False)
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    results = user_service.remove_test_users(force=True)

    # Assert
    assert dict(results)['taro@example.com'] == ACTION_DELETED
    assert _find('taro@example.com') is None


def test_remove_test_users_second_run_after_pw_reset_skips_inactive(runner, app):
    # Arrange: 1 回目でパスワードがランダムになるので、2 回目は「変更済み・無効」でスキップ
    _inactive_taro()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _run(runner, ['remove-test-users', '--yes'])

    # Act
    results = user_service.remove_test_users()

    # Assert
    assert dict(results)['taro@example.com'] == ACTION_SKIPPED_INACTIVE


def test_remove_test_users_admin_guard_ignores_disabled_pw_reset(app):
    # Arrange: 有効な管理者は 0 人。admin01 は無効・password123・データあり → DISABLED_PW_RESET
    admin = _make_user('admin01', '佐藤 管理', User.ROLE_ADMIN)
    admin.is_active = False
    db.session.commit()
    _add_report(admin.id)

    # Act: DISABLED_PW_RESET は有効な管理者を減らす処理ではないので、ガードで止まらない
    results = user_service.remove_test_users()

    # Assert
    assert dict(results)['admin01'] == ACTION_DISABLED_PW_RESET
    assert _find('admin01').is_active is False


def test_remove_test_users_admin_guard_counts_deleted_admin(app):
    # Arrange: 唯一の有効な管理者 admin01 が削除対象 → 中止（DELETED は数える）
    _make_user('admin01', '佐藤 管理', User.ROLE_ADMIN)

    # Act / Assert
    with pytest.raises(ServiceError) as excinfo:
        user_service.remove_test_users()
    assert str(excinfo.value) == MSG_NO_ADMIN_LEFT
    assert _find('admin01') is not None


# ---------------------------------------------------------------------------
# ID + 役割の一致
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('force', [False, True])
def test_remove_test_users_role_mismatch_is_treated_as_missing(app, force):
    # Arrange: staff01 という ID だが役割は管理者（本物の運用アカウント）。password123 のまま・報告あり
    _make_user('root', '管理者', User.ROLE_ADMIN)
    impostor = _make_user('staff01', '運用管理者', User.ROLE_ADMIN)
    _add_report(impostor.id)

    # Act
    results = user_service.remove_test_users(force=force)

    # Assert: ID と役割が一致しないので「アカウントなし」。--force でも触らない
    assert dict(results)['staff01'] == ACTION_SKIPPED_MISSING
    kept = _find('staff01')
    assert kept is not None and kept.role == User.ROLE_ADMIN
    assert kept.is_active is True and kept.check_password('password123')


def test_remove_test_users_admin01_with_staff_role_is_not_touched_even_with_force(runner, app):
    # Arrange: admin01 という ID の職員
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _make_user('admin01', '職員', User.ROLE_STAFF)

    # Act
    result = _run(runner, ['remove-test-users', '--yes', '--force'])

    # Assert
    assert result.exit_code == 0
    assert f'admin01: {ACTION_SKIPPED_MISSING}' in result.output
    assert _find('admin01').role == User.ROLE_STAFF


def test_remove_test_users_help_for_force_mentions_role(runner, app):
    # Act
    result = _run(runner, ['remove-test-users', '--help'])

    # Assert: 折り返しの改行・空白を除いて確認
    text = ''.join(result.output.split())
    assert '--force' in text
    assert '役割' in text
    assert 'taro@example.com=市民' in text and 'staff01=職員' in text and 'admin01=管理者' in text
