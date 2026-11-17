# flask コマンド restore-user（GĐ3a）と、削除済み（del_flag）アカウントに対する既存コマンドの動き。テスト DB のアプリで test_cli_runner を使う
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import (
    MSG_ACCOUNT_DELETED_RESTORE_FIRST, MSG_ACCOUNT_NOT_DELETED, MSG_ACCOUNT_RESTORED, MSG_ID_TAKEN, MSG_NO_ADMIN_LEFT,
    MSG_RESET_CODE_NOT_ALLOWED, MSG_USER_NOT_FOUND,
)
from app.extensions import db
from app.models import PasswordResetToken, User
from app.services import auth_service
from tests.conftest import lock_account, make_account, soft_delete

PW = 'Secret-pw1'
NEW_PW = 'NewPass-9876'
T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
LIST_HEADER = 'ID\t氏名\t役割\t削除日時\t削除者'


def _run(runner, args, input=None):
    return runner.invoke(args=args, input=input, catch_exceptions=False)


def _fresh(login_id):
    db.session.expire_all()
    return User.query.filter_by(login_id=login_id).one()


@pytest.fixture
def deleted_staff(app):
    """管理者 boss が削除した職員 worker1（削除日時は 2026-01-01 00:00 UTC = 09:00 JST）"""
    boss = make_account('boss', User.ROLE_ADMIN, name='統括 管理')
    make_account('boss2', User.ROLE_ADMIN, name='予備 管理')
    worker = make_account('worker1', User.ROLE_STAFF, name='山田 職員')
    return soft_delete(worker, by=boss, at=T0)


# ---------------------------------------------------------------------------
# restore-user <login_id>
# ---------------------------------------------------------------------------

def test_restore_user_restores_deleted_account(runner, deleted_staff):
    # Act
    result = _run(runner, ['restore-user', 'worker1'])

    # Assert
    assert result.exit_code == 0
    assert MSG_ACCOUNT_RESTORED.format(login_id='worker1') in result.output
    user = _fresh('worker1')
    assert user.del_flag is False and user.deleted_at is None and user.deleted_by_id is None


def test_restore_user_clears_lock_and_unused_codes(runner, deleted_staff):
    # Arrange
    lock_account(deleted_staff)
    db.session.add(PasswordResetToken(user_id=deleted_staff.id, token_hash='e' * 64, created_at=T0,
                                      expires_at=T0 + timedelta(days=3650)))
    db.session.commit()

    # Act
    result = _run(runner, ['restore-user', 'worker1'])

    # Assert
    assert result.exit_code == 0
    user = _fresh('worker1')
    assert user.locked_until is None and user.failed_login_count == 0
    assert PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).count() == 0


@pytest.mark.parametrize('role', [User.ROLE_STAFF, User.ROLE_ADMIN])
def test_restore_user_account_can_log_in_again_with_original_password(runner, deleted_staff, role):
    # Arrange
    target = deleted_staff if role == User.ROLE_STAFF else soft_delete(make_account('oldadmin', User.ROLE_ADMIN))

    # Act
    _run(runner, ['restore-user', target.login_id])

    # Assert
    assert auth_service.authenticate(role, target.login_id, 'password123').id == target.id


def test_restore_user_not_deleted_account_fails(runner, deleted_staff):
    # Act
    result = _run(runner, ['restore-user', 'boss'])

    # Assert
    assert result.exit_code == 1
    assert f"Error: {MSG_ACCOUNT_NOT_DELETED.format(login_id='boss')}" in result.output


def test_restore_user_unknown_login_id_fails(runner, deleted_staff):
    # Act
    result = _run(runner, ['restore-user', 'nobody'])

    # Assert
    assert result.exit_code == 1
    assert f"Error: {MSG_USER_NOT_FOUND.format(login_id='nobody')}" in result.output


def test_restore_user_takes_login_id_not_numeric_id(runner, deleted_staff):
    # Act: 数値の id では見つからない
    result = _run(runner, ['restore-user', str(deleted_staff.id)])

    # Assert
    assert result.exit_code == 1 and 'が見つかりません' in result.output
    assert _fresh('worker1').del_flag is True


def test_restore_user_without_arguments_is_a_usage_error(runner, deleted_staff):
    # Act
    result = _run(runner, ['restore-user'])

    # Assert: 終了コード 2。--list の案内が出る
    assert result.exit_code == 2
    assert '--list' in result.output
    assert _fresh('worker1').del_flag is True


def test_restore_user_twice_second_run_fails(runner, deleted_staff):
    # Arrange
    _run(runner, ['restore-user', 'worker1'])

    # Act
    result = _run(runner, ['restore-user', 'worker1'])

    # Assert
    assert result.exit_code == 1
    assert MSG_ACCOUNT_NOT_DELETED.format(login_id='worker1') in result.output


# ---------------------------------------------------------------------------
# restore-user --list
# ---------------------------------------------------------------------------

def test_restore_user_list_when_nothing_is_deleted(runner, app):
    # Act
    result = _run(runner, ['restore-user', '--list'])

    # Assert: 見出しは出さず、案内の 1 行だけ
    assert result.exit_code == 0
    assert result.output.strip() == '削除済みのアカウントはありません。'


def test_restore_user_list_prints_header_and_tab_separated_rows(runner, deleted_staff):
    # Act
    result = _run(runner, ['restore-user', '--list'])

    # Assert
    lines = result.output.strip().splitlines()
    assert result.exit_code == 0
    assert lines[0] == LIST_HEADER
    assert lines[1] == 'worker1\t山田 職員\t職員\t2026/01/01 09:00\tboss'


