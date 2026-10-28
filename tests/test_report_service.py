# 報告の作成・一覧・検索のテスト
from decimal import Decimal

import pytest

from app import ai
from app.ai.base import AddressEstimate, AIError, PhotoCheckResult
from app.constants import (
    MSG_ADDRESS_NOT_ESTIMATED, MSG_DESCRIPTION_REQUIRED, MSG_GPS_NOT_FOUND, MSG_LOCATION_REQUIRED,
    MSG_PHOTO_INAPPROPRIATE, MSG_PHOTO_INVALID, MSG_PHOTO_OUT_OF_FOCUS, MSG_PHOTO_REQUIRED,
    MSG_PHOTO_TOO_MANY, MSG_REPORT_ALL_REQUIRED, MSG_TITLE_REQUIRED, AiAnalysisStatus,
)
from app.extensions import db
from app.models import AiAnalysis, ReportStatusHistory
from app.services import ServiceError, ValidationError, report_service, status_service, storage_service
from app.services.report_service import InappropriatePhotoError
from tests.conftest import jpeg_bytes, upload

SAITAMA = (35.906, 139.624)


def _analysis(report, category, confidence, status=AiAnalysisStatus.SUCCEEDED.value):
    analysis = AiAnalysis(report=report, status=status, model_name='rule_based',
                          category_id=category.id if status == 'succeeded' else None,
                          urgency='high' if status == 'succeeded' else None,
                          department_id=category.default_department_id if status == 'succeeded' else None,
                          confidence=Decimal(confidence) if confidence else None)
    db.session.add(analysis)
    db.session.commit()
    return analysis


# 報告一覧画面：新しい順、ラベルは constants.py の日本語
def test_list_for_staff(make_report, road):
    old = make_report(title='古い報告', category_id=road.id)
    new = make_report(title='新しい報告', urgency=None)

    rows = report_service.list_for_staff()

    assert [r['id'] for r in rows] == [new.id, old.id]
    assert rows[1]['category'] == '道路' and rows[1]['urgency'] == '高' and rows[1]['status'] == '未対応'
    assert rows[0]['category'] == '未分類' and rows[0]['urgency'] == ''


# タイトルの部分一致（大文字小文字を区別しない・LIKE の特殊文字はそのまま検索）と ID 指定
def test_list_for_staff_search(make_report):
    hole = make_report(title='道路の穴 ABC')
    make_report(title='街灯の球切れ 100%')

    assert [r['id'] for r in report_service.list_for_staff(search='abc')] == [hole.id]
    assert [r['title'] for r in report_service.list_for_staff(search='%')] == ['街灯の球切れ 100%']
    assert [r['id'] for r in report_service.list_for_staff(report_id=hole.id)] == [hole.id]
    assert report_service.list_for_staff(search='存在しない') == []


# 詳細画面：選択できるステータスは現在値＋次の遷移先、対応履歴に担当者メモが出る
def test_get_for_staff(staff, make_report, road):
    report = make_report(category_id=road.id)
    _analysis(report, road, '0.92')
    status_service.update_report_status(report.id, 'in_progress', actor_id=staff.id, comment='現地確認')

    detail = report_service.get_for_staff(report.id)

    assert detail['status_options'] == [('in_progress', '対応中'), ('resolved', '対応済み')]
    assert detail['ai_department'] == '道路管理課'
    assert detail['ai_confidence'] == '92%'
    assert detail['department'] is None
    [history] = detail['history']
    assert history['user'] == '鈴木 健一'
    assert history['action'] == 'ステータスを「未対応」から「対応中」に変更'
    assert history['comment'] == '現地確認'


def test_get_for_staff_not_found(app):
    assert report_service.get_for_staff(999) is None


# 最新の判定が失敗なら信頼度は未判定（None）
def test_get_for_staff_ai_failed(make_report, road):
    report = make_report()
    _analysis(report, road, '0.92')
    _analysis(report, road, None, status=AiAnalysisStatus.FAILED.value)

    assert report_service.get_for_staff(report.id)['ai_confidence'] is None


# AI分析画面：判定ログの一覧とタイトル検索
def test_list_ai_analyses(make_report, road):
    report = make_report(title='公園の遊具破損')
    _analysis(report, road, '0.95')

    [log] = report_service.list_ai_analyses(search='遊具')
    assert log['id'] == report.id and log['confidence'] == '95%' and log['status'] == '判定完了・自動分類済み'
    assert report_service.list_ai_analyses(search='街灯') == []


