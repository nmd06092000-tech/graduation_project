# 管理者による職員・管理者アカウントの作成・一覧・削除（論理削除）・ロック解除・再設定コード発行（画面・flask コマンド共通）、
# 所属部署の選択肢、削除済みアカウントの復元
# 市民のプロフィール設定（お名前・メールアドレス・アイコン）
# flask コマンド用のアカウント操作（テストアカウントの削除・無効化）
import secrets
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import aliased, joinedload

from app.constants import (
    MSG_ACCOUNT_NOT_DELETED, MSG_ACCOUNT_SAVE_FAILED, MSG_ACCOUNT_TARGET_NOT_FOUND, MSG_CANNOT_DELETE_LAST_ADMIN,
    MSG_CANNOT_DELETE_SELF, MSG_DEPARTMENT_INVALID, MSG_DEPARTMENT_REQUIRED, MSG_EMAIL_NO_AT, MSG_EMAIL_REQUIRED,
    MSG_EMAIL_TAKEN, MSG_ICON_REQUIRED, MSG_ID_HAS_AT, MSG_ID_REQUIRED, MSG_ID_TAKEN, MSG_ID_TOO_LONG,
    MSG_NAME_EMAIL_REQUIRED, MSG_NAME_REQUIRED, MSG_NAME_TOO_LONG, MSG_NO_ADMIN_LEFT, MSG_USER_NOT_FOUND,
    TEST_ACCOUNT_PW, Role,
)
from app.extensions import db
from app.models import Department, PasswordResetToken, User
from app.services import ServiceError, ValidationError, auth_service, storage_service
from app.services.auth_service import LOGIN_ID_LABELS
from app.services.report_service import format_datetime
from app.utils.validators import validate_operator_password

NAME_MAX_LENGTH = 100
LOGIN_ID_MAX_LENGTH = 255
# 一覧の検索語の最大文字数（長すぎる値を URL・hidden・LIKE に持ち込まない）
SEARCH_QUERY_MAX_LENGTH = 100

# 管理者が画面で扱うアカウントの役割（市民は対象外）
MANAGED_ROLES = (User.ROLE_STAFF, User.ROLE_ADMIN)
# PostgreSQL の bigint の最大値（URL の ID が範囲外のときに DB エラーにしないための上限）
BIGINT_MAX = 2 ** 63 - 1

# 開発用の公開パスワードで作られていたテストアカウント（README に載っていたもの）。
# ID ごとに役割も決まっていて、ID と役割の両方が一致するアカウントだけを対象にする
# （同じ ID の別の役割のアカウントを巻き込まないため）。公開パスワード TEST_ACCOUNT_PW は app/constants.py
TEST_LOGIN_IDS = ('taro@example.com', 'staff01', 'admin01')
TEST_ACCOUNT_ROLES = {
    'taro@example.com': User.ROLE_CITIZEN,
    'staff01': User.ROLE_STAFF,
    'admin01': User.ROLE_ADMIN,
}

# remove_test_users が返す処理結果（画面・コマンドにそのまま表示する）
ACTION_DELETED = '削除'
ACTION_DISABLED = '無効化（データあり）'
# 既に無効化されているが公開パスワードのままだったもの（データあり）。無効のままパスワードだけ変える
ACTION_DISABLED_PW_RESET = '無効化済み・パスワード変更'
ACTION_SKIPPED_MISSING = 'スキップ（アカウントなし）'
ACTION_SKIPPED_INACTIVE = 'スキップ（無効化済み）'
ACTION_SKIPPED_CHANGED = 'スキップ（パスワード変更済み）'


