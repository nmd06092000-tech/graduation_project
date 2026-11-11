# 報告の作成（写真保存 → AI判定 → 重複チェック）、市民別一覧、職員向け検索・絞り込み
import math
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import ai
from app.ai.base import AIError, Choice, ClassificationChoices
from app.ai.gemini_client import prepare_image
from app.constants import (
    MSG_ADDRESS_NOT_ESTIMATED, MSG_ADDRESS_NOT_FOUND, MSG_ADDRESS_TOO_LONG, MSG_DESCRIPTION_REQUIRED,
    MSG_DESCRIPTION_TOO_LONG,
    MSG_GPS_NOT_FOUND, MSG_LOCATION_REQUIRED, MSG_PHOTO_INAPPROPRIATE, MSG_PHOTO_OUT_OF_FOCUS,
    MSG_PHOTO_REQUIRED, MSG_PHOTO_TOO_MANY, MSG_REPORT_ALL_REQUIRED, MSG_TITLE_REQUIRED, MSG_TITLE_TOO_LONG,
    MSG_NOTICE_REJUDGE_FAILED, MSG_NOTICE_REJUDGED_CHANGED, MSG_NOTICE_REJUDGED_SAME,
    OPEN_STATUSES, URGENCY_DESCRIPTIONS, WORKING_STATUSES, AiAnalysisStatus, AiAnalysisTrigger, LocationSource,
    ReportStatus, Role, Urgency, next_statuses,
)
from app.extensions import db
from app.models import AiAnalysis, Category, Department, Report, ReportPhoto, ReportStatusHistory
from app.services import ServiceError, ValidationError, status_service, storage_service
from app.utils import geo

# 画面に表示する日時のタイムゾーン（DB は timestamptz で保存）
JST = timezone(timedelta(hours=9), 'JST')

# AI分析画面「直近のAI自動判定結果」に表示する最大件数
AI_LOG_LIMIT = 50

# 新規報告の入力上限（報告画面の maxlength・「写真添付（3枚まで）」と同じ）
MAX_PHOTOS = 3
TITLE_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 1000
ADDRESS_MAX_LENGTH = 255

# マイページの「最近の報告」に表示する件数
RECENT_REPORT_LIMIT = 3


class InappropriatePhotoError(ServiceError):
    """写真撮影 代替フロー4c-1：報告に使えない写真（報告画面に戻す）"""


def capture_photo(file_storage, *, latitude=None, longitude=None, previous=None):
    """撮影した写真を一時保存し、報告の下書き（session に保存する dict）を返す。写真撮影 基本フロー4〜5

    ピントが合っていない写真（4b-1）・報告に使えない写真（4c-1）は保存しない。
    位置情報は写真の EXIF の GPS 情報を優先し、なければ撮影時にブラウザで取得した位置を使う。
    """
    image = storage_service.read_image(file_storage)
    if image is None:
        raise ServiceError(MSG_PHOTO_REQUIRED)
    _check_photo(image)

    coordinates = storage_service.extract_gps(image.data)
    source = LocationSource.PHOTO.value if coordinates else None
    if coordinates is None:
        coordinates = _parse_coordinates(latitude, longitude)
        source = LocationSource.DEVICE.value if coordinates else None
    relative = storage_service.save(image, storage_service.DRAFT_DIR)
    discard_draft(previous)
    return {
        'photo': relative,
        'latitude': coordinates[0] if coordinates else None,
        'longitude': coordinates[1] if coordinates else None,
        # 緯度・経度の取得元（写真の GPS・撮影時の端末の位置）。報告の location_source に引き継ぐ
        'location_source': source,
        'address': None,
        'address_estimated': False,
    }


def discard_draft(draft):
    """下書きの一時保存写真を削除する（報告画面に戻す・撮り直すとき）"""
    if draft:
        storage_service.delete(draft.get('photo'))