# サマリー：要人間チェックは未完了の報告の最新判定がしきい値（0.7）未満のもの
def test_ai_summary(make_report, road):
    low = make_report()
    _analysis(low, road, '0.95')
    _analysis(low, road, '0.50')          # 最新が低信頼度 → 対象
    recovered = make_report()
    _analysis(recovered, road, '0.40')
    _analysis(recovered, road, '0.90')    # 最新は高信頼度 → 対象外
    resolved = make_report(status='resolved')
    _analysis(resolved, road, '0.30')     # 対応済み → 対象外

    summary = report_service.ai_summary()

    assert summary['today_count'] == 5
    assert summary['low_confidence_count'] == 1
    assert summary['avg_confidence'] == '61.0'


# ---- 市民：新規報告 ----

def _create(citizen, **values):
    values = {
        'citizen_id': citizen.id,
        'title': '道路の大きな穴',
        'description': '車道に大きな穴が開いていて危険です。',
        'address': 'さいたま市大宮区桜木町1丁目 付近',
        'latitude': str(SAITAMA[0]),
        'longitude': str(SAITAMA[1]),
        'photos': [upload(jpeg_bytes())],
        **values,
    }
    return report_service.create_report(**values)


def _messages(excinfo):
    return excinfo.value.messages


# 新規報告 代替フロー2a-1：写真・位置情報・詳細内容が全て未入力（赤）
def test_create_report_all_required(citizen):
    with pytest.raises(ValidationError) as e:
        _create(citizen, photos=[], address='', latitude='', longitude='', description='')
    assert _messages(e) == [('danger', MSG_REPORT_ALL_REQUIRED)]


# 代替フロー2b-1〜2d-1：項目ごとのメッセージ（オレンジ）。件名は設計書にない必須チェック
@pytest.mark.parametrize('values, expected', [
    ({'photos': []}, MSG_PHOTO_REQUIRED),
    ({'address': ''}, MSG_LOCATION_REQUIRED),
    ({'latitude': '', 'longitude': ''}, MSG_LOCATION_REQUIRED),
    ({'description': '  '}, MSG_DESCRIPTION_REQUIRED),
    ({'title': ''}, MSG_TITLE_REQUIRED),
    ({'photos': [upload(jpeg_bytes()) for _ in range(4)]}, MSG_PHOTO_TOO_MANY.format(max=3)),
    ({'photos': [upload(b'not an image', 'a.jpg')]}, MSG_PHOTO_INVALID),
])
def test_create_report_validation(citizen, values, expected):
    with pytest.raises(ValidationError) as e:
        _create(citizen, **values)
    assert ('warning', expected) in _messages(e)


# 基本フロー2〜3：報告・写真・受付の対応履歴を保存し、AI（テストではキーワード判定）で緊急度を判定する
def test_create_report(app, citizen, road):
    report = _create(citizen)

    assert report.status == 'received'
    [photo] = report.photos
    assert photo.file_path.startswith('reports/') and storage_service.exists(photo.file_path)
    [history] = db.session.query(ReportStatusHistory).filter_by(report_id=report.id).all()
    assert history.from_status is None and history.changed_by_id == citizen.id
    [analysis] = report.ai_analyses
    assert analysis.status == 'succeeded' and analysis.model_name == 'rule_based'
    # AI の結果が確定値の初期値になる（担当部署はカテゴリの既定部署）
    assert report.category_id == road.id
    assert report.urgency == 'high'
    assert report.department_id == road.default_department_id


# 緯度・経度がなくても、添付写真の GPS 情報があれば位置情報として使う
def test_create_report_uses_photo_gps(citizen, road):
    report = _create(citizen, latitude='', longitude='', photos=[upload(jpeg_bytes(gps=SAITAMA))])
    assert (round(report.latitude, 3), round(report.longitude, 3)) == SAITAMA


# 撮影した写真（下書き）は報告の写真に移す
def test_create_report_with_draft_photo(citizen, road):
    draft = report_service.capture_photo(upload(jpeg_bytes(gps=SAITAMA)))

    report = _create(citizen, photos=[], latitude='', longitude='', draft=draft)

    [photo] = report.photos
    assert photo.file_path.startswith('reports/')
    assert not storage_service.exists(draft['photo'])
    assert report.latitude == pytest.approx(SAITAMA[0])


class _FailingClassifier:
    model_name = 'gemini-test'

    def classify(self, text, photos=(), *, choices):
        raise AIError('Gemini API エラー (429)', kind='quota')


# Gemini が失敗しても報告は登録され、失敗の行を残してキーワード判定で判定し直す
def test_create_report_ai_failure_falls_back(monkeypatch, citizen, road):
    monkeypatch.setattr(ai, 'get_classifier', lambda: _FailingClassifier())

    report = _create(citizen)

    analyses = sorted(report.ai_analyses, key=lambda a: a.id)
    assert [(a.status, a.model_name) for a in analyses] == [('failed', 'gemini-test'), ('succeeded', 'rule_based')]
    assert '[quota]' in analyses[0].error_message
    assert report.urgency == 'high'


