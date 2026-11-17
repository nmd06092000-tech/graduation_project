# GĐ3a の service テスト：user_service（一覧・削除・ロック解除・再設定コード・復元）と、論理削除（del_flag）が
# 認証のすべての経路（is_usable）で効いていること。テスト DB（TEST_DATABASE_URL）が必要
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import inspect, select

from app.constants import (
    MSG_ACCOUNT_DELETED_RESTORE_FIRST, MSG_ACCOUNT_DISABLED, MSG_ACCOUNT_NOT_DELETED, MSG_ACCOUNT_TARGET_NOT_FOUND,
    MSG_CANNOT_DELETE_LAST_ADMIN, MSG_CANNOT_DELETE_SELF, MSG_ID_TAKEN, MSG_LOGIN_FAILED, MSG_NO_ADMIN_LEFT,
    MSG_RESET_CODE_INVALID, MSG_RESET_CODE_NOT_ALLOWED, MSG_RESET_LINK_INVALID, MSG_USER_NOT_FOUND,
)
from app.extensions import db
from app.models import Department, PasswordResetToken, User
from app.models.user import load_user
from app.services import ServiceError, ValidationError, auth_service, user_service
from app.services.report_service import format_datetime
from tests.conftest import lock_account, make_account, sent_reset_token, soft_delete

NEW_PW = 'NewPass-9876'
T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
ROW_KEYS = {'id', 'login_id', 'name', 'role', 'role_label', 'department_name', 'is_active', 'is_locked',
            'locked_until_text'}


@pytest.fixture
def depts(app):
    """部署 2 つ（道路課・公園課）"""
    roads, parks = Department(name='道路課'), Department(name='公園課')
    db.session.add_all([roads, parks])
    db.session.commit()
    return roads, parks


@pytest.fixture
def world(depts):
    """管理者 2 人・職員 2 人（ID に _ と % を含む）・市民 1 人"""
    roads, parks = depts
    return {
        'admin': make_account('adm1', User.ROLE_ADMIN, name='佐藤 管理'),
        'admin2': make_account('adm2', User.ROLE_ADMIN, name='高橋 管理'),
        'staff': make_account('stf_1', User.ROLE_STAFF, name='山田 道路', department=roads),
        'staff2': make_account('stf%2', User.ROLE_STAFF, name='田中 公園', department=parks),
        'citizen': make_account('c@example.com', User.ROLE_CITIZEN, name='市民 太郎'),
        'roads': roads, 'parks': parks,
    }


def _ids(users):
    return sorted(user.login_id for user in users)


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _unused_tokens(user_id):
    return PasswordResetToken.query.filter_by(user_id=user_id, used_at=None).count()


def _bad_target(kind, world):
    """「対象なし」になる ID の種類 → 実際の ID（市民・削除済みは world から用意する）"""
    if kind == 'citizen':
        return world['citizen'].id
    if kind == 'deleted':
        return soft_delete(make_account('gone', User.ROLE_STAFF)).id
    return {'missing': 999999, 'zero': 0, 'negative': -1, 'over_bigint': 2 ** 63, 'huge': 10 ** 30,
            'string': str(world['staff'].id)}[kind]


NOT_FOUND_KINDS = ['missing', 'citizen', 'deleted', 'negative', 'over_bigint', 'string']


# ---------------------------------------------------------------------------
# list_accounts / count_accounts / account_filters
# ---------------------------------------------------------------------------

def test_list_accounts_returns_staff_and_admin_only(world):
    # Act / Assert: 市民は含まれない
    assert _ids(user_service.list_accounts()) == ['adm1', 'adm2', 'stf%2', 'stf_1']


