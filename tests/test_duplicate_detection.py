# 重複報告の自動検出（duplicate_service.detect_*）と「重複なし」（dismiss）のテスト
# 並行実行（pg_advisory_xact_lock の競合）は別接続の同時実行が必要なため、ここでは扱わない
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.constants import MSG_CSRF_EXPIRED, MSG_GROUP_DISMISSED, MSG_GROUP_NOT_OPEN, DuplicateGroupStatus
from app.extensions import db
from app.models import Category, DuplicateGroup, Report, User
from app.services import ServiceError, duplicate_service, report_service
from app.utils import geo
from tests.conftest import jpeg_bytes, upload

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
BASE_LAT, BASE_LNG = 35.906, 139.624
# 緯度1度の距離（m）。北へ m メートルずらした緯度を作る
METERS_PER_DEGREE = 111194.93


def _north(meters):
    return BASE_LAT + meters / METERS_PER_DEGREE


def _south(meters):
    return BASE_LAT - meters / METERS_PER_DEGREE


def _group(status=DuplicateGroupStatus.OPEN.value):
    group = DuplicateGroup(status=status)
    db.session.add(group)
    db.session.commit()
    return group


def _flashes(client):
    with client.session_transaction() as session:
        return [tuple(item) for item in session.get('_flashes', [])]


def _dismiss_url(group):
    return f'/staff/duplicates/{group.id}/dismiss'


def _open_pair(make_report, road, group=None, meters=50):
    """未対応で近い報告 2 件（確認待ちのグループ入り）"""
    group = group or _group()
    first = make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0, duplicate_group=group)
    second = make_report(category_id=road.id, latitude=_north(meters), created_at=T0, duplicate_group=group)
    return group, first, second


# ---- 検出：グループ化の条件 ----

# 同カテゴリ・100m 以内・30 日以内・未完了の報告があれば、新しい報告との 2 件で確認待ちのグループができる
def test_detect_creates_group_for_nearby_report(make_report, road):
    # Arrange
    old = make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)
    new = make_report(category_id=road.id, latitude=_north(50), created_at=T0 + timedelta(days=1))

    # Act
    result = duplicate_service.detect_for_report(new.id)

    # Assert: 戻り値・グループの中身
    group = db.session.get(DuplicateGroup, result['group_id'])
    assert result['created'] is True and result['size'] == 2 and result['added'] == 2
    assert 0 < result['similarity'] <= 1 and float(group.similarity) == pytest.approx(result['similarity'])
    assert group.status == DuplicateGroupStatus.OPEN.value
    assert {old.duplicate_group_id, new.duplicate_group_id} == {group.id}


# 対象外の候補：別カテゴリ・100m 超・30 日超・完了／取り消し・統合済み／重複なしのグループ・統合先あり
@pytest.mark.parametrize('case', [
    'other_category', 'too_far', 'too_old', 'resolved', 'cancelled', 'merged_group', 'dismissed_group', 'merged_child',
])
def test_detect_ignores_ineligible_candidate(make_report, road, case):
    # Arrange: 新しい報告の基準は (BASE_LAT, T0)。候補だけが条件を 1 つ外れている
    values = {'category_id': road.id, 'latitude': _north(50), 'created_at': T0}
    if case == 'other_category':
        other = Category(code='light', name='街灯', default_department_id=road.default_department_id)
        db.session.add(other)
        db.session.commit()
        values['category_id'] = other.id
    elif case == 'too_far':
        values['latitude'] = _north(150)
    elif case == 'too_old':
        values['created_at'] = T0 - timedelta(days=31)
    elif case in ('resolved', 'cancelled'):
        values['status'] = case
    elif case in ('merged_group', 'dismissed_group'):
        values['duplicate_group'] = _group(case.split('_')[0])
    elif case == 'merged_child':
        primary = make_report(category_id=road.id, latitude=_north(5000), created_at=T0)
        values['duplicate_of_id'] = primary.id
    candidate = make_report(**values)
    original_group_id = candidate.duplicate_group_id
    report = make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)

    # Act
    result = duplicate_service.detect_for_report(report.id)

    # Assert: グループは作られず、報告はグループなし、候補のグループは変わらない
    assert result is None
    db.session.refresh(candidate)
    assert report.duplicate_group_id is None and candidate.duplicate_group_id == original_group_id
    assert db.session.query(DuplicateGroup).count() == (
        1 if case in ('merged_group', 'dismissed_group') else 0)


