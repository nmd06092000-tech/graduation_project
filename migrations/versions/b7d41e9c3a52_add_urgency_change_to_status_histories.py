"""add urgency change to report_status_histories

Revision ID: b7d41e9c3a52
Revises: 5c2e8f1b7a90
Create Date: 2026-11-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d41e9c3a52'
down_revision = '5c2e8f1b7a90'
branch_labels = None
depends_on = None


def upgrade():
    # 職員による緊急度の変更（変更前・変更後）と判断変更理由。既存の履歴は NULL（緊急度の変更なし）
    with op.batch_alter_table('report_status_histories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('from_urgency', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('to_urgency', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('urgency_reason', sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            'ck_report_status_histories_from_urgency',
            "from_urgency IS NULL OR from_urgency IN ('high', 'medium', 'low')")
        batch_op.create_check_constraint(
            'ck_report_status_histories_to_urgency',
            "to_urgency IS NULL OR to_urgency IN ('high', 'medium', 'low')")


def downgrade():
    with op.batch_alter_table('report_status_histories', schema=None) as batch_op:
        batch_op.drop_constraint('ck_report_status_histories_to_urgency', type_='check')
        batch_op.drop_constraint('ck_report_status_histories_from_urgency', type_='check')
        batch_op.drop_column('urgency_reason')
        batch_op.drop_column('to_urgency')
        batch_op.drop_column('from_urgency')