def estimate_location(draft):
    """写真の GPS 情報から AI で住所を推定し、下書きに保存する。位置情報の確認 基本フロー3

    戻り値は (更新した下書き, 画面に表示するメッセージ。問題なければ None)。
    一度推定した住所は下書きに残し、画面の再表示で AI を呼び直さない。
    """
    if draft.get('latitude') is None:
        return draft, MSG_GPS_NOT_FOUND
    if not draft.get('address_estimated'):
        draft = {**draft, 'address': estimate_address(draft['latitude'], draft['longitude']),
                 'address_estimated': True}
    return draft, None if draft.get('address') else MSG_ADDRESS_NOT_ESTIMATED


def estimate_address(latitude, longitude):
    """緯度・経度から AI で住所を推定する。AI を使わない設定・AI の失敗時は None"""
    try:
        estimator = ai.get_address_estimator()
        if estimator is None:
            return None
        return estimator.estimate(latitude, longitude).address
    except AIError as e:
        current_app.logger.warning('住所の推定に失敗しました kind=%s', e.kind)
        return None


def geocode_address(address):
    """発生場所の住所から緯度・経度 (lat, lng) を検索する（GPS 情報がない報告の位置）

    検索しない設定（GEOCODER_URL が空）・見つからない・API の失敗時は None。
    """
    config = current_app.config
    return geo.search_address(address, url=config.get('GEOCODER_URL'), timeout=config.get('GEOCODER_TIMEOUT', 5))


def reverse_geocode(latitude, longitude):
    """GPS の緯度・経度から住所を求める（職員が発生場所と照合する・発生場所が未入力のときの住所）

    求めない設定（REVERSE_GEOCODER_URL が空）・埼玉県外・API の失敗時は None。
    """
    config = current_app.config
    return geo.reverse_geocode(latitude, longitude, url=config.get('REVERSE_GEOCODER_URL'),
                               timeout=config.get('GEOCODER_TIMEOUT', 5))


def create_report(*, citizen_id, title, description, address, latitude, longitude, photos=(), draft=None):
    """新規報告を登録し、AI で緊急度などを判定する。新規報告 基本フロー2〜3

    写真は撮影した写真（下書き）と添付ファイルの合計 3 枚まで。入力エラーは ValidationError。
    位置は GPS を優先し、なければ発生場所の住所から検索する。GPS の場合は位置から住所も求めて保存し（職員の照合用）、
    発生場所が未入力ならその住所を発生場所にする。
    報告を保存（コミット）してから AI を呼ぶため、AI が失敗しても報告は登録される。
    """
    title = (title or '').strip()
    description = (description or '').strip()
    address = (address or '').strip()
    draft_photo = draft.get('photo') if draft and storage_service.exists(draft.get('photo')) else None

    errors = []
    images = []
    for file_storage in photos:
        try:
            image = storage_service.read_image(file_storage)
        except ServiceError as e:
            errors.append(('warning', str(e)))
            continue
        if image:
            images.append(image)

    # 位置情報 = 発生場所（住所）と緯度・経度
    coordinates, source = _resolve_gps(latitude, longitude, draft if draft_photo else None, images)
    gps_address = None
    if coordinates is not None:
        # GPS の位置の住所。発生場所が未入力（入力し忘れ）ならこの住所を発生場所にする
        gps_address = reverse_geocode(*coordinates)
        if not address and gps_address:
            address = gps_address
    elif address and len(address) <= ADDRESS_MAX_LENGTH:
        # GPS がない（写真に GPS 情報がない・端末の位置情報を使えない）場合は、発生場所の住所から検索する
        coordinates = geocode_address(address)
        source = LocationSource.ADDRESS.value if coordinates else None
    has_photo = bool(images or draft_photo)
    has_location = bool(address) and coordinates is not None

    # 代替フロー2a-1（全て未入力は赤）、2b-1〜2d-1（項目ごとはオレンジ）
    if not has_photo and not has_location and not description:
        errors.insert(0, ('danger', MSG_REPORT_ALL_REQUIRED))
    else:
        if not has_photo:
            errors.append(('warning', MSG_PHOTO_REQUIRED))
        if not has_location:
            # 住所は入力されたが、GPS 情報がなく住所からも位置を検索できない場合は住所の確認を促す
            errors.append(('warning', MSG_ADDRESS_NOT_FOUND if address else MSG_LOCATION_REQUIRED))
        if not description:
            errors.append(('warning', MSG_DESCRIPTION_REQUIRED))
    if not title:
        errors.append(('warning', MSG_TITLE_REQUIRED))
    elif len(title) > TITLE_MAX_LENGTH:
        errors.append(('warning', MSG_TITLE_TOO_LONG))
    if len(description) > DESCRIPTION_MAX_LENGTH:
        errors.append(('warning', MSG_DESCRIPTION_TOO_LONG))
    if len(address) > ADDRESS_MAX_LENGTH:
        errors.append(('warning', MSG_ADDRESS_TOO_LONG))
    if len(images) + (1 if draft_photo else 0) > MAX_PHOTOS:
        errors.append(('warning', MSG_PHOTO_TOO_MANY.format(max=MAX_PHOTOS)))
    if errors:
        raise ValidationError(errors)

    report = Report(citizen_id=citizen_id, title=title, description=description, address=address,
                    latitude=coordinates[0], longitude=coordinates[1],
                    location_source=source, gps_address=gps_address)
    db.session.add(report)
    status_service.record_received(report, actor_id=citizen_id)

    saved = []
    try:
        if draft_photo:
            saved.append(storage_service.move(draft_photo, storage_service.REPORT_DIR))
        saved.extend(storage_service.save(image, storage_service.REPORT_DIR) for image in images)
        for relative in saved:
            report.photos.append(ReportPhoto(file_path=relative,
                                             content_type=storage_service.content_type_of(relative)))
        db.session.commit()
    except Exception:
        # 保存に失敗したら、書き込んだ写真も消す
        db.session.rollback()
        for relative in saved:
            storage_service.delete(relative)
        raise

    try:
        analyze_report(report.id)
    except Exception:
        # AI 判定の想定外のエラーでも報告の登録は成功として扱う（職員が後で分類できる）
        db.session.rollback()
        current_app.logger.exception('AI 判定で想定外のエラーが発生しました report=%s', report.id)
    return report


