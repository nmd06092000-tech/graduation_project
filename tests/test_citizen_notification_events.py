# 市民向けお知らせを作る3つの出来事（報告の受付・AI の初回判定・職員のステータス変更）のテスト
import pytest

from app import ai
from app.ai.base import AIError
from app.constants import MSG_COMMENT_REQUIRED, MSG_INVALID_STATUS, MSG_REPORT_ALL_REQUIRED, ReportStatus
from app.extensions import db
from app.models import AiAnalysis, CitizenNotification, DuplicateGroup, ReportStatusHistory, User
from app.services import ServiceError, ValidationError, citizen_notification_service, report_service, status_service
from tests.conftest import jpeg_bytes, make_account, upload


def _notifications(user_id=None):
    db.session.expire_all()
    query = CitizenNotification.query.order_by(CitizenNotification.id)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    return query.all()


def _create(citizen, **values):
    values = {
        'citizen_id': citizen.id,
        'title': '道路の大きな穴',
        'description': '車道に大きな穴が開いていて危険です。',
        'address': 'さいたま市大宮区桜木町1丁目 付近',
        'latitude': '35.906',
        'longitude': '139.624',
        'photos': [upload(jpeg_bytes())],
        **values,
    }
    return report_service.create_report(**values)


class _FailingClassifier:
    """Gemini の代わりに必ず失敗するクライアント（実 API は呼ばない）"""
    model_name = 'gemini-test'

    def classify(self, text, photos=(), *, choices):
        raise AIError('Gemini API エラー (429)', kind='quota')


class _SucceedingClassifier:
    """Gemini の代わりに成功するクライアント（結果はキーワード判定と同じ。実 API は呼ばない）"""
    model_name = 'gemini-test'

    def classify(self, text, photos=(), *, choices):
        return ai.get_fallback_classifier().classify(text, photos, choices=choices)


# ---- 受付（受け入れ基準1） ----

def test_report_received_notification_created_for_owner(citizen, road):
    # Act
    report = _create(citizen)

    # Assert: 受付のお知らせが本人宛に 1 件（報告番号・件名入り・未読）
    received = [n for n in _notifications(citizen.id) if n.type == 'report_received']
    assert len(received) == 1
    assert received[0].report_id == report.id
    assert f'#{report.id}' in received[0].message and '道路の大きな穴' in received[0].message
    assert received[0].read_at is None


def test_report_received_notification_not_created_when_validation_fails(citizen, road):
    # Act
    with pytest.raises(ValidationError) as e:
        _create(citizen, photos=[], address='', latitude='', longitude='', description='', title='')

    # Assert
    assert e.value.messages[0] == ('danger', MSG_REPORT_ALL_REQUIRED)
    assert _notifications() == []


def test_report_received_notification_goes_only_to_reporter(citizen, road):
    # Arrange
    other = make_account('hanako@example.com', User.ROLE_CITIZEN)

    # Act
    _create(citizen)

    # Assert
    assert _notifications(other.id) == []


# ---- AI 判定（受け入れ基準2） ----

def test_ai_judged_notification_created_on_initial_rule_based(citizen, road):
    # Act
    report = _create(citizen)

    # Assert: 受付 → AI判定 の順に 2 件。AI判定には緊急度が入る
    types = [n.type for n in _notifications(citizen.id)]
    assert types == ['report_received', 'ai_judged']
    judged = _notifications(citizen.id)[1]
    assert judged.report_id == report.id
    assert '緊急度：' in judged.message and 'カテゴリ：' in judged.detail


def test_ai_judged_notification_created_when_gemini_succeeds(monkeypatch, citizen, road):
    # Arrange
    monkeypatch.setattr(ai, 'get_classifier', lambda: _SucceedingClassifier())

    # Act
    _create(citizen)

    # Assert
    assert [n.type for n in _notifications(citizen.id)] == ['report_received', 'ai_judged']


def test_ai_judged_notification_created_once_after_fallback_to_rule_based(monkeypatch, citizen, road):
    # Arrange: Gemini が失敗 → キーワード判定に切り替わる
    monkeypatch.setattr(ai, 'get_classifier', lambda: _FailingClassifier())

    # Act
    report = _create(citizen)

    # Assert: 失敗の行が残っても、AI判定のお知らせは成功した 1 件だけ
    statuses = sorted(a.status for a in report.ai_analyses)
    assert statuses == ['failed', 'succeeded']
    assert [n.type for n in _notifications(citizen.id)] == ['report_received', 'ai_judged']


