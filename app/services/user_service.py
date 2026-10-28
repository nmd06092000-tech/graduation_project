# 市民の新規登録、管理者による職員アカウントの作成・停止・再開
# 市民のプロフィール設定（お名前・メールアドレス・アイコン）
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.constants import (
    MSG_EMAIL_NO_AT, MSG_EMAIL_REQUIRED, MSG_EMAIL_TAKEN, MSG_ICON_REQUIRED, MSG_NAME_EMAIL_REQUIRED,
    MSG_NAME_REQUIRED, MSG_NAME_TOO_LONG,
)
from app.extensions import db
from app.models import User
from app.services import ServiceError, ValidationError, storage_service

NAME_MAX_LENGTH = 100


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
