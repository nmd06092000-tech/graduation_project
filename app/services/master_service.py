# 管理者によるマスタ管理（部署・カテゴリの一覧・追加・名称変更・既定の担当部署の変更・非表示／再表示。3b）
# 非表示は論理削除（del_flag・deleted_at・deleted_by_id を同時に書く）。行は消さないので、過去の報告・職員の表示は変わらない
import secrets
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.ai.rule_based import OTHER_CATEGORY
from app.constants import (
    MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID, MSG_CATEGORY_DEFAULT_DEPARTMENT_REQUIRED, MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED,
    MSG_CATEGORY_NAME_REQUIRED, MSG_CATEGORY_OTHER_PROTECTED, MSG_CATEGORY_RESTORE_DEPARTMENT_HIDDEN,
    MSG_DEPARTMENT_IN_USE_BY_CATEGORY, MSG_DEPARTMENT_NAME_REQUIRED, MSG_MASTER_NAME_INVALID, MSG_MASTER_NAME_TAKEN,
    MSG_MASTER_NAME_TAKEN_HIDDEN, MSG_MASTER_NAME_TOO_LONG, MSG_MASTER_SAVE_FAILED, MSG_MASTER_TARGET_NOT_FOUND,
)
from app.extensions import db
from app.models import Category, Department, Report, User
from app.services import ServiceError, ValidationError
from app.services.report_service import format_datetime
from app.services.user_service import BIGINT_MAX

# 部署名・カテゴリ名の最大文字数（departments.name / categories.name の長さ）
NAME_MAX_LENGTH = 50
# 画面から作るカテゴリの内部コードは「cat_」＋ランダムな 16 進 8 文字（管理者は入力せず、後から変えない）
CATEGORY_CODE_PREFIX = 'cat_'
CATEGORY_CODE_ATTEMPTS = 5
# ポータルのカードに並べる「カテゴリ → 既定の担当部署」の最大件数
PORTAL_MAPPING_LIMIT = 6

# 種類ごとの文言の部品（入力欄の名前, 対象の呼び名）。MSG_MASTER_* の {label} / {item} に入れる
_LABELS = {
    Department: ('部署名', '部署'),
    Category: ('カテゴリ名', 'カテゴリ'),
}


# ---------------------------------------------------------------- 一覧・集計

def list_department_rows(include_deleted=False):
    """部署管理の一覧（ID 順）。1 回のクエリで所属職員数・カテゴリ数・報告件数まで数える

    各行は辞書 {id, name, is_deleted, deleted_at_text, user_count（削除されていないアカウント）,
    category_count（既定の担当部署にしている表示中のカテゴリ）, report_count（担当部署がこの部署の報告）}。
    include_deleted=False のときは非表示の部署を含めない。
    """
    user_count = (select(func.count(User.id))
                  .where(User.department_id == Department.id, User.del_flag.is_(False)).scalar_subquery())
    category_count = (select(func.count(Category.id))
                      .where(Category.default_department_id == Department.id, Category.del_flag.is_(False))
                      .scalar_subquery())
    report_count = select(func.count(Report.id)).where(Report.department_id == Department.id).scalar_subquery()
    query = select(Department, user_count, category_count, report_count).order_by(Department.id)
    if not include_deleted:
        query = query.where(Department.del_flag.is_(False))
    return [{'id': d.id, 'name': d.name, 'is_deleted': d.is_deleted, 'deleted_at_text': format_datetime(d.deleted_at),
             'user_count': users, 'category_count': categories, 'report_count': reports}
            for d, users, categories, reports in db.session.execute(query).all()]


def list_category_rows(include_deleted=False):
    """カテゴリ管理の一覧（ID 順）。既定の担当部署の名前・非表示かどうかと報告件数も 1 回のクエリで取る

    各行は辞書 {id, name, default_department_id, default_department_name, default_department_is_deleted,
    is_deleted, deleted_at_text, report_count（このカテゴリの報告）}。内部コード（code）は画面に出さないので含めない。
    """
    report_count = select(func.count(Report.id)).where(Report.category_id == Category.id).scalar_subquery()
    query = (select(Category, Department.name, Department.del_flag, report_count)
             .join(Department, Category.default_department_id == Department.id)
             .order_by(Category.id))
    if not include_deleted:
        query = query.where(Category.del_flag.is_(False))
    return [{'id': c.id, 'name': c.name, 'default_department_id': c.default_department_id,
             'default_department_name': department_name, 'default_department_is_deleted': department_deleted,
             'is_deleted': c.is_deleted, 'deleted_at_text': format_datetime(c.deleted_at), 'report_count': reports}
            for c, department_name, department_deleted, reports in db.session.execute(query).all()]