def test_list_accounts_excludes_deleted_accounts(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act / Assert
    assert _ids(user_service.list_accounts()) == ['adm1', 'adm2', 'stf%2']


def test_list_accounts_includes_disabled_accounts(world):
    # Arrange: 利用停止（is_active=False）は一覧に残る（削除はできる）
    world['staff'].is_active = False
    db.session.commit()

    # Act / Assert
    assert 'stf_1' in _ids(user_service.list_accounts())


def test_list_accounts_newest_first(world):
    # Arrange: 作成日時を明示して順序を固定する
    for index, key in enumerate(['admin', 'staff', 'admin2', 'staff2']):
        world[key].created_at = T0 + timedelta(days=index)
    db.session.commit()

    # Act
    result = [user.login_id for user in user_service.list_accounts()]

    # Assert
    assert result == ['stf%2', 'adm2', 'stf_1', 'adm1']


def test_list_accounts_same_created_at_orders_by_id_desc(world):
    # Arrange: 作成日時が同じなら ID の大きい（新しい）順
    for key in ('admin', 'staff', 'admin2', 'staff2'):
        world[key].created_at = T0
    db.session.commit()

    # Act
    result = [user.login_id for user in user_service.list_accounts()]

    # Assert: 作成順は adm1, adm2, stf_1, stf%2
    assert result == ['stf%2', 'stf_1', 'adm2', 'adm1']


def test_list_accounts_filters_by_role(world):
    # Act / Assert
    assert _ids(user_service.list_accounts(role='staff')) == ['stf%2', 'stf_1']
    assert _ids(user_service.list_accounts(role='admin')) == ['adm1', 'adm2']


def test_list_accounts_filters_by_department_id_as_int_or_text(world):
    # Act / Assert: 画面のクエリ文字列（文字列）でも ID でも同じ
    assert _ids(user_service.list_accounts(department_id=world['roads'].id)) == ['stf_1']
    assert _ids(user_service.list_accounts(department_id=str(world['parks'].id))) == ['stf%2']


@pytest.mark.parametrize('query, expected', [
    ('山田', ['stf_1']),
    ('管理', ['adm1', 'adm2']),
    ('道路', ['stf_1']),
])
def test_list_accounts_search_matches_name_partially(world, query, expected):
    # Act / Assert
    assert _ids(user_service.list_accounts(q=query)) == expected


@pytest.mark.parametrize('query', ['STF', 'stf', 'Stf_'])
def test_list_accounts_search_by_login_id_ignores_case(world, query):
    # Act / Assert: 大文字・小文字を区別しない部分一致（'Stf_' は「_」を文字として扱うので stf_1 だけ）
    expected = ['stf_1'] if query == 'Stf_' else ['stf%2', 'stf_1']
    assert _ids(user_service.list_accounts(q=query)) == expected


@pytest.mark.parametrize('query, expected', [
    ('%', ['stf%2']),      # % は「任意の文字列」ではなく文字そのもの
    ('_', ['stf_1']),      # _ は「任意の 1 文字」ではなく文字そのもの
    ('\\', []),            # バックスラッシュも文字として扱う（エラーにならない）
    ('%%', []),
    ('f%2', ['stf%2']),
])
def test_list_accounts_search_treats_like_wildcards_as_literal_characters(world, query, expected):
    # Act / Assert
    assert _ids(user_service.list_accounts(q=query)) == expected


@pytest.mark.parametrize('query', ['', '   ', None])
def test_list_accounts_blank_search_returns_all(world, query):
    # Act / Assert
    assert len(user_service.list_accounts(q=query)) == 4


def test_list_accounts_search_does_not_match_deleted_or_citizen(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act / Assert
    assert user_service.list_accounts(q='stf_1') == []
    assert user_service.list_accounts(q='example.com') == []


def test_list_accounts_combines_role_department_and_search(world):
    # Act / Assert: 条件は AND
    assert _ids(user_service.list_accounts(role='staff', department_id=world['roads'].id, q='山')) == ['stf_1']
    assert user_service.list_accounts(role='admin', department_id=world['roads'].id) == []
    assert user_service.list_accounts(role='staff', q='管理') == []


@pytest.mark.parametrize('role, department', [('citizen', 'abc'), ('ADMIN', '１２'), ('', ' '), ('staff; DROP', '1 OR 1=1')])
def test_list_accounts_ignores_invalid_role_and_department(world, role, department):
    # Act / Assert: 不正な値は無視して全件（エラーにしない）
    assert len(user_service.list_accounts(role=role, department_id=department)) == 4


def test_list_accounts_limit(world):
    # Act / Assert
    assert len(user_service.list_accounts(limit=2)) == 2
    assert len(user_service.list_accounts(limit=100)) == 4


def test_list_accounts_loads_department_without_extra_queries(world):
    # Act
    users = user_service.list_accounts()
    db.session.expire_all()
    users = user_service.list_accounts()

    # Assert: 部署は読み込み済み（1 行ごとに SELECT しない）
    assert all('department' not in inspect(user).unloaded for user in users)


def test_count_accounts_counts_staff_and_admin_not_deleted(world):
    # Arrange
    assert user_service.count_accounts() == 4
    soft_delete(world['staff2'], by=world['admin'])

    # Act / Assert: 市民と削除済みは数えない。利用停止は数える
    world['staff'].is_active = False
    db.session.commit()
    assert user_service.count_accounts() == 3


@pytest.mark.parametrize('role, department, q, expected', [
    ('staff', '5', ' abc ', {'role': 'staff', 'department': '5', 'q': 'abc'}),
    ('admin', None, None, {'role': 'admin', 'department': '', 'q': ''}),
    ('citizen', 'x1', ' a ', {'role': '', 'department': '', 'q': 'a'}),
    (None, '007', '', {'role': '', 'department': '7', 'q': ''}),
    ('', '１２', None, {'role': '', 'department': '', 'q': ''}),
    ('staff', '9' * 19, None, {'role': 'staff', 'department': '', 'q': ''}),
])
def test_account_filters_normalizes_query_values(app, role, department, q, expected):
    # Act / Assert
    assert user_service.account_filters(role, department, q) == expected


# ---------------------------------------------------------------------------
# account_row
# ---------------------------------------------------------------------------

def test_account_row_has_nine_fields_for_staff(world):
    # Act
    row = user_service.account_row(world['staff'])

    # Assert
    assert set(row) == ROW_KEYS
    assert row == {
        'id': world['staff'].id, 'login_id': 'stf_1', 'name': '山田 道路', 'role': 'staff', 'role_label': '職員',
        'department_name': '道路課', 'is_active': True, 'is_locked': False, 'locked_until_text': None}


def test_account_row_admin_has_empty_department_name(world):
    # Act
    row = user_service.account_row(world['admin'])

    # Assert
    assert row['department_name'] == '' and row['role_label'] == '管理者'


def test_account_row_locked_shows_deadline_in_japan_time(world, clock):
    # Arrange: 時計を固定してロックの期限を 10 分後にする
    world['staff'].locked_until = clock.now + timedelta(minutes=10)
    db.session.commit()

    # Act
    row = user_service.account_row(world['staff'])

    # Assert: 2026/01/01 09:10（JST）
    assert row['is_locked'] is True
    assert row['locked_until_text'] == '2026/01/01 09:10'


def test_account_row_expired_lock_is_not_locked(world, clock):
    # Arrange: 期限が過ぎたロックは「ロックなし」
    world['staff'].locked_until = clock.now - timedelta(minutes=1)
    db.session.commit()

    # Act
    row = user_service.account_row(world['staff'])

    # Assert
    assert row['is_locked'] is False and row['locked_until_text'] is None


def test_account_row_reports_is_active_false_for_disabled(world):
    # Arrange
    world['staff'].is_active = False
    db.session.commit()

    # Act / Assert
    assert user_service.account_row(world['staff'])['is_active'] is False


# ---------------------------------------------------------------------------
# delete_account
# ---------------------------------------------------------------------------

def test_delete_account_sets_all_three_columns_in_one_write(world):
    # Act
    result = user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Assert
    assert result.id == world['staff'].id
    fresh = _fresh(world['staff'].id)
    assert fresh.del_flag is True and fresh.is_deleted
    assert fresh.deleted_by_id == world['admin'].id
    assert abs(fresh.deleted_at - datetime.now(timezone.utc)) < timedelta(seconds=30)


def test_delete_account_keeps_the_rest_of_the_row(world):
    # Arrange
    password_hash = world['staff'].password_hash

    # Act
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Assert: 物理削除ではなく、ID・氏名・部署・パスワードは残る（復元できる）
    fresh = _fresh(world['staff'].id)
    assert (fresh.login_id, fresh.name, fresh.role) == ('stf_1', '山田 道路', 'staff')
    assert fresh.department_id == world['roads'].id and fresh.password_hash == password_hash


def test_delete_account_does_not_touch_other_accounts(world):
    # Act
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Assert
    for key in ('admin', 'admin2', 'staff2', 'citizen'):
        assert _fresh(world[key].id).del_flag is False


def test_delete_account_rejects_deleting_self(world):
    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.delete_account(world['admin'].id, actor_id=world['admin'].id)

    # Assert
    assert str(excinfo.value) == MSG_CANNOT_DELETE_SELF
    assert _fresh(world['admin'].id).del_flag is False


def test_delete_account_rejects_last_usable_admin(world):
    # Arrange: 管理者 2 人のうち 1 人は利用停止 → 使える管理者は adm2 だけ
    world['admin'].is_active = False
    db.session.commit()

    # Act: 実行者は別アカウント（id が違えば「自分自身」にならない）
    with pytest.raises(ServiceError) as excinfo:
        user_service.delete_account(world['admin2'].id, actor_id=world['staff'].id)

    # Assert
    assert str(excinfo.value) == MSG_CANNOT_DELETE_LAST_ADMIN
    assert _fresh(world['admin2'].id).del_flag is False


def test_delete_account_rejects_last_admin_when_other_admin_is_already_deleted(world):
    # Arrange: もう 1 人の管理者は削除済み（使えない管理者には数えない）
    soft_delete(world['admin'], by=world['admin2'])

    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.delete_account(world['admin2'].id, actor_id=world['staff'].id)

    # Assert
    assert str(excinfo.value) == MSG_CANNOT_DELETE_LAST_ADMIN


def test_delete_account_allows_admin_when_another_usable_admin_exists(world):
    # Act
    user_service.delete_account(world['admin2'].id, actor_id=world['admin'].id)

    # Assert
    assert _fresh(world['admin2'].id).del_flag is True
    assert _fresh(world['admin'].id).is_usable


def test_delete_account_allows_disabled_admin_even_if_only_other_admin(world):
    # Arrange: 使える管理者は adm1 だけ。利用停止の adm2 を消しても使える管理者は減らない
    world['admin2'].is_active = False
    db.session.commit()

    # Act
    user_service.delete_account(world['admin2'].id, actor_id=world['admin'].id)

    # Assert
    assert _fresh(world['admin2'].id).del_flag is True


def test_delete_account_allows_disabled_staff(world):
    # Arrange: 利用停止中でも削除できる（is_active は変わらない）
    world['staff'].is_active = False
    db.session.commit()

    # Act
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Assert
    fresh = _fresh(world['staff'].id)
    assert fresh.del_flag is True and fresh.is_active is False


@pytest.mark.parametrize('kind', NOT_FOUND_KINDS)
def test_delete_account_target_not_found(world, kind):
    # Arrange
    target_id = _bad_target(kind, world)

    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.delete_account(target_id, actor_id=world['admin'].id)

    # Assert: 市民・削除済み・存在しない・範囲外・数値でない ID は同じ「対象なし」。何も変わらない
    assert str(excinfo.value) == MSG_ACCOUNT_TARGET_NOT_FOUND
    assert _fresh(world['citizen'].id).del_flag is False
    assert User.query.filter_by(del_flag=True).count() == (1 if kind == 'deleted' else 0)


def test_delete_account_twice_second_time_is_not_found(world):
    # Arrange
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)
    deleted_at = _fresh(world['staff'].id).deleted_at

    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.delete_account(world['staff'].id, actor_id=world['admin2'].id)

    # Assert: 削除日時・削除者は最初のまま
    assert str(excinfo.value) == MSG_ACCOUNT_TARGET_NOT_FOUND
    fresh = _fresh(world['staff'].id)
    assert fresh.deleted_at == deleted_at and fresh.deleted_by_id == world['admin'].id


def test_delete_account_invalidates_unused_reset_code(world):
    # Arrange
    code, _ = auth_service.issue_reset_code('stf_1')
    assert _unused_tokens(world['staff'].id) == 1

    # Act
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Assert: 未使用のコードは使用済みにされ、復元しても使えない
    assert _unused_tokens(world['staff'].id) == 0
    assert PasswordResetToken.query.filter_by(user_id=world['staff'].id).count() == 1


def test_delete_account_keeps_login_id_reserved(world):
    # Arrange
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Act: 同じ ID で新規作成
    with pytest.raises(ValidationError) as excinfo:
        user_service.create_staff(login_id='stf_1', name='新人', password=NEW_PW, department_id=world['roads'].id)

    # Assert: 「既に登録されています」（削除済みであることは伝えない）
    assert excinfo.value.messages == [('warning', MSG_ID_TAKEN.format(label='職員ID'))]


def test_delete_account_failure_rolls_back_and_leaves_session_usable(world):
    # Act
    with pytest.raises(ServiceError):
        user_service.delete_account(999999, actor_id=world['admin'].id)

    # Assert: ロックを残さず、同じセッションで続けて削除できる
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)
    assert _fresh(world['staff'].id).del_flag is True


