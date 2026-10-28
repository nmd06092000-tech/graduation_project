# 重複グループの一覧・代表案件への統合のテスト
from decimal import Decimal

import pytest

from app.constants import MSG_GROUP_NOT_OPEN, MSG_PRIMARY_NOT_IN_GROUP, DuplicateGroupStatus
from app.extensions import db
from app.models import AiAnalysis, DuplicateGroup
from app.services import ServiceError, duplicate_service


@pytest.fixture
def group(app):
    group = DuplicateGroup(similarity=Decimal('0.960'))
    db.session.add(group)
    db.session.commit()
    return group


# 対応管理・重複確認 基本フロー1・3：確認待ちのグループのみ、AI 判定済みの報告は ai_detected
def test_list_candidate_groups(make_report, road, group):
    first = make_report(duplicate_group=group, category_id=road.id)
    second = make_report(duplicate_group=group, category_id=road.id)
    db.session.add(AiAnalysis(report=second, status='succeeded', model_name='rule_based',
                              category_id=road.id, urgency='high', confidence=Decimal('0.9')))
    db.session.add(DuplicateGroup(status=DuplicateGroupStatus.MERGED.value))
    db.session.commit()

    [row] = duplicate_service.list_candidate_groups()

    assert row['group_id'] == group.id
    assert row['title'] == 'さいたま市大宮区桜木町1丁目の道路'
    assert row['similarity'] == '96%'
    assert [(r['id'], r['reporter'], r['ai_detected']) for r in row['reports']] == [
        (first.id, '山田 太郎', False), (second.id, '山田 太郎', True)]


def test_list_candidate_groups_empty(app):
    assert duplicate_service.list_candidate_groups() == []


# 代表案件に統合する 基本フロー4：代表以外に duplicate_of_id、グループは統合済み
def test_merge_group(staff, make_report, group):
    primary = make_report(duplicate_group=group)
    child = make_report(duplicate_group=group)

    duplicate_service.merge_group(group.id, primary.id, actor_id=staff.id)

    assert primary.duplicate_of_id is None
    assert child.duplicate_of_id == primary.id
    assert group.status == DuplicateGroupStatus.MERGED.value
    assert group.resolved_by_id == staff.id and group.resolved_at is not None
    assert duplicate_service.list_candidate_groups() == []


def test_merge_group_rejects_report_outside_group(staff, make_report, group):
    make_report(duplicate_group=group)
    outsider = make_report()

    with pytest.raises(ServiceError, match=MSG_PRIMARY_NOT_IN_GROUP):
        duplicate_service.merge_group(group.id, outsider.id, actor_id=staff.id)


def test_merge_group_rejects_merged_group(staff, make_report, group):
    primary = make_report(duplicate_group=group)
    duplicate_service.merge_group(group.id, primary.id, actor_id=staff.id)

    with pytest.raises(ServiceError, match=MSG_GROUP_NOT_OPEN):
        duplicate_service.merge_group(group.id, primary.id, actor_id=staff.id)


def test_merge_group_not_found(staff):
    assert duplicate_service.merge_group(999, 1, actor_id=staff.id) is None
