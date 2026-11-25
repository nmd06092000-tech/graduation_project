# 重複報告の検出：同カテゴリ・近距離・未完了・最近の報告を重複グループにまとめ、職員が統合・重複なしを選ぶ
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from itertools import combinations

from flask import current_app
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.constants import (
    MSG_GROUP_NOT_OPEN, MSG_PRIMARY_NOT_IN_GROUP, OPEN_STATUSES, AiAnalysisStatus, DuplicateGroupStatus,
)
from app.extensions import db
from app.models import DuplicateGroup, Report
from app.services import ServiceError
from app.services.report_service import format_datetime, format_percent
from app.utils import geo

# 検出処理を1つずつ直列にする pg_advisory_xact_lock のキー（同時に届いた2件が同じ内容のグループを2つ作らないため）
DETECT_LOCK_KEY = 20261124

# 類似度の重み（距離・報告日時の近さ・件名の似かた。合計 1）
WEIGHT_DISTANCE = 0.5
WEIGHT_TIME = 0.2
WEIGHT_TITLE = 0.3

_OPEN_STATUS_VALUES = [s.value for s in OPEN_STATUSES]


def list_candidate_groups():
    """確認待ち（open）の重複グループ一覧（新しい順）。対応管理・重複確認 基本フロー1・3

    未完了の報告が2件未満になったグループ（残りが対応済み・取り消しなど）は、確認する必要がないので出さない。
    """
    stmt = (select(DuplicateGroup)
            .where(DuplicateGroup.status == DuplicateGroupStatus.OPEN.value)
            .options(*_group_load_options())
            .order_by(DuplicateGroup.created_at.desc(), DuplicateGroup.id.desc()))
    return [_group_dict(g) for g in db.session.execute(stmt).scalars()
            if sum(r.status in _OPEN_STATUS_VALUES for r in g.reports) >= 2]


def get_group(group_id):
    """指定した重複グループを1件取得する（統合・一括ステータス変更画面用）。存在しない場合は None"""
    group = db.session.get(DuplicateGroup, group_id, options=_group_load_options())
    return _group_dict(group) if group else None


def merge_group(group_id, primary_report_id, *, actor_id):
    """重複グループを代表案件に統合する。代表案件に統合する 基本フロー4

    代表以外の報告の duplicate_of_id に代表案件を設定し、グループを統合済みにする。
    ステータスは変えないため対応履歴は残さない。存在しないグループは None。
    """
    # 検出（detect_for_report）が同時に報告を追加するのを防ぐため、行をロックして読む
    group = db.session.get(DuplicateGroup, group_id, with_for_update=True, populate_existing=True)
    if group is None:
        return None
    if group.status != DuplicateGroupStatus.OPEN.value:
        db.session.rollback()
        raise ServiceError(MSG_GROUP_NOT_OPEN)
    primary = next((r for r in group.reports if r.id == primary_report_id), None)
    if primary is None:
        db.session.rollback()
        raise ServiceError(MSG_PRIMARY_NOT_IN_GROUP)

    for report in group.reports:
        report.duplicate_of_id = None if report.id == primary.id else primary.id
    group.status = DuplicateGroupStatus.MERGED.value
    group.resolved_by_id = actor_id
    group.resolved_at = datetime.now(timezone.utc)
    db.session.commit()
    return primary


def dismiss_group(group_id, *, actor_id):
    """重複グループを「重複なし」にして確認待ちの一覧から外す。重複なしにする（対応管理・重複確認）

    報告の duplicate_of_id・ステータスは変えず、グループだけを重複なし（dismissed）にする。
    グループ内の報告は再びグループにまとめられない。存在しないグループは None。
    """
    # 検出（detect_for_report）が同時に報告を追加するのを防ぐため、行をロックして読む
    group = db.session.get(DuplicateGroup, group_id, with_for_update=True, populate_existing=True)
    if group is None:
        return None
    if group.status != DuplicateGroupStatus.OPEN.value:
        db.session.rollback()
        raise ServiceError(MSG_GROUP_NOT_OPEN)
    group.status = DuplicateGroupStatus.DISMISSED.value
    group.resolved_by_id = actor_id
    group.resolved_at = datetime.now(timezone.utc)
    db.session.commit()
    return group


