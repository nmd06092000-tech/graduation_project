# 職員向けの通知（StaffNotification）モデル：AI緊急度の再判定などをダッシュボードで知らせる
# 既読は職員全員で共通（誰かが既読にすれば全員の未読一覧から消える）
import sqlalchemy as sa

from app.extensions import db


class StaffNotification(db.Model):
    __tablename__ = 'staff_notifications'

    __table_args__ = (
        # ダッシュボードの未読一覧（新しい順）
        sa.Index('ix_staff_notifications_read_at_created_at', 'read_at', 'created_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    report_id = db.Column(db.BigInteger, db.ForeignKey('reports.id', ondelete='RESTRICT'), nullable=False, index=True)
    # 通知の本文（constants.py の MSG_NOTICE_*）
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    # 既読にした日時と職員（NULL = 未読）
    read_at = db.Column(db.DateTime(timezone=True))
    read_by_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT'), index=True)

    report = db.relationship('Report')
    read_by = db.relationship('User')
