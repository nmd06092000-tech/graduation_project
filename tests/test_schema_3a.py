# GĐ3a のスキーマ（users の論理削除の列・CHECK・外部キー）とマイグレーション d4a91c7e5b36 のテスト
# マイグレーションの往復は TEST_DATABASE_URL のテスト DB だけで行う（開発 DB には触れない）
import logging
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User
from tests.conftest import make_account, soft_delete

MIGRATIONS_DIR = str(Path(__file__).resolve().parent.parent / 'migrations')
REVISION = 'd4a91c7e5b36'
PREVIOUS_REVISION = '2422f95d18ff'
PENDING_REVISION = 'e5b2c8f1a937'   # パスワード再設定の確認待ちハッシュ（pending_password_hash）。d4a91c7e5b36 の次
NOTIFICATIONS_REVISION = 'f6c3d9a2b148'    # 市民向けお知らせ（citizen_notifications）。e5b2c8f1a937 の次
LATEST_REVISION = 'a7d2e4b9c561'    # ai_analyses.trigger_type に manual（AIモデル再実行）を許可。f6c3d9a2b148 の次
HEAD_REVISION = '55278139fc30'    # 部署・カテゴリの非表示（3b）。a7d2e4b9c561 の次（現在の head）
SOFT_DELETE_COLUMNS = {'del_flag', 'deleted_at', 'deleted_by_id'}


def _inspector():
    return sa.inspect(db.engine)


def _column_names():
    return {column['name'] for column in _inspector().get_columns('users')}


def _check_names():
    return {check['name'] for check in _inspector().get_check_constraints('users')}


def _fk_names():
    return {fk['name'] for fk in _inspector().get_foreign_keys('users')}


# ---------------------------------------------------------------------------
# モデルから作ったスキーマ（conftest の create_all）
# ---------------------------------------------------------------------------

def test_users_has_soft_delete_columns_with_expected_types(app):
    # Act
    columns = {column['name']: column for column in _inspector().get_columns('users')}

    # Assert
    assert SOFT_DELETE_COLUMNS <= set(columns)
    assert columns['del_flag']['nullable'] is False
    assert isinstance(columns['del_flag']['type'], sa.Boolean)
    assert columns['deleted_at']['nullable'] is True and columns['deleted_at']['type'].timezone is True
    assert columns['deleted_by_id']['nullable'] is True and isinstance(columns['deleted_by_id']['type'], sa.BigInteger)


def test_new_user_defaults_to_not_deleted(app):
    # Act
    user = make_account('probe', User.ROLE_STAFF)
    db.session.refresh(user)

    # Assert
    assert user.del_flag is False and user.deleted_at is None and user.deleted_by_id is None
    assert user.is_deleted is False and user.is_usable is True


def test_del_flag_server_default_is_false_for_raw_inserts(app):
    # Act: モデルを通さない INSERT（旧データ相当）でも false
    with db.engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (login_id, name, role, password_hash) VALUES ('raw', 'raw', 'staff', 'x')"))
        value = connection.execute(sa.text("SELECT del_flag FROM users WHERE login_id = 'raw'")).scalar()

    # Assert
    assert value is False


def test_deleted_by_fk_is_named_restrict_and_indexed(app):
    # Act
    inspector = _inspector()
    foreign_keys = [fk for fk in inspector.get_foreign_keys('users') if fk['constrained_columns'] == ['deleted_by_id']]
    indexed = [index['column_names'] for index in inspector.get_indexes('users')]

    # Assert
    assert len(foreign_keys) == 1
    assert foreign_keys[0]['name'] == 'users_deleted_by_id_fkey'
    assert foreign_keys[0]['referred_table'] == 'users'
    assert foreign_keys[0]['options'].get('ondelete', '').upper() == 'RESTRICT'
    assert ['deleted_by_id'] in indexed


def test_check_constraint_exists_and_login_id_stays_unique(app):
    # Act
    inspector = _inspector()
    unique = [c['column_names'] for c in inspector.get_unique_constraints('users')]
    unique += [i['column_names'] for i in inspector.get_indexes('users') if i['unique']]

    # Assert
    assert 'ck_users_del_flag_consistent' in _check_names()
    assert ['login_id'] in unique