def test_restore_user_list_shows_dash_when_deleter_is_unknown(runner, app):
    # Arrange
    soft_delete(make_account('worker2', User.ROLE_STAFF, name='田中'), by=None, at=T0)

    # Act
    result = _run(runner, ['restore-user', '--list'])

    # Assert
    assert result.output.strip().splitlines()[1] == 'worker2\t田中\t職員\t2026/01/01 09:00\t-'


def test_restore_user_list_is_newest_deletion_first_and_only_deleted(runner, deleted_staff):
    # Arrange
    soft_delete(make_account('worker3', User.ROLE_STAFF), by=None, at=T0 + timedelta(hours=2))
    make_account('worker4', User.ROLE_STAFF)

    # Act
    lines = _run(runner, ['restore-user', '--list']).output.strip().splitlines()

    # Assert
    assert [line.split('\t')[0] for line in lines[1:]] == ['worker3', 'worker1']


def test_restore_user_list_option_does_not_restore(runner, deleted_staff):
    # Act: --list と ID を同時に渡しても復元はしない
    result = _run(runner, ['restore-user', '--list', 'worker1'])

    # Assert
    assert result.exit_code == 0 and LIST_HEADER in result.output
    assert _fresh('worker1').del_flag is True


# ---------------------------------------------------------------------------
# 既存コマンドと削除済みアカウント
# ---------------------------------------------------------------------------

def test_set_password_for_deleted_account_fails_and_changes_nothing(runner, deleted_staff):
    # Arrange
    lock_account(deleted_staff)
    old_hash = deleted_staff.password_hash

    # Act
    result = _run(runner, ['set-password', 'worker1'], input=f'{NEW_PW}\n{NEW_PW}\n')

    # Assert
    assert result.exit_code == 1
    assert f"Error: {MSG_ACCOUNT_DELETED_RESTORE_FIRST.format(login_id='worker1')}" in result.output
    user = _fresh('worker1')
    assert user.password_hash == old_hash and user.locked_until is not None and user.del_flag is True


def test_set_password_works_after_restore(runner, deleted_staff):
    # Arrange
    _run(runner, ['restore-user', 'worker1'])

    # Act
    result = _run(runner, ['set-password', 'worker1'], input=f'{NEW_PW}\n{NEW_PW}\n')

    # Assert
    assert result.exit_code == 0 and _fresh('worker1').check_password(NEW_PW)


def test_issue_reset_code_for_deleted_account_is_refused(runner, deleted_staff):
    # Act
    result = _run(runner, ['issue-reset-code', 'worker1'])

    # Assert
    assert result.exit_code == 1
    assert f"Error: {MSG_RESET_CODE_NOT_ALLOWED.format(login_id='worker1')}" in result.output
    assert PasswordResetToken.query.count() == 0


def test_create_admin_with_deleted_login_id_reports_duplicate(runner, deleted_staff):
    # Act
    result = _run(runner, ['create-admin', '--login-id', 'worker1', '--name', '新管理'], input=f'{PW}\n{PW}\n')

    # Assert: 「既に登録されています」。削除済みであることは伝えない。行は増えない
    assert result.exit_code == 1
    assert MSG_ID_TAKEN.format(label='管理者ID') in result.output
    assert User.query.filter_by(login_id='worker1').count() == 1


def test_create_staff_with_deleted_login_id_reports_duplicate(runner, deleted_staff, department):
    # Act
    result = _run(runner, ['create-staff', '--login-id', 'worker1', '--name', '新人', '--department', '道路管理課'],
                  input=f'{PW}\n{PW}\n')

    # Assert
    assert result.exit_code == 1
    assert MSG_ID_TAKEN.format(label='職員ID') in result.output
    assert User.query.filter_by(login_id='worker1').count() == 1


def test_create_staff_with_restored_login_id_is_still_a_duplicate(runner, deleted_staff, department):
    # Arrange: 復元しても ID は 1 つのまま
    _run(runner, ['restore-user', 'worker1'])

    # Act
    result = _run(runner, ['create-staff', '--login-id', 'worker1', '--name', '新人', '--department', '道路管理課'],
                  input=f'{PW}\n{PW}\n')

    # Assert
    assert result.exit_code == 1 and MSG_ID_TAKEN.format(label='職員ID') in result.output


def test_remove_test_users_aborts_when_only_other_admin_is_deleted(runner, app):
    # Arrange: テスト用の admin01 以外の管理者は削除済み（使えない管理者は「残る」に数えない）
    make_account('taro@example.com', User.ROLE_CITIZEN)
    admin01 = make_account('admin01', User.ROLE_ADMIN)
    soft_delete(make_account('realadmin', User.ROLE_ADMIN), by=admin01)

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_NO_ADMIN_LEFT}' in result.output
    assert User.query.filter_by(login_id='admin01').count() == 1


def test_remove_test_users_reports_deleted_test_account_as_inactive_when_password_changed(runner, app):
    # Arrange
    make_account('realadmin', User.ROLE_ADMIN)
    staff01 = make_account('staff01', User.ROLE_STAFF)
    staff01.set_password('Changed-pw-1')
    soft_delete(staff01)

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert
    assert result.exit_code == 0
    assert 'staff01: スキップ（無効化済み）' in result.output
    assert _fresh('staff01').del_flag is True
