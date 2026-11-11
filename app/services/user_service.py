# 市民の新規登録、管理者による職員アカウントの作成・停止・再開
# 市民のプロフィール設定（お名前・メールアドレス・アイコン）
# flask コマンド用のアカウント操作（管理者の作成、テストアカウントの削除・無効化）
import secrets

from flask import current_app
from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.constants import (
    MSG_ACCOUNT_SAVE_FAILED, MSG_EMAIL_NO_AT, MSG_EMAIL_REQUIRED, MSG_EMAIL_TAKEN, MSG_ICON_REQUIRED, MSG_ID_HAS_AT,
    MSG_ID_REQUIRED, MSG_ID_TAKEN, MSG_ID_TOO_LONG, MSG_NAME_EMAIL_REQUIRED, MSG_NAME_REQUIRED, MSG_NAME_TOO_LONG,
    MSG_NO_ADMIN_LEFT, TEST_ACCOUNT_PW,
)
from app.extensions import db
from app.models import PasswordResetToken, User
from app.services import ServiceError, ValidationError, storage_service
from app.services.auth_service import LOGIN_ID_LABELS
from app.utils.validators import validate_operator_password

NAME_MAX_LENGTH = 100
LOGIN_ID_MAX_LENGTH = 255

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


def create_admin(*, login_id, name, password):
    """管理者アカウントを作る（flask --app run create-admin）。担当部署は NULL

    管理者IDに @ は使えない・255文字以内・既存の ID と重複できない。表示名は100文字以内。
    パスワードは8文字以上で、公開されているテストアカウントのパスワードは使えない。
    入力エラーは ValidationError（'warning'）。予期しない DB エラーはロールバックして ServiceError にする。
    """
    login_id = (login_id or '').strip()
    name = (name or '').strip()
    label = LOGIN_ID_LABELS[User.ROLE_ADMIN]

    errors = []
    if not login_id:
        errors.append(('warning', MSG_ID_REQUIRED.format(label=label)))
    elif '@' in login_id:
        errors.append(('warning', MSG_ID_HAS_AT.format(label=label)))
    elif len(login_id) > LOGIN_ID_MAX_LENGTH:
        errors.append(('warning', MSG_ID_TOO_LONG.format(label=label)))
    elif db.session.execute(select(User.id).filter_by(login_id=login_id)).first():
        errors.append(('warning', MSG_ID_TAKEN.format(label=label)))
    if not name:
        errors.append(('warning', MSG_NAME_REQUIRED))
    elif len(name) > NAME_MAX_LENGTH:
        errors.append(('warning', MSG_NAME_TOO_LONG))
    errors.extend(validate_operator_password(password))
    if errors:
        raise ValidationError(errors)

    user = User(login_id=login_id, name=name, role=User.ROLE_ADMIN)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # 同時作成などで一意制約に違反した場合
        db.session.rollback()
        raise ValidationError([('warning', MSG_ID_TAKEN.format(label=label))])
    except SQLAlchemyError as e:
        # 予期しない DB エラー：スタックトレースをコマンドに出さず、種類だけログに残す
        db.session.rollback()
        current_app.logger.error('管理者アカウントを保存できませんでした: %s', type(e).__name__)
        raise ServiceError(MSG_ACCOUNT_SAVE_FAILED) from None
    return user


def remove_test_users(*, dry_run=False, force=False):
    """テストアカウント（TEST_LOGIN_IDS）を削除、またはデータがあれば無効化する（flask --app run remove-test-users）

    返り値は [(login_id, 結果)]。結果は ACTION_* のどれか。
    対象は ID と役割の両方が一致するアカウントだけ（TEST_ACCOUNT_ROLES。役割が違えば「アカウントなし」）。
    - 報告などのデータが紐づかない → 削除（再設定トークンも一緒に消す）
    - データが紐づく → is_active=False にし、パスワードをランダムな値に変える
    - 既に無効化済みでもパスワードが公開されたまま（または force）なら見逃さない：
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
    パスワード変更済みなら、有効なものは ACTION_SKIPPED_CHANGED、無効化済みのものは ACTION_SKIPPED_INACTIVE。
    """
    reference_columns = _user_reference_columns()
    plan = []
    for login_id in TEST_LOGIN_IDS:
        user = db.session.execute(
            select(User).filter_by(login_id=login_id, role=TEST_ACCOUNT_ROLES[login_id])).scalar_one_or_none()
        if user is None:
            plan.append((None, ACTION_SKIPPED_MISSING))
        elif not force and not user.check_password(TEST_ACCOUNT_PW):
            plan.append((user, ACTION_SKIPPED_CHANGED if user.is_active else ACTION_SKIPPED_INACTIVE))
        elif _has_related_data(user, reference_columns):
            plan.append((user, ACTION_DISABLED if user.is_active else ACTION_DISABLED_PW_RESET))
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
    """削除・無効化したあとに、有効な管理者が1人以上残ること（0人になるなら ServiceError）"""
    if not removed_users:
        return
    remaining = db.session.execute(
        select(func.count()).select_from(User).where(
            User.role == User.ROLE_ADMIN, User.is_active.is_(True),
            User.id.not_in([user.id for user in removed_users]))
    ).scalar()
    if remaining == 0:
        raise ServiceError(MSG_NO_ADMIN_LEFT)
