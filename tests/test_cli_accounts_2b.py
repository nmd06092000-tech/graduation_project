# flask コマンド create-staff / issue-reset-code（GĐ2b）のテスト。テスト DB のアプリで test_cli_runner を使う
import re

from app.constants import (
    MSG_DEPARTMENT_INVALID, MSG_ID_HAS_AT, MSG_ID_TAKEN, MSG_PASSWORD_PUBLIC, MSG_PASSWORD_TOO_SHORT,
    MSG_RESET_CODE_NOT_ALLOWED,
)
from app.extensions import db
from app.models import Department, User
from app.models.user import load_user
from app.services import auth_service
from tests.conftest import _make_user

PW = 'Secret-pw1'
NEW_PW = 'NewPass-9876'


def _run(runner, args, input=None):
    return runner.invoke(args=args, input=input, catch_exceptions=False)


def _find(login_id):
    return User.query.filter_by(login_id=login_id).first()


# ---------------------------------------------------------------------------
# create-staff
# ---------------------------------------------------------------------------

def test_create_staff_with_department_name(runner, department):
    # Act
    result = _run(runner, ['create-staff', '--login-id', 'staff10', '--name', '田中 次郎', '--department', '道路管理課'],
                  input=f'{PW}\n{PW}\n')

    # Assert
    assert result.exit_code == 0
    assert '職員アカウント「staff10」を作成しました。' in result.output
    user = _find('staff10')
    assert user.role == User.ROLE_STAFF and user.name == '田中 次郎' and user.department_id == department.id
    assert user.check_password(PW)
    # 入力したパスワードは画面に出ない
    assert PW not in result.output


def test_create_staff_with_department_id(runner, department):
    # Act
    result = _run(runner, ['create-staff', '--login-id', 'staff10', '--name', '田中', '--department', str(department.id)],
                  input=f'{PW}\n{PW}\n')

    # Assert
    assert result.exit_code == 0
    assert _find('staff10').department_id == department.id


def test_create_staff_prompts_for_everything_and_lists_departments(runner, department):
    # Act
    result = _run(runner, ['create-staff'], input=f'staff10\n田中\n{department.id}\n{PW}\n{PW}\n')

    # Assert: 部署の一覧（ID: 名前）を表示して入力を求める
    assert result.exit_code == 0
    assert f'{department.id}: 道路管理課' in result.output
    assert '職員ID' in result.output and '表示名' in result.output and '所属部署' in result.output
    assert _find('staff10').department_id == department.id


def test_create_staff_unknown_department_fails_without_creating(runner, department):
    # Act
    result = _run(runner, ['create-staff', '--login-id', 'staff10', '--name', '田中', '--department', '存在しない課'])

    # Assert: パスワードを聞く前に失敗する
    assert result.exit_code == 1
    assert f'Error: {MSG_DEPARTMENT_INVALID}' in result.output
    assert _find('staff10') is None


def test_create_staff_duplicate_id_fails(runner, department, staff):
    # Act
    result = _run(runner, ['create-staff', '--login-id', 'staff01', '--name', '別の人', '--department', '道路管理課'],
                  input=f'{PW}\n{PW}\n')

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_ID_TAKEN.format(label="職員ID")}' in result.output
    assert User.query.filter_by(login_id='staff01').count() == 1
    assert _find('staff01').name == '鈴木 健一'


def test_create_staff_id_with_at_fails(runner, department):
    # Act
    result = _run(runner, ['create-staff', '--login-id', 'a@example.com', '--name', '田中', '--department', '道路管理課'],
                  input=f'{PW}\n{PW}\n')

    # Assert
    assert result.exit_code == 1
    assert 'Error: ' + MSG_ID_HAS_AT.format(label='職員ID') in result.output
    assert _find('a@example.com') is None


def test_create_staff_short_and_public_password_fail(runner, department):
    # Act
    short = _run(runner, ['create-staff', '--login-id', 's1', '--name', '田中', '--department', '道路管理課'],
                 input='short\nshort\n')
    public = _run(runner, ['create-staff', '--login-id', 's1', '--name', '田中', '--department', '道路管理課'],
                  input='password123\npassword123\n')

    # Assert
    assert short.exit_code == 1 and MSG_PASSWORD_TOO_SHORT.format(length=5) in short.output
    assert public.exit_code == 1 and MSG_PASSWORD_PUBLIC in public.output
    assert _find('s1') is None


def test_create_staff_new_account_can_log_in(runner, client, department):
    # Arrange
    _run(runner, ['create-staff', '--login-id', 'staff10', '--name', '田中', '--department', '道路管理課'],
         input=f'{PW}\n{PW}\n')

    # Act
    response = client.post('/login/staff', data={'staff_id': 'staff10', 'password': PW})

    # Assert
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/staff/dashboard')


