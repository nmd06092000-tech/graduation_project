# flask コマンド（create-admin / set-password / remove-test-users）と、その service のテスト（GĐ2a）
# 運用者向けコマンドなので、テスト DB のアプリで test_cli_runner を使う。開発 DB には触れない
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import (
    MSG_ID_HAS_AT, MSG_ID_TAKEN, MSG_NO_ADMIN_LEFT, MSG_PASSWORD_TOO_SHORT, MSG_USER_NOT_FOUND, ReportStatus,
)
from app.extensions import db
from app.models import PasswordResetToken, Report, ReportStatusHistory, User
from app.services import ServiceError, ValidationError, auth_service, user_service
from app.services.user_service import (
    ACTION_DELETED, ACTION_DISABLED, ACTION_SKIPPED_CHANGED, ACTION_SKIPPED_INACTIVE, ACTION_SKIPPED_MISSING,
    TEST_LOGIN_IDS,
)
from tests.conftest import _make_user

FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


def _run(runner, args, input=None):
    """コマンドを実行する（予期しない例外は握りつぶさず失敗させる）"""
    return runner.invoke(args=args, input=input, catch_exceptions=False)


def _test_accounts():
    """README に載っていた 3 つのテストアカウント（パスワードは password123 のまま）"""
    taro = _make_user('taro@example.com', '山田 太郎', User.ROLE_CITIZEN)
    staff = _make_user('staff01', '鈴木 健一', User.ROLE_STAFF)
    admin = _make_user('admin01', '佐藤 管理', User.ROLE_ADMIN)
    return taro, staff, admin


def _find(login_id):
    return User.query.filter_by(login_id=login_id).first()


def _add_report(citizen_id):
    report = Report(citizen_id=citizen_id, title='道路の穴', description='穴があります。',
                    latitude=35.9, longitude=139.6, address='さいたま市', urgency='high')
    db.session.add(report)
    db.session.commit()
    return report


# ---------------------------------------------------------------------------
# create-admin
# ---------------------------------------------------------------------------

def test_create_admin_with_options_creates_admin(runner):
    # Act: ID・表示名はオプション、パスワードは隠し入力（2 回）
    result = _run(runner, ['create-admin', '--login-id', 'root', '--name', '管理者 太郎'], input='Secret-pw1\nSecret-pw1\n')

    # Assert
    assert result.exit_code == 0
    assert '管理者アカウント「root」を作成しました。' in result.output
    user = _find('root')
    assert user.role == User.ROLE_ADMIN
    assert user.name == '管理者 太郎'
    assert user.is_active is True
    assert user.department_id is None
    assert user.check_password('Secret-pw1')
    # 入力したパスワードは画面に出ない
    assert 'Secret-pw1' not in result.output


def test_create_admin_prompts_for_missing_id_and_name(runner):
    # Act
    result = _run(runner, ['create-admin'], input='root\nルート管理者\nSecret-pw1\nSecret-pw1\n')

    # Assert: 入力を求める文言が出て、作成される
    assert result.exit_code == 0
    assert '管理者ID' in result.output and '表示名' in result.output
    assert 'パスワード（8文字以上）' in result.output and 'パスワード（確認）' in result.output
    assert '管理者アカウント「root」を作成しました。' in result.output
    assert _find('root').name == 'ルート管理者'


def test_create_admin_new_admin_can_log_in(runner, client):
    # Arrange
    _run(runner, ['create-admin', '--login-id', 'root', '--name', '管理者'], input='Secret-pw1\nSecret-pw1\n')

    # Act
    response = client.post('/login/admin', data={'admin_id': 'root', 'password': 'Secret-pw1'})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/portal')


def test_create_admin_duplicate_id_fails_with_message(runner, admin):
    # Act
    result = _run(runner, ['create-admin', '--login-id', 'admin01', '--name', '別の人'], input='Secret-pw1\nSecret-pw1\n')

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_ID_TAKEN.format(label="管理者ID")}' in result.output
    assert User.query.filter_by(login_id='admin01').count() == 1
    assert _find('admin01').name == '佐藤 管理'


def test_create_admin_id_with_at_fails(runner, app):
    # Act
    result = _run(runner, ['create-admin', '--login-id', 'root@example.com', '--name', '管理者'],
                  input='Secret-pw1\nSecret-pw1\n')

    # Assert
    assert result.exit_code == 1
    assert 'Error: ' + MSG_ID_HAS_AT.format(label='管理者ID') in result.output
    assert User.query.count() == 0


def test_create_admin_short_password_fails(runner, app):
    # Act
    result = _run(runner, ['create-admin', '--login-id', 'root', '--name', '管理者'], input='short\nshort\n')

    # Assert
    assert result.exit_code == 1
    assert MSG_PASSWORD_TOO_SHORT.format(length=5) in result.output
    assert _find('root') is None