def update_profile(user_id, *, name, email):
    """お名前・メールアドレスを更新する。プロフィール設定 基本フロー2〜3

    メールアドレスはログイン ID なので小文字で保存し、他のアカウントとの重複は不可。
    入力エラーは ValidationError（2b-1 は赤、2c-1・2d-1 はオレンジ）。
    """
    user = db.session.get(User, user_id)
    name = (name or '').strip()
    email = (email or '').strip().lower()

    errors = []
    if not name and not email:
        errors.append(('danger', MSG_NAME_EMAIL_REQUIRED))
    elif not name:
        errors.append(('warning', MSG_NAME_REQUIRED))
    elif not email:
        errors.append(('warning', MSG_EMAIL_REQUIRED))
    if len(name) > NAME_MAX_LENGTH:
        errors.append(('warning', MSG_NAME_TOO_LONG))
    if email and '@' not in email:
        errors.append(('warning', MSG_EMAIL_NO_AT.format(email=email)))
    elif email and email != user.login_id and db.session.execute(
            select(User.id).filter_by(login_id=email)).first():
        errors.append(('warning', MSG_EMAIL_TAKEN))
    if errors:
        raise ValidationError(errors)

    user.name = name
    user.login_id = email
    try:
        db.session.commit()
    except IntegrityError:
        # 同時更新などで一意制約に違反した場合
        db.session.rollback()
        raise ValidationError([('warning', MSG_EMAIL_TAKEN)])
    return user


def update_icon(user_id, file_storage):
    """プロフィールアイコンを保存し、古いアイコンを削除する。プロフィール設定 基本フロー2"""
    image = storage_service.read_image(file_storage)
    if image is None:
        raise ServiceError(MSG_ICON_REQUIRED)
    user = db.session.get(User, user_id)
    old_path = user.icon_path
    new_path = storage_service.save(image, storage_service.ICON_DIR)
    user.icon_path = new_path
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        storage_service.delete(new_path)
        raise
    storage_service.delete(old_path)
    return user


def delete_icon(user_id):
    """プロフィールアイコンを削除し、初期アイコンに戻す（アイコン未設定のときは何もしない）"""
    user = db.session.get(User, user_id)
    old_path = user.icon_path
    if not old_path:
        return user
    user.icon_path = None
    db.session.commit()
    # DB の更新が確定してからファイルを消す（失敗時に画像だけ消えるのを防ぐ）
    storage_service.delete(old_path)
    return user


def list_departments(*, include_deleted=False):
    """所属部署の一覧 [(id, name)]（ID 順）。アカウント登録画面・マスタ管理の選択肢。行は .id / .name でも読める

    非表示の部署（3b）は既定では含めない（新しい選択肢にしないため）。
    ユーザー一覧の絞り込みのように、非表示の部署に所属する職員も探したいときは include_deleted=True。
    """
    query = select(Department.id, Department.name).order_by(Department.id)
    if not include_deleted:
        query = query.where(Department.del_flag.is_(False))
    return db.session.execute(query).all()


def find_department(value):
    """部署名または ID（数字）から Department を探す（flask --app run create-staff の --department 用）

    数字だけなら ID として探し、なければ（または数字以外なら）部署名の完全一致で探す。
    見つからない・非表示の部署なら ServiceError(MSG_DEPARTMENT_INVALID)。
    """
    value = (value or '').strip()
    department = None
    if value.isascii() and value.isdigit() and len(value) <= 18:
        department = db.session.get(Department, int(value))
    if department is None and value:
        department = db.session.execute(select(Department).filter_by(name=value)).scalar_one_or_none()
    if department is None or department.del_flag:
        raise ServiceError(MSG_DEPARTMENT_INVALID)
    return department


def create_admin(*, login_id, name, password, confirm_password=None):
    """管理者アカウントを作る（flask --app run create-admin、管理者の新規アカウント登録画面）。担当部署は NULL

    管理者IDに @ は使えない・255文字以内・既存の ID と重複できない。表示名は100文字以内。
    パスワードは8文字以上で、公開されているテストアカウントのパスワードは使えない
    （confirm_password を渡したときだけ、確認用パスワードとの一致も調べる）。
    入力エラーは ValidationError（'warning'）。予期しない DB エラーはロールバックして ServiceError にする。
    """
    return _create_account(role=User.ROLE_ADMIN, login_id=login_id, name=name, password=password,
                           confirm_password=confirm_password, department_id=None)