def analyze_report(report_id, trigger_type=AiAnalysisTrigger.INITIAL.value):
    """報告を AI で分類し、結果を ai_analyses に1行追加する。新規報告 基本フロー3

    Gemini が失敗した場合は失敗の行を残し、キーワード判定（rule_based）で判定し直す。
    新規報告時（initial）は、報告のカテゴリ・緊急度・担当部署が未設定なら AI の結果を初期値にする（職員の設定値は上書きしない）。
    再判定（rejudge）では報告の値を変更しない（変更するかは職員が判断する）。
    """
    report = db.session.get(Report, report_id)
    choices = _classification_choices()
    text = f'件名: {report.title}\n詳細内容: {report.description}'
    photos = _ai_photos(report)

    classifier = ai.get_classifier()
    try:
        result = _classify(classifier, text, photos, choices)
    except AIError as e:
        current_app.logger.warning('AI 判定に失敗しました report=%s kind=%s', report_id, e.kind)
        _add_failed_analysis(report, classifier.model_name, e, trigger_type)
        fallback = ai.get_fallback_classifier()
        if fallback.model_name == classifier.model_name:
            db.session.commit()
            return None
        try:
            result = _classify(fallback, text, photos, choices)
        except AIError as fallback_error:
            _add_failed_analysis(report, fallback.model_name, fallback_error, trigger_type)
            db.session.commit()
            return None

    analysis = _add_succeeded_analysis(report, result, trigger_type)
    db.session.commit()
    return analysis