def test_create_admin_asks_again_when_confirmation_differs(runner, app):
    # Act: 1 回目は確認が違う → 入力し直しを求められ、2 回目で作成
    result = _run(runner, ['create-admin', '--login-id', 'root', '--name', '管理者'],
                  input='Secret-pw1\nOther-pw22\nSecret-pw1\nSecret-pw1\n')

    # Assert
    assert result.exit_code == 0
    assert _find('root').check_password('Secret-pw1')


def test_create_admin_multiple_errors_are_printed_one_per_line(runner, admin):
    # Act: ID が重複 + パスワードが短い
    result = _run(runner, ['create-admin', '--login-id', 'admin01', '--name', '管理者'], input='short\nshort\n')

    # Assert
    assert result.exit_code == 1
    assert MSG_ID_TAKEN.format(label='管理者ID') in result.output
    assert MSG_PASSWORD_TOO_SHORT.format(length=5) in result.output


# ---------------------------------------------------------------------------
# set-password
# ---------------------------------------------------------------------------

def test_set_password_changes_password_and_clears_lock(runner, staff):
    # Arrange: ロック中のアカウント
    staff.failed_login_count = 3
    staff.locked_until = FUTURE
    db.session.commit()

    # Act
    result = _run(runner, ['set-password', 'staff01'], input='New-secret-1\nNew-secret-1\n')

    # Assert
    assert result.exit_code == 0
    assert '「staff01」のパスワードを変更しました。ログインのロックも解除しました。' in result.output
    assert '注意：' not in result.output
    db.session.refresh(staff)
    assert staff.check_password('New-secret-1')
    assert not staff.check_password('password123')
    assert staff.failed_login_count == 0
    assert staff.locked_until is None
    assert 'New-secret-1' not in result.output


def test_set_password_unlocks_so_login_works(runner, client, citizen):
    # Arrange: 5 回失敗してロックされた市民
    for _ in range(5):
        client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'wrong-password'})
    assert client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'password123'}).status_code == 200

    # Act
    _run(runner, ['set-password', citizen.login_id], input='New-secret-1\nNew-secret-1\n')

    # Assert: 新しいパスワードで入れる
    response = client.post('/login/citizen', data={'email': citizen.login_id, 'password': 'New-secret-1'})
    assert response.status_code == 302


def test_set_password_for_disabled_account_prints_notice_and_keeps_disabled(runner, staff):
    # Arrange
    staff.is_active = False
    db.session.commit()

    # Act
    result = _run(runner, ['set-password', 'staff01'], input='New-secret-1\nNew-secret-1\n')

    # Assert: 変更は成功、注意の 1 行が出る。利用停止のまま
    assert result.exit_code == 0
    assert '「staff01」のパスワードを変更しました。' in result.output
    assert '注意：このアカウントは利用停止中のため' in result.output
    db.session.refresh(staff)
    assert staff.is_active is False
    assert staff.check_password('New-secret-1')


def test_set_password_unknown_user_fails(runner, app):
    # Act
    result = _run(runner, ['set-password', 'nobody'], input='New-secret-1\nNew-secret-1\n')

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_USER_NOT_FOUND.format(login_id="nobody")}' in result.output


def test_set_password_short_password_fails_and_keeps_old(runner, staff):
    # Act
    result = _run(runner, ['set-password', 'staff01'], input='short\nshort\n')

    # Assert
    assert result.exit_code == 1
    assert 'Error: ' in result.output
    db.session.refresh(staff)
    assert staff.check_password('password123')


@pytest.mark.parametrize('login_id', ['taro@example.com', 'staff01', 'admin01'])
def test_set_password_works_for_every_role(runner, app, login_id):
    # Arrange
    _test_accounts()

    # Act
    result = _run(runner, ['set-password', login_id], input='New-secret-1\nNew-secret-1\n')

    # Assert
    assert result.exit_code == 0
    assert _find(login_id).check_password('New-secret-1')


# ---------------------------------------------------------------------------
# set_password_by_operator（service）
# ---------------------------------------------------------------------------

def test_set_password_by_operator_validates_length(staff):
    # Act / Assert
    with pytest.raises(ValidationError) as excinfo:
        auth_service.set_password_by_operator('staff01', 'abcdefg')
    assert excinfo.value.messages == [('warning', MSG_PASSWORD_TOO_SHORT.format(length=7))]
    db.session.refresh(staff)
    assert staff.check_password('password123')