@pytest.mark.parametrize('values', [
    {'del_flag': True, 'deleted_at': None},                    # 削除済みなのに日時がない
    {'del_flag': False, 'deleted_at': 'now'},                  # 未削除なのに日時がある
    {'del_flag': False, 'deleted_at': None, 'deleted_by': 'other'},   # 未削除なのに削除者がいる
    {'del_flag': False, 'deleted_at': 'now', 'deleted_by': 'other'},
])
def test_check_constraint_rejects_inconsistent_rows(app, values):
    # Arrange
    other_id = make_account('other', User.ROLE_ADMIN).id
    user = make_account('probe', User.ROLE_STAFF)
    user.del_flag = values['del_flag']
    user.deleted_at = sa.func.now() if values['deleted_at'] == 'now' else None
    user.deleted_by_id = other_id if values.get('deleted_by') else None

    # Act / Assert
    with pytest.raises(IntegrityError, match='ck_users_del_flag_consistent'):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize('with_deleter', [False, True])
def test_check_constraint_accepts_consistent_deleted_rows(app, with_deleter):
    # Arrange
    other = make_account('other', User.ROLE_ADMIN)
    user = make_account('probe', User.ROLE_STAFF)

    # Act: 削除済み（日時あり。削除者は NULL でもよい）
    soft_delete(user, by=other if with_deleter else None)

    # Assert
    db.session.refresh(user)
    assert user.del_flag is True and user.deleted_at is not None
    assert (user.deleted_by_id == other.id) is with_deleter


def test_restoring_columns_back_to_null_is_accepted(app):
    # Arrange
    other = make_account('other', User.ROLE_ADMIN)
    user = soft_delete(make_account('probe', User.ROLE_STAFF), by=other)

    # Act: 復元（3 列を同時に戻す）
    user.del_flag, user.deleted_at, user.deleted_by_id = False, None, None
    db.session.commit()

    # Assert
    db.session.refresh(user)
    assert user.is_usable is True


def test_deleter_row_cannot_be_physically_deleted_while_referenced(app):
    # Arrange: 削除者（admin）が他のアカウントの deleted_by_id から参照されている
    boss = make_account('boss', User.ROLE_ADMIN)
    soft_delete(make_account('probe', User.ROLE_STAFF), by=boss)

    # Act / Assert: ON DELETE RESTRICT
    db.session.delete(boss)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_deleted_by_must_reference_an_existing_user(app):
    # Arrange
    user = make_account('probe', User.ROLE_STAFF)
    user.del_flag, user.deleted_at, user.deleted_by_id = True, sa.func.now(), 999999

    # Act / Assert
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_duplicate_login_id_is_still_rejected_even_when_existing_row_is_deleted(app):
    # Arrange: 削除済みの ID は予約されたまま（UNIQUE は変わらない）
    soft_delete(make_account('probe', User.ROLE_STAFF))

    # Act / Assert
    db.session.add(User(login_id='probe', name='別人', role=User.ROLE_STAFF, password_hash='x'))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# マイグレーションのスクリプト（DB には触れない）
# ---------------------------------------------------------------------------

def test_migration_d4a91c7e5b36_follows_2422f95d18ff_and_head_is_a7d2e4b9c561():
    # Arrange
    config = AlembicConfig()
    config.set_main_option('script_location', MIGRATIONS_DIR)
    script = ScriptDirectory.from_config(config)

    # Act
    revision = script.get_revision(REVISION)

    # Assert
    assert revision.down_revision == PREVIOUS_REVISION
    assert script.get_heads() == [HEAD_REVISION]

def test_migration_e5b2c8f1a937_follows_d4a91c7e5b36():
    # Arrange
    config = AlembicConfig()
    config.set_main_option('script_location', MIGRATIONS_DIR)
    script = ScriptDirectory.from_config(config)

    # Act
    revision = script.get_revision(PENDING_REVISION)

    # Assert
    assert revision.down_revision == REVISION


def test_migration_f6c3d9a2b148_follows_e5b2c8f1a937():
    # Arrange
    config = AlembicConfig()
    config.set_main_option('script_location', MIGRATIONS_DIR)
    script = ScriptDirectory.from_config(config)

    # Act
    revision = script.get_revision(NOTIFICATIONS_REVISION)

    # Assert
    assert revision.down_revision == PENDING_REVISION