def rejudge_stale_reports(*, now=None):
    """未対応のまま AI_REJUDGE_HOURS 時間たった報告を AI で再判定し、職員へ通知する。AI緊急度の再判定ルール

    再判定は1報告につき1回（失敗しても再試行しない）。報告の緊急度などの確定値は変更せず、
    AI の判定が現在の緊急度と違えば見直しを促す通知にする。戻り値は再判定した報告の件数。
    flask --app run rejudge-urgency（app/cli.py）から定期実行する。
    """
    # 通知サービスは report_service の日時の書式を使うため、循環 import を避けてここで読み込む
    from app.services import notification_service

    now = now or datetime.now(timezone.utc)
    deadline = now - timedelta(hours=current_app.config['AI_REJUDGE_HOURS'])
    rejudged = select(AiAnalysis.report_id).where(AiAnalysis.trigger_type == AiAnalysisTrigger.REJUDGE.value)
    report_ids = db.session.execute(
        select(Report.id)
        .where(Report.status == ReportStatus.RECEIVED.value, Report.created_at <= deadline,
               Report.id.not_in(rejudged))
        .order_by(Report.created_at, Report.id)).scalars().all()

    count = 0
    for report_id in report_ids:
        try:
            analysis = analyze_report(report_id, trigger_type=AiAnalysisTrigger.REJUDGE.value)
            report = db.session.get(Report, report_id)
            notification_service.create(report_id, _rejudge_message(report, analysis))
            db.session.commit()
            count += 1
        except Exception:
            # 1件の想定外のエラーで他の報告の再判定を止めない（再判定の行がなければ次回また対象になる）
            db.session.rollback()
            current_app.logger.exception('AI 再判定で想定外のエラーが発生しました report=%s', report_id)
    return count


# 再判定の結果を職員に知らせる通知の本文
def _rejudge_message(report, analysis):
    values = {'report_id': report.id, 'title': report.title}
    if analysis is None:
        return MSG_NOTICE_REJUDGE_FAILED.format(**values)
    values['ai_urgency'] = Urgency.label_of(analysis.urgency)
    if analysis.urgency == report.urgency:
        return MSG_NOTICE_REJUDGED_SAME.format(**values)
    return MSG_NOTICE_REJUDGED_CHANGED.format(urgency=Urgency.label_of(report.urgency, '未判定'), **values)


def list_by_citizen(user_id, limit=None):
    """市民が自分で報告した内容の一覧（新しい順）。報告一覧画面 基本フロー1・マイページ"""
    stmt = (select(Report)
            .where(Report.citizen_id == user_id)
            .order_by(Report.created_at.desc(), Report.id.desc()))
    if limit:
        stmt = stmt.limit(limit)
    return [{
        'id': r.id,
        'title': r.title,
        'location': r.address,
        'date': format_datetime(r.created_at),
        'status': ReportStatus.label_of(r.status),
        # バッジの色分け用（components/_status_badge.html）
        'status_code': r.status,
        'description': r.description,
    } for r in db.session.execute(stmt).scalars()]


def citizen_summary(user_id):
    """マイページのサマリー（報告した件数・対応中・対応完了）

    対応中は 対応中・業者対応中・完了確認中 の合計。
    """
    counts = dict(db.session.execute(
        select(Report.status, func.count()).where(Report.citizen_id == user_id).group_by(Report.status)).all())
    return {
        'total': sum(counts.values()),
        'in_progress': sum(counts.get(s.value, 0) for s in WORKING_STATUSES),
        'resolved': counts.get(ReportStatus.RESOLVED.value, 0),
    }


def get_complete_info(report_id, user_id):
    """新規報告完了画面に表示する内容（件名・AI が判定した緊急度）。本人の報告でなければ None"""
    report = db.session.get(Report, report_id)
    if report is None or report.citizen_id != user_id:
        return None
    return {'id': report.id, 'title': report.title, 'urgency': Urgency.label_of(report.urgency)}


# 撮影写真を AI で確認する（写真撮影 4b-1・4c-1）。AI を使わない設定・AI の失敗時は確認せずに進める
def _check_photo(image):
    try:
        checker = ai.get_photo_checker()
        if checker is None:
            return
        result = checker.check(prepare_image(image.data, image.content_type))
    except AIError as e:
        current_app.logger.warning('写真の確認に失敗しました kind=%s', e.kind)
        return
    if not result.appropriate:
        raise InappropriatePhotoError(MSG_PHOTO_INAPPROPRIATE)
    if not result.in_focus:
        raise ServiceError(MSG_PHOTO_OUT_OF_FOCUS)


