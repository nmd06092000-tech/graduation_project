# ログイン（入力チェック・認証・連続失敗による一時ロック）、パスワードの再設定・変更、
# 運用者（flask コマンド）によるパスワード設定・再設定コードの発行
import hashlib
import math
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from flask import current_app
from sqlalchemy import and_, case, delete, func, or_, select, update
from werkzeug.security import check_password_hash, generate_password_hash

from app.constants import (
    MAIL_RESET_BODY, MAIL_RESET_SUBJECT, MSG_ACCOUNT_DELETED_RESTORE_FIRST, MSG_ACCOUNT_DISABLED,
    MSG_CURRENT_PASSWORD_REQUIRED,
    MSG_CURRENT_PASSWORD_WRONG, MSG_EMAIL_NO_AT, MSG_EMAIL_REQUIRED, MSG_ID_HAS_AT, MSG_ID_OR_PASSWORD_REQUIRED,
    MSG_ID_REQUIRED, MSG_LOGIN_FAILED, MSG_LOGIN_LOCKED, MSG_PASSWORD_SAME_AS_CURRENT, MSG_RESET_CODE_INVALID,
    MSG_RESET_CODE_NOT_ALLOWED, MSG_RESET_CODE_REQUIRED, MSG_RESET_LINK_INVALID, MSG_USER_NOT_FOUND,
)
from app.extensions import db
from app.models import PasswordResetToken, User
from app.services import ServiceError, ValidationError
from app.utils import mail
from app.utils.mail import MailError
from app.utils.validators import validate_new_password, validate_operator_password

# ログイン画面の ID 欄の名前（メッセージの {label} に入る）
LOGIN_ID_LABELS = {
    User.ROLE_CITIZEN: 'メールアドレス',
    User.ROLE_STAFF: '職員ID',
    User.ROLE_ADMIN: '管理者ID',
}

# 市民への再設定メールの送りすぎ防止（同じアカウントに、前回から 60 秒以内・直近 1 時間に 3 回まで）
RESET_MAIL_MIN_INTERVAL = timedelta(seconds=60)
RESET_MAIL_MAX_PER_HOUR = 3
# 再設定コード（職員・管理者）：読み間違いやすい文字（0 O 1 I L）を除いた 31 種 × 10 文字（約 49 ビット）
RESET_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
RESET_CODE_LENGTH = 10


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
    ID 不一致・削除済み・パスワード違い・利用停止は ServiceError、ロック中は ValidationError('warning')。
    削除済みのアカウントは「ID がない」ときと同じ扱い（偽のハッシュで計算し、同じ MSG_LOGIN_FAILED。失敗回数は増やさない）。
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
    if user is None or user.is_deleted:
        # ID が存在するかどうか（削除済みを含む）で応答時間が変わらないよう、偽のハッシュで同じ計算をする
        check_password_hash(_dummy_hash(), password)
        raise ServiceError(MSG_LOGIN_FAILED.format(label=label))

    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        # ロック中はパスワードを調べない（正しくても入れない。失敗を重ねても期限は延びない）
        raise _locked_error(_remaining_minutes(user.locked_until, now))

    if not user.check_password(password):
        # 必ず例外が返る（ここから先には進まない）
        raise _record_failure(user, now, label)
    if not user.is_usable:
        # 削除済みは上で除外済みなので、ここに来るのは利用停止（is_active=False）だけ
        raise ServiceError(MSG_ACCOUNT_DISABLED)

    _record_success(user, now)
    return user


def is_locked(user):
    """ログインの一時ロック中か（locked_until が現在より先）。管理者のアカウント一覧の表示・ロック解除ボタンの判定に使う"""
    return user.locked_until is not None and user.locked_until > _now()


def invalidate_reset_tokens(user_id):
    """そのユーザーの未使用の再設定トークン・コードをすべて使用済みにする（コミットは呼び出し側。アカウントの削除・復元で使う）"""
    _invalidate_reset_tokens(user_id, _now())