# 対象の報告自体が対象外：カテゴリなし・完了済み・統合先あり・すでにグループあり
@pytest.mark.parametrize('case', ['no_category', 'resolved', 'merged_child', 'already_grouped'])
def test_detect_skips_ineligible_report(make_report, road, case):
    # Arrange: 近くに条件を満たす候補がいる状態で、報告のほうを対象外にする
    candidate = make_report(category_id=road.id, latitude=_north(50), created_at=T0)
    values = {'category_id': road.id, 'latitude': BASE_LAT, 'created_at': T0}
    if case == 'no_category':
        values['category_id'] = None
    elif case == 'resolved':
        values['status'] = 'resolved'
    elif case == 'merged_child':
        values['duplicate_of_id'] = candidate.id
    else:
        values['duplicate_group'] = _group()
    report = make_report(**values)
    groups_before = db.session.query(DuplicateGroup).count()

    # Act
    result = duplicate_service.detect_for_report(report.id)

    # Assert
    assert result is None
    db.session.refresh(candidate)
    assert candidate.duplicate_group_id is None
    assert db.session.query(DuplicateGroup).count() == groups_before


# 距離の境界：99m はグループ化、101m はしない（Haversine で実際の距離も確かめる）
@pytest.mark.parametrize('meters, grouped', [(99, True), (101, False)])
def test_detect_radius_boundary(make_report, road, meters, grouped):
    make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)
    report = make_report(category_id=road.id, latitude=_north(meters), created_at=T0)
    assert abs(geo.distance_m(BASE_LAT, BASE_LNG, _north(meters), BASE_LNG) - meters) < 0.5

    result = duplicate_service.detect_for_report(report.id)

    assert (result is not None) is grouped


# 確認待ちのグループにいる候補があれば、そのグループに報告だけが入る（グループのない候補は巻き込まない）
def test_detect_joins_open_group_without_pulling_ungrouped(make_report, road):
    # Arrange
    group, member, _ = _open_pair(make_report, road, meters=30)
    ungrouped = make_report(category_id=road.id, latitude=_north(40), created_at=T0)
    new = make_report(category_id=road.id, latitude=_north(20), created_at=T0)

    # Act
    result = duplicate_service.detect_for_report(new.id)

    # Assert
    assert result['group_id'] == group.id and result['created'] is False
    assert result['size'] == 3 and result['added'] == 1
    db.session.refresh(ungrouped)
    assert new.duplicate_group_id == group.id and ungrouped.duplicate_group_id is None


# 候補が 2 つのグループにいる場合は、類似度の高い組のあるグループに入る（グループどうしは統合しない）
def test_detect_picks_higher_similarity_group_without_merging_groups(make_report, road):
    # Arrange: 報告 new に対し、グループ A は 30m、グループ B は 80m
    group_a, *_ = _open_pair(make_report, road, meters=30)
    group_b = _group()
    for meters in (80, 80):
        make_report(category_id=road.id, latitude=_south(meters), created_at=T0, duplicate_group=group_b)
    new = make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)

    # Act
    result = duplicate_service.detect_for_report(new.id)

    # Assert
    assert result['group_id'] == group_a.id and result['size'] == 3
    assert new.duplicate_group_id == group_a.id
    db.session.refresh(group_b)
    assert len(group_b.reports) == 2 and group_b.status == DuplicateGroupStatus.OPEN.value
    assert db.session.query(DuplicateGroup).count() == 2


