# ステータス変更の唯一の窓口：遷移チェック・対応履歴の記録・報告の取り消し
from app.constants import (
    MSG_COMMENT_REQUIRED, MSG_GROUP_INVALID_STATUS, MSG_INVALID_STATUS,
    ReportStatus, next_statuses,
)
from app.extensions import db
from app.models import DuplicateGroup, Report, ReportStatusHistory, User
from app.services import ServiceError


def record_received(report, *, actor_id):
    """新規報告の受付を対応履歴に記録する（コミットは呼び出し側）。新規報告 基本フロー2"""
    report.status = ReportStatus.RECEIVED.value
    db.session.add(ReportStatusHistory(
        report=report, from_status=None, to_status=ReportStatus.RECEIVED.value, changed_by_id=actor_id))
    return report


def change_status(report, to_status, *, actor, comment=None, public_message=None):
    """報告のステータスを変更し、対応履歴を1行追加する（コミットは呼び出し側）

    to_status が現在と同じ場合はコメントのみの更新として履歴だけを残す。
    """
    to_status = ReportStatus(to_status)
    if to_status != report.status and to_status not in next_statuses(report.status, actor.role):
        raise ServiceError(MSG_INVALID_STATUS.format(
            current=ReportStatus.label_of(report.status), status=to_status.label))

    db.session.add(ReportStatusHistory(
        report=report,
        from_status=report.status,
        to_status=to_status.value,
        changed_by_id=actor.id,
        comment=comment or None,
        public_message=public_message or None,
    ))
    report.status = to_status.value
    return report


def update_report_status(report_id, to_status, *, actor_id, comment):
    """職員が報告の対応状況と担当者メモを更新する。詳細・対応更新画面 基本フロー2

    担当者メモ・進捗コメントは必須（代替フロー2a-1）。存在しない報告は None。
    """
    report = db.session.get(Report, report_id)
    if report is None:
        return None
    comment = (comment or '').strip()
    if not comment:
        raise ServiceError(MSG_COMMENT_REQUIRED)
    if to_status not in ReportStatus.values():
        raise ServiceError(MSG_INVALID_STATUS.format(
            current=ReportStatus.label_of(report.status), status=to_status))

    change_status(report, to_status, actor=db.session.get(User, actor_id), comment=comment)
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

    targets = [r for r in group.reports
               if r.status not in (ReportStatus.CANCELLED.value, to_status)]
    invalid = [r for r in targets if to_status not in next_statuses(r.status, actor.role)]
    if to_status not in ReportStatus.values():
        invalid = group.reports
    if invalid:
        raise ServiceError(MSG_GROUP_INVALID_STATUS.format(
            status=ReportStatus.label_of(to_status),
            report_ids='、'.join(f'#{r.id}（{ReportStatus.label_of(r.status)}）' for r in invalid)))

    for report in targets:
        change_status(report, to_status, actor=actor)
    db.session.commit()
    return group
