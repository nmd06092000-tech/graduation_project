# 市民向けのお知らせ：作成（報告の受付・AI判定・職員の対応状況の変更）・一覧・未読件数・既読
# 既読は本人ごと（職員向けの notification_service とは別。既読は職員全員で共通）
# 作成系の関数はコミットしない（呼び出し元の報告登録・AI判定・ステータス変更と同じトランザクションで保存する）
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.constants import (
    MSG_CNOTICE_AI_JUDGED, MSG_CNOTICE_AI_JUDGED_DETAIL, MSG_CNOTICE_RECEIVED, MSG_CNOTICE_RECEIVED_DETAIL,
    MSG_CNOTICE_STAFF_MESSAGE, MSG_CNOTICE_STATUS, MSG_CNOTICE_STATUS_DETAIL,
    CitizenNotificationType, ReportStatus, Urgency,
)
from app.extensions import db
from app.models import Category, CitizenNotification

# お知らせ画面に表示する最大件数（新しい順）
LIST_LIMIT = 100

# URL の ID が bigint を超えると DB エラーになるため、範囲外は「存在しない」として扱う
BIGINT_MAX = 2 ** 63 - 1


def create(report, type_, message, detail=None):
    """お知らせを1件追加する（コミットは呼び出し側）。宛先は報告した市民本人"""
    notification = CitizenNotification(
        user_id=report.citizen_id, report=report, type=CitizenNotificationType(type_).value,
        message=message, detail=detail or None)
    db.session.add(notification)
    return notification


def notify_report_received(report):
    """報告を受け付けたお知らせ。新規報告 基本フロー2"""
    values = {'report_id': report.id, 'title': report.title}
    return create(report, CitizenNotificationType.REPORT_RECEIVED, MSG_CNOTICE_RECEIVED.format(**values),
                  MSG_CNOTICE_RECEIVED_DETAIL.format(**values))


def notify_ai_judged(report, analysis):
    """AI の判定（成功）が終わったお知らせ。カテゴリ・緊急度は AI の判定結果（職員の確定値ではない）"""
    category = db.session.get(Category, analysis.category_id)
    values = {'report_id': report.id, 'title': report.title, 'urgency': Urgency.label_of(analysis.urgency)}
    return create(report, CitizenNotificationType.AI_JUDGED, MSG_CNOTICE_AI_JUDGED.format(**values),
                  MSG_CNOTICE_AI_JUDGED_DETAIL.format(category=category.name if category else '', **values))


def notify_status_changed(report, from_status, to_status, *, public_message=None):
    """職員が対応状況を変更したお知らせ。職員名・内部メモ（comment）は含めず、公開用メッセージだけを付ける"""
    values = {'report_id': report.id, 'title': report.title, 'status': ReportStatus.label_of(to_status)}
    detail = MSG_CNOTICE_STATUS_DETAIL.format(from_status=ReportStatus.label_of(from_status), **values)
    public_message = (public_message or '').strip()
    if public_message:
        detail += MSG_CNOTICE_STAFF_MESSAGE.format(message=public_message)
    return create(report, CitizenNotificationType.STAFF_UPDATE, MSG_CNOTICE_STATUS.format(**values), detail)


def list_for_user(user_id, limit=LIST_LIMIT):
    """本人のお知らせ（新しい順）。お知らせ画面 基本フロー1"""
    # 日時の書式は report_service にあるが、report_service がこのサービスを使うため、循環 import を避けてここで読み込む
    from app.services.report_service import format_datetime

    stmt = (select(CitizenNotification)
            .where(CitizenNotification.user_id == user_id)
            .order_by(CitizenNotification.created_at.desc(), CitizenNotification.id.desc())
            .limit(limit))
    return [{
        'id': n.id,
        'report_id': n.report_id,
        'type': n.type,
        'type_label': CitizenNotificationType.label_of(n.type),
        'created_at_text': format_datetime(n.created_at),
        'message': n.message,
        # 詳細がないお知らせは、1行の文言をそのまま詳細にする
        'detail': n.detail or n.message,
        'is_unread': n.read_at is None,
    } for n in db.session.execute(stmt).scalars()]


def count_unread(user_id):
    """本人の未読件数（ヘッダーの通知ベルのバッジ）"""
    return db.session.scalar(
        select(func.count()).select_from(CitizenNotification)
        .where(CitizenNotification.user_id == user_id, CitizenNotification.read_at.is_(None)))


def count_unread_or_zero(user_id):
    """ヘッダーのバッジ用の未読件数。DB エラーのときは 0（エラー画面まで表示できなくならないようにする）"""
    try:
        return count_unread(user_id)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning('お知らせの未読件数を取得できませんでした user=%s', user_id, exc_info=True)
        return 0


def mark_read(notification_id, *, user_id):
    """お知らせを既読にして、本人の未読件数を返す。本人のお知らせでなければ（存在しない場合も）None

    既読済みならそのまま（何度呼んでも同じ）。
    """
    if not 0 < notification_id <= BIGINT_MAX:
        return None
    notification = db.session.scalar(
        select(CitizenNotification).where(CitizenNotification.id == notification_id,
                                          CitizenNotification.user_id == user_id))
    if notification is None:
        return None
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.session.commit()
    return count_unread(user_id)


def mark_all_read(user_id):
    """本人の未読をすべて既読にし、既読にした件数を返す（一覧に出ていない100件目より古いお知らせも含む）"""
    result = db.session.execute(
        update(CitizenNotification)
        .where(CitizenNotification.user_id == user_id, CitizenNotification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc)))
    db.session.commit()
    return result.rowcount