# 同じ報告を 2 回検出してもグループは増えず、2 回目は何もしない
def test_detect_twice_is_idempotent(make_report, road):
    make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)
    new = make_report(category_id=road.id, latitude=_north(50), created_at=T0)

    first = duplicate_service.detect_for_report(new.id)
    second = duplicate_service.detect_for_report(new.id)

    assert first is not None and second is None
    assert db.session.query(DuplicateGroup).count() == 1


# 一括検出：件数の集計、2 回目は同じグループを増やさない
def test_detect_all_counts_and_is_idempotent(make_report, road):
    # Arrange: r1・r2・r4 は近い、r3 は遠い
    make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)
    make_report(category_id=road.id, latitude=_north(30), created_at=T0 + timedelta(minutes=1))
    make_report(category_id=road.id, latitude=_north(5000), created_at=T0 + timedelta(minutes=2))
    make_report(category_id=road.id, latitude=_north(60), created_at=T0 + timedelta(minutes=3))

    # Act
    first = duplicate_service.detect_all()
    second = duplicate_service.detect_all()

    # Assert
    assert first == {'scanned': 4, 'created': 1, 'joined': 0, 'grouped': 3, 'failed': 0}
    assert second == {'scanned': 1, 'created': 0, 'joined': 0, 'grouped': 0, 'failed': 0}
    assert db.session.query(DuplicateGroup).count() == 1


# 一括検出：1 件の失敗で全体を止めず、failed に数える
def test_detect_all_continues_after_failure(monkeypatch, make_report, road):
    first = make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)
    make_report(category_id=road.id, latitude=_north(30), created_at=T0 + timedelta(minutes=1))
    real = duplicate_service.detect_for_report

    def flaky(report_id):
        if report_id == first.id:
            raise RuntimeError('検出エラー')
        return real(report_id)
    monkeypatch.setattr(duplicate_service, 'detect_for_report', flaky)

    result = duplicate_service.detect_all()

    assert result['scanned'] == 2 and result['failed'] == 1 and result['created'] == 1


# ---- 報告作成との連携・CLI ----

def _create(citizen, **values):
    values = {
        'citizen_id': citizen.id, 'title': '道路の大きな穴', 'description': '車道に大きな穴が開いていて危険です。',
        'address': 'さいたま市大宮区桜木町1丁目 付近', 'latitude': str(BASE_LAT), 'longitude': str(BASE_LNG),
        'photos': [upload(jpeg_bytes())], **values,
    }
    return report_service.create_report(**values)


# 報告作成時に自動検出：同じ場所・同じ内容の 2 件目で確認待ちのグループができる
def test_create_report_detects_duplicate(citizen, road):
    first = _create(citizen)
    assert first.duplicate_group_id is None

    second = _create(citizen)

    db.session.refresh(first)
    assert second.duplicate_group_id is not None and first.duplicate_group_id == second.duplicate_group_id
    assert second.duplicate_group.status == DuplicateGroupStatus.OPEN.value


# 重複検出で想定外のエラーが起きても、報告の登録は成功する
def test_create_report_succeeds_when_detection_fails(monkeypatch, citizen, road):
    def boom(report_id):
        raise RuntimeError('検出エラー')
    monkeypatch.setattr(duplicate_service, 'detect_for_report', boom)

    report = _create(citizen)

    assert db.session.get(Report, report.id) is not None
    assert report.duplicate_group_id is None


# flask detect-duplicates：日本語で結果を表示し、終了コードは 0
def test_cli_detect_duplicates(runner, make_report, road):
    make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0)
    make_report(category_id=road.id, latitude=_north(30), created_at=T0)

    result = runner.invoke(args=['detect-duplicates'])

    assert result.exit_code == 0, result.output
    assert '2 件の報告を調べました' in result.output
    assert '新しいグループ 1 件' in result.output and '失敗 0 件' in result.output
    assert db.session.query(DuplicateGroup).count() == 1


# ---- 類似度 ----

def _pseudo(meters=0, days=0, title='道路の穴'):
    return SimpleNamespace(latitude=_north(meters), longitude=BASE_LNG, created_at=T0 + timedelta(days=days), title=title)