def test_no_ai_judged_notification_when_ai_fails_completely(monkeypatch, citizen, road):
    # Arrange: Gemini もキーワード判定も失敗する
    def failing_classify(*args, **kwargs):
        raise AIError('テスト用の失敗', kind='timeout')
    monkeypatch.setattr(report_service, '_classify', failing_classify)

    # Act
    report = _create(citizen)

    # Assert: 報告は登録され、受付のお知らせだけ（AI判定は作らない）
    assert report.id is not None
    assert all(a.status == 'failed' for a in report.ai_analyses)
    assert [n.type for n in _notifications(citizen.id)] == ['report_received']


def test_no_ai_judged_notification_when_ai_raises_unexpected_error(monkeypatch, citizen, road):
    # Arrange
    def broken():
        raise RuntimeError('boom')
    monkeypatch.setattr(ai, 'get_classifier', broken)

    # Act
    report = _create(citizen)

    # Assert: 報告もその受付のお知らせも残る
    assert report.id is not None
    assert [n.type for n in _notifications(citizen.id)] == ['report_received']


def test_no_ai_judged_notification_on_rejudge(citizen, road, make_report):
    # Arrange
    report = make_report()

    # Act: 再判定（rejudge）は成功しても市民に知らせない
    analysis = report_service.analyze_report(report.id, trigger_type='rejudge')

    # Assert
    assert analysis is not None and analysis.status == 'succeeded'
    assert _notifications() == []


def test_no_citizen_notification_on_stale_rejudge_batch(citizen, road, make_report):
    # Arrange
    from datetime import datetime, timedelta, timezone
    make_report(created_at=datetime.now(timezone.utc) - timedelta(hours=13))

    # Act
    assert report_service.rejudge_stale_reports() == 1

    # Assert
    assert _notifications() == []


# ---- 職員のステータス変更（受け入れ基準3） ----

def test_staff_status_change_creates_notification_for_report_owner(citizen, staff, make_report):
    # Arrange
    report = make_report()

    # Act
    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='現地確認に向かいます')

    # Assert
    [n] = _notifications(citizen.id)
    assert n.type == 'staff_update' and n.report_id == report.id and n.read_at is None
    assert f'「{ReportStatus.IN_PROGRESS.label}」' in n.message
    assert f'「{ReportStatus.RECEIVED.label}」から「{ReportStatus.IN_PROGRESS.label}」' in n.detail


def test_each_staff_status_change_creates_one_notification(citizen, staff, make_report):
    # Arrange
    report = make_report()

    # Act
    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='対応開始')
    status_service.update_report_status(report.id, 'confirming', actor_id=staff.id, comment='確認へ')

    # Assert
    assert len(_notifications(citizen.id)) == 2
    assert db.session.query(ReportStatusHistory).filter_by(report_id=report.id).count() == 2


def test_status_change_notification_goes_only_to_report_owner(citizen, staff, make_report):
    # Arrange
    other = make_account('hanako@example.com', User.ROLE_CITIZEN)
    report = make_report()

    # Act
    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='対応開始')

    # Assert
    assert len(_notifications(citizen.id)) == 1
    assert _notifications(other.id) == []


def test_status_change_via_route_creates_notification(client, login, citizen, staff, make_report):
    # Arrange
    report = make_report()
    login(client, User.ROLE_STAFF, staff.login_id)

    # Act
    response = client.post(f'/staff/reports/{report.id}/status', data={
        'status': ReportStatus.IN_PROGRESS.value, 'comment': '現地確認に向かいます。'})

    # Assert
    assert response.status_code == 302
    assert [n.type for n in _notifications(citizen.id)] == ['staff_update']


def test_no_notification_for_comment_only_update(citizen, staff, make_report):
    # Arrange: 状態は同じ（未対応のまま）で、ノートだけ追加する
    report = make_report()

    # Act
    status_service.update_report_status(report.id, 'received', actor_id=staff.id, comment='メモだけ追加')

    # Assert: 履歴は残るがお知らせは作らない
    assert db.session.query(ReportStatusHistory).filter_by(report_id=report.id).count() == 1
    assert _notifications() == []


