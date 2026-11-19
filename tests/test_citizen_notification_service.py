# 市民向けお知らせ（citizen_notification_service）の作成・一覧・未読件数・既読のテスト
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.constants import (
    MSG_CNOTICE_AI_JUDGED, MSG_CNOTICE_RECEIVED, MSG_CNOTICE_STATUS, CitizenNotificationType, ReportStatus,
)
from app.extensions import db
from app.models import AiAnalysis, CitizenNotification, User
from app.services import citizen_notification_service as service
from tests.conftest import make_account

BASE_TIME = datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def other(app):
    """別の市民（他人のお知らせを触れないことの確認用）"""
    return make_account('hanako@example.com', User.ROLE_CITIZEN, name='佐藤 花子')


def _all():
    db.session.expire_all()
    return CitizenNotification.query.order_by(CitizenNotification.id).all()


# ---- create ----

def test_create_saves_notification_for_report_owner_as_unread(citizen, make_report):
    # Arrange
    report = make_report()

    # Act
    service.create(report, CitizenNotificationType.AI_JUDGED, '1行', '詳細')
    db.session.commit()

    # Assert
    [n] = _all()
    assert (n.user_id, n.report_id, n.type, n.message, n.detail) == (
        citizen.id, report.id, 'ai_judged', '1行', '詳細')
    assert n.read_at is None and n.created_at is not None


def test_create_stores_empty_detail_as_null(citizen, make_report):
    # Act
    service.create(make_report(), 'report_received', '1行', '')
    db.session.commit()

    # Assert
    assert _all()[0].detail is None


def test_create_rejects_unknown_type(citizen, make_report):
    # Act / Assert
    with pytest.raises(ValueError):
        service.create(make_report(), 'unknown', '1行')


def test_create_does_not_commit(citizen, make_report):
    # Arrange
    report = make_report()

    # Act: commit せずに rollback する
    service.create(report, 'report_received', '1行')
    db.session.rollback()

    # Assert: コミットは呼び出し側の責任（rollback で消える）
    assert _all() == []


# ---- notify_* ----

def test_notify_report_received_uses_report_number_and_title(citizen, make_report):
    # Arrange
    report = make_report(title='道路の大きな穴')

    # Act
    service.notify_report_received(report)
    db.session.commit()

    # Assert
    [n] = _all()
    assert n.type == 'report_received'
    assert n.message == MSG_CNOTICE_RECEIVED.format(report_id=report.id, title='道路の大きな穴')
    assert '職員が確認します' in n.detail


def test_notify_ai_judged_has_category_and_urgency_but_no_reason(citizen, make_report, road):
    # Arrange
    report = make_report()
    analysis = AiAnalysis(report=report, status='succeeded', model_name='rule_based',
                          category_id=road.id, urgency='high',
                          department_id=road.default_department_id, confidence=0.9,
                          raw_response={'reason': '内部向けの判断理由'})
    db.session.add(analysis)
    db.session.commit()

    # Act
    service.notify_ai_judged(report, analysis)
    db.session.commit()

    # Assert
    [n] = _all()
    assert n.type == 'ai_judged'
    assert n.message == MSG_CNOTICE_AI_JUDGED.format(report_id=report.id, title=report.title, urgency='高')
    assert 'カテゴリ：道路' in n.detail and '緊急度：高' in n.detail
    assert '内部向けの判断理由' not in n.detail


def test_notify_status_changed_has_from_to_labels(citizen, make_report):
    # Arrange
    report = make_report()

    # Act
    service.notify_status_changed(report, ReportStatus.RECEIVED.value, ReportStatus.IN_PROGRESS.value)
    db.session.commit()

    # Assert
    [n] = _all()
    assert n.type == 'staff_update'
    assert n.message == MSG_CNOTICE_STATUS.format(
        report_id=report.id, title=report.title, status=ReportStatus.IN_PROGRESS.label)
    assert f'「{ReportStatus.RECEIVED.label}」から「{ReportStatus.IN_PROGRESS.label}」' in n.detail
    assert '職員からのメッセージ' not in n.detail