def set_password_by_operator(login_id, password):
    """運用者が flask コマンドでパスワードを設定する（確認欄なし。8文字以上）。ロックも解除する

    公開されているテストアカウントのパスワード（TEST_ACCOUNT_PW）は使えない（ValidationError）。
    利用停止中（is_active=False）のアカウントは、パスワードを変えても利用停止のまま（自動では再開しない）。
    削除済みのアカウントは ServiceError(MSG_ACCOUNT_DELETED_RESTORE_FIRST)（先に restore-user で復元する）。
    """
    errors = validate_operator_password(password)
    if errors:
        raise ValidationError(errors)
    user = db.session.execute(select(User).filter_by(login_id=(login_id or '').strip())).scalar_one_or_none()
    if user is None:
        raise ServiceError(MSG_USER_NOT_FOUND.format(login_id=login_id))
    if user.is_deleted:
        raise ServiceError(MSG_ACCOUNT_DELETED_RESTORE_FIRST.format(login_id=user.login_id))
    user.set_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    db.session.commit()
    return user


def request_password_reset(email):
    """市民のパスワード再設定メールを送る（1 つ目の画面）。パスワード再設定 基本フロー2〜3

    入力エラー（未入力・@ なし）は ValidationError（'warning'）。それ以外は結果を返さない：
    メールアドレスが未登録・市民以外・利用停止中・送りすぎ（RESET_MAIL_*）・送信失敗のどれでも、呼び出し側は
    同じ MSG_RESET_MAIL_SENT を表示する（登録の有無が分からないように）。
    有効なトークンは 1 つだけ（古いものは使用済みにする）。メールはコミットのあとに送り、
    送信に失敗したら作ったトークンを消す（失敗を送信回数に数えない）。トークンそのものはログに出さない。
    """
    email = (email or '').strip().lower()
    if not email:
        raise ValidationError([('warning', MSG_EMAIL_REQUIRED)])
    if '@' not in email:
        raise ValidationError([('warning', MSG_EMAIL_NO_AT.format(email=email))])

    now = _now()
    # 同じアカウントへの同時リクエストで回数制限をすり抜けないよう、行ロックを取って順番に処理する
    user = db.session.execute(
        select(User).where(User.login_id == email, User.role == User.ROLE_CITIZEN, User.is_usable).with_for_update()
    ).scalar_one_or_none()
    if user is None or _reset_mail_throttled(user.id, now):
        db.session.rollback()
        return

    user_id, name = user.id, user.name
    _invalidate_reset_tokens(user_id, now)
    raw_token = secrets.token_urlsafe(32)
    token = PasswordResetToken(
        user_id=user_id, token_hash=_hash_secret(raw_token), created_at=now,
        expires_at=now + timedelta(minutes=current_app.config['PASSWORD_RESET_TOKEN_MINUTES']))
    db.session.add(token)
    db.session.commit()
    token_id = token.id

    try:
        mail.send_mail(email, MAIL_RESET_SUBJECT, MAIL_RESET_BODY.format(
            name=name, url=_reset_link(raw_token), minutes=current_app.config['PASSWORD_RESET_TOKEN_MINUTES']))
    except MailError as e:
        current_app.logger.error('パスワード再設定メールを送信できませんでした user=%s 原因=%s', user_id, e)
        db.session.execute(delete(PasswordResetToken).where(PasswordResetToken.id == token_id))
        db.session.commit()


def is_reset_token_valid(token):
    """メールのリンクのトークンがまだ使えるか（未使用・期限内・市民の有効なアカウント）。表示用で、消費はしない"""
    if not token:
        return False
    return db.session.execute(
        select(PasswordResetToken.id).where(_usable_mail_token(_hash_secret(token), _now()))
    ).first() is not None


