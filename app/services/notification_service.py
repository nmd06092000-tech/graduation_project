# 職員向けの通知（AI緊急度の再判定など）：作成・未読一覧・既読
# 既読は職員全員で共通（誰かが既読にすれば全員の未読一覧から消える）
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.extensions import db
from app.models import StaffNotification
from app.services.report_service import format_datetime

# ダッシュボードに表示する未読通知の最大件数
UNREAD_LIMIT = 10


def create(report_id, message):
    """通知を1件追加する（コミットは呼び出し側）"""
    notification = StaffNotification(report_id=report_id, message=message)
    db.session.add(notification)
    return notification


def list_unread(limit=UNREAD_LIMIT):
    """未読の通知（新しい順）。ダッシュボード"""
    stmt = (select(StaffNotification)
            .where(StaffNotification.read_at.is_(None))
            .order_by(StaffNotification.created_at.desc(), StaffNotification.id.desc())
            .limit(limit))
    return [{
        'id': n.id,
        'report_id': n.report_id,
        'message': n.message,
        'date': format_datetime(n.created_at),
    } for n in db.session.execute(stmt).scalars()]


def count_unread():
    return db.session.scalar(
        select(func.count()).select_from(StaffNotification).where(StaffNotification.read_at.is_(None)))


def mark_read(notification_id, *, actor_id):
    """通知を既読にする。存在しない通知は False（既読済みならそのまま True）"""
    notification = db.session.get(StaffNotification, notification_id)
    if notification is None:
        return False
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        notification.read_by_id = actor_id
        db.session.commit()
    return True


def mark_all_read(*, actor_id):
    """未読の通知をすべて既読にし、既読にした件数を返す"""
    unread = db.session.execute(
        select(StaffNotification).where(StaffNotification.read_at.is_(None))).scalars().all()
    now = datetime.now(timezone.utc)
    for notification in unread:
        notification.read_at = now
        notification.read_by_id = actor_id
    db.session.commit()
    return len(unread)
