"""add login lockout, users department and password reset tokens

Revision ID: 2422f95d18ff
Revises: 069ae4f17650
Create Date: 2026-11-11 14:06:00.261606

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2422f95d18ff'
down_revision = '069ae4f17650'
branch_labels = None
depends_on = None


def upgrade():
    # パスワード再設定トークン（2b で使用）。トークン本体は保存せずハッシュだけ保存する
    op.create_table('password_reset_tokens',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_password_reset_tokens_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        # ログイン失敗の連続回数と一時ロックの期限。既存ユーザーは 0 回・ロックなし
        batch_op.add_column(sa.Column('failed_login_count', sa.Integer(), server_default=sa.text('0'), nullable=False))
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
        # 職員の担当部署（既存データは NULL）
        batch_op.add_column(sa.Column('department_id', sa.BigInteger(), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_department_id'), ['department_id'], unique=False)
        # 制約名は PostgreSQL の既定（テーブル_列_fkey）に合わせる。downgrade で名前を指定して消すため
        batch_op.create_foreign_key('users_department_id_fkey', 'departments', ['department_id'], ['id'],
                                    ondelete='RESTRICT')


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('users_department_id_fkey', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_users_department_id'))
        batch_op.drop_column('department_id')
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_count')

    with op.batch_alter_table('password_reset_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_password_reset_tokens_user_id'))

    op.drop_table('password_reset_tokens')