def count_hidden():
    """非表示の部署・カテゴリの件数 {'departments': n, 'categories': n}。「非表示も表示（n）」の件数表示用"""
    departments, categories = db.session.execute(select(
        select(func.count(Department.id)).where(Department.del_flag.is_(True)).scalar_subquery(),
        select(func.count(Category.id)).where(Category.del_flag.is_(True)).scalar_subquery(),
    )).one()
    return {'departments': departments, 'categories': categories}


def portal_summary(limit=PORTAL_MAPPING_LIMIT):
    """ポータルのカード用：{mappings: [{category_name, department_name}]（表示中のカテゴリ。ID 順で limit 件まで）,
    category_total, department_total（表示中の件数）}"""
    rows = db.session.execute(
        select(Category.name, Department.name)
        .join(Department, Category.default_department_id == Department.id)
        .where(Category.del_flag.is_(False)).order_by(Category.id).limit(limit)).all()
    category_total = db.session.scalar(select(func.count(Category.id)).where(Category.del_flag.is_(False)))
    department_total = db.session.scalar(select(func.count(Department.id)).where(Department.del_flag.is_(False)))
    return {'mappings': [{'category_name': category, 'department_name': department} for category, department in rows],
            'category_total': category_total, 'department_total': department_total}


# ---------------------------------------------------------------- 部署

def create_department(name):
    """部署を追加する。部署管理 追加

    名前は前後の空白を除き、必須・50文字以内・制御文字なし・重複不可（非表示の部署の名前とも重複不可）。
    入力エラーは ValidationError（未入力は赤 'danger'、それ以外は 'warning'）。
    """
    name = _clean_name(name)
    _validate_department_name(name)
    department = Department(name=name)
    db.session.add(department)
    _commit(Department, name)
    return department


def rename_department(department_id, name):
    """部署の名前を変える（自分自身の名前と同じなら何も変えず成功）。部署管理 名称変更

    対象がなければ ServiceError(MSG_MASTER_TARGET_NOT_FOUND)。入力の規則は create_department と同じ。
    """
    department = _lock_department(department_id)
    if department is None or department.is_deleted:
        # 非表示の部署は画面から名前を変えられない（再表示だけ）。手動 POST も「見つからない」として扱う
        db.session.rollback()
        raise ServiceError(MSG_MASTER_TARGET_NOT_FOUND.format(item=_LABELS[Department][1]))
    name = _clean_name(name)
    _validate_department_name(name, exclude_id=department.id)
    department.name = name
    _commit(Department, name)
    return department


def hide_department(department_id, *, actor_id):
    """部署を非表示にする（新しい報告・職員登録・AI の選択肢から外れる）。部署管理 非表示

    既定の担当部署にしている表示中のカテゴリがあるときは非表示にできない（MSG_DEPARTMENT_IN_USE_BY_CATEGORY）。
    所属職員がいても非表示にできる。部署の行を先にロックするので、同時にカテゴリが追加されてもすり抜けない。
    すでに非表示なら何も変えず成功。
    """
    department = _lock_department(department_id)
    if department is None:
        db.session.rollback()
        raise ServiceError(MSG_MASTER_TARGET_NOT_FOUND.format(item=_LABELS[Department][1]))
    if department.is_deleted:
        db.session.rollback()
        return department
    names = db.session.execute(
        select(Category.name).where(Category.default_department_id == department.id, Category.del_flag.is_(False))
        .order_by(Category.id)).scalars().all()
    if names:
        db.session.rollback()
        raise ServiceError(MSG_DEPARTMENT_IN_USE_BY_CATEGORY.format(names='、'.join(names)))
    _set_hidden(department, actor_id)
    _commit()
    return department


def restore_department(department_id):
    """非表示の部署を表示に戻す（すでに表示中なら何も変えず成功）。部署管理 再表示"""
    department = _lock_department(department_id)
    if department is None:
        db.session.rollback()
        raise ServiceError(MSG_MASTER_TARGET_NOT_FOUND.format(item=_LABELS[Department][1]))
    _set_visible(department)
    _commit()
    return department


# ---------------------------------------------------------------- カテゴリ

def create_category(name, default_department_id):
    """カテゴリを追加する。カテゴリ管理 追加

    default_department_id は departments.id（画面の選択値の文字列でもよい）。表示中の部署だけ選べる。
    名前・部署の両方が未入力なら赤 'danger' の 1 件、片方だけなら 'warning'（ValidationError）。
    内部コードは自動で採番する（cat_ ＋ 16 進 8 文字）。
    """
    name = _clean_name(name)
    _validate_category(name, default_department_id)
    category = Category(code=_new_category_code(), name=name,
                        default_department_id=_department_id_from_text(str(default_department_id)))
    db.session.add(category)
    _commit(Category, name)
    return category


