"""add citizen_notifications

Revision ID: f6c3d9a2b148
Revises: e5b2c8f1a937
Create Date: 2026-11-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6c3d9a2b148'
down_revision = 'e5b2c8f1a937'
branch_labels = None
depends_on = None


def upgrade():
    # 市民向けのお知らせ（報告の受付・AI判定・職員の対応状況の変更）。既読は本人ごと（read_at が NULL = 未読）
    op.create_table('citizen_notifications',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('report_id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("type IN ('report_received', 'ai_judged', 'staff_update')", name='ck_citizen_notifications_type'),
    sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('citizen_notifications', schema=None) as batch_op:
        batch_op.create_index('ix_citizen_notifications_user_id_created_at', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_citizen_notifications_report_id'), ['report_id'], unique=False)


def downgrade():
    with op.batch_alter_table('citizen_notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_citizen_notifications_report_id'))
        batch_op.drop_index('ix_citizen_notifications_user_id_created_at')

    op.drop_table('citizen_notifications')