def test_migration_a7d2e4b9c561_follows_f6c3d9a2b148_and_is_single_head():
    # Arrange
    config = AlembicConfig()
    config.set_main_option('script_location', MIGRATIONS_DIR)
    script = ScriptDirectory.from_config(config)

    # Act
    revision = script.get_revision(LATEST_REVISION)

    # Assert
    assert revision.down_revision == NOTIFICATIONS_REVISION
    assert script.get_heads() == [HEAD_REVISION]



# ---------------------------------------------------------------------------
# マイグレーションの往復（テスト DB。create_all のテーブルを捨て、最初から alembic で作る）
# ---------------------------------------------------------------------------

@contextmanager
def _keep_logging():
    """alembic の env.py が呼ぶ logging.config.fileConfig は、ルートのハンドラとほかのロガーの有効状態を書き換えるので、
    実行前の状態に戻す（あとのテストの caplog などが効かなくならないように）"""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    disabled = {name: logger.disabled for name, logger in logging.Logger.manager.loggerDict.items()
                if isinstance(logger, logging.Logger)}
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        for name, flag in disabled.items():
            logging.getLogger(name).disabled = flag


def _migrate_up(revision='head'):
    with _keep_logging():
        upgrade(directory=MIGRATIONS_DIR, revision=revision)


def _migrate_down(revision):
    with _keep_logging():
        downgrade(directory=MIGRATIONS_DIR, revision=revision)


@pytest.fixture
def migrated(app):
    """テスト DB の create_all で作ったテーブルを捨て、マイグレーションを直前のリビジョンまで流した状態で渡す"""
    db.session.remove()
    db.drop_all()
    with db.engine.begin() as connection:
        connection.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
    _migrate_up(PREVIOUS_REVISION)
    yield
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
    db.drop_all()


def _insert_legacy_user(login_id='legacy'):
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (login_id, name, role, password_hash) VALUES (:login_id, '旧データ', 'staff', 'x')"),
            {'login_id': login_id})


def test_migration_upgrade_adds_columns_constraint_and_keeps_existing_rows(migrated):
    # Arrange: 直前のリビジョンの状態
    assert not SOFT_DELETE_COLUMNS & _column_names()
    _insert_legacy_user()

    # Act
    _migrate_up()

    # Assert: 列・CHECK・外部キー・索引ができ、旧データは未削除になる
    assert SOFT_DELETE_COLUMNS <= _column_names()
    assert 'ck_users_del_flag_consistent' in _check_names()
    assert 'users_deleted_by_id_fkey' in _fk_names()
    assert ['deleted_by_id'] in [index['column_names'] for index in _inspector().get_indexes('users')]
    with db.engine.connect() as connection:
        row = connection.execute(sa.text(
            "SELECT del_flag, deleted_at, deleted_by_id FROM users WHERE login_id = 'legacy'")).one()
    assert tuple(row) == (False, None, None)


def test_migration_upgrade_enforces_check_and_restrict(migrated):
    # Arrange
    _migrate_up()

    # Act / Assert: 不整合な行は拒否される
    with pytest.raises(IntegrityError, match='ck_users_del_flag_consistent'):
        with db.engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO users (login_id, name, role, password_hash, del_flag) VALUES ('bad', 'bad', 'staff', 'x', true)"))
    # 削除者として参照されている行は消せない（ON DELETE RESTRICT）
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (login_id, name, role, password_hash) VALUES ('boss', 'boss', 'admin', 'x')"))
        connection.execute(sa.text(
            "INSERT INTO users (login_id, name, role, password_hash, del_flag, deleted_at, deleted_by_id) "
            "SELECT 'gone', 'gone', 'staff', 'x', true, now(), id FROM users WHERE login_id = 'boss'"))
    with pytest.raises(IntegrityError):
        with db.engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM users WHERE login_id = 'boss'"))


def test_migration_matches_models_for_users_table(migrated):
    # Arrange
    _migrate_up()

    # Act: マイグレーション後のスキーマとモデルの差（autogenerate が出す差分）
    with db.engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), db.metadata)

    # Assert: users テーブルに差分がない（列・制約・索引の定義がモデルと一致）
    users_diff = [item for item in diff if 'users' in repr(item)]
    assert users_diff == []


