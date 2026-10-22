"""baseline users table

Flask-Migrate 導入前に db.create_all() で作成済みの users テーブル（icon_path 追加前）を表す基準リビジョン。
既存の DB では `flask db stamp 8d99a80ec994` で適用済みにし、新しい DB では upgrade でそのまま作成する。

Revision ID: 8d99a80ec994
Revises:
Create Date: 2026-10-21 12:43:09.790814

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8d99a80ec994'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('login_id', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('citizen', 'staff', 'admin')", name='ck_users_role'),
        sa.PrimaryKeyConstraint('id', name='users_pkey'),
        sa.UniqueConstraint('login_id', name='users_login_id_key'),
    )


def downgrade():
    op.drop_table('users')