def create_staff(*, login_id, name, password, department_id, confirm_password=None):
    """職員アカウントを作る（flask --app run create-staff、管理者の新規アカウント登録画面）。担当部署は必須

    department_id は departments.id（画面の選択値の文字列でもよい）。空・存在しない部署は ValidationError。
    そのほかの規則は create_admin と同じ（職員IDに @ は使えない・255文字以内・重複不可 など）。
    """
    return _create_account(role=User.ROLE_STAFF, login_id=login_id, name=name, password=password,
                           confirm_password=confirm_password, department_id=department_id)


def _create_account(*, role, login_id, name, password, confirm_password, department_id):
    """職員・管理者アカウントの作成（入力チェック → 保存）。create_staff / create_admin の共通処理"""
    login_id = (login_id or '').strip()
    name = (name or '').strip()
    label = LOGIN_ID_LABELS[role]

    errors = []
    if not login_id:
        errors.append(('warning', MSG_ID_REQUIRED.format(label=label)))
    elif '@' in login_id:
        errors.append(('warning', MSG_ID_HAS_AT.format(label=label)))
    elif len(login_id) > LOGIN_ID_MAX_LENGTH:
        errors.append(('warning', MSG_ID_TOO_LONG.format(label=label)))
    elif _login_id_exists(login_id):
        errors.append(('warning', MSG_ID_TAKEN.format(label=label)))
    if not name:
        errors.append(('warning', MSG_NAME_REQUIRED))
    elif len(name) > NAME_MAX_LENGTH:
        errors.append(('warning', MSG_NAME_TOO_LONG))
    department = None
    if role == User.ROLE_STAFF:
        department_text = str(department_id if department_id is not None else '').strip()
        if not department_text:
            errors.append(('warning', MSG_DEPARTMENT_REQUIRED))
        else:
            department = _department_by_id(department_text)
            if department is None:
                errors.append(('warning', MSG_DEPARTMENT_INVALID))
    errors.extend(validate_operator_password(password, confirm_password))
    if errors:
        raise ValidationError(errors)

    user = User(login_id=login_id, name=name, role=role, department_id=department.id if department else None)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # 同時作成などで一意制約に違反した場合（ID の重複か、直前に部署が削除されたか）
        db.session.rollback()
        if _login_id_exists(login_id):
            raise ValidationError([('warning', MSG_ID_TAKEN.format(label=label))])
        raise ValidationError([('warning', MSG_DEPARTMENT_INVALID)])
    except SQLAlchemyError as e:
        # 予期しない DB エラー：スタックトレースをコマンドに出さず、種類だけログに残す
        db.session.rollback()
        current_app.logger.error('アカウントを保存できませんでした role=%s: %s', role, type(e).__name__)
        raise ServiceError(MSG_ACCOUNT_SAVE_FAILED) from None
    return user


def _login_id_exists(login_id):
    return db.session.execute(select(User.id).filter_by(login_id=login_id)).first() is not None


def _department_by_id(text):
    """ID（数字の文字列）から Department を返す。数字でない・範囲外・存在しない・非表示（3b）なら None"""
    if not (text.isascii() and text.isdigit()) or len(text) > 18:
        return None
    department = db.session.get(Department, int(text))
    return None if department is None or department.del_flag else department


def account_filters(role, department, q):
    """アカウント一覧の絞り込み条件（クエリ文字列）を画面に戻す形 {role, department, q}（すべて文字列）にそろえる

    役割が staff / admin 以外、部署が数字でないものは無視して空文字にする（list_accounts と同じ規則）。
    """
    department_id = _clean_department_id(department)
    return {
        'role': _clean_role(role) or '',
        'department': str(department_id) if department_id is not None else '',
        'q': (q or '').strip()[:SEARCH_QUERY_MAX_LENGTH],
    }


