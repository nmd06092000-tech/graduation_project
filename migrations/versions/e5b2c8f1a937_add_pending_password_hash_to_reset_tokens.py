"""add password_reset_tokens.pending_password_hash

Revision ID: e5b2c8f1a937
Revises: d4a91c7e5b36
Create Date: 2026-11-17 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b2c8f1a937'
down_revision = 'd4a91c7e5b36'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        # 確認待ちの新しいパスワードのハッシュ。既存のトークンは NULL（= 使えない。もう一度申請してもらう）
        batch_op.add_column(sa.Column('pending_password_hash', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.drop_column('pending_password_hash')
