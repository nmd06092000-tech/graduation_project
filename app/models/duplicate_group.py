# 重複候補グループ（DuplicateGroup）モデル：同カテゴリ・近距離・未完了の報告をまとめ、職員が統合・解除する
import sqlalchemy as sa

from app.constants import DuplicateGroupStatus
from app.extensions import db


class DuplicateGroup(db.Model):
    __tablename__ = 'duplicate_groups'

    __table_args__ = (
        sa.CheckConstraint(DuplicateGroupStatus.check_sql('status'), name='ck_duplicate_groups_status'),
        sa.CheckConstraint('similarity IS NULL OR similarity BETWEEN 0 AND 1',
                           name='ck_duplicate_groups_similarity'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # open（確認待ち）/ merged（統合済み）/ dismissed（重複なし）
    status = db.Column(db.String(20), nullable=False, index=True,
                       default=DuplicateGroupStatus.OPEN.value, server_default=DuplicateGroupStatus.OPEN.value)
    # 検出時の類似度（0〜1。画面では % 表示）
    similarity = db.Column(db.Numeric(4, 3))
    # 統合・解除した職員と日時
    resolved_by_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    resolved_at = db.Column(db.DateTime(timezone=True))
    # 検出日時
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    reports = db.relationship('Report', back_populates='duplicate_group', order_by='Report.created_at')
    resolved_by = db.relationship('User')