def list_accounts(*, role=None, department_id=None, q=None, limit=None):
    """職員・管理者アカウントの一覧（削除済みを除く）。新しい順。管理者のユーザー一覧 基本フロー

    role は staff / admin（それ以外は無視）、department_id は ID（数字でなければ無視）、
    q は氏名または ID の部分一致（大文字・小文字を区別しない。% と _ は文字として扱う）。
    所属部署は読み込み済みで返す（画面で 1 行ごとに SELECT しない）。
    """
    query = (select(User).options(joinedload(User.department))
             .where(User.role.in_(MANAGED_ROLES), User.del_flag.is_(False))
             .order_by(User.created_at.desc(), User.id.desc()))
    role = _clean_role(role)
    if role:
        query = query.where(User.role == role)
    department_id = _clean_department_id(department_id)
    if department_id is not None:
        query = query.where(User.department_id == department_id)
    q = (q or '').strip()
    if q:
        pattern = f'%{_escape_like(q)}%'
        query = query.where(User.name.ilike(pattern, escape='\\') | User.login_id.ilike(pattern, escape='\\'))
    if limit is not None:
        query = query.limit(limit)
    return list(db.session.execute(query).scalars())


def count_accounts():
    """一覧に出る職員・管理者アカウント（削除済みを除く）の総数。ポータルの「全 N 件」"""
    return db.session.execute(
        select(func.count()).select_from(User).where(User.role.in_(MANAGED_ROLES), User.del_flag.is_(False))
    ).scalar()


def account_row(user):
    """一覧・ポータルの 1 行分の辞書（テンプレートが読む形）。ロックの期限は日本時間 YYYY/MM/DD HH:MM、ロックなしは None"""
    locked = auth_service.is_locked(user)
    return {
        'id': user.id,
        'login_id': user.login_id,
        'name': user.name,
        'role': user.role,
        'role_label': Role.label_of(user.role),
        'department_name': user.department.name if user.department else '',
        'is_active': user.is_active,
        'is_locked': locked,
        'locked_until_text': format_datetime(user.locked_until) if locked else None,
    }


def delete_account(user_id, *, actor_id):
    """職員・管理者アカウントを論理削除する（del_flag・deleted_at・deleted_by_id を同時に書く）。管理者のユーザー一覧 削除

    自分自身（ServiceError(MSG_CANNOT_DELETE_SELF)）と、使える管理者が他にいなくなる削除
    （MSG_CANNOT_DELETE_LAST_ADMIN）はできない。対象が存在しない・市民・削除済みなら MSG_ACCOUNT_TARGET_NOT_FOUND。
    使える管理者を先に ID 順でロックしてから数えるので、2 人の管理者が同時に削除し合っても 0 人にならない
    （同じ順でロックするためデッドロックもしない）。未使用の再設定コード・トークンも使用済みにして 1 回でコミットする。
    ログイン中のセッションは is_usable の判定で次のリクエストから無効になる。
    """
    if user_id == actor_id:
        raise ServiceError(MSG_CANNOT_DELETE_SELF)
    admins = db.session.execute(
        select(User).where(User.role == User.ROLE_ADMIN, User.is_usable).order_by(User.id).with_for_update()
    ).scalars().all()
    user = _find_manageable_account(user_id, lock=True)
    if user is None:
        db.session.rollback()
        raise ServiceError(MSG_ACCOUNT_TARGET_NOT_FOUND)
    if user.role == User.ROLE_ADMIN and user.is_usable and len(admins) <= 1:
        db.session.rollback()
        raise ServiceError(MSG_CANNOT_DELETE_LAST_ADMIN)

    user.del_flag = True
    user.deleted_at = datetime.now(timezone.utc)
    user.deleted_by_id = actor_id
    auth_service.invalidate_reset_tokens(user.id)
    db.session.commit()
    return user


def unlock_account(user_id):
    """ログインの一時ロックを解除する（失敗回数も 0 に戻す）。管理者のユーザー一覧 ロック解除

    ロックされていないアカウントに対しても成功扱い（何も変わらない）。対象が存在しない・市民・削除済みなら
    ServiceError(MSG_ACCOUNT_TARGET_NOT_FOUND)。
    """
    user = _find_manageable_account(user_id, lock=True)
    if user is None:
        db.session.rollback()
        raise ServiceError(MSG_ACCOUNT_TARGET_NOT_FOUND)
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()
    return user


