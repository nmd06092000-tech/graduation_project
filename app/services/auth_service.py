# ログイン（入力チェック・認証・連続失敗による一時ロック）と、運用者（flask コマンド）によるパスワード設定
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from flask import current_app
from sqlalchemy import case, or_, select, update
from werkzeug.security import check_password_hash, generate_password_hash

from app.constants import (
    MSG_ACCOUNT_DISABLED, MSG_ID_HAS_AT, MSG_ID_OR_PASSWORD_REQUIRED, MSG_ID_REQUIRED, MSG_LOGIN_FAILED,
    MSG_LOGIN_LOCKED, MSG_USER_NOT_FOUND,
)
from app.extensions import db
from app.models import User
from app.services import ServiceError, ValidationError
from app.utils.validators import validate_new_password, validate_operator_password

# ログイン画面の ID 欄の名前（メッセージの {label} に入る）
LOGIN_ID_LABELS = {
    User.ROLE_CITIZEN: 'メールアドレス',
    User.ROLE_STAFF: '職員ID',
    User.ROLE_ADMIN: '管理者ID',
}


def _now():
    """現在時刻（UTC）。ロックの判定・期限の計算はすべてここを通す（テストでは差し替える）"""
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _dummy_hash():
    """存在しない ID でも check_password_hash を実行して応答時間をそろえるための偽のハッシュ"""
    return generate_password_hash('dummy-password-for-timing')


def authenticate(role, login_id, password):
    """ID とパスワードで認証し、成功したら User を返す。ログイン 基本フロー2〜3・代替フロー

    入力エラーは ValidationError（全て未入力は赤 'danger'、そのほかは 'warning'）、
    ID 不一致・パスワード違い・利用停止は ServiceError、ロック中は ValidationError('warning')。
    ロックの回数・期限の更新はこの中でコミットしてから例外を投げる。
    """
    label = LOGIN_ID_LABELS[role]
    login_id = (login_id or '').strip()
    password = password or ''
    _check_login_input(role, label, login_id, password)
    if role == User.ROLE_CITIZEN:
        # メールアドレスは大文字・小文字を区別しない（小文字で保存している）
        login_id = login_id.lower()

    user = db.session.execute(select(User).filter_by(login_id=login_id, role=role)).scalar_one_or_none()
    if user is None:
        # ID が存在するかどうかで応答時間が変わらないよう、偽のハッシュで同じ計算をする
        check_password_hash(_dummy_hash(), password)
        raise ServiceError(MSG_LOGIN_FAILED.format(label=label))

    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        # ロック中はパスワードを調べない（正しくても入れない。失敗を重ねても期限は延びない）
        raise _locked_error(_remaining_minutes(user.locked_until, now))

    if not user.check_password(password):
        # 必ず例外が返る（ここから先には進まない）
        raise _record_failure(user, now, label)
    if not user.is_active:
        raise ServiceError(MSG_ACCOUNT_DISABLED)

    _record_success(user, now)
    return user


def set_password_by_operator(login_id, password):
    """運用者が flask コマンドでパスワードを設定する（確認欄なし。8文字以上）。ロックも解除する

    公開されているテストアカウントのパスワード（TEST_ACCOUNT_PW）は使えない（ValidationError）。
    利用停止中（is_active=False）のアカウントは、パスワードを変えても利用停止のまま（自動では再開しない）。
    """
    errors = validate_operator_password(password)
    if errors:
        raise ValidationError(errors)
    user = db.session.execute(select(User).filter_by(login_id=(login_id or '').strip())).scalar_one_or_none()
    if user is None:
        raise ServiceError(MSG_USER_NOT_FOUND.format(login_id=login_id))
    user.set_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()
    return user


def _check_login_input(role, label, login_id, password):
    """ログイン画面の入力チェック（DB は見ない）。失敗回数には数えない"""
    if not login_id and not password:
        raise ValidationError([('danger', MSG_ID_OR_PASSWORD_REQUIRED.format(label=label))])
    errors = []
    if not login_id:
        errors.append(('warning', MSG_ID_REQUIRED.format(label=label)))
    elif role != User.ROLE_CITIZEN and '@' in login_id:
        # 職員・管理者の ID に @ は使えない（市民のメールアドレスと区別するため）
        errors.append(('warning', MSG_ID_HAS_AT.format(label=label)))
    errors.extend(validate_new_password(password))
    if errors:
        raise ValidationError(errors)


def _not_locked(now):
    """UPDATE の WHERE 条件：ロックされていない（期限なし、または期限切れ）"""
    return or_(User.locked_until.is_(None), User.locked_until <= now)


def _remaining_minutes(locked_until, now):
    """ロックの残り分数（切り上げ。最低 1 分）"""
    return max(1, math.ceil((locked_until - now).total_seconds() / 60))


def _locked_by_other_request(user_id, now):
    """読み出しのあとに別のリクエストがロックした場合の例外。残り分数は DB から読み直す"""
    db.session.rollback()
    locked_until = db.session.execute(select(User.locked_until).where(User.id == user_id)).scalar_one_or_none()
    return _locked_error(_remaining_minutes(locked_until, now) if locked_until is not None else 1)


def _record_failure(user, now, label):
    """パスワード違いを1回数え、投げるべき例外を返す（呼び出し側が raise する）

    上限に達したらロックして ValidationError('warning')、それ以外は ServiceError。
    複数ワーカーでも数え漏れがないよう、読み出さずに1本の UPDATE で加算とロックを同時に行う。
    WHERE にロックされていない条件も入れるので、先に別のリクエストがロックしていたら 0 行更新になり、
    その場合は回数を加算せずロック中の例外にする（失敗を重ねてもロックの期限は延びない）。
    上限に達した回は回数を 0 に戻してロックの期限を入れる（ロック明けは 0 回から数え直し）。
    """
    max_failures = current_app.config['LOGIN_MAX_FAILURES']
    lock_minutes = current_app.config['LOGIN_LOCK_MINUTES']
    user_id = user.id
    next_count = User.failed_login_count + 1
    reached = next_count >= max_failures
    count = db.session.execute(
        update(User).where(User.id == user_id, _not_locked(now))
        .values(failed_login_count=case((reached, 0), else_=next_count),
                locked_until=case((reached, now + timedelta(minutes=lock_minutes)), else_=User.locked_until))
        .returning(User.failed_login_count)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if count is None:
        return _locked_by_other_request(user_id, now)
    db.session.commit()
    if count == 0:
        current_app.logger.warning('ログイン失敗が続いたためアカウントを一時ロックしました user=%s', user_id)
        return _locked_error(lock_minutes)
    return ServiceError(MSG_LOGIN_FAILED.format(label=label))


def _record_success(user, now):
    """ログイン成功：失敗回数とロックを解除し、最終ログイン日時を記録する

    パスワードを調べている間に別のリクエストがロックした場合に入れてしまわないよう、
    WHERE にロックされていない条件を入れた1本の UPDATE で行う。0 行更新ならロック中の例外にする。
    """
    user_id = user.id
    updated = db.session.execute(
        update(User).where(User.id == user_id, _not_locked(now))
        .values(failed_login_count=0, locked_until=None, last_login_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if updated == 0:
        raise _locked_by_other_request(user_id, now)
    db.session.commit()


def _locked_error(minutes):
    """ロック中のメッセージ（オレンジ 'warning'）"""
    return ValidationError([('warning', MSG_LOGIN_LOCKED.format(minutes=minutes))])