def detect_for_report(report_id):
    """報告1件の重複を検出し、重複グループにまとめる。報告作成・重複検出コマンドから呼ぶ

    同カテゴリ・DUPLICATE_RADIUS_M メートル以内・報告日時の差が DUPLICATE_WINDOW_DAYS 日以内・未完了の報告が候補。
    候補が確認待ちのグループにいればそのグループに入り（複数なら類似度の最も高い組があるグループ。グループどうしは
    統合しない）、いなければ報告と「グループのない候補」で新しいグループを作る。
    グループにまとめたら結果の dict（group_id・created・size・added・similarity）、何もしなかったら None
    （報告が存在しない・カテゴリ未設定・未完了でない・統合済み・すでにグループあり・候補なし・
    候補のグループがロック後に処理済み（重複なし・統合））。1回のコミットで終える。
    """
    # 同時に届いた報告が同じグループを2つ作らないよう、検出全体を直列にする（コミット・ロールバックで解放される）
    db.session.execute(select(func.pg_advisory_xact_lock(DETECT_LOCK_KEY)))
    # ロックを待つ間に他の検出が進んでいる可能性があるため、最新の状態で読み直す
    report = db.session.get(Report, report_id, populate_existing=True)
    if (report is None or report.category_id is None or report.status not in _OPEN_STATUS_VALUES
            or report.duplicate_of_id is not None or report.duplicate_group_id is not None):
        db.session.rollback()
        return None

    config = current_app.config
    radius_m, window_days = config['DUPLICATE_RADIUS_M'], config['DUPLICATE_WINDOW_DAYS']
    candidates = _find_candidates(report, radius_m=radius_m, window_days=window_days)
    if not candidates:
        db.session.rollback()
        return None

    group, members = _choose_group(report, candidates, radius_m=radius_m, window_days=window_days)
    if group is None:
        # 候補のグループが（重複なし・統合で）処理済みになった直後。次回の検出に任せる
        db.session.rollback()
        return None
    created = group.id is None
    if created:
        db.session.add(group)
    for member in members:
        group.reports.append(member)
    db.session.flush()
    similarity = _round_similarity(group_similarity(group.reports, radius_m=radius_m, window_days=window_days))
    group.similarity = similarity
    # コミットすると属性が破棄されるため、返す値は先に取り出す
    result = {'group_id': group.id, 'created': created, 'size': len(group.reports),
              'added': len(members), 'similarity': similarity}
    db.session.commit()
    return result


def detect_all():
    """未完了でカテゴリがありグループのない報告をすべて検出し直す（flask detect-duplicates）。結果の dict を返す

    scanned: 対象の報告数 / created: 新しく作ったグループ / joined: 既存グループに入った報告 /
    grouped: 新しくグループに入った報告の合計 / failed: 検出に失敗した報告。
    再実行しても、すでにグループにいる報告は対象外なので同じグループは増えない。1件の失敗で全体を止めない。
    """
    stmt = (select(Report.id)
            .where(Report.category_id.is_not(None), Report.status.in_(_OPEN_STATUS_VALUES),
                   Report.duplicate_of_id.is_(None), Report.duplicate_group_id.is_(None))
            .order_by(Report.created_at, Report.id))
    report_ids = list(db.session.scalars(stmt))
    db.session.rollback()

    result = {'scanned': len(report_ids), 'created': 0, 'joined': 0, 'grouped': 0, 'failed': 0}
    for report_id in report_ids:
        try:
            detected = detect_for_report(report_id)
        except Exception:
            db.session.rollback()
            result['failed'] += 1
            current_app.logger.exception('重複の検出に失敗しました report=%s', report_id)
            continue
        if detected is None:
            continue
        result['grouped'] += detected['added']
        result['created' if detected['created'] else 'joined'] += 1
    return result


def pair_similarity(report_a, report_b, *, radius_m=None, window_days=None):
    """2件の報告の類似度（0〜1）。距離 0.5・報告日時の近さ 0.2・件名の似かた 0.3 の重みづけ

    距離は radius_m、日時の差は window_days で 1〜0 に直す（範囲外の項は 0）。
    """
    radius_m = radius_m or current_app.config['DUPLICATE_RADIUS_M']
    window_days = window_days or current_app.config['DUPLICATE_WINDOW_DAYS']
    distance = geo.distance_m(report_a.latitude, report_a.longitude, report_b.latitude, report_b.longitude)
    days = abs((report_a.created_at - report_b.created_at).total_seconds()) / 86400
    score = (WEIGHT_DISTANCE * _closeness(distance, radius_m)
             + WEIGHT_TIME * _closeness(days, window_days)
             + WEIGHT_TITLE * _title_similarity(report_a.title, report_b.title))
    return min(1.0, max(0.0, score))


def group_similarity(reports, *, radius_m=None, window_days=None):
    """グループの類似度：メンバー全組み合わせの pair_similarity の平均（2件未満は None）"""
    pairs = list(combinations(reports, 2))
    if not pairs:
        return None
    total = sum(pair_similarity(a, b, radius_m=radius_m, window_days=window_days) for a, b in pairs)
    return total / len(pairs)


