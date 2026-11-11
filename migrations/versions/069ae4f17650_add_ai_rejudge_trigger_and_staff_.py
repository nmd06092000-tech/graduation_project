"""add ai rejudge trigger and staff notifications

Revision ID: 069ae4f17650
Revises: c3f8a2d61e07
Create Date: 2026-11-10 10:37:27.057343

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '069ae4f17650'
down_revision = 'c3f8a2d61e07'
branch_labels = None
depends_on = None


def upgrade():
    # 職員向けの通知（AI緊急度の再判定など）。既読は職員全員で共通
    op.create_table('staff_notifications',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('report_id', sa.BigInteger(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_by_id', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['read_by_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('staff_notifications', schema=None) as batch_op:
        batch_op.create_index('ix_staff_notifications_read_at_created_at', ['read_at', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_staff_notifications_read_by_id'), ['read_by_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_staff_notifications_report_id'), ['report_id'], unique=False)

    # AI 判定のきっかけ（initial = 新規報告時、rejudge = 再判定）。既存の判定は initial
    with op.batch_alter_table('ai_analyses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trigger_type', sa.String(length=20), server_default='initial', nullable=False))
        batch_op.create_check_constraint('ck_ai_analyses_trigger_type', "trigger_type IN ('initial', 'rejudge')")


def downgrade():
    with op.batch_alter_table('ai_analyses', schema=None) as batch_op:
        batch_op.drop_constraint('ck_ai_analyses_trigger_type', type_='check')
        batch_op.drop_column('trigger_type')

    with op.batch_alter_table('staff_notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_staff_notifications_report_id'))
        batch_op.drop_index(batch_op.f('ix_staff_notifications_read_by_id'))
        batch_op.drop_index('ix_staff_notifications_read_at_created_at')

    op.drop_table('staff_notifications')