def test_create_staff_with_no_departments_fails_on_prompt(runner, app):
    # Act: 部署が 1 件もないとき
    result = _run(runner, ['create-staff', '--login-id', 's1', '--name', '田中'], input='なし\n')

    # Assert
    assert result.exit_code == 1
    assert f'Error: {MSG_DEPARTMENT_INVALID}' in result.output
    assert Department.query.count() == 0 and _find('s1') is None


# ---------------------------------------------------------------------------
# issue-reset-code
# ---------------------------------------------------------------------------

def test_issue_reset_code_prints_code_and_expiry(runner, staff):
    # Act
    result = _run(runner, ['issue-reset-code', 'staff01'])

    # Assert
    assert result.exit_code == 0
    match = re.search(r'「staff01」の再設定コード: ([A-Z0-9]{5}-[A-Z0-9]{5})', result.output)
    assert match
    assert re.search(r'有効期限: \d{4}/\d{2}/\d{2} \d{2}:\d{2}（日本時間）・1回だけ使えます。', result.output)


def test_issue_reset_code_printed_code_resets_password(runner, staff, client):
    # Arrange
    result = _run(runner, ['issue-reset-code', 'staff01'])
    code = re.search(r'再設定コード: (\S+)', result.output).group(1)

    # Act: 画面から再設定する
    response = client.post('/login/staff/reset-password', data={
        'login_id': 'staff01', 'reset_code': code, 'new_password': NEW_PW, 'confirm_password': NEW_PW})

    # Assert
    assert response.status_code == 302
    assert auth_service.authenticate(User.ROLE_STAFF, 'staff01', NEW_PW).id == staff.id


def test_issue_reset_code_works_for_admin(runner, admin):
    # Act
    result = _run(runner, ['issue-reset-code', 'admin01'])

    # Assert
    assert result.exit_code == 0 and '「admin01」の再設定コード' in result.output


def test_issue_reset_code_reissue_invalidates_previous(runner, staff, client):
    # Arrange
    first = re.search(r'再設定コード: (\S+)', _run(runner, ['issue-reset-code', 'staff01']).output).group(1)
    _run(runner, ['issue-reset-code', 'staff01'])

    # Act
    response = client.post('/login/staff/reset-password', data={
        'login_id': 'staff01', 'reset_code': first, 'new_password': NEW_PW, 'confirm_password': NEW_PW})

    # Assert: 古いコードは通らず、パスワードもそのまま
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(User, staff.id).check_password('password123')


def test_issue_reset_code_refuses_citizen_and_unknown(runner, citizen):
    # Act
    citizen_result = _run(runner, ['issue-reset-code', 'taro@example.com'])
    unknown_result = _run(runner, ['issue-reset-code', 'nobody'])

    # Assert
    assert citizen_result.exit_code == 1
    assert 'Error: ' + MSG_RESET_CODE_NOT_ALLOWED.format(login_id='taro@example.com') in citizen_result.output
    assert unknown_result.exit_code == 1
    assert 'Error: ' + MSG_RESET_CODE_NOT_ALLOWED.format(login_id='nobody') in unknown_result.output


def test_issue_reset_code_refuses_inactive_account(runner, staff):
    # Arrange
    staff.is_active = False
    db.session.commit()

    # Act
    result = _run(runner, ['issue-reset-code', 'staff01'])

    # Assert
    assert result.exit_code == 1
    assert 'Error: ' + MSG_RESET_CODE_NOT_ALLOWED.format(login_id='staff01') in result.output


def test_issue_reset_code_does_not_log_the_code(runner, staff, caplog):
    # Act
    with caplog.at_level('DEBUG'):
        result = _run(runner, ['issue-reset-code', 'staff01'])

    # Assert: コードは画面にだけ出て、ログには残らない
    code = re.search(r'再設定コード: (\S+)', result.output).group(1)
    assert code not in caplog.text and code.replace('-', '') not in caplog.text


# ---------------------------------------------------------------------------
# set-password（既存コマンド）：セッションの印が変わり、古いセッションが無効になる（基準 4）
# ---------------------------------------------------------------------------

def test_set_password_command_invalidates_existing_session(runner, staff):
    # Arrange
    old_value = staff.get_id()

    # Act
    result = _run(runner, ['set-password', 'staff01'], input=f'{NEW_PW}\n{NEW_PW}\n')

    # Assert
    assert result.exit_code == 0
    assert load_user(old_value) is None
    db.session.expire_all()
    assert load_user(db.session.get(User, staff.id).get_id()) is not None


def test_create_staff_does_not_touch_other_accounts(runner, department):
    # Arrange
    other = _make_user('staff99', '他の職員', User.ROLE_STAFF)
    before = other.password_hash

    # Act
    _run(runner, ['create-staff', '--login-id', 'staff10', '--name', '田中', '--department', '道路管理課'],
         input=f'{PW}\n{PW}\n')

    # Assert
    db.session.expire_all()
    assert db.session.get(User, other.id).password_hash == before