# ---------------------------------------------------------------------------
# unlock_account
# ---------------------------------------------------------------------------

def test_unlock_account_clears_lock_and_failure_count(world):
    # Arrange
    lock_account(world['staff'])

    # Act
    result = user_service.unlock_account(world['staff'].id)

    # Assert
    assert result.id == world['staff'].id
    fresh = _fresh(world['staff'].id)
    assert fresh.locked_until is None and fresh.failed_login_count == 0


def test_unlock_account_allows_login_immediately(world):
    # Arrange
    lock_account(world['staff'])
    with pytest.raises(ValidationError):
        auth_service.authenticate('staff', 'stf_1', 'password123')

    # Act
    user_service.unlock_account(world['staff'].id)

    # Assert
    assert auth_service.authenticate('staff', 'stf_1', 'password123').id == world['staff'].id


def test_unlock_account_succeeds_when_not_locked_and_changes_nothing_else(world):
    # Arrange
    before = world['staff'].password_hash

    # Act
    user_service.unlock_account(world['staff'].id)

    # Assert
    fresh = _fresh(world['staff'].id)
    assert fresh.locked_until is None and fresh.failed_login_count == 0
    assert fresh.password_hash == before and fresh.is_active is True


def test_unlock_account_keeps_disabled_flag(world):
    # Arrange
    world['staff'].is_active = False
    lock_account(world['staff'])

    # Act
    user_service.unlock_account(world['staff'].id)

    # Assert: ロックだけ解除。利用停止は解除しない
    fresh = _fresh(world['staff'].id)
    assert fresh.locked_until is None and fresh.is_active is False