# GPS の緯度・経度と取得元を決める。画面の座標 → 撮影時の位置 → 添付写真の GPS 情報の順。なければ (None, None)
# 画面の座標は、撮影時の位置を初期値にしたもの（取得元は撮影時と同じ）か、報告画面で取得した端末の位置
def _resolve_gps(latitude, longitude, draft, images):
    draft_coordinates = _parse_coordinates(draft.get('latitude'), draft.get('longitude')) if draft else None
    draft_source = (draft.get('location_source') if draft else None) or LocationSource.DEVICE.value
    coordinates = _parse_coordinates(latitude, longitude)
    if coordinates is not None:
        if draft_coordinates is not None and all(
                math.isclose(a, b, abs_tol=1e-7) for a, b in zip(coordinates, draft_coordinates)):
            return coordinates, draft_source
        return coordinates, LocationSource.DEVICE.value
    if draft_coordinates is not None:
        return draft_coordinates, draft_source
    coordinates = next(filter(None, (storage_service.extract_gps(i.data) for i in images)), None)
    return (coordinates, LocationSource.PHOTO.value) if coordinates else (None, None)


# 緯度・経度の文字列を数値にする。未入力・範囲外は None
def _parse_coordinates(latitude, longitude):
    try:
        lat, lng = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


# AI に渡す候補（カテゴリ・担当部署はマスタ、緊急度は constants.py）
def _classification_choices():
    categories = db.session.execute(select(Category).order_by(Category.id)).scalars()
    departments = db.session.execute(select(Department).order_by(Department.id)).scalars()
    return ClassificationChoices(
        categories=[Choice(c.code, c.name) for c in categories],
        urgencies=[Choice(u.value, f'{u.label}（{URGENCY_DESCRIPTIONS[u]}）') for u in Urgency],
        departments=[Choice(str(d.id), d.name) for d in departments],
    )


# AI に渡す写真（縮小・EXIF 除去済み）。読めない写真は送らない
def _ai_photos(report):
    photos = []
    for photo in report.photos:
        try:
            photos.append(prepare_image(storage_service.read_bytes(photo.file_path), photo.content_type))
        except (AIError, OSError):
            current_app.logger.warning('AI に渡す写真を読み込めませんでした photo=%s', photo.id)
    return photos


# 分類を実行する。カテゴリ・緊急度がそろわない結果は失敗として扱う
def _classify(classifier, text, photos, choices):
    result = classifier.classify(text, photos, choices=choices)
    if result.category_code is None or result.urgency_code is None:
        raise AIError('カテゴリまたは緊急度を判定できませんでした', kind='invalid_response')
    return result


def _add_failed_analysis(report, model_name, error, trigger_type=AiAnalysisTrigger.INITIAL.value):
    db.session.add(AiAnalysis(report=report, status=AiAnalysisStatus.FAILED.value, model_name=model_name,
                              error_message=f'[{error.kind}] {error}', trigger_type=trigger_type))


def _add_succeeded_analysis(report, result, trigger_type=AiAnalysisTrigger.INITIAL.value):
    category = db.session.execute(select(Category).filter_by(code=result.category_code)).scalar_one()
    department = (db.session.get(Department, int(result.department_code))
                  if result.department_code else None) or category.default_department
    analysis = AiAnalysis(
        report=report,
        status=AiAnalysisStatus.SUCCEEDED.value,
        category_id=category.id,
        urgency=result.urgency_code,
        department_id=department.id,
        confidence=Decimal(str(round(result.confidence, 3))),
        model_name=result.model_name[:100],
        raw_response=result.raw_response,
        trigger_type=trigger_type,
    )
    db.session.add(analysis)
    # 再判定では確定値を変更しない（職員が報告詳細で判断する）
    if trigger_type != AiAnalysisTrigger.INITIAL.value:
        return analysis
    # 未設定の確定値だけ AI の結果で埋める
    if report.category_id is None:
        report.category_id = category.id
    if report.urgency is None:
        report.urgency = result.urgency_code
    if report.department_id is None:
        report.department_id = department.id
    return analysis


