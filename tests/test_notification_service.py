# 職員向けの通知（未読一覧・既読）のテスト
from app.extensions import db
from app.services import notification_service


def _notify(report, message):
    notification = notification_service.create(report.id, message)
    db.session.commit()
    return notification


# 未読は新しい順。既読は職員全員で共通
def test_unread_and_mark_read(staff, make_report):
    report = make_report()
    first = _notify(report, '1件目')
    second = _notify(report, '2件目')

    assert [n['message'] for n in notification_service.list_unread()] == ['2件目', '1件目']
    assert notification_service.count_unread() == 2

    assert notification_service.mark_read(first.id, actor_id=staff.id) is True
    assert first.read_by_id == staff.id and first.read_at is not None
    assert [n['id'] for n in notification_service.list_unread()] == [second.id]


def test_mark_all_read(staff, make_report):
    report = make_report()
    _notify(report, '1件目')
    _notify(report, '2件目')

    assert notification_service.mark_all_read(actor_id=staff.id) == 2
    assert notification_service.list_unread() == [] and notification_service.count_unread() == 0


def test_mark_read_not_found(staff):
    assert notification_service.mark_read(999, actor_id=staff.id) is False