@pytest.mark.parametrize('kind', NOT_FOUND_KINDS)
def test_unlock_account_target_not_found(world, kind):
    # Arrange
    target_id = _bad_target(kind, world)

    # Act / Assert
    with pytest.raises(ServiceError) as excinfo:
        user_service.unlock_account(target_id)
    assert str(excinfo.value) == MSG_ACCOUNT_TARGET_NOT_FOUND


# ---------------------------------------------------------------------------
# issue_reset_code_for_account
# ---------------------------------------------------------------------------

def test_issue_reset_code_returns_display_data_and_code_works(app, world, clock):
    # Act
    issued = user_service.issue_reset_code_for_account(world['staff'].id)

    # Assert: 返り値のキー・形式・有効期限（日本時間の文字列）
    assert set(issued) == {'name', 'login_id', 'code', 'expires_at_text', 'is_locked'}
    assert issued['name'] == '山田 道路' and issued['login_id'] == 'stf_1' and issued['is_locked'] is False
    assert len(issued['code']) == 11 and issued['code'][5] == '-'
    expected_expiry = clock.now + timedelta(minutes=app.config['PASSWORD_RESET_TOKEN_MINUTES'])
    assert issued['expires_at_text'] == format_datetime(expected_expiry)

    # Assert: そのコードで本当にパスワードを再設定できる
    user = auth_service.reset_password_with_code('staff', 'stf_1', issued['code'], NEW_PW, NEW_PW)
    assert user.check_password(NEW_PW)


