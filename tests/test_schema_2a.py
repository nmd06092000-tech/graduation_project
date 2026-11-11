# GĐ2a のスキーマ（users の追加列・password_reset_tokens・マイグレーション）のテスト
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.extensions import db

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / 'migrations'


def _inspector():
    return sa.inspect(db.engine)


def test_users_has_lockout_columns_and_department(app):
    # Act
    columns = {column['name']: column for column in _inspector().get_columns('users')}

    # Assert
    assert {'failed_login_count', 'locked_until', 'department_id'} <= set(columns)
    assert columns['failed_login_count']['nullable'] is False
    assert columns['locked_until']['nullable'] is True
    assert columns['locked_until']['type'].timezone is True
    assert columns['department_id']['nullable'] is True


def test_new_user_defaults_to_zero_failures_and_no_lock(citizen):
    # Assert: DB の既定値（server_default / default）
    db.session.refresh(citizen)
    assert citizen.failed_login_count == 0
    assert citizen.locked_until is None
    assert citizen.department_id is None


def test_users_department_fk_is_restrict_and_indexed(app):
    # Act
    inspector = _inspector()
    foreign_keys = [fk for fk in inspector.get_foreign_keys('users') if fk['constrained_columns'] == ['department_id']]
    indexed = [index['column_names'] for index in inspector.get_indexes('users')]

    # Assert
    assert len(foreign_keys) == 1
    assert foreign_keys[0]['referred_table'] == 'departments'
    assert foreign_keys[0]['options'].get('ondelete', '').upper() == 'RESTRICT'
    assert ['department_id'] in indexed


def test_password_reset_tokens_table_columns_constraints(app):
    # Act
    inspector = _inspector()
    columns = {column['name']: column for column in inspector.get_columns('password_reset_tokens')}
    foreign_keys = inspector.get_foreign_keys('password_reset_tokens')
    unique_columns = [constraint['column_names'] for constraint in inspector.get_unique_constraints('password_reset_tokens')]
    unique_columns += [index['column_names'] for index in inspector.get_indexes('password_reset_tokens') if index['unique']]
    indexed = [index['column_names'] for index in inspector.get_indexes('password_reset_tokens')]

    # Assert
    assert set(columns) == {'id', 'user_id', 'token_hash', 'created_at', 'expires_at', 'used_at'}
    assert columns['used_at']['nullable'] is True
    assert columns['expires_at']['nullable'] is False
    assert columns['token_hash']['nullable'] is False
    assert ['token_hash'] in unique_columns
    assert ['user_id'] in indexed
    assert foreign_keys[0]['referred_table'] == 'users'
    assert foreign_keys[0]['options'].get('ondelete', '').upper() == 'CASCADE'


def test_migration_2422f95d18ff_follows_previous_and_is_single_head():
    # Arrange: DB には触れず、migrations フォルダのスクリプトだけを読む
    config = AlembicConfig()
    config.set_main_option('script_location', str(MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(config)

    # Act
    revision = script.get_revision('2422f95d18ff')

    # Assert
    assert revision.down_revision == '069ae4f17650'
    assert len(script.get_heads()) == 1