# pair_similarity：距離 0.5 + 日時 0.2 + 件名 0.3。範囲外の項は 0、全角半角・空白は無視。group_similarity は 2 件未満で None
@pytest.mark.parametrize('b, expected', [
    (_pseudo(), 1.0),
    (_pseudo(meters=150), 0.5),
    (_pseudo(days=45), 0.8),
    (_pseudo(title='ab'), 0.7),
    (_pseudo(meters=50), 0.75),
    (_pseudo(title='道 路 の 穴'), 1.0),
])
def test_pair_similarity_components(b, expected):
    a = _pseudo()

    score = duplicate_service.pair_similarity(a, b, radius_m=100, window_days=30)

    assert score == pytest.approx(expected, abs=0.01)
    assert duplicate_service.group_similarity([a], radius_m=100, window_days=30) is None
    assert duplicate_service.group_similarity([], radius_m=100, window_days=30) is None
    assert duplicate_service.group_similarity([a, b], radius_m=100, window_days=30) == pytest.approx(score)


# ---- 重複なし（サービス） ----

# 重複なし：グループは dismissed・処理者と日時を記録し、報告は変わらず、一覧から外れ、以後同じ報告は再グループされない
def test_dismiss_group_marks_dismissed_and_blocks_regrouping(staff, make_report, road):
    group, first, second = _open_pair(make_report, road)

    result = duplicate_service.dismiss_group(group.id, actor_id=staff.id)

    assert result.id == group.id
    assert group.status == DuplicateGroupStatus.DISMISSED.value
    assert group.resolved_by_id == staff.id and group.resolved_at is not None
    assert first.duplicate_of_id is None and first.status == 'received' and second.duplicate_group_id == group.id
    assert duplicate_service.list_candidate_groups() == []
    # 重複なしにしたグループの報告の近くに新しい報告が来ても、その報告とはグループにならない
    new = make_report(category_id=road.id, latitude=_north(10), created_at=T0)
    assert duplicate_service.detect_for_report(new.id) is None


# 処理済み（統合済み・重複なし）は MSG_GROUP_NOT_OPEN、存在しないグループは None
def test_dismiss_group_rejects_processed_and_missing(staff, make_report, road):
    group, *_ = _open_pair(make_report, road)
    duplicate_service.dismiss_group(group.id, actor_id=staff.id)

    with pytest.raises(ServiceError, match=MSG_GROUP_NOT_OPEN):
        duplicate_service.dismiss_group(group.id, actor_id=staff.id)
    merged = _group(DuplicateGroupStatus.MERGED.value)
    with pytest.raises(ServiceError, match=MSG_GROUP_NOT_OPEN):
        duplicate_service.dismiss_group(merged.id, actor_id=staff.id)
    assert duplicate_service.dismiss_group(99999, actor_id=staff.id) is None


# ---- 重複なし（画面・ルート） ----

# 成功：重複グループ一覧へリダイレクト、成功メッセージ、グループは重複なし
def test_route_dismiss_success(client, login, staff, make_report, road):
    group, *_ = _open_pair(make_report, road)
    login(client, User.ROLE_STAFF, staff.login_id)

    response = client.post(_dismiss_url(group))

    assert response.status_code == 302 and response.headers['Location'] == '/staff/duplicates'
    assert ('success', MSG_GROUP_DISMISSED.format(group_id=group.id)) in _flashes(client)
    db.session.refresh(group)
    assert group.status == DuplicateGroupStatus.DISMISSED.value and group.resolved_by_id == staff.id


# 処理済みのグループ：danger の MSG_GROUP_NOT_OPEN で一覧へ戻る。存在しないグループは 404
def test_route_dismiss_not_open_and_not_found(client, login, staff, make_report, road):
    login(client, User.ROLE_STAFF, staff.login_id)
    merged = _group(DuplicateGroupStatus.MERGED.value)

    response = client.post(_dismiss_url(merged))

    assert response.status_code == 302 and response.headers['Location'] == '/staff/duplicates'
    assert ('danger', MSG_GROUP_NOT_OPEN) in _flashes(client)
    assert client.post('/staff/duplicates/99999/dismiss').status_code == 404