def list_for_staff(search='', report_id=None):
    """職員向けの報告一覧（新しい順）。報告一覧画面 基本フロー1

    report_id 指定時はその1件、search 指定時はタイトルの部分一致（大文字小文字を区別しない）で絞り込む。
    """
    stmt = (select(Report)
            .options(selectinload(Report.category))
            .order_by(Report.created_at.desc(), Report.id.desc()))
    if report_id:
        stmt = stmt.where(Report.id == report_id)
    elif search:
        stmt = stmt.where(Report.title.ilike(f'%{_escape_like(search)}%', escape='\\'))
    return [_staff_row(r) for r in db.session.execute(stmt).scalars()]


def get_for_staff(report_id):
    """職員向けの報告詳細（報告内容・AI判定・対応履歴・変更できるステータス）。詳細・対応更新画面 基本フロー1

    存在しない場合は None。
    """
    report = db.session.get(Report, report_id, options=[
        selectinload(Report.category),
        selectinload(Report.department),
        selectinload(Report.ai_analyses).selectinload(AiAnalysis.department),
        selectinload(Report.histories).selectinload(ReportStatusHistory.changed_by),
    ])
    if report is None:
        return None

    latest_ai = report.latest_ai_analysis
    ai_succeeded = latest_ai is not None and latest_ai.status == AiAnalysisStatus.SUCCEEDED
    # 現在のステータス（コメントのみの更新用）と、職員が変更できる次のステータス
    status_options = [ReportStatus(report.status)] + next_statuses(report.status, Role.STAFF)
    return {
        **_staff_row(report),
        'address': report.address,
        # 位置情報の照合用：緯度・経度の取得元（NULL は記録前の報告）と GPS の位置から求めた住所
        'location_source': LocationSource.label_of(report.location_source, '不明'),
        'is_gps': report.location_source in LocationSource.gps_values(),
        'gps_address': report.gps_address,
        'latitude': report.latitude,
        'longitude': report.longitude,
        'description': report.description,
        'status_code': report.status,
        'status_options': [(s.value, s.label) for s in status_options],
        # 緊急度の判定：最新の AI 判定緊急度と、職員が最終判断として選べる緊急度（確定値は reports.urgency）
        'urgency_code': report.urgency,
        'ai_urgency_code': latest_ai.urgency if ai_succeeded else None,
        'ai_urgency': Urgency.label_of(latest_ai.urgency) if ai_succeeded else None,
        'urgency_options': [(u.value, u.label) for u in Urgency],
        # 確定した担当部署（職員が設定）と、最新の AI 判定での推定担当部署
        'department': report.department.name if report.department else None,
        'ai_department': latest_ai.department.name if ai_succeeded and latest_ai.department else None,
        'ai_confidence': format_percent(latest_ai.confidence) if ai_succeeded else None,
        # TODO: 写真は instance/uploads/ から権限チェック付きのルートで配信する（storage_service と配信ルートが未実装）
        'image_url': '',
        'history': [_history_row(h) for h in report.histories],
    }


def list_ai_analyses(search='', report_id=None):
    """直近の AI 自動判定結果（新しい順）。AI分析・自動判定ログ 基本フロー2〜5

    絞り込みは list_for_staff と同じく報告 ID または報告タイトルの部分一致。
    """
    stmt = (select(AiAnalysis)
            .join(AiAnalysis.report)
            .options(selectinload(AiAnalysis.report),
                     selectinload(AiAnalysis.category),
                     selectinload(AiAnalysis.department))
            .order_by(AiAnalysis.created_at.desc(), AiAnalysis.id.desc())
            .limit(AI_LOG_LIMIT))
    if report_id:
        stmt = stmt.where(AiAnalysis.report_id == report_id)
    elif search:
        stmt = stmt.where(Report.title.ilike(f'%{_escape_like(search)}%', escape='\\'))
    return [{
        'id': a.report_id,
        'title': a.report.title,
        'category': a.category.name if a.category else '—',
        'department': a.department.name if a.department else '—',
        'urgency': Urgency.label_of(a.urgency),
        'confidence': format_percent(a.confidence),
        'status': AiAnalysisStatus.label_of(a.status),
        # 再判定状況（初回判定 / 再判定済み）
        'trigger_type': a.trigger_type,
        'trigger': AiAnalysisTrigger.label_of(a.trigger_type),
        'date': format_datetime(a.created_at),
    } for a in db.session.execute(stmt).scalars()]


