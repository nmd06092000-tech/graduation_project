# 対応履歴（ReportStatusHistory）モデル：変更前後のステータス・緊急度・変更者・コメント・日時
# 追記のみのテーブル（更新・削除しない）。書き込みは status_service のみ
import sqlalchemy as sa

from app.constants import ReportStatus, Urgency
from app.extensions import db


class ReportStatusHistory(db.Model):
    __tablename__ = 'report_status_histories'

    __table_args__ = (
        sa.CheckConstraint(f"from_status IS NULL OR {ReportStatus.check_sql('from_status')}",
                           name='ck_report_status_histories_from_status'),
        sa.CheckConstraint(ReportStatus.check_sql('to_status'), name='ck_report_status_histories_to_status'),
        sa.CheckConstraint(f"from_urgency IS NULL OR {Urgency.check_sql('from_urgency')}",
                           name='ck_report_status_histories_from_urgency'),
        sa.CheckConstraint(f"to_urgency IS NULL OR {Urgency.check_sql('to_urgency')}",
                           name='ck_report_status_histories_to_urgency'),
        # 報告詳細の対応履歴（報告ごと・古い順）
        sa.Index('ix_report_status_histories_report_id_created_at', 'report_id', 'created_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    report_id = db.Column(db.BigInteger, db.ForeignKey('reports.id', ondelete='RESTRICT'), nullable=False)
    # 変更前（NULL = 新規受付）と変更後のステータス。同じ値の場合はコメントのみの更新
    from_status = db.Column(db.String(20))
    to_status = db.Column(db.String(20), nullable=False)
    # 変更者（NULL = システムによる自動処理）
    changed_by_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    # 職員による緊急度の変更（to_urgency が NULL = 緊急度は変更していない。from_urgency の NULL = 未判定から）
    from_urgency = db.Column(db.String(20))
    to_urgency = db.Column(db.String(20))
    # 緊急度の判断変更理由（AI の判定と異なる緊急度にした理由。職員のみ閲覧）
    urgency_reason = db.Column(db.Text)
    # 担当者メモ・進捗コメント（職員のみ閲覧）
    comment = db.Column(db.Text)
    # 市民向け返信メッセージ（市民の対応状況画面に表示）
    public_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    report = db.relationship('Report', back_populates='histories')
    changed_by = db.relationship('User')
