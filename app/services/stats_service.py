# ダッシュボード・統計・地図用の集計、統計レポートの出力（PDF / CSV）
import csv
import io
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import extract, func, select

from app.constants import (
    OPEN_STATUSES, URGENCY_DESCRIPTIONS, ReportStatus, Urgency,
)
from app.extensions import db
from app.models import Category, Report, ReportStatusHistory
from app.services.report_service import JST

# ダッシュボードの「本日の優先対応案件」に表示する最大件数
PRIORITY_LIMIT = 5

# 統計画面の「月別報告件数トレンド」に表示する月数（今月を含む）
TREND_MONTHS = 6

# 地図画像（static/images/map.avif）に対応させる埼玉県の範囲（緯度・経度）
# Google Maps のキー未設定時に表示する地図画像用。画像上の位置 top/left（%）への概算
MAP_BOUNDS = {'north': 36.29, 'south': 35.74, 'west': 138.70, 'east': 139.91}

# PDF の日本語フォント（IPAexゴシック。使った文字だけを PDF に埋め込むため、閲覧環境にフォントがなくても表示できる）
# ライセンスは app/fonts/IPA_Font_License_Agreement_v1.0.txt
PDF_FONT = 'IPAexGothic'
pdfmetrics.registerFont(TTFont(PDF_FONT, str(Path(__file__).resolve().parent.parent / 'fonts' / 'ipaexg.ttf')))

# 出力形式ごとの MIME タイプ
EXPORT_MIMETYPES = {'csv': 'text/csv', 'pdf': 'application/pdf'}

# 集計対象外のステータス（市民が取り消した報告）
_EXCLUDED_STATUSES = (ReportStatus.CANCELLED.value,)
_OPEN_STATUS_VALUES = [s.value for s in OPEN_STATUSES]


def dashboard_summary():
    """ダッシュボードのステータス別件数（未対応・対応中・対応済み）。ダッシュボード 基本フロー1"""
    counts = _count_by(Report.status)
    return {s.value: counts.get(s.value, 0)
            for s in (ReportStatus.RECEIVED, ReportStatus.IN_PROGRESS, ReportStatus.RESOLVED)}


def count_by_urgency():
    """緊急度別の件数と割合（高・中・低。未判定は含めない）。ダッシュボード 基本フロー1"""
    counts = _count_by(Report.urgency)
    total = sum(counts.get(u.value, 0) for u in Urgency)
    return [{
        'code': u.value,
        'label': u.label,
        'description': URGENCY_DESCRIPTIONS[u],
        'count': counts.get(u.value, 0),
        'percentage': _percent(counts.get(u.value, 0), total),
    } for u in Urgency]


def priority_reports():
    """本日の優先対応案件：緊急度「高」の未完了の報告（古い順）。ダッシュボード 基本フロー1"""
    stmt = (select(Report)
            .where(Report.urgency == Urgency.HIGH.value, Report.status.in_(_OPEN_STATUS_VALUES))
            .order_by(Report.created_at, Report.id)
            .limit(PRIORITY_LIMIT))
    return [{
        'id': r.id,
        'title': r.title,
        'location': r.address,
        'urgency': Urgency.label_of(r.urgency),
    } for r in db.session.execute(stmt).scalars()]


def map_filter_options():
    """地図の絞り込み欄の選択肢（カテゴリは categories.id、危険度は Urgency のコード）"""
    categories = db.session.execute(select(Category.id, Category.name).order_by(Category.id)).all()
    return {
        'categories': [(str(c.id), c.name) for c in categories],
        'urgencies': [(u.value, u.label) for u in Urgency],
    }


def map_points(category='all', urgency='all'):
    """地図に表示する未完了の報告（緊急度・緯度経度と地図画像上の配置位置 top/left）。地図表示 基本フロー1・6・7

    'all' 以外を指定するとカテゴリ（categories.id）・緊急度（コード）で絞り込む。不正な値は無視する。
    Google Maps は latitude/longitude、キー未設定時の地図画像は top/left を使う。
    """
    stmt = (select(Report)
            .where(Report.status.in_(_OPEN_STATUS_VALUES))
            .order_by(Report.created_at.desc(), Report.id.desc()))
    if category != 'all' and category.isdigit():
        stmt = stmt.where(Report.category_id == int(category))
    if urgency in Urgency.values():
        stmt = stmt.where(Report.urgency == urgency)

    points = []
    for r in db.session.execute(stmt).scalars():
        top, left = _to_map_position(r.latitude, r.longitude)
        points.append({
            'id': r.id,
            'title': r.title,
            'location': r.address,
            'urgency': Urgency.label_of(r.urgency),
            # ピンの色分け用（high / medium / low、未判定は None）
            'urgency_code': r.urgency,
            'latitude': r.latitude,
            'longitude': r.longitude,
            'top': top,
            'left': left,
        })
    return points


def summary():
    """統計画面のサマリー（総件数・解決済み件数・対応完了率・平均対応日数）。統計・レポート分析 基本フロー1

    平均対応日数：報告日時から最初に「対応済み」になった日時までの平均。
    """
    counts = _count_by(Report.status)
    total = sum(counts.values())
    resolved = counts.get(ReportStatus.RESOLVED.value, 0)

    resolved_at = (select(ReportStatusHistory.report_id,
                          func.min(ReportStatusHistory.created_at).label('resolved_at'))
                   .where(ReportStatusHistory.to_status == ReportStatus.RESOLVED.value)
                   .group_by(ReportStatusHistory.report_id)
                   .subquery())
    avg_seconds = db.session.scalar(
        select(func.avg(extract('epoch', resolved_at.c.resolved_at - Report.created_at)))
        .join(resolved_at, resolved_at.c.report_id == Report.id)
        .where(Report.status == ReportStatus.RESOLVED.value))
    return {
        'total_reports': total,
        'resolved_reports': resolved,
        'resolution_rate': _percent(resolved, total) if total else '—',
        'avg_response_days': '—' if avg_seconds is None else f'{float(avg_seconds) / 86400:.1f}日',
    }