def test_migration_downgrade_removes_columns_and_keeps_rows_then_upgrades_again(migrated):
    # Arrange
    _migrate_up()
    _insert_legacy_user()

    # Act
    _migrate_down(PREVIOUS_REVISION)

    # Assert: 列・CHECK・外部キー・索引が消え、ユーザーの行は残る
    assert not SOFT_DELETE_COLUMNS & _column_names()
    assert 'ck_users_del_flag_consistent' not in _check_names()
    assert 'users_deleted_by_id_fkey' not in _fk_names()
    assert ['deleted_by_id'] not in [index['column_names'] for index in _inspector().get_indexes('users')]
    with db.engine.connect() as connection:
        assert connection.execute(sa.text("SELECT count(*) FROM users WHERE login_id = 'legacy'")).scalar() == 1

    # Act: もう一度 upgrade（往復できる）
    _migrate_up()

    # Assert
    assert SOFT_DELETE_COLUMNS <= _column_names()
    with db.engine.connect() as connection:
        assert connection.execute(sa.text("SELECT del_flag FROM users WHERE login_id = 'legacy'")).scalar() is False


# ---------------------------------------------------------------------------
# マイグレーション e5b2c8f1a937（password_reset_tokens.pending_password_hash）の往復
# ---------------------------------------------------------------------------

def _token_columns():
    return {column['name']: column for column in _inspector().get_columns('password_reset_tokens')}


@pytest.fixture
def migrated_before_pending(app):
    """テスト DB を d4a91c7e5b36（pending_password_hash の直前）まで alembic で作った状態で渡す"""
    db.session.remove()
    db.drop_all()
    with db.engine.begin() as connection:
        connection.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
    _migrate_up(REVISION)
    yield
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
    db.drop_all()


def test_migration_e5b2c8f1a937_adds_nullable_column_and_old_tokens_become_null(migrated_before_pending):
    # Arrange: 直前のリビジョン（列なし）。既存のトークンが 1 行ある
    assert 'pending_password_hash' not in _token_columns()
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (login_id, name, role, password_hash) VALUES ('old@example.com', '旧', 'citizen', 'x')"))
        connection.execute(sa.text(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) "
            "SELECT id, repeat('a', 64), now() + interval '1 hour' FROM users WHERE login_id = 'old@example.com'"))

    # Act
    _migrate_up()

    # Assert: NULL 可の文字列 255 の列ができ、既存のトークンは NULL（= メールのリンクとしては使えない）
    column = _token_columns()['pending_password_hash']
    assert column['nullable'] is True and column['type'].length == 255
    with db.engine.connect() as connection:
        assert connection.execute(sa.text(
            "SELECT pending_password_hash FROM password_reset_tokens WHERE token_hash = repeat('a', 64)")).scalar() is None


def test_migration_e5b2c8f1a937_matches_models_for_password_reset_tokens(migrated_before_pending):
    # Arrange
    _migrate_up()

    # Act
    with db.engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), db.metadata)

    # Assert: password_reset_tokens にモデルとの差分がない
    assert [item for item in diff if 'password_reset_tokens' in repr(item)] == []


def test_migration_e5b2c8f1a937_downgrade_drops_column_keeps_rows_and_upgrades_again(migrated_before_pending):
    # Arrange
    _migrate_up()
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (login_id, name, role, password_hash) VALUES ('t@example.com', 't', 'citizen', 'x')"))
        connection.execute(sa.text(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, pending_password_hash) "
            "SELECT id, repeat('b', 64), now() + interval '1 hour', 'hash' FROM users WHERE login_id = 't@example.com'"))

    # Act
    _migrate_down(REVISION)

    # Assert: 列だけが消え、トークンの行は残る
    assert 'pending_password_hash' not in _token_columns()
    with db.engine.connect() as connection:
        assert connection.execute(sa.text(
            "SELECT count(*) FROM password_reset_tokens WHERE token_hash = repeat('b', 64)")).scalar() == 1

    # Act: もう一度 upgrade（往復できる）
    _migrate_up()

    # Assert
    assert 'pending_password_hash' in _token_columns()


# ---------------------------------------------------------------------------
# citizen_notifications（市民向けお知らせ。マイグレーション f6c3d9a2b148）
# ---------------------------------------------------------------------------