def test_set_password_by_operator_unknown_user_raises_service_error(app):
    # Act / Assert
    with pytest.raises(ServiceError) as excinfo:
        auth_service.set_password_by_operator('nobody', 'New-secret-1')
    assert str(excinfo.value) == MSG_USER_NOT_FOUND.format(login_id='nobody')


def test_set_password_by_operator_does_not_reactivate(staff):
    # Arrange
    staff.is_active = False
    db.session.commit()

    # Act
    auth_service.set_password_by_operator('staff01', 'New-secret-1')

    # Assert
    db.session.refresh(staff)
    assert staff.is_active is False


# ---------------------------------------------------------------------------
# remove-test-users
# ---------------------------------------------------------------------------

def test_remove_test_users_aborts_when_not_confirmed(runner, app):
    # Arrange
    _test_accounts()

    # Act: 確認に n
    result = _run(runner, ['remove-test-users'], input='n\n')

    # Assert
    assert result.exit_code == 1
    assert 'Aborted!' in result.output
    assert User.query.count() == 3


def test_remove_test_users_confirmed_with_y_runs(runner, app):
    # Arrange: 有効な管理者が残るよう、もう 1 人作っておく
    _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    result = _run(runner, ['remove-test-users'], input='y\n')

    # Assert
    assert result.exit_code == 0
    assert 'taro@example.com: 削除' in result.output
    assert User.query.count() == 1


def test_remove_test_users_deletes_accounts_without_data(runner, app):
    # Arrange
    _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: 3 行（login_id: 結果）。テストアカウントは消え、root は残る
    assert result.exit_code == 0
    assert f'taro@example.com: {ACTION_DELETED}' in result.output
    assert f'staff01: {ACTION_DELETED}' in result.output
    assert f'admin01: {ACTION_DELETED}' in result.output
    assert [user.login_id for user in User.query.all()] == ['root']
    assert 'ドライラン' not in result.output


def test_remove_test_users_disables_account_with_report_and_randomizes_password(runner, app):
    # Arrange: 報告のある市民
    taro, _, _ = _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _add_report(taro.id)

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: 市民は無効化（データは残る）、データのない 2 つは削除
    assert result.exit_code == 0
    assert f'taro@example.com: {ACTION_DISABLED}' in result.output
    taro = _find('taro@example.com')
    assert taro.is_active is False
    assert not taro.check_password('password123')
    assert Report.query.count() == 1
    assert _find('staff01') is None and _find('admin01') is None


def test_remove_test_users_disables_staff_with_status_history(runner, app):
    # Arrange: 状態履歴の変更者になっている職員（報告は別の市民のもの）
    _, staff, _ = _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    other = _make_user('hanako@example.com', '花子', User.ROLE_CITIZEN)
    report = _add_report(other.id)
    db.session.add(ReportStatusHistory(report_id=report.id, to_status=ReportStatus.RECEIVED.value,
                                       changed_by_id=staff.id))
    db.session.commit()

    # Act
    results = user_service.remove_test_users()

    # Assert: FK を動的に調べるので、履歴だけでも無効化になる
    assert dict(results)['staff01'] == ACTION_DISABLED
    assert _find('staff01').is_active is False


def test_remove_test_users_dry_run_prints_but_does_not_change(runner, app):
    # Arrange
    taro, _, _ = _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _add_report(taro.id)

    # Act: --dry-run は確認を求めない（input なし）
    result = _run(runner, ['remove-test-users', '--dry-run'])

    # Assert
    assert result.exit_code == 0
    assert 'ドライラン：変更は保存されません。' in result.output
    assert f'taro@example.com: {ACTION_DISABLED}' in result.output
    assert f'staff01: {ACTION_DELETED}' in result.output
    assert User.query.count() == 4
    assert _find('taro@example.com').is_active is True
    assert _find('taro@example.com').check_password('password123')


def test_remove_test_users_second_run_skips(runner, app):
    # Arrange: 1 回目で taro は無効化・他は削除
    taro, _, _ = _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _add_report(taro.id)
    _run(runner, ['remove-test-users', '--yes'])

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert
    assert result.exit_code == 0
    assert f'taro@example.com: {ACTION_SKIPPED_INACTIVE}' in result.output
    assert f'staff01: {ACTION_SKIPPED_MISSING}' in result.output
    assert f'admin01: {ACTION_SKIPPED_MISSING}' in result.output


def test_remove_test_users_blocked_when_last_admin(runner, app):
    # Arrange: 有効な管理者は admin01 だけ
    _test_accounts()

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: 実行できず、何も変更されない（同じトランザクションの taro / staff01 も残る）
    assert result.exit_code == 1
    assert f'Error: {MSG_NO_ADMIN_LEFT}' in result.output
    assert User.query.count() == 3
    assert all(user.is_active for user in User.query.all())