def reset_password_with_token(token, new_password, confirm_password):
    """メールのリンクのトークンで市民のパスワードを再設定する（2 つ目の画面）。パスワード再設定 基本フロー5〜6

    パスワードの入力チェックを先に行う（入力ミスでトークンを使い切らない）→ トークンを 1 本の UPDATE で
    消費する（同時に 2 回使えない）→ 市民の有効なアカウントのパスワードを変更し、失敗回数・ロックを解除し、
    ほかのトークンも使用済みにして 1 回でコミットする。
    入力エラーは ValidationError、トークンが無効・期限切れ・使用済みは ServiceError(MSG_RESET_LINK_INVALID)。
    """
    errors = validate_operator_password(new_password, confirm_password)
    if errors:
        raise ValidationError(errors)

    now = _now()
    user_id = db.session.execute(
        update(PasswordResetToken).where(_usable_mail_token(_hash_secret(token or ''), now))
        .values(used_at=now).returning(PasswordResetToken.user_id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if user_id is None:
        db.session.rollback()
        raise ServiceError(MSG_RESET_LINK_INVALID)

    user = db.session.get(User, user_id)
    _apply_new_password(user, new_password, now)
    db.session.commit()
    return user


def issue_reset_code(login_id):
    """職員・管理者の再設定コードを発行する（flask --app run issue-reset-code）。返り値は (表示用のコード, 有効期限)

    有効な職員・管理者アカウントだけが対象（それ以外は ServiceError(MSG_RESET_CODE_NOT_ALLOWED)）。
    コードは 1 回だけ使える・PASSWORD_RESET_TOKEN_MINUTES 分有効で、DB にはハッシュだけを保存する。
    そのアカウントの未使用のコード・トークンは使用済みにする（有効なのは常に最新の 1 つ）。
    表示用のコードは「XXXXX-XXXXX」の形（入力時はハイフン・空白・小文字を無視する）。
    """
    login_id = (login_id or '').strip()
    now = _now()
    user = db.session.execute(
        select(User).where(User.login_id == login_id, User.role.in_((User.ROLE_STAFF, User.ROLE_ADMIN)),
                           User.is_usable).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        db.session.rollback()
        raise ServiceError(MSG_RESET_CODE_NOT_ALLOWED.format(login_id=login_id))

    _invalidate_reset_tokens(user.id, now)
    code = ''.join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(RESET_CODE_LENGTH))
    expires_at = now + timedelta(minutes=current_app.config['PASSWORD_RESET_TOKEN_MINUTES'])
    db.session.add(PasswordResetToken(
        user_id=user.id, token_hash=_hash_secret(code), created_at=now, expires_at=expires_at))
    db.session.commit()
    half = RESET_CODE_LENGTH // 2
    return f'{code[:half]}-{code[half:]}', expires_at


def reset_password_with_code(role, login_id, code, new_password, confirm_password):
    """再設定コードで職員・管理者のパスワードを再設定する（role は職員の画面なら staff、管理者の画面なら admin）

    入力チェック（ID・コード・パスワード。失敗回数には数えない）→ ID と役割が一致する有効なアカウントを探す →
    ロック中なら ValidationError('warning')（ログインと同じ）→ コードを 1 本の UPDATE で消費する。
    アカウントがない・役割が違う・利用停止・コードが違う・期限切れ・使用済みは、同じ
    ServiceError(MSG_RESET_CODE_INVALID)（ID の存在が分からないように）。アカウントがあってコードが違うときは
    ログインと同じ失敗回数（_record_failure）に数え、上限でロックする。成功したらパスワードを変更し、ロックを解除する。
    """
    if role not in (User.ROLE_STAFF, User.ROLE_ADMIN):
        raise ValueError(f'再設定コードは職員・管理者だけが使えます: {role!r}')
    label = LOGIN_ID_LABELS[role]
    login_id = (login_id or '').strip()
    code = _normalize_code(code)

    errors = []
    if not login_id:
        errors.append(('warning', MSG_ID_REQUIRED.format(label=label)))
    elif '@' in login_id:
        errors.append(('warning', MSG_ID_HAS_AT.format(label=label)))
    if not code:
        errors.append(('warning', MSG_RESET_CODE_REQUIRED))
    errors.extend(validate_operator_password(new_password, confirm_password))
    if errors:
        raise ValidationError(errors)

    now = _now()
    user = db.session.execute(select(User).filter_by(login_id=login_id, role=role)).scalar_one_or_none()
    if user is None or not user.is_usable:
        raise ServiceError(MSG_RESET_CODE_INVALID)
    if user.locked_until is not None and user.locked_until > now:
        raise _locked_error(_remaining_minutes(user.locked_until, now))

    consumed = db.session.execute(
        update(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.token_hash == _hash_secret(code),
            PasswordResetToken.used_at.is_(None), PasswordResetToken.expires_at > now)
        .values(used_at=now).returning(PasswordResetToken.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if consumed is None:
        # 必ず例外が返る（ここから先には進まない）
        raise _record_failure(user, now, label, message=MSG_RESET_CODE_INVALID)

    _apply_new_password(user, new_password, now)
    db.session.commit()
    return user


def change_password(user_id, *, current_password, new_password, confirm_password):
    """ログイン中のユーザーが自分のパスワードを変更する。パスワード変更 基本フロー2〜3

    順序：入力チェック（現在のパスワード未入力・新しいパスワードの規則と確認欄。失敗回数には数えない）→
    ロック中なら ValidationError('warning') → 現在のパスワードの確認（違えば _record_failure で失敗回数に数える）→
    新しいパスワードが現在と同じなら ValidationError。成功したら失敗回数・ロックを解除し、未使用の再設定トークンも
    使用済みにして 1 回でコミットする。呼び出し側（ルート）は、実行中のセッションだけが残るよう再ログインさせる
    （パスワードの印が変わるので、ほかのセッションは無効になる）。
    """
    current_password = current_password or ''
    errors = []
    if not current_password:
        errors.append(('warning', MSG_CURRENT_PASSWORD_REQUIRED))
    errors.extend(validate_operator_password(new_password, confirm_password))
    if errors:
        raise ValidationError(errors)

    user = db.session.get(User, user_id)
    if user is None or not user.is_usable:
        raise ServiceError(MSG_ACCOUNT_DISABLED)
    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        raise _locked_error(_remaining_minutes(user.locked_until, now))
    if not user.check_password(current_password):
        # 必ず例外が返る（ここから先には進まない）
        raise _record_failure(user, now, LOGIN_ID_LABELS[user.role], message=MSG_CURRENT_PASSWORD_WRONG)
    if new_password == current_password:
        raise ValidationError([('warning', MSG_PASSWORD_SAME_AS_CURRENT)])

    _apply_new_password(user, new_password, now)
    db.session.commit()
    return user


def _hash_secret(value):
    """トークン・再設定コードを DB に保存する形（SHA-256 の 16 進数 64 文字）にする"""
    return hashlib.sha256(value.encode()).hexdigest()


def _normalize_code(code):
    """入力された再設定コードをそろえる（大文字にし、ハイフン・空白などを除く）"""
    return ''.join(ch for ch in (code or '').upper() if ch.isalnum())


def _reset_link(raw_token):
    """メールに書く再設定ページの URL。元は PUBLIC_BASE_URL だけ（リクエストの Host は使わない）"""
    path = current_app.url_map.bind('localhost').build('auth.reset_password_confirm', {'token': raw_token})
    return f"{current_app.config['PUBLIC_BASE_URL']}{path}"


def _usable_mail_token(token_hash, now):
    """UPDATE / SELECT の WHERE 条件：未使用・期限内で、持ち主が使える（利用停止・削除済みでない）市民のトークン

    職員・管理者のコードは含めない。
    """
    return and_(PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
                PasswordResetToken.user_id.in_(
                    select(User.id).where(User.role == User.ROLE_CITIZEN, User.is_usable)))


def _reset_mail_throttled(user_id, now):
    """再設定メールの送りすぎか（前回から RESET_MAIL_MIN_INTERVAL 以内、または直近 1 時間に RESET_MAIL_MAX_PER_HOUR 回）"""
    latest, count = db.session.execute(
        select(func.max(PasswordResetToken.created_at), func.count()).where(
            PasswordResetToken.user_id == user_id, PasswordResetToken.created_at > now - timedelta(hours=1))
    ).one()
    return count >= RESET_MAIL_MAX_PER_HOUR or (latest is not None and latest > now - RESET_MAIL_MIN_INTERVAL)


def _invalidate_reset_tokens(user_id, now):
    """そのユーザーの未使用の再設定トークン・コードをすべて使用済みにする（コミットは呼び出し側）"""
    db.session.execute(
        update(PasswordResetToken).where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now).execution_options(synchronize_session=False))


def _apply_new_password(user, new_password, now):
    """パスワードを変更し、ログインの失敗回数・ロックを解除し、未使用の再設定トークン・コードを使用済みにする

    コミットは呼び出し側。パスワードの印が変わるので、変更前のセッション・「ログイン状態を保持する」Cookie は無効になる。
    """
    user.set_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    _invalidate_reset_tokens(user.id, now)


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


def _record_failure(user, now, label, *, message=MSG_LOGIN_FAILED):
    """パスワード（再設定コード・変更時の現在のパスワード）違いを1回数え、投げるべき例外を返す（呼び出し側が raise する）

    上限に達したらロックして ValidationError('warning')、それ以外は ServiceError(message)。
    message は既定でログイン失敗の文言（{label} を ID 欄の名前に置き換える）。
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
    return ServiceError(message.format(label=label))


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
