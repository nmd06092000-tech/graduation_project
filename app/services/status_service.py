# ステータス変更の唯一の窓口：遷移チェック・対応履歴の記録・報告の取り消し
# 職員による緊急度の変更も対応履歴に記録するため、ここで行う
from sqlalchemy import select

from app.constants import (
    MSG_COMMENT_REQUIRED, MSG_GROUP_INVALID_STATUS, MSG_INVALID_STATUS, MSG_INVALID_URGENCY,
    MSG_REPORT_CANCEL_NOT_ALLOWED, MSG_URGENCY_REASON_REQUIRED, ReportStatus, Role, Urgency, next_statuses,
)
from app.extensions import db
from app.models import DuplicateGroup, Report, ReportStatusHistory, User
from app.services import ServiceError, citizen_notification_service


def record_received(report, *, actor_id):
    """新規報告の受付を対応履歴に記録する（コミットは呼び出し側）。新規報告 基本フロー2"""
    report.status = ReportStatus.RECEIVED.value
    db.session.add(ReportStatusHistory(
        report=report, from_status=None, to_status=ReportStatus.RECEIVED.value, changed_by_id=actor_id))
    return report


def change_status(report, to_status, *, actor, comment=None, public_message=None,
                  urgency=None, urgency_reason=None):
    """報告のステータスを変更し、対応履歴を1行追加する（コミットは呼び出し側）

    to_status が現在と同じ場合はコメントのみの更新として履歴だけを残す。
    urgency が現在の緊急度と異なる場合は、緊急度も変更して同じ履歴の行に記録する。
    職員がステータスを実際に変更した場合は、報告した市民にお知らせを追加する（コメントのみ・緊急度のみ・市民の取り消しは対象外）。
    """
    to_status = ReportStatus(to_status)
    from_status = report.status
    if to_status != from_status and to_status not in next_statuses(from_status, actor.role):
        raise ServiceError(MSG_INVALID_STATUS.format(
            current=ReportStatus.label_of(from_status), status=to_status.label))

    history = ReportStatusHistory(
        report=report,
        from_status=from_status,
        to_status=to_status.value,
        changed_by_id=actor.id,
        comment=comment or None,
        public_message=public_message or None,
    )
    if urgency and urgency != report.urgency:
        history.from_urgency = report.urgency
        history.to_urgency = urgency
        history.urgency_reason = urgency_reason or None
        report.urgency = urgency
    db.session.add(history)
    report.status = to_status.value
    if actor.role == Role.STAFF.value and to_status.value != from_status:
        citizen_notification_service.notify_status_changed(
            report, from_status, to_status.value, public_message=public_message)
    return report


def update_report_status(report_id, to_status, *, actor_id, comment, urgency='', urgency_reason=''):
    """職員が報告の対応状況・緊急度と担当者メモを更新する。詳細・対応更新画面 基本フロー2

    担当者メモ・進捗コメントは必須（代替フロー2a-1）。
    緊急度は空なら変更しない。最新の AI 判定と異なる緊急度に変更する場合は判断変更理由が必須。
    存在しない報告は None。
    """
    # 同じ報告への同時更新（職員どうし・市民の取り消し）で履歴が食い違わないよう、行をロックして最新の状態を読む
    report = db.session.get(Report, report_id, with_for_update=True, populate_existing=True)
    if report is None:
        return None
    comment = (comment or '').strip()
    urgency = (urgency or '').strip()
    urgency_reason = (urgency_reason or '').strip()
    if not comment:
        raise ServiceError(MSG_COMMENT_REQUIRED)
    if to_status not in ReportStatus.values():
        raise ServiceError(MSG_INVALID_STATUS.format(
            current=ReportStatus.label_of(report.status), status=to_status))
    if urgency and urgency not in Urgency.values():
        raise ServiceError(MSG_INVALID_URGENCY)
    if urgency and urgency != report.urgency and not urgency_reason:
        # 報告詳細で職員が見ている AI 判定緊急度（最新の成功）と比べる
        latest_ai = report.latest_succeeded_ai_analysis
        ai_urgency = latest_ai.urgency if latest_ai else None
        if ai_urgency and urgency != ai_urgency:
            raise ServiceError(MSG_URGENCY_REASON_REQUIRED)

    change_status(report, to_status, actor=db.session.get(User, actor_id), comment=comment,
                  urgency=urgency, urgency_reason=urgency_reason)
    db.session.commit()
    return report


def change_group_status(group_id, to_status, *, actor_id):
    """重複グループ内の報告に同じステータスを一括で適用する。一括対応ステータス変更 基本フロー4

    取り消し済みの報告と、すでに変更後のステータスの報告は対象外。
    1件でも遷移できない報告があれば、どの報告も変更しない。存在しないグループは None。
    """
    group = db.session.get(DuplicateGroup, group_id)
    if group is None:
        return None
    actor = db.session.get(User, actor_id)

    # グループ内の報告の行をロックして最新のステータスを読む（並びは group.reports と同じ。エラー文の ID 順を保つ）
    reports = db.session.execute(
        select(Report).where(Report.duplicate_group_id == group.id)
        .order_by(Report.created_at, Report.id)
        .with_for_update().execution_options(populate_existing=True)).scalars().all()
    targets = [r for r in reports
               if r.status not in (ReportStatus.CANCELLED.value, to_status)]
    invalid = [r for r in targets if to_status not in next_statuses(r.status, actor.role)]
    if to_status not in ReportStatus.values():
        invalid = reports
    if invalid:
        raise ServiceError(MSG_GROUP_INVALID_STATUS.format(
            status=ReportStatus.label_of(to_status),
            report_ids='、'.join(f'#{r.id}（{ReportStatus.label_of(r.status)}）' for r in invalid)))

    for report in targets:
        change_status(report, to_status, actor=actor)
    db.session.commit()
    return group


def cancel_by_citizen(report_id, *, citizen_id):
    """市民が自分の報告を取り消す（未対応の報告のみ）。報告を取り消す 基本フロー

    他人の報告・存在しない報告・範囲外の ID は None（呼び出し側で 404）。
    すでに取り消し済みなら何もせずその報告を返す（二重送信・再読み込みでも履歴を増やさない）。
    未対応以外は ServiceError。取り消しでは市民へのお知らせを作らない（change_status の規則）。
    """
    if not 0 < report_id <= citizen_notification_service.BIGINT_MAX:
        return None
    # 職員の対応更新と同時に走っても、片方だけが成立するよう行をロックして最新のステータスを読む
    report = db.session.execute(
        select(Report).where(Report.id == report_id, Report.citizen_id == citizen_id)
        .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if report is None:
        return None
    if report.status == ReportStatus.CANCELLED.value:
        return report
    if ReportStatus.CANCELLED not in next_statuses(report.status, Role.CITIZEN):
        raise ServiceError(MSG_REPORT_CANCEL_NOT_ALLOWED.format(
            report_id=report.id, status=ReportStatus.label_of(report.status)))

    change_status(report, ReportStatus.CANCELLED, actor=db.session.get(User, citizen_id))
    db.session.commit()
    return report
