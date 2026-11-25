"""add departments and categories soft delete columns (del_flag, deleted_at, deleted_by_id)

Revision ID: 55278139fc30
Revises: a7d2e4b9c561
Create Date: 2026-11-25 10:36:35.470654

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '55278139fc30'
down_revision = 'a7d2e4b9c561'
branch_labels = None
depends_on = None

# 非表示なら日時が必須、表示中なら日時・実行者は NULL（users の ck_users_del_flag_consistent と同じ形）
_DEL_FLAG_CHECK = '(del_flag AND deleted_at IS NOT NULL) OR (NOT del_flag AND deleted_at IS NULL AND deleted_by_id IS NULL)'


def upgrade():
    # 部署・カテゴリの非表示（3b）。既存の行は del_flag=false・非表示日時/実行者なし
    for table in ('departments', 'categories'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('del_flag', sa.Boolean(), server_default=sa.text('false'), nullable=False))
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column('deleted_by_id', sa.BigInteger(), nullable=True))
            batch_op.create_index(batch_op.f(f'ix_{table}_deleted_by_id'), ['deleted_by_id'], unique=False)
            # 制約名は PostgreSQL の既定（テーブル_列_fkey）に合わせる。downgrade で名前を指定して消すため
            batch_op.create_foreign_key(f'{table}_deleted_by_id_fkey', 'users', ['deleted_by_id'], ['id'],
                                        ondelete='RESTRICT')
            batch_op.create_check_constraint(f'ck_{table}_del_flag_consistent', _DEL_FLAG_CHECK)


def downgrade():
    # 列を消すと非表示だった部署・カテゴリは再び選択肢に戻る（行・名前・報告との紐づきは失われない）
    for table in ('categories', 'departments'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f'ck_{table}_del_flag_consistent', type_='check')
            batch_op.drop_constraint(f'{table}_deleted_by_id_fkey', type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{table}_deleted_by_id'))
            batch_op.drop_column('deleted_by_id')
            batch_op.drop_column('deleted_at')
            batch_op.drop_column('del_flag')
