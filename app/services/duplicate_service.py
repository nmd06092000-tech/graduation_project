# 重複報告の検出：同カテゴリ・近距離・未完了・最近の報告を候補として提示
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.constants import (
    MSG_GROUP_NOT_OPEN, MSG_PRIMARY_NOT_IN_GROUP, AiAnalysisStatus, DuplicateGroupStatus,
)
from app.extensions import db
from app.models import DuplicateGroup, Report
from app.services import ServiceError
from app.services.report_service import format_datetime, format_percent

# TODO: 報告作成時の重複候補の検出（utils/geo.py で同カテゴリ・近距離・未完了・最近の報告を探す）は未実装


def list_candidate_groups():
    """確認待ち（open）の重複グループ一覧（新しい順）。対応管理・重複確認 基本フロー1・3"""
    stmt = (select(DuplicateGroup)
            .where(DuplicateGroup.status == DuplicateGroupStatus.OPEN.value)
            .options(*_group_load_options())
            .order_by(DuplicateGroup.created_at.desc(), DuplicateGroup.id.desc()))
    return [_group_dict(g) for g in db.session.execute(stmt).scalars()]


def get_group(group_id):
    """指定した重複グループを1件取得する（統合・一括ステータス変更画面用）。存在しない場合は None"""
    group = db.session.get(DuplicateGroup, group_id, options=_group_load_options())
    return _group_dict(group) if group else None


def merge_group(group_id, primary_report_id, *, actor_id):
    """重複グループを代表案件に統合する。代表案件に統合する 基本フロー4

    代表以外の報告の duplicate_of_id に代表案件を設定し、グループを統合済みにする。
    ステータスは変えないため対応履歴は残さない。存在しないグループは None。
    """
    group = db.session.get(DuplicateGroup, group_id)
    if group is None:
        return None
    if group.status != DuplicateGroupStatus.OPEN.value:
        raise ServiceError(MSG_GROUP_NOT_OPEN)
    primary = next((r for r in group.reports if r.id == primary_report_id), None)
    if primary is None:
        raise ServiceError(MSG_PRIMARY_NOT_IN_GROUP)

    for report in group.reports:
        report.duplicate_of_id = None if report.id == primary.id else primary.id
    group.status = DuplicateGroupStatus.MERGED.value
    group.resolved_by_id = actor_id
    group.resolved_at = datetime.now(timezone.utc)
    db.session.commit()
    return primary


def _group_load_options():
    return [selectinload(DuplicateGroup.reports).selectinload(Report.citizen),
            selectinload(DuplicateGroup.reports).selectinload(Report.category),
            selectinload(DuplicateGroup.reports).selectinload(Report.ai_analyses)]


# 画面用のグループ（タイトルは最も古い報告の住所＋カテゴリ名）
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
        'reports': [{
            'id': r.id,
            'title': r.title,
            'reporter': r.citizen.name,
            'date': format_datetime(r.created_at),
            'ai_detected': any(a.status == AiAnalysisStatus.SUCCEEDED for a in r.ai_analyses),
        } for r in group.reports],
    }