def ai_summary():
    """AI分析画面のサマリー（本日の判定件数・平均分類信頼度・要人間チェック件数）。AI分析・自動判定ログ 基本フロー2

    要人間チェック：未完了の報告のうち、最新の判定の信頼度がしきい値（AI_LOW_CONFIDENCE_THRESHOLD）未満のもの。
    """
    today_start = datetime.combine(datetime.now(JST).date(), time.min, tzinfo=JST)
    today_count = db.session.scalar(
        select(func.count()).select_from(AiAnalysis).where(AiAnalysis.created_at >= today_start))
    avg_confidence = db.session.scalar(
        select(func.avg(AiAnalysis.confidence)).where(AiAnalysis.status == AiAnalysisStatus.SUCCEEDED.value))

    # 報告ごとの最新の判定（created_at → id の降順で1件目）
    latest = (select(AiAnalysis.report_id, AiAnalysis.status, AiAnalysis.confidence,
                     func.row_number().over(
                         partition_by=AiAnalysis.report_id,
                         order_by=(AiAnalysis.created_at.desc(), AiAnalysis.id.desc())).label('rn'))
              .subquery())
    low_confidence_count = db.session.scalar(
        select(func.count())
        .select_from(latest)
        .join(Report, Report.id == latest.c.report_id)
        .where(latest.c.rn == 1,
               latest.c.status == AiAnalysisStatus.SUCCEEDED.value,
               latest.c.confidence < current_app.config['AI_LOW_CONFIDENCE_THRESHOLD'],
               Report.status.in_([s.value for s in OPEN_STATUSES])))
    return {
        'today_count': today_count,
        'avg_confidence': None if avg_confidence is None else f'{float(avg_confidence) * 100:.1f}',
        'low_confidence_count': low_confidence_count,
    }


def format_datetime(value):
    """画面表示用の日時（日本時間 YYYY/MM/DD HH:MM）"""
    if value is None:
        return ''
    # タイムゾーンなしの値は UTC とみなす
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST).strftime('%Y/%m/%d %H:%M')


def format_percent(value):
    """0〜1 の値を画面表示用の % 文字列にする（例: 0.98 → '98%'）"""
    if value is None:
        return ''
    return f'{float(value) * 100:.0f}%'


# 一覧の1行（報告一覧・詳細で共通の項目）。ラベルは constants.py から取る
def _staff_row(report):
    return {
        'id': report.id,
        'title': report.title,
        'category': report.category.name if report.category else '未分類',
        'location': report.address,
        'urgency': Urgency.label_of(report.urgency),
        'status': ReportStatus.label_of(report.status),
        # バッジの色分け用（components/_status_badge.html）
        'status_code': report.status,
        'date': format_datetime(report.created_at),
    }


# 対応履歴の1行（変更者・内容・担当者メモ・判断変更理由・日時）
def _history_row(history):
    to_label = ReportStatus.label_of(history.to_status)
    actions = []
    if history.from_status is None:
        actions.append('新規報告を受信')
    elif history.from_status != history.to_status:
        actions.append(f'ステータスを「{ReportStatus.label_of(history.from_status)}」から「{to_label}」に変更')
    # to_urgency がある行は、職員が緊急度を変更した（from_urgency の NULL は未判定から）
    if history.to_urgency:
        actions.append(f'緊急度を「{Urgency.label_of(history.from_urgency, "未判定")}」から'
                       f'「{Urgency.label_of(history.to_urgency)}」に変更')
    if not actions:
        actions.append(f'コメントを追加（{to_label}）')
    return {
        'date': format_datetime(history.created_at),
        # changed_by_id が NULL の場合はシステムによる自動処理
        'user': history.changed_by.name if history.changed_by else 'システム',
        'action': ' / '.join(actions),
        'comment': history.comment or '',
        'urgency_reason': history.urgency_reason or '',
    }


# LIKE の特殊文字（% _ \）をエスケープする
def _escape_like(text):
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