def issue_reset_code_for_account(user_id):
    """画面から職員・管理者の再設定コードを発行する。管理者のユーザー一覧 再設定コード発行

    返り値は画面に 1 回だけ出す辞書 {name, login_id, code, expires_at_text, is_locked}（コードの平文を含む。
    保存しない・ログに出さない）。発行そのものは auth_service.issue_reset_code と同じ（前のコード・トークンは無効になる）。
    ロック中のアカウントにも発行できる（is_locked で画面が「先にロック解除」を案内する）。
    対象が存在しない・市民・削除済みは MSG_ACCOUNT_TARGET_NOT_FOUND、利用停止は MSG_RESET_CODE_NOT_ALLOWED。
    """
    user = _find_manageable_account(user_id)
    if user is None:
        raise ServiceError(MSG_ACCOUNT_TARGET_NOT_FOUND)
    name, login_id, locked = user.name, user.login_id, auth_service.is_locked(user)
    code, expires_at = auth_service.issue_reset_code(login_id)
    return {'name': name, 'login_id': login_id, 'code': code,
            'expires_at_text': format_datetime(expires_at), 'is_locked': locked}


def restore_account(login_id):
    """削除済みアカウントを復元する（flask --app run restore-user）。ロック・失敗回数・未使用のコード/トークンも消す

    存在しなければ ServiceError(MSG_USER_NOT_FOUND)、削除されていなければ MSG_ACCOUNT_NOT_DELETED。
    復元後は元のパスワードで入れる（忘れたときは issue-reset-code / set-password）。
    """
    login_id = (login_id or '').strip()
    user = db.session.execute(select(User).filter_by(login_id=login_id).with_for_update()).scalar_one_or_none()
    if user is None:
        db.session.rollback()
        raise ServiceError(MSG_USER_NOT_FOUND.format(login_id=login_id))
    if not user.is_deleted:
        db.session.rollback()
        raise ServiceError(MSG_ACCOUNT_NOT_DELETED.format(login_id=user.login_id))
    user.del_flag = False
    user.deleted_at = None
    user.deleted_by_id = None
    user.failed_login_count = 0
    user.locked_until = None
    auth_service.invalidate_reset_tokens(user.id)
    db.session.commit()
    return user


def list_deleted_accounts():
    """削除済みアカウントの一覧（flask --app run restore-user --list）。削除が新しい順

    各行は辞書 {login_id, name, role, role_label, deleted_at_text（日本時間）, deleted_by（削除者の ID。不明なら空）}。
    """
    deleter = aliased(User)
    rows = db.session.execute(
        select(User, deleter.login_id).outerjoin(deleter, User.deleted_by_id == deleter.id)
        .where(User.del_flag.is_(True)).order_by(User.deleted_at.desc(), User.id.desc())
    ).all()
    return [{'login_id': user.login_id, 'name': user.name, 'role': user.role,
             'role_label': Role.label_of(user.role), 'deleted_at_text': format_datetime(user.deleted_at),
             'deleted_by': deleted_by_login_id or ''}
            for user, deleted_by_login_id in rows]


def _find_manageable_account(user_id, *, lock=False):
    """管理者が操作できるアカウント（削除済みでない職員・管理者）を返す。なければ None（市民・削除済みもなし扱い）"""
    if not isinstance(user_id, int) or not 0 < user_id <= BIGINT_MAX:
        # bigint の範囲外の値は DB に渡さず「なし」にする（DB エラー＝500 にしない）
        return None
    query = select(User).where(User.id == user_id, User.role.in_(MANAGED_ROLES), User.del_flag.is_(False))
    if lock:
        query = query.with_for_update()
    return db.session.execute(query).scalar_one_or_none()


def _clean_role(role):
    """絞り込みの役割：staff / admin だけ通す（それ以外は None）"""
    return role if role in MANAGED_ROLES else None


def _clean_department_id(value):
    """絞り込みの部署：数字（ASCII・18桁以内）だけ int にする（それ以外は None）"""
    text = str(value if value is not None else '').strip()
    if text.isascii() and text.isdigit() and len(text) <= 18:
        return int(text)
    return None


