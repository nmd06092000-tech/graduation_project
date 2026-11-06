"""add reports location_source and gps_address

Revision ID: 5c2e8f1b7a90
Revises: 0eec1fda8b89
Create Date: 2026-11-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5c2e8f1b7a90'
down_revision = '0eec1fda8b89'
branch_labels = None
depends_on = None


def upgrade():
    # 緯度・経度の取得元（photo / device / address）と GPS の位置から求めた住所。既存の報告は NULL（不明）
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('gps_address', sa.String(length=255), nullable=True))
        batch_op.create_check_constraint(
            'ck_reports_location_source',
            "location_source IS NULL OR location_source IN ('photo', 'device', 'address')")


def downgrade():
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_constraint('ck_reports_location_source', type_='check')
        batch_op.drop_column('gps_address')
        batch_op.drop_column('location_source')