# 権限：未ログインはログイン画面へ、市民・管理者は 403。いずれもグループは変わらない。GET は 405
@pytest.mark.parametrize('who', [None, User.ROLE_CITIZEN, User.ROLE_ADMIN])
def test_route_dismiss_requires_staff_role(client, login, citizen, admin, make_report, road, who):
    group, *_ = _open_pair(make_report, road)
    if who:
        login(client, who, citizen.login_id if who == User.ROLE_CITIZEN else admin.login_id)

    response = client.post(_dismiss_url(group))

    assert response.status_code == (302 if who is None else 403)
    if who is None:
        assert '/login/staff' in response.headers['Location']
    db.session.refresh(group)
    assert group.status == DuplicateGroupStatus.OPEN.value


def test_route_dismiss_get_not_allowed(client, login, staff, make_report, road):
    group, *_ = _open_pair(make_report, road)
    login(client, User.ROLE_STAFF, staff.login_id)

    assert client.get(_dismiss_url(group)).status_code == 405


# CSRF トークンなし：警告つきで 302、グループは変わらない
def test_route_dismiss_without_csrf_token(csrf_client, csrf_login, staff, make_report, road):
    group, *_ = _open_pair(make_report, road)
    csrf_login(csrf_client, User.ROLE_STAFF, staff.login_id)

    response = csrf_client.post(_dismiss_url(group))

    assert response.status_code == 302
    assert ('warning', MSG_CSRF_EXPIRED) in _flashes(csrf_client)
    db.session.refresh(group)
    assert group.status == DuplicateGroupStatus.OPEN.value


# ---- 一覧・画面表示 ----

# 未完了の報告が 2 件未満（残りが対応済み・取り消し）の確認待ちグループは一覧に出さない
def test_list_candidate_groups_hides_group_with_fewer_than_two_open_reports(make_report, road):
    shown, *_ = _open_pair(make_report, road)
    hidden = _group()
    make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0, duplicate_group=hidden)
    make_report(category_id=road.id, latitude=_north(20), created_at=T0, duplicate_group=hidden, status='resolved')
    cancelled = _group()
    make_report(category_id=road.id, latitude=BASE_LAT, created_at=T0, duplicate_group=cancelled)
    make_report(category_id=road.id, latitude=_north(20), created_at=T0, duplicate_group=cancelled, status='cancelled')

    assert [row['group_id'] for row in duplicate_service.list_candidate_groups()] == [shown.id]


# 画面用の dict：検出日時・住所・先頭からの距離（先頭は None）
def test_group_dict_has_address_distance_and_detected_at(make_report, road):
    group, first, second = _open_pair(make_report, road, meters=50)

    [row] = duplicate_service.list_candidate_groups()

    assert isinstance(row['detected_at'], str) and row['detected_at']
    [first_row, second_row] = row['reports']
    assert first_row['address'] == first.address and first_row['distance_m'] is None
    assert second_row['address'] == second.address and second_row['distance_m'] == 50


# /staff/duplicates：「自動検出」の表示、住所・距離の列、グループごとの重複なしモーダル（POST フォーム + csrf_token）
def test_duplicates_page_shows_auto_detected_and_dismiss_modal(csrf_client, csrf_login, staff, make_report, road):
    group, *_ = _open_pair(make_report, road, meters=50)
    csrf_login(csrf_client, User.ROLE_STAFF, staff.login_id)

    html = csrf_client.get('/staff/duplicates').get_data(as_text=True)

    assert '自動検出' in html and '検出日時' in html and '50m' in html and '住所' in html
    assert f'id="dismissModal{group.id}"' in html
    form = re.search(rf'<form method="post" action="{re.escape(_dismiss_url(group))}">(.*?)</form>', html, re.S)
    assert form, '重複なしのフォームがありません'
    assert re.search(r'name="csrf_token" value="[^"]+"', form.group(1))