def update_category(category_id, name, default_department_id):
    """カテゴリの名前と既定の担当部署を変える。カテゴリ管理 編集

    既定の担当部署を変えても、すでにある報告の担当部署は変わらない（これから登録・判定される報告から新しい部署になる）。
    入力の規則は create_category と同じ（自分自身の名前と同じ名前は可）。非表示のカテゴリは編集できない（MSG_MASTER_TARGET_NOT_FOUND）。
    """
    # 部署 → カテゴリの順に行をロックする（restore_category と同じ順にしてデッドロックを避ける）
    _lock_department(_department_id_from_text(str(default_department_id if default_department_id is not None else '')))
    category = _lock_category_checked(category_id)
    if category.is_deleted:
        # 非表示のカテゴリは画面から編集できない（再表示だけ）。手動 POST も「見つからない」として扱う
        db.session.rollback()
        raise ServiceError(MSG_MASTER_TARGET_NOT_FOUND.format(item=_LABELS[Category][1]))
    name = _clean_name(name)
    _validate_category(name, default_department_id, category=category)
    category.name = name
    category.default_department_id = _department_id_from_text(str(default_department_id))
    _commit(Category, name)
    return category


def hide_category(category_id, *, actor_id):
    """カテゴリを非表示にする（新しい報告・AI の選択肢から外れる。過去の報告のカテゴリ表示は変わらない）。カテゴリ管理 非表示

    AI（キーワード判定）が既定の分類に使う「その他」（code=other）は非表示にできない（MSG_CATEGORY_OTHER_PROTECTED）。
    すでに非表示なら何も変えず成功。
    """
    category = _lock_category_checked(category_id)
    if category.code == OTHER_CATEGORY:
        name = category.name
        db.session.rollback()
        raise ServiceError(MSG_CATEGORY_OTHER_PROTECTED.format(name=name))
    _set_hidden(category, actor_id)
    _commit()
    return category


def restore_category(category_id):
    """非表示のカテゴリを表示に戻す。カテゴリ管理 再表示

    既定の担当部署が非表示のときは戻せない（MSG_CATEGORY_RESTORE_DEPARTMENT_HIDDEN）。
    部署 → カテゴリの順に行をロックする（部署の非表示・カテゴリ追加と同じ順）。すでに表示中なら何も変えず成功。
    """
    department_id = db.session.scalar(select(Category.default_department_id).where(Category.id == _bounded_id(category_id)))
    if department_id is None:
        db.session.rollback()
        raise ServiceError(MSG_MASTER_TARGET_NOT_FOUND.format(item=_LABELS[Category][1]))
    department = _lock_department(department_id)
    category = _lock_category_checked(category_id)
    if category.default_department_id != department.id:
        # 部署の行を読んだあとに update_category が既定部署を変えた場合、古い部署で調べないよう安全側で中止する（再操作で解決）
        db.session.rollback()
        raise ServiceError(MSG_MASTER_SAVE_FAILED)
    if category.is_deleted and department.is_deleted:
        department_name = department.name
        db.session.rollback()
        raise ServiceError(MSG_CATEGORY_RESTORE_DEPARTMENT_HIDDEN.format(department=department_name))
    _set_visible(category)
    _commit()
    return category


# ---------------------------------------------------------------- 内部の共通処理

def _clean_name(value):
    """入力された名前の前後の空白（全角を含む）を除く。None は空文字"""
    return (value or '').strip()


def _name_format_errors(name, label):
    """名前の形式エラー [(カテゴリ, 文言)]。空の名前は呼び出し側で扱う。長さ → 使えない文字の順で最初の 1 件だけ返す

    使えない文字：改行・タブ・制御文字・ゼロ幅文字など（名前は AI のプロンプトにも入るため）。全角スペースは使える。
    """
    if len(name) > NAME_MAX_LENGTH:
        return [('warning', MSG_MASTER_NAME_TOO_LONG.format(label=label))]
    if not all(ch.isprintable() or ch == '　' for ch in name):
        return [('warning', MSG_MASTER_NAME_INVALID.format(label=label))]
    return []


def _name_conflict_error(model, name, *, exclude_id=None):
    """同じ名前の行があれば重複エラー ('warning', 文言)、なければ None（非表示の行なら専用の文言）"""
    query = select(model.del_flag).where(model.name == name)
    if exclude_id is not None:
        query = query.where(model.id != exclude_id)
    hidden = db.session.execute(query).scalar_one_or_none()
    if hidden is None:
        return None
    label, item = _LABELS[model]
    message = MSG_MASTER_NAME_TAKEN_HIDDEN if hidden else MSG_MASTER_NAME_TAKEN
    return 'warning', message.format(label=label, item=item)