def _notification_columns():
    return {column['name']: column for column in _inspector().get_columns('citizen_notifications')}


def _assert_notification_schema():
    columns = _notification_columns()
    assert set(columns) == {'id', 'user_id', 'report_id', 'type', 'message', 'detail', 'created_at', 'read_at'}
    for name in ('user_id', 'report_id', 'type', 'message', 'created_at'):
        assert columns[name]['nullable'] is False
    for name in ('detail', 'read_at'):
        assert columns[name]['nullable'] is True
    assert columns['type']['type'].length == 20
    assert columns['created_at']['type'].timezone is True and columns['read_at']['type'].timezone is True
    assert 'ck_citizen_notifications_type' in {c['name'] for c in _inspector().get_check_constraints('citizen_notifications')}
    foreign_keys = {fk['constrained_columns'][0]: fk for fk in _inspector().get_foreign_keys('citizen_notifications')}
    assert foreign_keys['user_id']['referred_table'] == 'users'
    assert foreign_keys['user_id']['options']['ondelete'] == 'CASCADE'
    assert foreign_keys['report_id']['referred_table'] == 'reports'
    assert foreign_keys['report_id']['options']['ondelete'] == 'RESTRICT'
    index_columns = [index['column_names'] for index in _inspector().get_indexes('citizen_notifications')]
    assert ['user_id', 'created_at'] in index_columns and ['report_id'] in index_columns


def test_citizen_notifications_schema_from_models(app):
    # Act / Assert: create_all で作ったテーブルの列・CHECK・外部キー・索引
    _assert_notification_schema()


def test_citizen_notifications_check_rejects_unknown_type(citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()

    # Act / Assert: 種類は report_received / ai_judged / staff_update だけ
    with pytest.raises(IntegrityError, match='ck_citizen_notifications_type'):
        make_citizen_notification(report, type='unknown')
    db.session.rollback()


def test_citizen_notifications_report_delete_is_restricted(citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    make_citizen_notification(report)

    # Act / Assert: お知らせのある報告は消せない（ON DELETE RESTRICT）
    with pytest.raises(IntegrityError):
        db.session.execute(sa.text('DELETE FROM reports WHERE id = :id'), {'id': report.id})
        db.session.commit()
    db.session.rollback()


def test_citizen_notifications_are_deleted_with_their_user(citizen, make_report, make_citizen_notification):
    # Arrange: 宛先が別の市民のお知らせ（報告者とは別ユーザーを消す）
    other = make_account('other@example.com', User.ROLE_CITIZEN)
    make_citizen_notification(make_report(), user_id=other.id)

    # Act
    db.session.execute(sa.text('DELETE FROM users WHERE id = :id'), {'id': other.id})
    db.session.commit()

    # Assert: ON DELETE CASCADE で一緒に消える
    assert db.session.execute(sa.text('SELECT count(*) FROM citizen_notifications')).scalar() == 0


@pytest.fixture
def migrated_before_notifications(app):
    """テスト DB を e5b2c8f1a937（citizen_notifications の直前）まで alembic で作った状態で渡す"""
    db.session.remove()
    db.drop_all()
    with db.engine.begin() as connection:
        connection.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
    _migrate_up(PENDING_REVISION)
    yield
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
    db.drop_all()


def test_migration_f6c3d9a2b148_creates_citizen_notifications_matching_models(migrated_before_notifications):
    # Arrange: 直前のリビジョンにはテーブルがない
    assert 'citizen_notifications' not in _inspector().get_table_names()

    # Act
    _migrate_up()

    # Assert: 列・CHECK・外部キー・索引ができ、モデルとの差分がない
    _assert_notification_schema()
    with db.engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), db.metadata)
    assert [item for item in diff if 'citizen_notifications' in repr(item)] == []


def test_migration_f6c3d9a2b148_downgrade_drops_table_and_upgrades_again(migrated_before_notifications):
    # Arrange
    _migrate_up()

    # Act
    _migrate_down(PENDING_REVISION)

    # Assert: テーブルだけが消え、users などは残る
    assert 'citizen_notifications' not in _inspector().get_table_names()
    assert 'users' in _inspector().get_table_names()

    # Act: もう一度 upgrade（往復できる）
    _migrate_up()

    # Assert
    assert 'citizen_notifications' in _inspector().get_table_names()