def test_no_notification_for_urgency_only_change(citizen, staff, make_report):
    # Arrange
    report = make_report(urgency='high')

    # Act: 状態は同じまま緊急度だけ変更する
    status_service.update_report_status(report.id, 'received', actor_id=staff.id, comment='緊急度を見直し',
                                        urgency='low', urgency_reason='軽微なため')

    # Assert
    db.session.refresh(report)
    assert report.urgency == 'low'
    assert _notifications() == []


def test_no_notification_when_citizen_cancels_own_report(citizen, make_report):
    # Arrange
    report = make_report()

    # Act: 市民本人の取り消し
    status_service.change_status(report, 'cancelled', actor=citizen, comment='取り消します')
    db.session.commit()

    # Assert
    assert report.status == ReportStatus.CANCELLED.value
    assert _notifications() == []


def test_no_notification_when_status_change_validation_fails(citizen, staff, make_report):
    # Arrange
    report = make_report()

    # Act / Assert: コメント未入力
    with pytest.raises(ServiceError, match=MSG_COMMENT_REQUIRED):
        status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='')
    # Act / Assert: 遷移表にない変更（未対応 → 完了確認中）
    with pytest.raises(ServiceError) as e:
        status_service.update_report_status(report.id, 'confirming', actor_id=staff.id, comment='確認')
    assert str(e.value) == MSG_INVALID_STATUS.format(
        current=ReportStatus.RECEIVED.label, status=ReportStatus.CONFIRMING.label)
    db.session.rollback()

    # Assert
    assert _notifications() == []


def test_group_status_change_notifies_each_report_owner_once(citizen, staff, make_report):
    # Arrange: 別々の市民の報告 2 件 + 取り消し済み 1 件 + すでに対応中 1 件が同じグループ
    other = make_account('hanako@example.com', User.ROLE_CITIZEN)
    group = DuplicateGroup()
    db.session.add(group)
    mine = make_report(duplicate_group=group)
    theirs = make_report(citizen_id=other.id, duplicate_group=group)
    make_report(duplicate_group=group, status='cancelled')
    make_report(duplicate_group=group, status='in_progress')

    # Act
    status_service.change_group_status(group.id, 'in_progress', actor_id=staff.id)

    # Assert: 変更された 2 件の持ち主に 1 件ずつ（取り消し済み・すでに対応中は対象外）
    assert [n.report_id for n in _notifications(other.id)] == [theirs.id]
    assert [n.report_id for n in _notifications(citizen.id)] == [mine.id]
    assert all(n.type == 'staff_update' for n in _notifications())


def test_group_status_change_creates_nothing_when_a_report_cannot_transition(citizen, staff, make_report):
    # Arrange: 完了確認中へは未対応から直接変更できない → 全件変更なし
    group = DuplicateGroup()
    db.session.add(group)
    make_report(duplicate_group=group)

    # Act
    with pytest.raises(ServiceError):
        status_service.change_group_status(group.id, 'confirming', actor_id=staff.id)

    # Assert
    assert _notifications() == []


# ---- 内部情報を出さない（受け入れ基準3） ----

def test_staff_internal_info_never_appears_in_notification(citizen, staff, make_report, road):
    # Arrange: AI 判定済みの報告に、内部メモ・緊急度の理由つきで職員が変更する
    report = make_report(urgency='high')
    db.session.add(AiAnalysis(report=report, status='succeeded', model_name='rule_based',
                              category_id=road.id, urgency='high',
                              department_id=road.default_department_id, confidence=0.9))
    db.session.commit()

    # Act
    status_service.update_report_status(
        report.id, 'in_progress', actor_id=staff.id, comment='内部メモ：近隣トラブルあり',
        urgency='low', urgency_reason='緊急度を下げた内部理由')

    # Assert: 職員名・コメント・緊急度の理由はどのお知らせにも入らない
    for n in _notifications(citizen.id):
        text = f'{n.message}\n{n.detail}'
        assert '内部メモ' not in text and '近隣トラブル' not in text
        assert '緊急度を下げた内部理由' not in text
        assert staff.name not in text and staff.login_id not in text


def test_notify_status_changed_public_message_is_only_text_added(citizen, make_report):
    # Arrange: public_message は呼び出し側が渡した公開用の文だけを足す（comment は引数にない）
    report = make_report()

    # Act
    citizen_notification_service.notify_status_changed(
        report, 'received', 'in_progress', public_message='来週対応します。')
    db.session.commit()

    # Assert
    assert _notifications(citizen.id)[0].detail.endswith('職員からのメッセージ：来週対応します。')