def _validate_department_name(name, *, exclude_id=None):
    """部署名の入力チェック。エラーがあれば（ロックを手放して）ValidationError"""
    errors = []
    if not name:
        errors.append(('danger', MSG_DEPARTMENT_NAME_REQUIRED))
    else:
        errors.extend(_name_format_errors(name, _LABELS[Department][0]))
        if not errors and (conflict := _name_conflict_error(Department, name, exclude_id=exclude_id)):
            errors.append(conflict)
    _raise_if_any(errors)


def _validate_category(name, default_department_id, *, category=None):
    """カテゴリ名・既定の担当部署の入力チェック。選んだ部署の行をロックしてから調べ、エラーなら ValidationError

    部署は表示中のものだけ選べる（非表示の部署は常に MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID）。
    """
    department_text = str(default_department_id if default_department_id is not None else '').strip()
    errors = []
    if not name and not department_text:
        errors.append(('danger', MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED))
        _raise_if_any(errors)
    if not name:
        errors.append(('warning', MSG_CATEGORY_NAME_REQUIRED))
    else:
        errors.extend(_name_format_errors(name, _LABELS[Category][0]))
        if not errors and (conflict := _name_conflict_error(
                Category, name, exclude_id=category.id if category else None)):
            errors.append(conflict)
    if not department_text:
        errors.append(('warning', MSG_CATEGORY_DEFAULT_DEPARTMENT_REQUIRED))
    elif not _selectable_department(department_text):
        errors.append(('warning', MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID))
    _raise_if_any(errors)


def _selectable_department(text):
    """既定の担当部署として選べる部署か（表示中の部署だけ。行をロックして調べる）"""
    department_id = _department_id_from_text(text)
    department = _lock_department(department_id) if department_id else None
    return department is not None and not department.is_deleted


def _department_id_from_text(text):
    """画面の選択値（数字の文字列）を部署 ID にする。数字でない・長すぎる値は 0（存在しない ID）"""
    text = text.strip()
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 18 else 0


def _raise_if_any(errors):
    """エラーがあれば、ロックを手放してから ValidationError にする"""
    if errors:
        db.session.rollback()
        raise ValidationError(errors)


def _bounded_id(value):
    """bigint の範囲に収まる正の整数ならそのまま、そうでなければ 0（DB エラー＝500 にせず「なし」にする）"""
    return value if isinstance(value, int) and 0 < value <= BIGINT_MAX else 0


def _lock_department(department_id):
    """部署の行を FOR UPDATE で読む（なければ None）。他の更新と同時に走っても整合を保つ"""
    return db.session.execute(
        select(Department).where(Department.id == _bounded_id(department_id))
        .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()


def _lock_category_checked(category_id):
    """カテゴリの行を FOR UPDATE で読む。なければ ServiceError(MSG_MASTER_TARGET_NOT_FOUND)"""
    category = db.session.execute(
        select(Category).where(Category.id == _bounded_id(category_id))
        .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if category is None:
        db.session.rollback()
        raise ServiceError(MSG_MASTER_TARGET_NOT_FOUND.format(item=_LABELS[Category][1]))
    return category


def _set_hidden(row, actor_id):
    """非表示にする。del_flag・deleted_at・deleted_by_id は同じ書き込みで変える（CHECK を満たすため）"""
    if row.is_deleted:
        return
    row.del_flag = True
    row.deleted_at = datetime.now(timezone.utc)
    row.deleted_by_id = actor_id


def _set_visible(row):
    """表示に戻す（非表示の記録を消す）"""
    row.del_flag = False
    row.deleted_at = None
    row.deleted_by_id = None


def _new_category_code():
    """まだ使われていないカテゴリの内部コード（cat_ ＋ 16 進 8 文字）を作る"""
    for _ in range(CATEGORY_CODE_ATTEMPTS):
        code = CATEGORY_CODE_PREFIX + secrets.token_hex(4)
        if db.session.execute(select(Category.id).filter_by(code=code)).first() is None:
            return code
    raise ServiceError(MSG_MASTER_SAVE_FAILED)


def _commit(model=None, name=None):
    """コミットする。名前の一意制約に違反したら（同時登録など）重複エラー、そのほかの DB エラーは保存失敗にする

    model・name を渡したときだけ、重複を調べて「既に登録されています」を返す（非表示の行との重複も区別する）。
    """
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        conflict = _name_conflict_error(model, name) if model is not None else None
        if conflict:
            raise ValidationError([conflict]) from None
        current_app.logger.error('マスタを保存できませんでした: %s', type(e).__name__)
        raise ServiceError(MSG_MASTER_SAVE_FAILED) from None
    except SQLAlchemyError as e:
        # 予期しない DB エラー：スタックトレースを画面に出さず、種類だけログに残す
        db.session.rollback()
        current_app.logger.error('マスタを保存できませんでした: %s', type(e).__name__)
        raise ServiceError(MSG_MASTER_SAVE_FAILED) from None