def test_notify_status_changed_appends_public_message(citizen, make_report):
    # Act
    service.notify_status_changed(make_report(), 'received', 'in_progress', public_message='  現地を確認します。  ')
    db.session.commit()

    # Assert: 前後の空白は除いて、詳細の末尾に付く
    assert _all()[0].detail.endswith('職員からのメッセージ：現地を確認します。')


@pytest.mark.parametrize('public_message', [None, '', '   '])
def test_notify_status_changed_ignores_blank_public_message(citizen, make_report, public_message):
    # Act
    service.notify_status_changed(make_report(), 'received', 'in_progress', public_message=public_message)
    db.session.commit()

    # Assert
    assert '職員からのメッセージ' not in _all()[0].detail


# ---- list_for_user ----

def test_list_for_user_returns_only_own_newest_first(citizen, other, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    old = make_citizen_notification(report, message='古い', created_at=BASE_TIME)
    new = make_citizen_notification(report, message='新しい', created_at=BASE_TIME + timedelta(hours=1))
    make_citizen_notification(make_report(citizen_id=other.id), message='他人の', created_at=BASE_TIME)

    # Act
    items = service.list_for_user(citizen.id)

    # Assert
    assert [i['id'] for i in items] == [new.id, old.id]


def test_list_for_user_orders_same_time_by_id_desc(citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    first = make_citizen_notification(report, created_at=BASE_TIME)
    second = make_citizen_notification(report, created_at=BASE_TIME)

    # Act / Assert
    assert [i['id'] for i in service.list_for_user(citizen.id)] == [second.id, first.id]


def test_list_for_user_item_has_display_fields(citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    n = make_citizen_notification(report, type='staff_update', message='1行', detail='詳細',
                                  created_at=BASE_TIME)

    # Act
    [item] = service.list_for_user(citizen.id)

    # Assert: 日時は日本時間（UTC 09:00 → 18:00）
    assert item == {
        'id': n.id, 'report_id': report.id, 'type': 'staff_update', 'type_label': '職員から',
        'created_at_text': '2026/11/01 18:00', 'message': '1行', 'detail': '詳細', 'is_unread': True}


def test_list_for_user_detail_falls_back_to_message(citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report(), message='1行だけ', detail=None)

    # Act
    [item] = service.list_for_user(citizen.id)

    # Assert
    assert item['detail'] == '1行だけ'


def test_list_for_user_marks_read_item_not_unread(citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report(), read_at=BASE_TIME)

    # Act / Assert
    assert service.list_for_user(citizen.id)[0]['is_unread'] is False


def test_list_for_user_limits_to_100_newest(citizen, make_report, make_citizen_notification):
    # Arrange: 101 件（一番古い 1 件だけが一覧から外れる）
    report = make_report()
    db.session.add_all([
        CitizenNotification(user_id=citizen.id, report_id=report.id, type='report_received', message=f'{i}',
                            created_at=BASE_TIME + timedelta(minutes=i)) for i in range(101)])
    db.session.commit()

    # Act
    items = service.list_for_user(citizen.id)

    # Assert
    assert len(items) == 100
    assert items[0]['message'] == '100' and items[-1]['message'] == '1'


def test_list_for_user_respects_explicit_limit(citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    for _ in range(3):
        make_citizen_notification(report)

    # Act / Assert
    assert len(service.list_for_user(citizen.id, limit=2)) == 2


def test_list_for_user_empty(citizen):
    # Act / Assert
    assert service.list_for_user(citizen.id) == []


# ---- count_unread ----

def test_count_unread_counts_only_own_unread(citizen, other, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    make_citizen_notification(report)
    make_citizen_notification(report)
    make_citizen_notification(report, read_at=BASE_TIME)
    make_citizen_notification(make_report(citizen_id=other.id))

    # Act / Assert
    assert service.count_unread(citizen.id) == 2
    assert service.count_unread(other.id) == 1


def test_count_unread_is_zero_without_notifications(citizen):
    # Act / Assert
    assert service.count_unread(citizen.id) == 0


def test_count_unread_or_zero_returns_count(citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())

    # Act / Assert
    assert service.count_unread_or_zero(citizen.id) == 1


def test_count_unread_or_zero_returns_zero_on_db_error(citizen, monkeypatch):
    # Arrange: DB エラー（画面の表示を止めない）
    def broken(user_id):
        raise SQLAlchemyError('boom')
    monkeypatch.setattr(service, 'count_unread', broken)

    # Act / Assert
    assert service.count_unread_or_zero(citizen.id) == 0


# ---- mark_read ----

def test_mark_read_sets_read_at_and_returns_remaining_unread(citizen, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    target = make_citizen_notification(report)
    make_citizen_notification(report)

    # Act
    remaining = service.mark_read(target.id, user_id=citizen.id)

    # Assert
    assert remaining == 1
    db.session.expire_all()
    assert db.session.get(CitizenNotification, target.id).read_at is not None


def test_mark_read_is_idempotent_and_keeps_first_read_at(citizen, make_report, make_citizen_notification):
    # Arrange
    target = make_citizen_notification(make_report(), read_at=BASE_TIME)

    # Act
    remaining = service.mark_read(target.id, user_id=citizen.id)

    # Assert: 既読済みを再度呼んでも日時は変わらない
    assert remaining == 0
    db.session.expire_all()
    assert db.session.get(CitizenNotification, target.id).read_at == BASE_TIME


def test_mark_read_of_other_users_notification_returns_none_and_keeps_unread(
        citizen, other, make_report, make_citizen_notification):
    # Arrange
    others = make_citizen_notification(make_report(citizen_id=other.id))

    # Act
    result = service.mark_read(others.id, user_id=citizen.id)

    # Assert
    assert result is None
    db.session.expire_all()
    assert db.session.get(CitizenNotification, others.id).read_at is None


@pytest.mark.parametrize('notification_id', [0, -1, 999999, 2 ** 63, 2 ** 70])
def test_mark_read_returns_none_for_missing_or_out_of_range_id(citizen, notification_id):
    # Act / Assert: 存在しない・bigint の範囲外の ID は DB エラーにせず None
    assert service.mark_read(notification_id, user_id=citizen.id) is None


# ---- mark_all_read ----

def test_mark_all_read_marks_only_own_unread_and_returns_count(
        citizen, other, make_report, make_citizen_notification):
    # Arrange
    report = make_report()
    make_citizen_notification(report)
    make_citizen_notification(report)
    already = make_citizen_notification(report, read_at=BASE_TIME)
    others = make_citizen_notification(make_report(citizen_id=other.id))

    # Act
    count = service.mark_all_read(citizen.id)

    # Assert
    assert count == 2
    db.session.expire_all()
    assert service.count_unread(citizen.id) == 0
    assert db.session.get(CitizenNotification, already.id).read_at == BASE_TIME
    assert db.session.get(CitizenNotification, others.id).read_at is None


def test_mark_all_read_is_idempotent(citizen, make_report, make_citizen_notification):
    # Arrange
    make_citizen_notification(make_report())
    service.mark_all_read(citizen.id)

    # Act / Assert
    assert service.mark_all_read(citizen.id) == 0


def test_mark_all_read_includes_notifications_beyond_list_limit(citizen, make_report):
    # Arrange: 一覧に出ない 101 件目も既読にする
    report = make_report()
    db.session.add_all([
        CitizenNotification(user_id=citizen.id, report_id=report.id, type='report_received', message=f'{i}')
        for i in range(101)])
    db.session.commit()

    # Act / Assert
    assert service.mark_all_read(citizen.id) == 101
    assert service.count_unread(citizen.id) == 0
