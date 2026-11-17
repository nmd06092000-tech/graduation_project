"""add users soft delete columns (del_flag, deleted_at, deleted_by_id)

Revision ID: d4a91c7e5b36
Revises: 2422f95d18ff
Create Date: 2026-11-16 12:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a91c7e5b36'
down_revision = '2422f95d18ff'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        # 論理削除（3a）。既存ユーザーは del_flag=false・削除日時/削除者なし
        batch_op.add_column(sa.Column('del_flag', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('deleted_by_id', sa.BigInteger(), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_deleted_by_id'), ['deleted_by_id'], unique=False)
        # 制約名は PostgreSQL の既定（テーブル_列_fkey）に合わせる。downgrade で名前を指定して消すため
        batch_op.create_foreign_key('users_deleted_by_id_fkey', 'users', ['deleted_by_id'], ['id'],
                                    ondelete='RESTRICT')
        # 削除済みなら削除日時が必須、未削除なら削除日時・削除者は NULL
        batch_op.create_check_constraint(
            'ck_users_del_flag_consistent',
            '(del_flag AND deleted_at IS NOT NULL) OR (NOT del_flag AND deleted_at IS NULL AND deleted_by_id IS NULL)')


def downgrade():
    # del_flag の列を消すと削除済みアカウントが「使える」状態に戻ってしまうため、先に利用停止にしておく
    # （削除済み = is_active=false にして、元に戻した後もログインできないようにする）
    op.execute("UPDATE users SET is_active = false WHERE del_flag")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('ck_users_del_flag_consistent', type_='check')
        batch_op.drop_constraint('users_deleted_by_id_fkey', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_users_deleted_by_id'))
        batch_op.drop_column('deleted_by_id')
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('del_flag')