def _closeness(value, limit):
    """0 のとき 1、limit 以上のとき 0 になる近さ（0〜1）"""
    return min(1.0, max(0.0, 1 - value / limit))


def _title_similarity(title_a, title_b):
    """件名の似かた（0〜1）。全角半角をそろえ（NFKC）、空白を除いて SequenceMatcher で比べる"""
    a, b = (''.join(unicodedata.normalize('NFKC', t or '').split()) for t in (title_a, title_b))
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _round_similarity(value):
    """duplicate_groups.similarity（Numeric(4,3)）に入る 0〜1 の値にする"""
    return round(min(1.0, max(0.0, value or 0.0)), 3)


def _find_candidates(report, *, radius_m, window_days):
    """report と重複する候補の報告（古い順）。SQL で緯度・経度の範囲に絞り、距離は Haversine で確かめる

    条件：同カテゴリ・未完了・統合されていない・報告日時の差が window_days 日以内・radius_m メートル以内。
    統合済み・重複なしのグループにいる報告は除く（グループなし・確認待ちのグループにいる報告だけ）。
    """
    min_lat, max_lat, min_lng, max_lng = geo.bounding_box(report.latitude, report.longitude, radius_m)
    window = timedelta(days=window_days)
    stmt = (select(Report)
            .outerjoin(DuplicateGroup, Report.duplicate_group_id == DuplicateGroup.id)
            .where(Report.id != report.id,
                   Report.category_id == report.category_id,
                   Report.status.in_(_OPEN_STATUS_VALUES),
                   Report.duplicate_of_id.is_(None),
                   or_(Report.duplicate_group_id.is_(None),
                       DuplicateGroup.status == DuplicateGroupStatus.OPEN.value),
                   Report.created_at.between(report.created_at - window, report.created_at + window),
                   Report.latitude.between(min_lat, max_lat),
                   Report.longitude.between(min_lng, max_lng))
            .order_by(Report.created_at, Report.id))
    return [c for c in db.session.scalars(stmt)
            if geo.distance_m(report.latitude, report.longitude, c.latitude, c.longitude) <= radius_m]


def _choose_group(report, candidates, *, radius_m, window_days):
    """報告が入るグループと、そのグループに新しく入る報告のリスト (group, members) を決める

    確認待ちのグループにいる候補があれば、報告との類似度が最も高い候補のグループに報告だけが入る
    （グループの統合はしない）。なければ新しいグループ（未保存の DuplicateGroup）に報告と、グループのない候補が入る。
    ロック後に候補のグループが処理済みになっていた場合は (None, [])。
    """
    grouped = [c for c in candidates if c.duplicate_group_id is not None]
    if not grouped:
        group = DuplicateGroup(status=DuplicateGroupStatus.OPEN.value)
        return group, [report, *candidates]
    best = max(grouped, key=lambda c: pair_similarity(report, c, radius_m=radius_m, window_days=window_days))
    group = db.session.get(DuplicateGroup, best.duplicate_group_id, with_for_update=True, populate_existing=True)
    if group is None or group.status != DuplicateGroupStatus.OPEN.value:
        return None, []
    return group, [report]


def _group_load_options():
    return [selectinload(DuplicateGroup.reports).selectinload(Report.citizen),
            selectinload(DuplicateGroup.reports).selectinload(Report.category),
            selectinload(DuplicateGroup.reports).selectinload(Report.ai_analyses)]


# 画面用のグループ（タイトルは最も古い報告の住所＋カテゴリ名）
# detected_at: グループの検出日時
# address / distance_m: 報告の住所と、グループの最初の報告までの距離（m、整数。最初の報告は None）
# ai_detected: AI 判定が成功している報告（画面で AI分析画面へのリンクに切り替える）
def _group_dict(group):
    first = group.reports[0] if group.reports else None
    category = first.category.name if first and first.category else '未分類'
    return {
        'group_id': group.id,
        'status': group.status,
        'title': f'{first.address}の{category}' if first else '',
        'category': category,
        'similarity': format_percent(group.similarity) or '—',
        'detected_at': format_datetime(group.created_at),
        'reports': [{
            'id': r.id,
            'title': r.title,
            'address': r.address,
            'distance_m': None if r is first else round(
                geo.distance_m(first.latitude, first.longitude, r.latitude, r.longitude)),
            'reporter': r.citizen.name,
            'date': format_datetime(r.created_at),
            'ai_detected': any(a.status == AiAnalysisStatus.SUCCEEDED for a in r.ai_analyses),
        } for r in group.reports],
    }