def _escape_like(text):
    """LIKE / ILIKE の特殊文字（バックスラッシュ・%・_）を文字として扱えるようにエスケープする（escape にバックスラッシュを渡して使う）"""
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def remove_test_users(*, dry_run=False, force=False):
    """テストアカウント（TEST_LOGIN_IDS）を削除、またはデータがあれば無効化する（flask --app run remove-test-users）

    返り値は [(login_id, 結果)]。結果は ACTION_* のどれか。
    対象は ID と役割の両方が一致するアカウントだけ（TEST_ACCOUNT_ROLES。役割が違えば「アカウントなし」）。
    - 報告などのデータが紐づかない → 削除（再設定トークンも一緒に消す）
    - データが紐づく → is_active=False にし、パスワードをランダムな値に変える
    - 既に無効化済み・削除済み（del_flag）でもパスワードが公開されたまま（または force）なら見逃さない：
      データなし → 削除、データあり → 無効のままパスワードだけ変える（ACTION_DISABLED_PW_RESET）
    - スキップ：存在しない（または役割違い）、パスワードが password123 でなくなっている（force なら処理する）
    処理後に有効な管理者が1人も残らない場合は ServiceError（何も変更しない）。
    すべて1トランザクション。dry_run なら変更を取り消して結果だけ返す。
    """
    plan = _plan_test_user_removal(force)
    removed = [user for user, action in plan if action in (ACTION_DELETED, ACTION_DISABLED)]
    try:
        _ensure_admin_remains(removed)
        for user, action in plan:
            if action == ACTION_DELETED:
                db.session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
                db.session.delete(user)
            elif action in (ACTION_DISABLED, ACTION_DISABLED_PW_RESET):
                db.session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
                user.is_active = False
                # 公開されているパスワードではもう入れないようにする（本人もログインしない前提のランダム値）
                user.set_password(secrets.token_urlsafe(32))
        results = [(login_id, action) for (_, action), login_id in zip(plan, TEST_LOGIN_IDS)]
        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return results


def _plan_test_user_removal(force):
    """TEST_LOGIN_IDS ごとに [(User または None, 結果)] を決める（DB は変更しない）

    判定の順：ID と役割が一致するアカウントがない → パスワード変更済み（force なし）→ データの有無。
    パスワード変更済みなら、使えるものは ACTION_SKIPPED_CHANGED、使えないもの（無効化済み・削除済み）は ACTION_SKIPPED_INACTIVE。
    """
    reference_columns = _user_reference_columns()
    plan = []
    for login_id in TEST_LOGIN_IDS:
        user = db.session.execute(
            select(User).filter_by(login_id=login_id, role=TEST_ACCOUNT_ROLES[login_id])).scalar_one_or_none()
        if user is None:
            plan.append((None, ACTION_SKIPPED_MISSING))
        elif not force and not user.check_password(TEST_ACCOUNT_PW):
            plan.append((user, ACTION_SKIPPED_CHANGED if user.is_usable else ACTION_SKIPPED_INACTIVE))
        elif _has_related_data(user, reference_columns):
            plan.append((user, ACTION_DISABLED if user.is_usable else ACTION_DISABLED_PW_RESET))
        else:
            plan.append((user, ACTION_DELETED))
    return plan


def _user_reference_columns():
    """users.id を参照している外部キー列のうち、ON DELETE CASCADE でないもの（消すとエラーになる＝データが紐づく）

    モデルから動的に集めるので、あとで users を参照するテーブルが増えても取りこぼさない。
    """
    columns = []
    for table in db.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            if foreign_key.column.table is User.__table__ and (foreign_key.ondelete or '').upper() != 'CASCADE':
                columns.append(foreign_key.parent)
    return columns


def _has_related_data(user, reference_columns):
    """報告・状態履歴・通知など、ユーザーに紐づく行が1つでもあるか"""
    return any(
        db.session.execute(select(exists().where(column == user.id))).scalar()
        for column in reference_columns
    )


def _ensure_admin_remains(removed_users):
    """削除・無効化したあとに、使える管理者（利用停止でも削除済みでもない）が1人以上残ること（0人になるなら ServiceError）"""
    if not removed_users:
        return
    remaining = db.session.execute(
        select(func.count()).select_from(User).where(
            User.role == User.ROLE_ADMIN, User.is_usable,
            User.id.not_in([user.id for user in removed_users]))
    ).scalar()
    if remaining == 0:
        raise ServiceError(MSG_NO_ADMIN_LEFT)
