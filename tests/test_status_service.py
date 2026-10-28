# ステータス遷移・取り消し・対応履歴のテスト
import pytest

from app.constants import MSG_COMMENT_REQUIRED, ReportStatus
from app.extensions import db
from app.models import DuplicateGroup, ReportStatusHistory
from app.services import ServiceError, status_service


def _histories(report):
    return db.session.query(ReportStatusHistory).filter_by(report_id=report.id).all()


# 詳細・対応更新画面 基本フロー2：未対応 → 対応中 と担当者メモが対応履歴に残る
def test_update_report_status_records_history(staff, make_report):
    report = make_report()

    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='現地確認に向かいます')

    assert report.status == ReportStatus.IN_PROGRESS.value
    [history] = _histories(report)
    assert (history.from_status, history.to_status) == ('received', 'in_progress')
    assert history.changed_by_id == staff.id
    assert history.comment == '現地確認に向かいます'


# 代替フロー2a-1：担当者メモ・進捗コメントが未入力
@pytest.mark.parametrize('comment', ['', '   '])
def test_update_report_status_requires_comment(staff, make_report, comment):
    report = make_report()

    with pytest.raises(ServiceError, match=MSG_COMMENT_REQUIRED):
        status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment=comment)

    assert report.status == ReportStatus.RECEIVED.value
    assert _histories(report) == []


# 職員は 未対応 → 対応済み に直接変更できる
def test_update_report_status_received_to_resolved(staff, make_report):
    report = make_report()

    status_service.update_report_status(report.id, 'resolved', actor_id=staff.id, comment='現地で対応完了')

    assert report.status == ReportStatus.RESOLVED.value
    [history] = _histories(report)
    assert (history.from_status, history.to_status) == ('received', 'resolved')


# README の状態遷移にない変更（対応中 → 未対応、職員による取り消し、対応済み → 対応中）は拒否する
@pytest.mark.parametrize('current, new', [
    ('in_progress', 'received'),
    ('received', 'cancelled'),
    ('resolved', 'in_progress'),
    ('received', 'unknown'),
])
def test_update_report_status_rejects_invalid_transition(staff, make_report, current, new):
    report = make_report(status=current)

    with pytest.raises(ServiceError):
        status_service.update_report_status(report.id, new, actor_id=staff.id, comment='メモ')

    assert report.status == current


# ステータスを変えずにコメントだけ追加した場合も履歴を残す
def test_update_report_status_comment_only(staff, make_report):
    report = make_report(status='in_progress')

    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='業者手配済み')

    [history] = _histories(report)
    assert history.from_status == history.to_status == 'in_progress'


def test_update_report_status_not_found(staff):
    assert status_service.update_report_status(999, 'in_progress', actor_id=staff.id, comment='メモ') is None


# 一括対応ステータス変更 基本フロー4：取り消し済みと変更後と同じステータスの報告は対象外
def test_change_group_status(staff, make_report):
    group = DuplicateGroup()
    db.session.add(group)
    received = make_report(duplicate_group=group)
    already = make_report(duplicate_group=group, status='in_progress')
    cancelled = make_report(duplicate_group=group, status='cancelled')

    status_service.change_group_status(group.id, 'in_progress', actor_id=staff.id)

    assert received.status == already.status == 'in_progress'
    assert cancelled.status == 'cancelled'
    assert len(_histories(received)) == 1
    assert _histories(already) == [] and _histories(cancelled) == []


# 1件でも遷移できない報告があれば、どの報告も変更しない
def test_change_group_status_all_or_nothing(staff, make_report):
    group = DuplicateGroup()
    db.session.add(group)
    received = make_report(duplicate_group=group)
    resolved = make_report(duplicate_group=group, status='resolved')

    with pytest.raises(ServiceError, match=f'#{resolved.id}'):
        status_service.change_group_status(group.id, 'in_progress', actor_id=staff.id)

    assert received.status == 'received'
    assert resolved.status == 'resolved'
    assert _histories(received) == []


def test_change_group_status_not_found(staff):
    assert status_service.change_group_status(999, 'in_progress', actor_id=staff.id) is None