# AI 判定で想定外のエラーが起きても報告の登録は成功する
def test_create_report_unexpected_ai_error(monkeypatch, citizen, road):
    def broken():
        raise RuntimeError('boom')
    monkeypatch.setattr(ai, 'get_classifier', broken)

    report = _create(citizen)

    assert report.id is not None and report.ai_analyses == []


# ---- 市民：写真撮影・位置情報の確認 ----

class _FakeChecker:
    def __init__(self, in_focus=True, appropriate=True):
        self.result = PhotoCheckResult(in_focus=in_focus, appropriate=appropriate, model_name='fake')

    def check(self, photo):
        return self.result


# 写真撮影 基本フロー5：写真を一時保存し、EXIF の GPS 情報を位置情報にする
def test_capture_photo(app):
    draft = report_service.capture_photo(upload(jpeg_bytes(gps=SAITAMA)), latitude='1', longitude='2')

    assert draft['photo'].startswith('drafts/') and storage_service.exists(draft['photo'])
    assert (round(draft['latitude'], 3), round(draft['longitude'], 3)) == SAITAMA


# 写真に GPS 情報がなければ撮影時の端末の位置情報を使う。撮り直したら前の写真は消す
def test_capture_photo_browser_location_and_retake(app):
    first = report_service.capture_photo(upload(jpeg_bytes()), latitude='35.9', longitude='139.6')
    second = report_service.capture_photo(upload(jpeg_bytes()), previous=first)

    assert (first['latitude'], first['longitude']) == (35.9, 139.6)
    assert second['latitude'] is None
    assert not storage_service.exists(first['photo'])


# 代替フロー4b-1（ピント）・4c-1（不適切な写真）は保存しない
@pytest.mark.parametrize('checker, error, message', [
    (_FakeChecker(in_focus=False), ServiceError, MSG_PHOTO_OUT_OF_FOCUS),
    (_FakeChecker(appropriate=False), InappropriatePhotoError, MSG_PHOTO_INAPPROPRIATE),
])
def test_capture_photo_rejected(monkeypatch, app, checker, error, message):
    monkeypatch.setattr(ai, 'get_photo_checker', lambda: checker)

    with pytest.raises(error, match=message):
        report_service.capture_photo(upload(jpeg_bytes()))


def test_capture_photo_requires_file(app):
    with pytest.raises(ServiceError, match=MSG_PHOTO_REQUIRED):
        report_service.capture_photo(None)


class _FakeEstimator:
    def __init__(self, address):
        self.address = address
        self.calls = 0

    def estimate(self, latitude, longitude):
        self.calls += 1
        return AddressEstimate(address=self.address, model_name='fake')


# 位置情報の確認 基本フロー3：推定した住所を下書きに残し、再表示では AI を呼ばない
def test_estimate_location(monkeypatch, app):
    estimator = _FakeEstimator('埼玉県さいたま市大宮区桜木町1丁目 付近')
    monkeypatch.setattr(ai, 'get_address_estimator', lambda: estimator)
    draft = {'photo': 'drafts/x.jpg', 'latitude': SAITAMA[0], 'longitude': SAITAMA[1]}

    draft, message = report_service.estimate_location(draft)
    draft, _ = report_service.estimate_location(draft)

    assert message is None and draft['address'] == '埼玉県さいたま市大宮区桜木町1丁目 付近'
    assert estimator.calls == 1


# 代替フロー3a-1：GPS 情報がない／住所を推定できない（AI を使わない設定を含む）
def test_estimate_location_failures(app):
    assert report_service.estimate_location({'latitude': None})[1] == MSG_GPS_NOT_FOUND
    draft, message = report_service.estimate_location({'latitude': SAITAMA[0], 'longitude': SAITAMA[1]})
    assert message == MSG_ADDRESS_NOT_ESTIMATED and draft['address'] is None


# ---- 市民：マイページ・報告一覧・完了画面 ----

def test_citizen_lists(citizen, staff, make_report):
    make_report(title='古い')
    make_report(title='対応中', status='in_progress')
    newest = make_report(title='新しい', status='resolved')
    other = make_report(citizen_id=staff.id, title='他人の報告')

    assert [r['title'] for r in report_service.list_by_citizen(citizen.id)] == ['新しい', '対応中', '古い']
    assert len(report_service.list_by_citizen(citizen.id, limit=2)) == 2
    assert report_service.citizen_summary(citizen.id) == {'total': 3, 'in_progress': 1, 'resolved': 1}
    assert report_service.get_complete_info(newest.id, citizen.id)['urgency'] == '高'
    # 他人の報告の完了画面は表示しない
    assert report_service.get_complete_info(other.id, citizen.id) is None