def test_remove_test_users_blocked_when_other_admin_is_inactive(runner, app):
    # Arrange: 別の管理者は利用停止中 → 有効な管理者として数えない
    _test_accounts()
    other = _make_user('root', '管理者', User.ROLE_ADMIN)
    other.is_active = False
    db.session.commit()

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert
    assert result.exit_code == 1
    assert MSG_NO_ADMIN_LEFT in result.output
    assert User.query.count() == 4


def test_remove_test_users_skips_admin_with_changed_password_and_runs(runner, app):
    # Arrange: admin01 は本番用にパスワード変更済み（他に管理者はいない）
    _test_accounts()
    _find('admin01').set_password('Production-pw1')
    db.session.commit()

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: admin01 はスキップ（有効なまま残る）ので、残りの 2 つは処理できる
    assert result.exit_code == 0
    assert f'admin01: {ACTION_SKIPPED_CHANGED}' in result.output
    assert f'taro@example.com: {ACTION_DELETED}' in result.output
    assert [user.login_id for user in User.query.all()] == ['admin01']
    assert _find('admin01').is_active is True


def test_remove_test_users_force_processes_changed_password_account(runner, app):
    # Arrange: admin01 はパスワード変更済み、別の管理者 root がいる
    _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _find('admin01').set_password('Production-pw1')
    db.session.commit()

    # Act
    result = _run(runner, ['remove-test-users', '--yes', '--force'])

    # Assert: --force なら admin01 も対象（データなし → 削除）
    assert result.exit_code == 0
    assert f'admin01: {ACTION_DELETED}' in result.output
    assert _find('admin01') is None


def test_remove_test_users_force_still_blocked_when_it_would_remove_last_admin(runner, app):
    # Arrange: admin01（変更済み）が唯一の管理者
    _test_accounts()
    _find('admin01').set_password('Production-pw1')
    db.session.commit()

    # Act
    result = _run(runner, ['remove-test-users', '--yes', '--force'])

    # Assert
    assert result.exit_code == 1
    assert MSG_NO_ADMIN_LEFT in result.output
    assert _find('admin01') is not None and User.query.count() == 3


def test_remove_test_users_deletes_user_with_reset_token(runner, app):
    # Arrange: 再設定トークンを持つ市民（報告なし）
    taro, _, _ = _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    db.session.add(PasswordResetToken(user_id=taro.id, token_hash='a' * 64, expires_at=FUTURE))
    db.session.commit()

    # Act
    result = _run(runner, ['remove-test-users', '--yes'])

    # Assert: トークンも一緒に消えて削除できる
    assert result.exit_code == 0
    assert f'taro@example.com: {ACTION_DELETED}' in result.output
    assert _find('taro@example.com') is None
    assert PasswordResetToken.query.count() == 0


def test_remove_test_users_disabled_user_with_token_also_loses_token(runner, app):
    # Arrange: 報告があり、トークンもある
    taro, _, _ = _test_accounts()
    _make_user('root', '管理者', User.ROLE_ADMIN)
    _add_report(taro.id)
    db.session.add(PasswordResetToken(user_id=taro.id, token_hash='b' * 64, expires_at=FUTURE))
    db.session.commit()

    # Act
    results = user_service.remove_test_users()

    # Assert: 無効化したアカウントで再設定できないよう、トークンは消す
    assert dict(results)['taro@example.com'] == ACTION_DISABLED
    assert PasswordResetToken.query.count() == 0


def test_remove_test_users_service_returns_results_in_fixed_order(app):
    # Arrange: staff01 だけ存在、管理者は root
    _make_user('staff01', '鈴木', User.ROLE_STAFF)
    _make_user('root', '管理者', User.ROLE_ADMIN)

    # Act
    results = user_service.remove_test_users(dry_run=True)

    # Assert
    assert [login_id for login_id, _ in results] == list(TEST_LOGIN_IDS)
    assert [action for _, action in results] == [ACTION_SKIPPED_MISSING, ACTION_DELETED, ACTION_SKIPPED_MISSING]
    assert _find('staff01') is not None  # dry_run は保存しない


def test_remove_test_users_does_not_touch_other_accounts(runner, app):
    # Arrange
    _test_accounts()
    root = _make_user('root', '管理者', User.ROLE_ADMIN)
    other = _make_user('hanako@example.com', '花子', User.ROLE_CITIZEN)
    _add_report(other.id)

    # Act
    _run(runner, ['remove-test-users', '--yes'])

    # Assert
    assert _find('root').id == root.id and _find('root').is_active
    assert _find('hanako@example.com').is_active
    assert Report.query.count() == 1