def test_issue_reset_code_stores_only_the_hash(world):
    # Act
    issued = user_service.issue_reset_code_for_account(world['staff'].id)

    # Assert: DB に平文のコードは残らない
    stored = PasswordResetToken.query.filter_by(user_id=world['staff'].id).one()
    assert issued['code'] not in stored.token_hash
    assert issued['code'].replace('-', '') not in stored.token_hash
    assert len(stored.token_hash) == 64


def test_issue_reset_code_for_locked_account_succeeds_and_flags_it(world):
    # Arrange
    lock_account(world['staff'])

    # Act
    issued = user_service.issue_reset_code_for_account(world['staff'].id)

    # Assert: 発行はできる。ロックは解除しない
    assert issued['is_locked'] is True
    assert _fresh(world['staff'].id).locked_until is not None


def test_issue_reset_code_for_expired_lock_is_not_flagged_locked(world, clock):
    # Arrange
    world['staff'].locked_until = clock.now - timedelta(minutes=1)
    db.session.commit()

    # Act / Assert
    assert user_service.issue_reset_code_for_account(world['staff'].id)['is_locked'] is False


def test_issue_reset_code_replaces_previous_code(world):
    # Arrange
    first = user_service.issue_reset_code_for_account(world['staff'].id)['code']

    # Act
    second = user_service.issue_reset_code_for_account(world['staff'].id)['code']

    # Assert: 古いコードは使えず、有効なのは最新の 1 つ
    assert _unused_tokens(world['staff'].id) == 1
    with pytest.raises(ServiceError) as excinfo:
        auth_service.reset_password_with_code('staff', 'stf_1', first, NEW_PW, NEW_PW)
    assert str(excinfo.value) == MSG_RESET_CODE_INVALID
    assert auth_service.reset_password_with_code('staff', 'stf_1', second, NEW_PW, NEW_PW).check_password(NEW_PW)


def test_issue_reset_code_works_for_admin(world):
    # Act
    issued = user_service.issue_reset_code_for_account(world['admin2'].id)

    # Assert
    assert issued['login_id'] == 'adm2' and len(issued['code']) == 11


@pytest.mark.parametrize('kind', NOT_FOUND_KINDS)
def test_issue_reset_code_target_not_found(world, kind):
    # Arrange
    target_id = _bad_target(kind, world)

    # Act / Assert: 市民・削除済みには発行しない。トークンは作られない
    with pytest.raises(ServiceError) as excinfo:
        user_service.issue_reset_code_for_account(target_id)
    assert str(excinfo.value) == MSG_ACCOUNT_TARGET_NOT_FOUND
    assert PasswordResetToken.query.count() == 0


def test_issue_reset_code_rejects_disabled_account(world):
    # Arrange
    world['staff'].is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.issue_reset_code_for_account(world['staff'].id)

    # Assert: 利用停止は「有効な職員・管理者ではない」
    assert str(excinfo.value) == MSG_RESET_CODE_NOT_ALLOWED.format(login_id='stf_1')
    assert PasswordResetToken.query.count() == 0


# ---------------------------------------------------------------------------
# restore_account / list_deleted_accounts
# ---------------------------------------------------------------------------

def test_restore_account_clears_flags_lock_and_tokens(world):
    # Arrange: ロック中・コード発行済みの状態で削除し、さらに削除後にロックが残った状態にする
    auth_service.issue_reset_code('stf_1')
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)
    lock_account(world['staff'])
    db.session.add(PasswordResetToken(
        user_id=world['staff'].id, token_hash='f' * 64, created_at=T0, expires_at=T0 + timedelta(days=3650)))
    db.session.commit()

    # Act
    result = user_service.restore_account('stf_1')

    # Assert
    assert result.id == world['staff'].id
    fresh = _fresh(world['staff'].id)
    assert fresh.del_flag is False and fresh.deleted_at is None and fresh.deleted_by_id is None
    assert fresh.locked_until is None and fresh.failed_login_count == 0
    assert _unused_tokens(world['staff'].id) == 0