def count_by_category():
    """カテゴリ別の件数と割合（件数の多い順。未分類の報告があれば最後に追加）。統計・レポート分析 基本フロー1"""
    counts = _count_by(Report.category_id)
    total = sum(counts.values())
    categories = db.session.execute(select(Category.id, Category.name).order_by(Category.id)).all()
    rows = [{'category': c.name, 'count': counts.get(c.id, 0)} for c in categories]
    rows.sort(key=lambda row: row['count'], reverse=True)
    if counts.get(None):
        rows.append({'category': '未分類', 'count': counts[None]})
    for row in rows:
        row['percentage'] = _percent(row['count'], total)
    return rows


def monthly_trend(months=TREND_MONTHS):
    """月別の報告件数（日本時間の月で集計。件数 0 の月も含めて古い順）。統計・レポート分析 基本フロー1"""
    now = datetime.now(JST)
    month_keys = []
    year, month = now.year, now.month
    for _ in range(months):
        month_keys.insert(0, (year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)

    month_start = func.date_trunc('month', func.timezone('Asia/Tokyo', Report.created_at))
    rows = db.session.execute(
        select(month_start.label('month'), func.count())
        .where(Report.status.notin_(_EXCLUDED_STATUSES),
               Report.created_at >= datetime(*month_keys[0], 1, tzinfo=JST))
        .group_by('month')).all()
    counts = {(m.year, m.month): n for m, n in rows}
    return {
        'labels': [f'{y}/{m:02d}' for y, m in month_keys],
        'counts': [counts.get(key, 0) for key in month_keys],
    }


def export_report(fmt):
    """統計レポートを PDF または CSV で作る。統計・レポート分析 基本フロー4〜7・代替フロー7a-1

    画面と同じ集計（サマリー・カテゴリ別・月別）を出力する。
    戻り値は (ファイルの中身, MIME タイプ, ファイル名)。対応していない形式は None。
    """
    if fmt not in EXPORT_MIMETYPES:
        return None
    now = datetime.now(JST)
    sections = _report_sections()
    data = _build_csv(now, sections) if fmt == 'csv' else _build_pdf(now, sections)
    return data, EXPORT_MIMETYPES[fmt], f'statistics_report_{now:%Y%m%d_%H%M}.{fmt}'


# レポートの各表（見出し, 列名, 行）。画面の統計・レポート分析と同じ内容
def _report_sections():
    stats = summary()
    trend = monthly_trend()
    return [
        ('サマリー', ['項目', '値'], [
            ['総報告件数', f"{stats['total_reports']}件"],
            ['解決済み件数', f"{stats['resolved_reports']}件"],
            ['対応完了率', stats['resolution_rate']],
            ['平均対応日数', stats['avg_response_days']],
        ]),
        ('カテゴリ別トラブル発生割合', ['カテゴリ', '件数', '割合'],
         [[row['category'], row['count'], row['percentage']] for row in count_by_category()]),
        ('月別報告件数トレンド', ['月', '件数'],
         [[label, count] for label, count in zip(trend['labels'], trend['counts'])]),
    ]


# CSV（Excel で文字化けしないよう BOM 付き UTF-8）。表と表の間は空行
def _build_csv(now, sections):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['統計・レポート分析'])
    writer.writerow(['出力日時', f'{now:%Y/%m/%d %H:%M}'])
    for title, header, rows in sections:
        writer.writerow([])
        writer.writerow([title])
        writer.writerow(header)
        writer.writerows(rows)
    return buffer.getvalue().encode('utf-8-sig')


# PDF（A4 縦。見出しと罫線付きの表）
def _build_pdf(now, sections):
    title_style = ParagraphStyle('title', fontName=PDF_FONT, fontSize=16, leading=22, spaceAfter=4)
    heading_style = ParagraphStyle('heading', fontName=PDF_FONT, fontSize=12, leading=16, spaceAfter=4)
    note_style = ParagraphStyle('note', fontName=PDF_FONT, fontSize=9, leading=12, textColor=colors.grey)
    table_style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), PDF_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#adb5bd')),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])

    story = [Paragraph('統計・レポート分析', title_style),
             Paragraph(f'出力日時：{now:%Y/%m/%d %H:%M}', note_style),
             Spacer(1, 6 * mm)]
    for title, header, rows in sections:
        story.append(Paragraph(title, heading_style))
        story.append(Table([header] + rows, colWidths=[70 * mm] + [40 * mm] * (len(header) - 1),
                           style=table_style, hAlign='LEFT'))
        story.append(Spacer(1, 6 * mm))

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, title='統計・レポート分析',
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm).build(story)
    return buffer.getvalue()


# 取り消し済みを除いた報告を column ごとに数える（{値: 件数}）
def _count_by(column):
    rows = db.session.execute(
        select(column, func.count())
        .where(Report.status.notin_(_EXCLUDED_STATUSES))
        .group_by(column)).all()
    return {value: n for value, n in rows}


def _percent(count, total):
    return f'{count / total * 100:.1f}%' if total else '0.0%'


# 緯度・経度を地図画像上の位置（%）に変換する。範囲外は端に寄せる
def _to_map_position(latitude, longitude):
    b = MAP_BOUNDS
    top = (b['north'] - latitude) / (b['north'] - b['south']) * 100
    left = (longitude - b['west']) / (b['east'] - b['west']) * 100
    return f'{min(max(top, 0), 100):.1f}%', f'{min(max(left, 0), 100):.1f}%'
