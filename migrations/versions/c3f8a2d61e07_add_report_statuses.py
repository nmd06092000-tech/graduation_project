"""add report statuses (contractor, confirming, on_hold, watching, not_applicable)

Revision ID: c3f8a2d61e07
Revises: b7d41e9c3a52
Create Date: 2026-11-10 13:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c3f8a2d61e07'
down_revision = 'b7d41e9c3a52'
branch_labels = None
depends_on = None

# README の状態遷移に合わせたステータスのコード（constants.ReportStatus と同じ並び）
NEW_VALUES = ("'received', 'in_progress', 'contractor', 'confirming', 'on_hold', 'watching', "
              "'resolved', 'not_applicable', 'cancelled'")
OLD_VALUES = "'received', 'in_progress', 'resolved', 'cancelled'"


def _replace_checks(values):
    op.drop_constraint('ck_reports_status', 'reports', type_='check')
    op.create_check_constraint('ck_reports_status', 'reports', f'status IN ({values})')
    op.drop_constraint('ck_report_status_histories_from_status', 'report_status_histories', type_='check')
    op.create_check_constraint('ck_report_status_histories_from_status', 'report_status_histories',
                               f'from_status IS NULL OR from_status IN ({values})')
    op.drop_constraint('ck_report_status_histories_to_status', 'report_status_histories', type_='check')
    op.create_check_constraint('ck_report_status_histories_to_status', 'report_status_histories',
                               f'to_status IN ({values})')


def upgrade():
    _replace_checks(NEW_VALUES)


def downgrade():
    # 新しいステータスの報告・履歴が残っていると失敗する（先に旧ステータスへ戻すこと）
    _replace_checks(OLD_VALUES)
