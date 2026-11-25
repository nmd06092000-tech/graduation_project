"""add manual to ai_analyses trigger_type

Revision ID: a7d2e4b9c561
Revises: f6c3d9a2b148
Create Date: 2026-11-24 12:40:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a7d2e4b9c561'
down_revision = 'f6c3d9a2b148'
branch_labels = None
depends_on = None


def upgrade():
    # AI判定のきっかけに manual（職員が AI分析画面の「再実行」で実行）を追加する。列の変更・既存データの変更はなし
    with op.batch_alter_table('ai_analyses', schema=None) as batch_op:
        batch_op.drop_constraint('ck_ai_analyses_trigger_type', type_='check')
        batch_op.create_check_constraint(
            'ck_ai_analyses_trigger_type', "trigger_type IN ('initial', 'rejudge', 'manual')")


def downgrade():
    # 旧 CHECK では manual を保存できないため、先に rejudge に置き換える
    op.execute("UPDATE ai_analyses SET trigger_type = 'rejudge' WHERE trigger_type = 'manual'")
    with op.batch_alter_table('ai_analyses', schema=None) as batch_op:
        batch_op.drop_constraint('ck_ai_analyses_trigger_type', type_='check')
        batch_op.create_check_constraint(
            'ck_ai_analyses_trigger_type', "trigger_type IN ('initial', 'rejudge')")
