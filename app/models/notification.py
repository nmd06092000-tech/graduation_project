# 通知モデル
#  - StaffNotification（職員向け）：AI緊急度の再判定などをダッシュボードで知らせる。既読は職員全員で共通
#  - CitizenNotification（市民向け）：報告の受付・AI判定・職員の対応状況の変更を知らせる。既読は本人ごと
import sqlalchemy as sa

from app.constants import CitizenNotificationType
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


class CitizenNotification(db.Model):
    __tablename__ = 'citizen_notifications'

    __table_args__ = (
        sa.CheckConstraint(CitizenNotificationType.check_sql('type'), name='ck_citizen_notifications_type'),
        # お知らせ画面の一覧（本人の分を新しい順）と未読件数
        sa.Index('ix_citizen_notifications_user_id_created_at', 'user_id', 'created_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # 通知を受け取る市民（ユーザーを物理削除するときは通知も消える）
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    report_id = db.Column(db.BigInteger, db.ForeignKey('reports.id', ondelete='RESTRICT'), nullable=False, index=True)
    # 通知の種類（report_received / ai_judged / staff_update）
    type = db.Column(db.String(20), nullable=False)
    # 一覧に表示する1行の文言と、開いたときの詳細（constants.py の MSG_CNOTICE_*）。detail が NULL なら message を表示する
    message = db.Column(db.Text, nullable=False)
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    # 既読にした日時（NULL = 未読）
    read_at = db.Column(db.DateTime(timezone=True))

    report = db.relationship('Report')
