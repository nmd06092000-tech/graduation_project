# AI判定結果（AIAnalysis）モデル：カテゴリ・緊急度・担当部署・信頼度・生レスポンス
# 判定のたびに1行追加する。reports の確定値は上書きしない
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.constants import AiAnalysisStatus, Urgency
from app.extensions import db


class AiAnalysis(db.Model):
    __tablename__ = 'ai_analyses'

    __table_args__ = (
        sa.CheckConstraint(AiAnalysisStatus.check_sql('status'), name='ck_ai_analyses_status'),
        sa.CheckConstraint(f"urgency IS NULL OR {Urgency.check_sql('urgency')}", name='ck_ai_analyses_urgency'),
        sa.CheckConstraint('confidence IS NULL OR confidence BETWEEN 0 AND 1', name='ck_ai_analyses_confidence'),
        # 判定成功時は分類結果がそろっていること
        sa.CheckConstraint(
            f"status <> '{AiAnalysisStatus.SUCCEEDED.value}' OR "
            '(category_id IS NOT NULL AND urgency IS NOT NULL AND confidence IS NOT NULL)',
            name='ck_ai_analyses_succeeded_result'),
        # 報告ごとの最新の判定結果
        sa.Index('ix_ai_analyses_report_id_created_at', 'report_id', 'created_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    report_id = db.Column(db.BigInteger, db.ForeignKey('reports.id', ondelete='RESTRICT'), nullable=False)
    # 判定の成否（succeeded / failed）
    status = db.Column(db.String(20), nullable=False)
    # AI 分類カテゴリ・判定緊急度・推定担当部署
    category_id = db.Column(db.BigInteger, db.ForeignKey('categories.id', ondelete='RESTRICT'), index=True)
    urgency = db.Column(db.String(20))
    department_id = db.Column(db.BigInteger, db.ForeignKey('departments.id', ondelete='RESTRICT'), index=True)
    # 信頼度（0〜1。画面では % 表示）
    confidence = db.Column(db.Numeric(4, 3))
    # 使用した分類器・モデル名（例: rule_based）
    model_name = db.Column(db.String(100), nullable=False)
    # AI の生レスポンス
    raw_response = db.Column(JSONB)
    # 失敗時のエラー内容
    error_message = db.Column(db.Text)
    # 処理日時
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True)

    report = db.relationship('Report', back_populates='ai_analyses')
    category = db.relationship('Category')
    department = db.relationship('Department')