def test_restore_account_allows_login_with_original_password(world):
    # Arrange
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Act
    user_service.restore_account('stf_1')

    # Assert: 一覧にも戻る
    assert auth_service.authenticate('staff', 'stf_1', 'password123').id == world['staff'].id
    assert 'stf_1' in _ids(user_service.list_accounts())


def test_restore_account_strips_whitespace_in_login_id(world):
    # Arrange
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Act / Assert
    assert user_service.restore_account('  stf_1 ').login_id == 'stf_1'


def test_restore_account_not_deleted_raises(world):
    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.restore_account('stf_1')

    # Assert
    assert str(excinfo.value) == MSG_ACCOUNT_NOT_DELETED.format(login_id='stf_1')


def test_restore_account_unknown_login_id_raises(world):
    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.restore_account('nobody')

    # Assert
    assert str(excinfo.value) == MSG_USER_NOT_FOUND.format(login_id='nobody')


def test_restore_account_does_not_accept_numeric_id(world):
    # Arrange: 引数は login_id（数値の id ではない）
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.restore_account(str(world['staff'].id))

    # Assert
    assert str(excinfo.value) == MSG_USER_NOT_FOUND.format(login_id=str(world['staff'].id))
    assert _fresh(world['staff'].id).del_flag is True


def test_restore_account_twice_second_raises_not_deleted(world):
    # Arrange
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)
    user_service.restore_account('stf_1')

    # Act / Assert
    with pytest.raises(ServiceError, match='削除されていない'):
        user_service.restore_account('stf_1')


def test_list_deleted_accounts_empty(world):
    # Act / Assert
    assert user_service.list_deleted_accounts() == []


def test_list_deleted_accounts_rows_newest_deletion_first(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'], at=T0)
    soft_delete(world['staff2'], by=world['admin2'], at=T0 + timedelta(hours=1))

    # Act
    rows = user_service.list_deleted_accounts()

    # Assert: 削除が新しい順。削除日時は日本時間、削除者は ID
    assert rows == [
        {'login_id': 'stf%2', 'name': '田中 公園', 'role': 'staff', 'role_label': '職員',
         'deleted_at_text': '2026/01/01 10:00', 'deleted_by': 'adm2'},
        {'login_id': 'stf_1', 'name': '山田 道路', 'role': 'staff', 'role_label': '職員',
         'deleted_at_text': '2026/01/01 09:00', 'deleted_by': 'adm1'},
    ]


def test_list_deleted_accounts_without_deleter_has_empty_name(world):
    # Arrange: 削除者が不明（deleted_by_id なし）の削除済みアカウント
    soft_delete(world['staff'], by=None, at=T0)

    # Act / Assert
    assert user_service.list_deleted_accounts()[0]['deleted_by'] == ''


def test_list_deleted_accounts_excludes_active_accounts(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act / Assert
    assert [row['login_id'] for row in user_service.list_deleted_accounts()] == ['stf_1']


# ---------------------------------------------------------------------------
# User.is_usable / is_deleted / load_user
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('is_active, deleted, usable', [
    (True, False, True), (False, False, False), (True, True, False), (False, True, False),
])
def test_user_is_usable_truth_table_python_and_sql(app, is_active, deleted, usable):
    # Arrange
    user = make_account('probe', User.ROLE_STAFF, is_active=is_active)
    if deleted:
        soft_delete(user)

    # Act
    in_sql = db.session.execute(select(User.id).where(User.id == user.id, User.is_usable)).first() is not None

    # Assert: Python 側の判定と SQL 式の判定が一致する
    assert user.is_usable is usable
    assert in_sql is usable
    assert user.is_deleted is deleted


def test_load_user_returns_none_for_deleted_account(app):
    # Arrange
    user = make_account('probe', User.ROLE_STAFF)
    session_value = user.get_id()
    assert load_user(session_value).id == user.id

    # Act
    soft_delete(user)

    # Assert: 削除済みは、パスワードの印が合っていても復元しない
    assert load_user(session_value) is None


def test_load_user_returns_none_for_disabled_account(app):
    # Arrange
    user = make_account('probe', User.ROLE_STAFF, is_active=False)

    # Act / Assert
    assert load_user(user.get_id()) is None


def test_load_user_works_again_after_restore(world):
    # Arrange
    session_value = world['staff'].get_id()
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)
    assert load_user(session_value) is None

    # Act
    user_service.restore_account('stf_1')

    # Assert
    assert load_user(session_value).id == world['staff'].id


# ---------------------------------------------------------------------------
# 認証の各経路で削除済みは「使えない」（is_usable）
# ---------------------------------------------------------------------------

def _login_failed_message(login_id='stf_1'):
    return MSG_LOGIN_FAILED.format(label='職員ID')


@pytest.mark.parametrize('password', ['password123', 'wrong-password'])
def test_authenticate_deleted_account_looks_like_unknown_id(world, password):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with pytest.raises(ServiceError) as deleted_error:
        auth_service.authenticate('staff', 'stf_1', password)
    with pytest.raises(ServiceError) as unknown_error:
        auth_service.authenticate('staff', 'no_such_id', password)

    # Assert: ID がない場合と完全に同じ文言（削除済みだと分からない）
    assert str(deleted_error.value) == str(unknown_error.value) == _login_failed_message()


def test_authenticate_deleted_account_never_counts_failures_or_locks(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act: 上限（5 回）を超えて試す
    for _ in range(8):
        with pytest.raises(ServiceError) as excinfo:
            auth_service.authenticate('staff', 'stf_1', 'wrong-password')
        assert str(excinfo.value) == _login_failed_message()

    # Assert: 失敗回数は増えず、ロックもされない（ロックの文言も出ない）
    fresh = _fresh(world['staff'].id)
    assert fresh.failed_login_count == 0 and fresh.locked_until is None and fresh.last_login_at is None


def test_authenticate_deleted_account_runs_dummy_hash_and_not_real_password_check(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with mock.patch('app.services.auth_service.check_password_hash') as dummy_check, \
            mock.patch.object(User, 'check_password', autospec=True) as real_check:
        with pytest.raises(ServiceError):
            auth_service.authenticate('staff', 'stf_1', 'password123')

    # Assert: 応答時間をそろえるため偽のハッシュで計算し、本物のパスワードは調べない
    dummy_check.assert_called_once()
    real_check.assert_not_called()


def test_authenticate_deleted_and_locked_account_shows_generic_failure(world):
    # Arrange: ロックが残ったまま削除された
    lock_account(world['staff'])
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate('staff', 'stf_1', 'password123')

    # Assert: ロックの文言（ValidationError）ではなく通常の失敗文言
    assert not isinstance(excinfo.value, ValidationError)
    assert str(excinfo.value) == _login_failed_message()


def test_authenticate_disabled_account_still_reports_disabled(world):
    # Arrange: 利用停止（is_active=False）は従来どおり別の文言
    world['staff'].is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.authenticate('staff', 'stf_1', 'password123')

    # Assert
    assert str(excinfo.value) == MSG_ACCOUNT_DISABLED


def test_authenticate_succeeds_again_after_restore(world):
    # Arrange
    user_service.delete_account(world['staff'].id, actor_id=world['admin'].id)

    # Act
    user_service.restore_account('stf_1')

    # Assert
    assert auth_service.authenticate('staff', 'stf_1', 'password123').id == world['staff'].id


def test_issue_reset_code_rejects_deleted_account(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.issue_reset_code('stf_1')

    # Assert
    assert str(excinfo.value) == MSG_RESET_CODE_NOT_ALLOWED.format(login_id='stf_1')
    assert PasswordResetToken.query.count() == 0


def test_reset_password_with_code_rejects_account_deleted_after_issue(world):
    # Arrange: コード発行後、トークンの無効化をせずに削除フラグだけ立てる（is_usable の判定だけを見る）
    code, _ = auth_service.issue_reset_code('stf_1')
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.reset_password_with_code('staff', 'stf_1', code, NEW_PW, NEW_PW)

    # Assert
    assert str(excinfo.value) == MSG_RESET_CODE_INVALID
    assert _fresh(world['staff'].id).check_password('password123')


def test_change_password_rejects_deleted_account(world):
    # Arrange
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.change_password(world['staff'].id, current_password='password123',
                                     new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert
    assert str(excinfo.value) == MSG_ACCOUNT_DISABLED
    assert _fresh(world['staff'].id).check_password('password123')


def test_request_password_reset_sends_nothing_for_deleted_citizen(world, outbox):
    # Arrange
    soft_delete(world['citizen'])

    # Act
    auth_service.request_password_reset('c@example.com')

    # Assert: 通常と同じく結果は返さず、メールもトークンも作らない
    outbox.assert_not_called()
    assert PasswordResetToken.query.count() == 0


def test_request_password_reset_still_sends_for_active_citizen(world, outbox):
    # Act
    auth_service.request_password_reset('c@example.com')

    # Assert: 対照（削除していなければ送る）
    outbox.assert_called_once()


def test_mail_token_of_account_deleted_after_issue_is_unusable(world, outbox):
    # Arrange: メールを送ったあと（トークンの無効化をせずに）削除フラグだけ立てる
    auth_service.request_password_reset('c@example.com')
    token = sent_reset_token(outbox)
    assert auth_service.is_reset_token_valid(token) is True
    soft_delete(world['citizen'])

    # Act / Assert
    assert auth_service.is_reset_token_valid(token) is False
    with pytest.raises(ServiceError) as excinfo:
        auth_service.reset_password_with_token(token, NEW_PW, NEW_PW)
    assert str(excinfo.value) == MSG_RESET_LINK_INVALID
    assert _fresh(world['citizen'].id).check_password('password123')


def test_set_password_by_operator_rejects_deleted_account(world):
    # Arrange
    lock_account(world['staff'])
    soft_delete(world['staff'], by=world['admin'])

    # Act
    with pytest.raises(ServiceError) as excinfo:
        auth_service.set_password_by_operator('stf_1', NEW_PW)

    # Assert: パスワードもロックも変えない
    assert str(excinfo.value) == MSG_ACCOUNT_DELETED_RESTORE_FIRST.format(login_id='stf_1')
    fresh = _fresh(world['staff'].id)
    assert fresh.check_password('password123') and fresh.locked_until is not None


def test_invalidate_reset_tokens_does_not_commit(world):
    # Arrange
    auth_service.issue_reset_code('stf_1')

    # Act: コミットは呼び出し側。rollback すれば元に戻る
    auth_service.invalidate_reset_tokens(world['staff'].id)
    db.session.rollback()

    # Assert
    assert _unused_tokens(world['staff'].id) == 1


def test_is_locked_reflects_locked_until(world, clock):
    # Arrange / Act / Assert
    assert auth_service.is_locked(world['staff']) is False
    world['staff'].locked_until = clock.now + timedelta(seconds=1)
    assert auth_service.is_locked(world['staff']) is True
    clock.advance(seconds=2)
    assert auth_service.is_locked(world['staff']) is False


# ---------------------------------------------------------------------------
# remove_test_users と削除済み（del_flag）
# ---------------------------------------------------------------------------

def _test_accounts():
    """公開パスワードのテストアカウント（ID と役割の組み合わせは TEST_ACCOUNT_ROLES と同じ）"""
    return (make_account('taro@example.com', User.ROLE_CITIZEN), make_account('staff01', User.ROLE_STAFF),
            make_account('admin01', User.ROLE_ADMIN))


def test_remove_test_users_does_not_count_deleted_admin_as_remaining(app):
    # Arrange: admin01 以外の管理者は削除済み（使えない）
    _, _, admin01 = _test_accounts()
    soft_delete(make_account('realadmin', User.ROLE_ADMIN), by=admin01)

    # Act
    with pytest.raises(ServiceError) as excinfo:
        user_service.remove_test_users()

    # Assert: 使える管理者が 0 人になるので中止。何も変わらない
    assert str(excinfo.value) == MSG_NO_ADMIN_LEFT
    assert User.query.filter_by(login_id='admin01').count() == 1


def test_remove_test_users_ok_when_other_usable_admin_exists_besides_deleted_one(app):
    # Arrange
    _, _, admin01 = _test_accounts()
    make_account('realadmin', User.ROLE_ADMIN)
    soft_delete(make_account('oldadmin', User.ROLE_ADMIN), by=admin01)

    # Act
    results = dict(user_service.remove_test_users(force=True))

    # Assert: admin01 は「削除した管理者」として参照されている（deleted_by_id）ので、削除ではなく無効化
    assert results['admin01'] == user_service.ACTION_DISABLED


def test_remove_test_users_deleted_test_account_with_changed_password_is_skipped_as_inactive(app):
    # Arrange
    _, staff01, _ = _test_accounts()
    make_account('realadmin', User.ROLE_ADMIN)
    staff01.set_password('Changed-pw-1')
    soft_delete(staff01)

    # Act
    results = dict(user_service.remove_test_users())

    # Assert: 使えないアカウントなので「パスワード変更済み」ではなく「無効化済み」扱い
    assert results['staff01'] == user_service.ACTION_SKIPPED_INACTIVE


def test_remove_test_users_deleted_test_account_without_data_is_removed(app):
    # Arrange: 削除済みでも公開パスワードのままなら見逃さない
    _, staff01, _ = _test_accounts()
    make_account('realadmin', User.ROLE_ADMIN)
    soft_delete(staff01)

    # Act
    results = dict(user_service.remove_test_users())

    # Assert
    assert results['staff01'] == user_service.ACTION_DELETED
    assert User.query.filter_by(login_id='staff01').count() == 0


def test_remove_test_users_deleted_test_account_with_data_gets_password_reset_and_stays_deleted(app):
    # Arrange: 削除者として参照されている（データあり）削除済みの staff01
    _, staff01, _ = _test_accounts()
    realadmin = make_account('realadmin', User.ROLE_ADMIN)
    soft_delete(staff01)
    soft_delete(make_account('other', User.ROLE_STAFF), by=staff01)
    old_hash = staff01.password_hash

    # Act
    results = dict(user_service.remove_test_users())

    # Assert: 削除済みのまま、パスワードだけ変える
    assert results['staff01'] == user_service.ACTION_DISABLED_PW_RESET
    fresh = _fresh(staff01.id)
    assert fresh.del_flag is True and fresh.password_hash != old_hash
    assert realadmin.is_usable
