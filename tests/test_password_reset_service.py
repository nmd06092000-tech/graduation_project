# パスワード再設定・変更（GĐ2b）の service テスト：auth_service（トークン・再設定コード・change_password）と
# ユーザーのセッションの印（User.get_id / load_user）。メールは outbox（モック）で受け、SMTP にはつながない
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app.constants import (
    MSG_ACCOUNT_DISABLED, MSG_CURRENT_PASSWORD_REQUIRED, MSG_CURRENT_PASSWORD_WRONG, MSG_EMAIL_NO_AT,
    MSG_EMAIL_REQUIRED, MSG_ID_HAS_AT, MSG_ID_REQUIRED, MSG_LOGIN_LOCKED, MSG_PASSWORD_MISMATCH,
    MSG_NEW_PASSWORD_CONFIRM_REQUIRED, MSG_NEW_PASSWORD_REQUIRED,
    MSG_PASSWORD_PUBLIC, MSG_PASSWORD_REQUIRED, MSG_PASSWORD_SAME_AS_CURRENT, MSG_PASSWORD_TOO_SHORT, MSG_RESET_CODE_INVALID,
    MSG_RESET_CODE_NOT_ALLOWED, MSG_RESET_CODE_REQUIRED, MSG_RESET_LINK_INVALID,
)
from app.extensions import db
from app.models import PasswordResetToken, User
from app.models.user import load_user
from app.services import ServiceError, ValidationError, auth_service
from app.utils.mail import MailError
from tests.conftest import _make_user, sent_reset_token

NEW_PW = 'NewPass-9876'
CODE_FORMAT = re.compile(r'^[A-HJKMNP-Z2-9]{5}-[A-HJKMNP-Z2-9]{5}$')


def _tokens(user_id):
    return PasswordResetToken.query.filter_by(user_id=user_id).order_by(PasswordResetToken.id).all()


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _request(email='taro@example.com', password=NEW_PW):
    auth_service.request_password_reset(email, password, password)


def _pending(user_id):
    """そのユーザーのトークンの確認待ちハッシュ（id 順）"""
    db.session.expire_all()
    return [t.pending_password_hash for t in _tokens(user_id)]


def _lock_message(minutes):
    return MSG_LOGIN_LOCKED.format(minutes=minutes)


@pytest.fixture
def operator_staff(app):
    return _make_user('staff01', '鈴木 健一', User.ROLE_STAFF)


@pytest.fixture
def operator_admin(app):
    return _make_user('admin01', '佐藤 管理', User.ROLE_ADMIN)


# ---------------------------------------------------------------------------
# request_password_reset：入力チェック・メールを送る/送らない・保存される形
# ---------------------------------------------------------------------------

def test_request_reset_creates_hashed_token_and_sends_link(citizen, clock, outbox):
    # Act
    result = auth_service.request_password_reset('taro@example.com', NEW_PW, NEW_PW)

    # Assert: 返り値なし。メールは本人宛て・件名と本文に再設定リンク
    assert result is None
    outbox.assert_called_once()
    to, subject, body = outbox.call_args.args
    assert to == 'taro@example.com'
    assert '再設定' in subject
    raw = sent_reset_token(outbox)
    assert f'http://localhost:5000/reset-password/confirm/{raw}' in body
    assert '山田 太郎' in body
    # DB には SHA-256 のハッシュだけ（平文のトークンは残らない）・有効期限 30 分
    [token] = _tokens(citizen.id)
    assert token.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert len(token.token_hash) == 64 and raw not in token.token_hash
    assert token.expires_at == clock.now + timedelta(minutes=30)
    assert token.used_at is None


def test_request_reset_stores_only_hash_of_pending_password_and_keeps_current_password(citizen, outbox):
    # Act
    _request()

    # Assert: 確認待ちはハッシュ（平文ではない）。ユーザーのパスワードはまだ変わらない
    [token] = _tokens(citizen.id)
    assert token.pending_password_hash and NEW_PW not in token.pending_password_hash
    assert check_password_hash(token.pending_password_hash, NEW_PW)
    fresh = _fresh(citizen.id)
    assert fresh.check_password('password123') and not fresh.check_password(NEW_PW)
    assert auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', 'password123').id == citizen.id
    with pytest.raises(ServiceError):
        auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', NEW_PW)


def test_request_reset_mail_body_does_not_contain_password(citizen, outbox):
    # Act
    _request()

    # Assert: 件名・本文にパスワードも確認待ちのハッシュも入らない。確定のボタン名・「開くだけでは変更されない」旨はある
    _, subject, body = outbox.call_args.args
    [token] = _tokens(citizen.id)
    assert NEW_PW not in body and NEW_PW not in subject
    assert token.pending_password_hash not in body
    assert 'パスワードの変更を確定する' in body and 'リンクを開くだけでは変更されません' in body


def test_request_reset_normalizes_email_case_and_spaces(citizen, outbox):
    # Act
    auth_service.request_password_reset('  Taro@Example.COM ', NEW_PW, NEW_PW)

    # Assert
    assert outbox.call_args.args[0] == 'taro@example.com'
    assert len(_tokens(citizen.id)) == 1


@pytest.mark.parametrize('email, new, confirm, expected', [
    ('', NEW_PW, NEW_PW, [('warning', MSG_EMAIL_REQUIRED)]),
    ('   ', NEW_PW, NEW_PW, [('warning', MSG_EMAIL_REQUIRED)]),
    (None, NEW_PW, NEW_PW, [('warning', MSG_EMAIL_REQUIRED)]),
    ('taro.example.com', NEW_PW, NEW_PW, [('warning', MSG_EMAIL_NO_AT.format(email='taro.example.com'))]),
    ('taro@example.com', '', '', [('warning', MSG_NEW_PASSWORD_REQUIRED), ('warning', MSG_NEW_PASSWORD_CONFIRM_REQUIRED)]),
    ('taro@example.com', None, None, [('warning', MSG_NEW_PASSWORD_REQUIRED), ('warning', MSG_NEW_PASSWORD_CONFIRM_REQUIRED)]),
    # 両方空なら 2 件とも出す。確認欄だけ空なら確認欄の文言だけ
    ('taro@example.com', NEW_PW, '', [('warning', MSG_NEW_PASSWORD_CONFIRM_REQUIRED)]),
    ('taro@example.com', 'short', 'short', [('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
    ('taro@example.com', NEW_PW, NEW_PW + 'x', [('warning', MSG_PASSWORD_MISMATCH)]),
    ('taro@example.com', 'password123', 'password123', [('warning', MSG_PASSWORD_PUBLIC)]),
    # メールとパスワードの誤りは 1 つの ValidationError にまとめる
    ('', 'short', 'x', [('warning', MSG_EMAIL_REQUIRED), ('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
    ('taro.example.com', NEW_PW, 'different-pw',
     [('warning', MSG_EMAIL_NO_AT.format(email='taro.example.com')), ('warning', MSG_PASSWORD_MISMATCH)]),
])
def test_request_reset_input_errors_are_collected_and_create_nothing(citizen, outbox, email, new, confirm, expected):
    # Act
    with pytest.raises(ValidationError) as e:
        auth_service.request_password_reset(email, new, confirm)

    # Assert: トークンもメールもなし。パスワードは変わらない
    assert e.value.messages == expected
    outbox.assert_not_called()
    assert _tokens(citizen.id) == []
    assert _fresh(citizen.id).check_password('password123')


def test_request_reset_input_error_keeps_existing_valid_token(citizen, clock, outbox):
    # Arrange: すでに有効な申請がある
    _request()
    raw = sent_reset_token(outbox)
    clock.advance(seconds=61)

    # Act: 入力ミスの申請
    with pytest.raises(ValidationError):
        auth_service.request_password_reset('taro@example.com', 'short', 'short')

    # Assert: 前のリンクはそのまま使える（入力エラーでは無効化しない）
    assert auth_service.is_reset_token_valid(raw) is True
    assert outbox.call_count == 1


def test_request_reset_unknown_email_is_silent(citizen, outbox):
    # Act: 例外にも返り値にもならず、メールもトークンも作らない（登録の有無が分からない）
    result = auth_service.request_password_reset('nobody@example.com', NEW_PW, NEW_PW)

    # Assert
    assert result is None
    outbox.assert_not_called()
    assert PasswordResetToken.query.count() == 0


def test_request_reset_inactive_citizen_gets_no_mail(citizen, outbox):
    # Arrange
    citizen.is_active = False
    db.session.commit()

    # Act
    assert auth_service.request_password_reset('taro@example.com', NEW_PW, NEW_PW) is None

    # Assert
    outbox.assert_not_called()
    assert PasswordResetToken.query.count() == 0


@pytest.mark.parametrize('role', [User.ROLE_STAFF, User.ROLE_ADMIN])
def test_request_reset_non_citizen_with_email_like_id_gets_no_mail(app, outbox, role):
    # Arrange: 役割が市民ではないアカウント（ID が @ 付きでも市民用の画面では対象外）
    operator = _make_user('ops@example.com', '運用', role)

    # Act
    auth_service.request_password_reset('ops@example.com', NEW_PW, NEW_PW)

    # Assert: 結果は返さず、トークンもメールもなし。パスワードも変わらない
    outbox.assert_not_called()
    assert _tokens(operator.id) == []
    assert _fresh(operator.id).check_password('password123')


def test_request_reset_new_token_invalidates_old_one(citizen, clock, outbox):
    # Arrange
    _request()
    old = sent_reset_token(outbox)
    clock.advance(seconds=61)

    # Act
    _request()
    new = sent_reset_token(outbox)

    # Assert: 未使用で残るのは最新の 1 つだけ。古いリンクは使えない
    assert old != new
    assert len([t for t in _tokens(citizen.id) if t.used_at is None]) == 1
    assert auth_service.is_reset_token_valid(old) is False
    assert auth_service.is_reset_token_valid(new) is True
    # 古いトークンの確認待ちハッシュは消え、新しいほうだけが持つ
    first, second = _tokens(citizen.id)
    db.session.expire_all()
    assert first.pending_password_hash is None and second.pending_password_hash is not None


def test_request_reset_new_password_of_second_request_is_the_one_applied(citizen, clock, outbox):
    # Arrange: 1 回目は NEW_PW、2 回目は別のパスワードで申請
    _request(password=NEW_PW)
    old = sent_reset_token(outbox)
    clock.advance(seconds=61)
    _request(password='Second-pw-1')
    new = sent_reset_token(outbox)

    # Act / Assert: 古いリンクでは何も変わらず、新しいリンクで 2 回目のパスワードになる
    with pytest.raises(ServiceError):
        auth_service.confirm_password_reset(old)
    assert _fresh(citizen.id).check_password('password123')
    auth_service.confirm_password_reset(new)
    fresh = _fresh(citizen.id)
    assert fresh.check_password('Second-pw-1') and not fresh.check_password(NEW_PW)


# ---------------------------------------------------------------------------
# 送りすぎの制限：60 秒の間隔・1 時間に 3 回まで
# ---------------------------------------------------------------------------

def test_request_reset_within_60_seconds_sends_nothing(citizen, clock, outbox):
    # Arrange
    _request()
    clock.advance(seconds=59)

    # Act: 例外にならず（同じ表示になる）、メールも新しいトークンも作らない
    _request()

    # Assert
    assert outbox.call_count == 1
    assert len(_tokens(citizen.id)) == 1


def test_request_reset_allowed_again_after_60_seconds(citizen, clock, outbox):
    # Arrange
    _request()
    clock.advance(seconds=60)

    # Act
    _request()

    # Assert
    assert outbox.call_count == 2


def test_request_reset_limited_to_3_per_hour(citizen, clock, outbox):
    # Arrange: 61 秒おきに 3 回送る
    for _ in range(3):
        _request()
        clock.advance(seconds=61)

    # Act: 4 回目（最初から 183 秒後）
    _request()

    # Assert: 送られない
    assert outbox.call_count == 3
    assert len(_tokens(citizen.id)) == 3


def test_request_reset_allowed_again_when_oldest_is_over_an_hour_old(citizen, clock, outbox):
    # Arrange
    for _ in range(3):
        _request()
        clock.advance(seconds=61)
    # Act: 最初のトークンから 1 時間以上たつと直近 1 時間は 2 回になる
    clock.advance(hours=1)
    _request()

    # Assert
    assert outbox.call_count == 4


def test_request_reset_throttle_is_per_account(citizen, clock, outbox):
    # Arrange: 別の市民
    other = _make_user('hana@example.com', '山田 花子', User.ROLE_CITIZEN)
    _request()

    # Act: すぐに別アカウントへ
    _request('hana@example.com')

    # Assert
    assert outbox.call_count == 2
    assert len(_tokens(other.id)) == 1


def test_request_reset_mail_failure_deletes_token_and_stays_silent(citizen, clock, outbox, caplog):
    # Arrange: 送信は MailError。本文からトークンを取っておき、ログに出ていないことも確かめる
    captured = {}

    def _fail(to, subject, body):
        captured['body'] = body
        raise MailError('SMTPException')
    outbox.side_effect = _fail

    # Act
    with caplog.at_level('DEBUG'):
        assert auth_service.request_password_reset('taro@example.com', NEW_PW, NEW_PW) is None

    # Assert: 作ったトークン（確認待ちハッシュごと）は消え、リンクもパスワードもログに残らない。パスワードは変わらない
    assert _tokens(citizen.id) == []
    raw = re.search(r'/reset-password/confirm/([A-Za-z0-9_-]+)', captured['body']).group(1)
    assert raw not in caplog.text and NEW_PW not in caplog.text
    assert _fresh(citizen.id).check_password('password123')
    assert 'パスワード再設定メールを送信できませんでした' in caplog.text
    # 失敗は回数に数えない：すぐにもう一度試せる
    outbox.side_effect = None
    _request()
    assert len(_tokens(citizen.id)) == 1


# ---------------------------------------------------------------------------
# is_reset_token_valid
# ---------------------------------------------------------------------------

def test_is_reset_token_valid_true_for_fresh_token_and_does_not_consume(citizen, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)

    # Act / Assert: 何度調べても使用済みにならない
    assert auth_service.is_reset_token_valid(raw) is True
    assert auth_service.is_reset_token_valid(raw) is True
    assert _tokens(citizen.id)[0].used_at is None


@pytest.mark.parametrize('value', ['', None, 'garbage', 'A' * 43])
def test_is_reset_token_valid_false_for_unknown_values(citizen, outbox, value):
    # Arrange
    _request()

    # Act / Assert
    assert auth_service.is_reset_token_valid(value) is False


def test_is_reset_token_valid_false_after_expiry(citizen, clock, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)

    # Act / Assert: 29 分 59 秒はまだ有効、ちょうど 30 分で無効（expires_at > now）
    clock.advance(minutes=29, seconds=59)
    assert auth_service.is_reset_token_valid(raw) is True
    clock.advance(seconds=1)
    assert auth_service.is_reset_token_valid(raw) is False


def test_is_reset_token_valid_false_when_owner_is_inactive(citizen, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    citizen.is_active = False
    db.session.commit()

    # Act / Assert
    assert auth_service.is_reset_token_valid(raw) is False


def test_is_reset_token_valid_false_for_staff_reset_code(operator_staff):
    # Arrange: 職員の再設定コードを市民のリンクのトークンとして使えない
    code, _ = auth_service.issue_reset_code('staff01')

    # Act / Assert
    assert auth_service.is_reset_token_valid(code) is False
    assert auth_service.is_reset_token_valid(code.replace('-', '')) is False


# ---------------------------------------------------------------------------
# confirm_password_reset（メールのリンクの確認画面で「確定する」を押したとき）
# ---------------------------------------------------------------------------

def _mail_token(user_id, raw, clock, *, pending='hash', used_at=None, minutes=30):
    """メールのリンク相当のトークンを手で作る（pending=None で「確認待ちのパスワードなし」）"""
    pending_hash = generate_password_hash(NEW_PW) if pending == 'hash' else pending
    token = PasswordResetToken(
        user_id=user_id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), created_at=clock.now,
        expires_at=clock.now + timedelta(minutes=minutes), used_at=used_at, pending_password_hash=pending_hash)
    db.session.add(token)
    db.session.commit()
    return token


def test_confirm_reset_changes_password_and_consumes_token(citizen, clock, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    clock.advance(minutes=5)

    # Act
    user = auth_service.confirm_password_reset(raw)

    # Assert: 申請のときのパスワードが有効になり、古いパスワードは使えない。トークンは使用済み・ハッシュは NULL
    assert user.id == citizen.id
    fresh = _fresh(citizen.id)
    assert fresh.check_password(NEW_PW) and not fresh.check_password('password123')
    [token] = _tokens(citizen.id)
    assert token.used_at == clock.now and token.pending_password_hash is None
    assert auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', NEW_PW).id == citizen.id
    with pytest.raises(ServiceError):
        auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', 'password123')


def test_confirm_reset_applies_the_stored_hash_itself(citizen, outbox):
    # Arrange
    _request()
    [token] = _tokens(citizen.id)
    stored = token.pending_password_hash

    # Act
    auth_service.confirm_password_reset(sent_reset_token(outbox))

    # Assert: 確認待ちのハッシュがそのままユーザーのハッシュになる（再ハッシュしない）
    assert _fresh(citizen.id).password_hash == stored


def test_is_reset_token_valid_does_not_change_password(citizen, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)

    # Act
    assert auth_service.is_reset_token_valid(raw) is True

    # Assert: 確認画面の表示用の判定では何も変わらない
    assert _fresh(citizen.id).check_password('password123')
    assert _pending(citizen.id)[0] is not None


def test_confirm_reset_cannot_be_used_twice(citizen, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    auth_service.confirm_password_reset(raw)
    changed_hash = _fresh(citizen.id).password_hash

    # Act: 同じリンクをもう一度
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset(raw)

    # Assert
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(citizen.id).password_hash == changed_hash
    assert auth_service.is_reset_token_valid(raw) is False


def test_confirm_reset_rejected_when_expired(citizen, clock, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    clock.advance(minutes=30)

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset(raw)

    # Assert
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(citizen.id).check_password('password123')


def test_confirm_reset_still_valid_just_before_expiry(citizen, clock, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    clock.advance(minutes=29, seconds=59)

    # Act
    auth_service.confirm_password_reset(raw)

    # Assert
    assert _fresh(citizen.id).check_password(NEW_PW)


@pytest.mark.parametrize('token', ['', None, 'unknown-token'])
def test_confirm_reset_rejects_unknown_token(citizen, token):
    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset(token)

    # Assert
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(citizen.id).check_password('password123')


def test_confirm_reset_old_token_is_invalid_after_new_request(citizen, clock, outbox):
    # Arrange
    _request()
    old = sent_reset_token(outbox)
    clock.advance(seconds=61)
    _request()

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset(old)

    # Assert
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(citizen.id).check_password('password123')


def test_confirm_reset_rejects_token_without_pending_hash(citizen, clock):
    # Arrange: 確認待ちのパスワードがない（仕組みが変わる前に送ったメールのトークン相当）
    _mail_token(citizen.id, 'legacy-token-value', clock, pending=None)

    # Act / Assert: 表示用の判定も確定も無効。パスワードは変わらず、トークンも消費されない
    assert auth_service.is_reset_token_valid('legacy-token-value') is False
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset('legacy-token-value')
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(citizen.id).check_password('password123')
    assert _tokens(citizen.id)[0].used_at is None


def test_confirm_reset_rejects_already_used_token_even_with_pending_hash(citizen, clock):
    # Arrange: 使用済みなのにハッシュが残っている行（あってはならないが、使えてはいけない）
    _mail_token(citizen.id, 'used-token-value', clock, used_at=clock.now)

    # Act / Assert
    assert auth_service.is_reset_token_valid('used-token-value') is False
    with pytest.raises(ServiceError):
        auth_service.confirm_password_reset('used-token-value')
    assert _fresh(citizen.id).check_password('password123')


def test_confirm_reset_clears_lock_failures_and_other_tokens_and_hashes(citizen, clock, outbox):
    # Arrange: ロック中・失敗 3 回・別の未使用トークン（確認待ちハッシュ付きも含め全部）が残っている
    _request()
    raw = sent_reset_token(outbox)
    citizen.failed_login_count = 3
    citizen.locked_until = clock.now + timedelta(minutes=10)
    db.session.add(PasswordResetToken(user_id=citizen.id, token_hash='f' * 64, expires_at=clock.now + timedelta(hours=1),
                                      pending_password_hash=generate_password_hash('Other-pw-1')))
    db.session.commit()

    # Act
    auth_service.confirm_password_reset(raw)

    # Assert: ロック解除・回数 0、未使用のトークンは残らず、確認待ちのハッシュはすべて NULL。その場でログインできる
    fresh = _fresh(citizen.id)
    assert fresh.failed_login_count == 0 and fresh.locked_until is None
    assert all(t.used_at is not None for t in _tokens(citizen.id))
    assert _pending(citizen.id) == [None, None]
    assert auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', NEW_PW).id == citizen.id


def test_confirm_reset_invalidates_old_session_value(citizen, outbox):
    # Arrange: 確定前のセッションの印
    old_value = citizen.get_id()
    _request()
    assert load_user(old_value).id == citizen.id

    # Act
    auth_service.confirm_password_reset(sent_reset_token(outbox))

    # Assert: パスワードの印が変わるので、古いセッションは無効
    db.session.expire_all()
    assert load_user(old_value) is None


def test_request_reset_alone_does_not_invalidate_old_session(citizen, outbox):
    # Arrange
    old_value = citizen.get_id()

    # Act: 申請だけ（確定していない）
    _request()

    # Assert: 元のセッションはそのまま使える
    db.session.expire_all()
    assert load_user(old_value).id == citizen.id


def test_confirm_reset_rejected_when_owner_became_inactive(citizen, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    citizen.is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset(raw)

    # Assert
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(citizen.id).check_password('password123')


def test_confirm_reset_rejects_staff_reset_code(operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.confirm_password_reset(code)

    # Assert
    assert str(e.value) == MSG_RESET_LINK_INVALID
    assert _fresh(operator_staff.id).check_password('password123')


def test_reset_code_tokens_have_no_pending_hash(operator_staff):
    # Act
    auth_service.issue_reset_code('staff01')

    # Assert: 職員・管理者の再設定コードは確認待ちのハッシュを持たない
    assert _pending(operator_staff.id) == [None]


def test_issue_reset_code_does_not_touch_citizen_pending_token(citizen, operator_staff, outbox):
    # Arrange: 市民の確認待ちトークン
    _request()

    # Act: 職員のコード発行は職員のトークンだけを無効にする（市民の確認待ちには触れない）
    auth_service.issue_reset_code('staff01')

    # Assert
    assert _pending(citizen.id)[0] is not None


def test_confirm_reset_succeeds_only_once_under_concurrency(app, citizen, outbox):
    # Arrange: 同じリンクを同時に 2 つのリクエスト（別接続）から使う
    _request()
    raw = sent_reset_token(outbox)
    barrier = Barrier(2)

    def _attempt(_):
        with app.app_context():
            try:
                barrier.wait(timeout=10)
                auth_service.confirm_password_reset(raw)
                return 'ok'
            except ServiceError as e:
                return str(e)
            finally:
                db.session.remove()

    # Act
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_attempt, [0, 1]))

    # Assert: 成功は 1 回だけ。もう 1 つはリンク無効。申請のパスワードに変わり、ハッシュは NULL
    assert sorted(results) == sorted(['ok', MSG_RESET_LINK_INVALID])
    assert _tokens(citizen.id)[0].used_at is not None
    assert _fresh(citizen.id).check_password(NEW_PW)
    assert _pending(citizen.id) == [None]


# ---------------------------------------------------------------------------
# issue_reset_code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('login_id, role', [('staff01', User.ROLE_STAFF), ('admin01', User.ROLE_ADMIN)])
def test_issue_reset_code_for_staff_and_admin(clock, operator_staff, operator_admin, login_id, role):
    # Act
    code, expires_at = auth_service.issue_reset_code(f' {login_id} ')

    # Assert: XXXXX-XXXXX の形・30 分有効・DB にはハッシュだけ
    assert CODE_FORMAT.match(code)
    assert expires_at == clock.now + timedelta(minutes=30)
    user = User.query.filter_by(login_id=login_id).one()
    [token] = _tokens(user.id)
    assert token.token_hash == hashlib.sha256(code.replace('-', '').encode()).hexdigest()
    assert token.expires_at == expires_at and token.used_at is None


def test_issue_reset_code_is_random_each_time(operator_staff):
    # Act
    codes = {auth_service.issue_reset_code('staff01')[0] for _ in range(5)}

    # Assert
    assert len(codes) == 5


def test_issue_reset_code_invalidates_previous_code(operator_staff):
    # Arrange
    first, _ = auth_service.issue_reset_code('staff01')

    # Act
    second, _ = auth_service.issue_reset_code('staff01')

    # Assert: 古いコードは使えず、新しいコードだけ使える
    with pytest.raises(ServiceError):
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', first, NEW_PW, NEW_PW)
    auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', second, NEW_PW, NEW_PW)


def test_issue_reset_code_refuses_citizen(citizen):
    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.issue_reset_code('taro@example.com')

    # Assert
    assert str(e.value) == MSG_RESET_CODE_NOT_ALLOWED.format(login_id='taro@example.com')
    assert PasswordResetToken.query.count() == 0


@pytest.mark.parametrize('login_id', ['nobody', '', '  '])
def test_issue_reset_code_refuses_unknown_id(operator_staff, login_id):
    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.issue_reset_code(login_id)

    # Assert
    assert str(e.value) == MSG_RESET_CODE_NOT_ALLOWED.format(login_id=login_id.strip())
    assert PasswordResetToken.query.count() == 0


def test_issue_reset_code_refuses_inactive_account(operator_staff):
    # Arrange
    operator_staff.is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.issue_reset_code('staff01')

    # Assert
    assert str(e.value) == MSG_RESET_CODE_NOT_ALLOWED.format(login_id='staff01')


# ---------------------------------------------------------------------------
# reset_password_with_code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('role, login_id', [(User.ROLE_STAFF, 'staff01'), (User.ROLE_ADMIN, 'admin01')])
def test_reset_with_code_changes_password(operator_staff, operator_admin, role, login_id):
    # Arrange
    code, _ = auth_service.issue_reset_code(login_id)

    # Act
    user = auth_service.reset_password_with_code(role, login_id, code, NEW_PW, NEW_PW)

    # Assert
    assert user.login_id == login_id
    assert _fresh(user.id).check_password(NEW_PW)
    assert auth_service.authenticate(role, login_id, NEW_PW).id == user.id
    assert all(t.used_at is not None for t in _tokens(user.id))


@pytest.mark.parametrize('transform', [str.lower, lambda c: c.replace('-', ''), lambda c: f' {c.lower()} ',
                                       lambda c: c.replace('-', ' ')])
def test_reset_with_code_ignores_case_hyphen_and_spaces(operator_staff, transform):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')

    # Act
    auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', transform(code), NEW_PW, NEW_PW)

    # Assert
    assert _fresh(operator_staff.id).check_password(NEW_PW)


def test_reset_with_code_cannot_be_reused(operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')
    auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, 'Another-pw-1', 'Another-pw-1')

    # Assert
    assert str(e.value) == MSG_RESET_CODE_INVALID
    assert _fresh(operator_staff.id).check_password(NEW_PW)


def test_reset_with_code_rejected_when_expired(clock, operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')

    # Act / Assert: ちょうど 30 分で期限切れ（直前の 29 分 59 秒は別のテストで確認）
    clock.advance(minutes=30)
    with pytest.raises(ServiceError) as e:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)
    assert str(e.value) == MSG_RESET_CODE_INVALID
    assert _fresh(operator_staff.id).check_password('password123')


def test_reset_with_code_still_valid_just_before_expiry(clock, operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')
    clock.advance(minutes=29, seconds=59)

    # Act
    auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)

    # Assert
    assert _fresh(operator_staff.id).check_password(NEW_PW)


def test_reset_with_code_staff_code_cannot_reset_admin_page(operator_staff, operator_admin):
    # Arrange: 職員のコードで、管理者の画面（role=admin）から管理者のパスワードを変えようとする
    code, _ = auth_service.issue_reset_code('staff01')

    # Act / Assert: 職員の ID を管理者の画面へ入れても、管理者のコードを職員の画面へ入れても同じ一文
    with pytest.raises(ServiceError) as e:
        auth_service.reset_password_with_code(User.ROLE_ADMIN, 'staff01', code, NEW_PW, NEW_PW)
    assert str(e.value) == MSG_RESET_CODE_INVALID
    with pytest.raises(ServiceError) as e:
        auth_service.reset_password_with_code(User.ROLE_ADMIN, 'admin01', code, NEW_PW, NEW_PW)
    assert str(e.value) == MSG_RESET_CODE_INVALID
    assert _fresh(operator_staff.id).check_password('password123')
    assert _fresh(operator_admin.id).check_password('password123')


def test_reset_with_code_admin_code_cannot_reset_staff_page(operator_staff, operator_admin):
    # Arrange
    code, _ = auth_service.issue_reset_code('admin01')

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'admin01', code, NEW_PW, NEW_PW)

    # Assert: 役割違いは「ID が存在しない」のと同じ文言・コードも消費されない
    assert str(e.value) == MSG_RESET_CODE_INVALID
    assert _tokens(operator_admin.id)[0].used_at is None


def test_reset_with_code_cannot_target_citizen(citizen):
    # Act: 市民の ID（@ 入り）を職員の画面へ
    with pytest.raises(ValidationError) as e:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'taro@example.com', 'ABCDE-FGHJK', NEW_PW, NEW_PW)

    # Assert: 職員・管理者の ID に @ は使えないので入力チェックで止まり、市民のパスワードは変わらない
    assert e.value.messages == [('warning', MSG_ID_HAS_AT.format(label='職員ID'))]
    assert _fresh(citizen.id).check_password('password123')


def test_reset_with_code_rejects_citizen_role(operator_staff):
    # Act / Assert: role に citizen は渡せない（プログラムの誤り）
    with pytest.raises(ValueError):
        auth_service.reset_password_with_code(User.ROLE_CITIZEN, 'staff01', 'ABCDE-FGHJK', NEW_PW, NEW_PW)


def test_reset_with_code_unknown_id_and_wrong_code_give_same_message(operator_staff):
    # Arrange
    auth_service.issue_reset_code('staff01')

    # Act
    with pytest.raises(ServiceError) as unknown:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'nobody', 'ABCDE-FGHJK', NEW_PW, NEW_PW)
    with pytest.raises(ServiceError) as wrong:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', 'ZZZZZ-ZZZZZ', NEW_PW, NEW_PW)

    # Assert
    assert str(unknown.value) == str(wrong.value) == MSG_RESET_CODE_INVALID


def test_reset_with_code_inactive_account_gives_same_message(operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')
    operator_staff.is_active = False
    db.session.commit()

    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)

    # Assert
    assert str(e.value) == MSG_RESET_CODE_INVALID
    assert _fresh(operator_staff.id).check_password('password123')


def test_reset_with_code_wrong_code_counts_toward_lockout(clock, operator_staff):
    # Arrange
    auth_service.issue_reset_code('staff01')

    # Act
    for _ in range(2):
        with pytest.raises(ServiceError):
            auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', 'ZZZZZ-ZZZZZ', NEW_PW, NEW_PW)

    # Assert: ログインと同じ失敗回数に加算される
    assert _fresh(operator_staff.id).failed_login_count == 2


def test_reset_with_code_locks_after_5_wrong_codes_and_blocks_correct_code(clock, operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')
    for _ in range(4):
        with pytest.raises(ServiceError):
            auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', 'ZZZZZ-ZZZZZ', NEW_PW, NEW_PW)

    # Act: 5 回目でロック
    with pytest.raises(ValidationError) as locked:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', 'ZZZZZ-ZZZZZ', NEW_PW, NEW_PW)

    # Assert
    assert locked.value.messages == [('warning', _lock_message(15))]
    # ロック中は正しいコードでも通らず、コードも消費されない。ログインもできない
    with pytest.raises(ValidationError) as still:
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)
    assert still.value.messages == [('warning', _lock_message(15))]
    assert _tokens(operator_staff.id)[0].used_at is None
    with pytest.raises(ValidationError):
        auth_service.authenticate(User.ROLE_STAFF, 'staff01', 'password123')
    # ロックが明ければ同じコードで再設定できる
    clock.advance(minutes=15)
    auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)
    assert _fresh(operator_staff.id).check_password(NEW_PW)


def test_reset_with_code_success_clears_failure_count(operator_staff):
    # Arrange
    code, _ = auth_service.issue_reset_code('staff01')
    with pytest.raises(ServiceError):
        auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', 'ZZZZZ-ZZZZZ', NEW_PW, NEW_PW)

    # Act
    auth_service.reset_password_with_code(User.ROLE_STAFF, 'staff01', code, NEW_PW, NEW_PW)

    # Assert
    assert _fresh(operator_staff.id).failed_login_count == 0


@pytest.mark.parametrize('login_id, code, new, confirm, expected', [
    ('', 'ABCDE-FGHJK', NEW_PW, NEW_PW, [('warning', MSG_ID_REQUIRED.format(label='職員ID'))]),
    ('staff@x', 'ABCDE-FGHJK', NEW_PW, NEW_PW, [('warning', MSG_ID_HAS_AT.format(label='職員ID'))]),
    ('staff01', '', NEW_PW, NEW_PW, [('warning', MSG_RESET_CODE_REQUIRED)]),
    ('staff01', ' - ', NEW_PW, NEW_PW, [('warning', MSG_RESET_CODE_REQUIRED)]),
    ('staff01', 'ABCDE-FGHJK', 'short', 'short', [('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
    ('staff01', 'ABCDE-FGHJK', NEW_PW, 'different-pw', [('warning', MSG_PASSWORD_MISMATCH)]),
    ('staff01', 'ABCDE-FGHJK', 'password123', 'password123', [('warning', MSG_PASSWORD_PUBLIC)]),
    ('', '', 'short', 'x', [('warning', MSG_ID_REQUIRED.format(label='職員ID')), ('warning', MSG_RESET_CODE_REQUIRED),
                            ('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
])
def test_reset_with_code_input_errors_do_not_count_as_failures(operator_staff, login_id, code, new, confirm, expected):
    # Arrange
    auth_service.issue_reset_code('staff01')

    # Act
    with pytest.raises(ValidationError) as e:
        auth_service.reset_password_with_code(User.ROLE_STAFF, login_id, code, new, confirm)

    # Assert: 入力エラーは失敗回数に数えず、コードも消費しない
    assert e.value.messages == expected
    assert _fresh(operator_staff.id).failed_login_count == 0
    assert _tokens(operator_staff.id)[0].used_at is None


def test_reset_with_code_admin_page_uses_admin_label(operator_admin):
    # Act
    with pytest.raises(ValidationError) as e:
        auth_service.reset_password_with_code(User.ROLE_ADMIN, '', 'ABCDE-FGHJK', NEW_PW, NEW_PW)

    # Assert
    assert e.value.messages == [('warning', MSG_ID_REQUIRED.format(label='管理者ID'))]


# ---------------------------------------------------------------------------
# change_password
# ---------------------------------------------------------------------------

def test_change_password_success(citizen):
    # Act
    user = auth_service.change_password(
        citizen.id, current_password='password123', new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert
    assert user.id == citizen.id
    fresh = _fresh(citizen.id)
    assert fresh.check_password(NEW_PW) and not fresh.check_password('password123')
    assert auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', NEW_PW).id == citizen.id


def test_change_password_wrong_current_counts_failure_and_keeps_password(citizen):
    # Act
    with pytest.raises(ServiceError) as e:
        auth_service.change_password(
            citizen.id, current_password='wrong-password', new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert
    assert str(e.value) == MSG_CURRENT_PASSWORD_WRONG
    fresh = _fresh(citizen.id)
    assert fresh.failed_login_count == 1 and fresh.check_password('password123')


def test_change_password_locks_after_5_wrong_current_passwords(clock, citizen):
    # Arrange
    for _ in range(4):
        with pytest.raises(ServiceError):
            auth_service.change_password(
                citizen.id, current_password='wrong-password', new_password=NEW_PW, confirm_password=NEW_PW)

    # Act
    with pytest.raises(ValidationError) as locked:
        auth_service.change_password(
            citizen.id, current_password='wrong-password', new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert: ロック。ログインにも効き、ロック中は正しい現在のパスワードでも変えられない
    assert locked.value.messages == [('warning', _lock_message(15))]
    with pytest.raises(ValidationError) as still:
        auth_service.change_password(
            citizen.id, current_password='password123', new_password=NEW_PW, confirm_password=NEW_PW)
    assert still.value.messages == [('warning', _lock_message(15))]
    with pytest.raises(ValidationError):
        auth_service.authenticate(User.ROLE_CITIZEN, 'taro@example.com', 'password123')
    assert _fresh(citizen.id).check_password('password123')


@pytest.mark.parametrize('current, new, confirm, expected', [
    ('', NEW_PW, NEW_PW, [('warning', MSG_CURRENT_PASSWORD_REQUIRED)]),
    (None, NEW_PW, NEW_PW, [('warning', MSG_CURRENT_PASSWORD_REQUIRED)]),
    ('password123', 'short', 'short', [('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
    ('password123', NEW_PW, 'different-pw', [('warning', MSG_PASSWORD_MISMATCH)]),
    ('password123', 'password123', 'password123', [('warning', MSG_PASSWORD_PUBLIC)]),
    ('', 'short', 'x', [('warning', MSG_CURRENT_PASSWORD_REQUIRED),
                        ('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
])
def test_change_password_input_errors_do_not_count_as_failures(citizen, current, new, confirm, expected):
    # Act
    with pytest.raises(ValidationError) as e:
        auth_service.change_password(citizen.id, current_password=current, new_password=new, confirm_password=confirm)

    # Assert
    assert e.value.messages == expected
    fresh = _fresh(citizen.id)
    assert fresh.failed_login_count == 0 and fresh.check_password('password123')


def test_change_password_rejects_same_as_current(citizen):
    # Arrange: 現在のパスワードを（公開されていない）別の値にしておく
    citizen.set_password('Current-pw-1')
    db.session.commit()

    # Act
    with pytest.raises(ValidationError) as e:
        auth_service.change_password(
            citizen.id, current_password='Current-pw-1', new_password='Current-pw-1', confirm_password='Current-pw-1')

    # Assert: 失敗回数には数えない
    assert e.value.messages == [('warning', MSG_PASSWORD_SAME_AS_CURRENT)]
    assert _fresh(citizen.id).failed_login_count == 0


def test_change_password_success_clears_failure_count_and_invalidates_tokens(citizen, clock, outbox):
    # Arrange
    _request()
    raw = sent_reset_token(outbox)
    with pytest.raises(ServiceError):
        auth_service.change_password(
            citizen.id, current_password='wrong-password', new_password=NEW_PW, confirm_password=NEW_PW)

    # Act
    auth_service.change_password(
        citizen.id, current_password='password123', new_password=NEW_PW, confirm_password=NEW_PW)

    # Assert: 失敗回数 0・変更前に送ったメールのリンクは使えない
    assert _fresh(citizen.id).failed_login_count == 0
    assert auth_service.is_reset_token_valid(raw) is False


def test_change_password_rejects_inactive_and_missing_user(citizen):
    # Arrange
    citizen.is_active = False
    db.session.commit()

    # Act / Assert
    with pytest.raises(ServiceError) as inactive:
        auth_service.change_password(
            citizen.id, current_password='password123', new_password=NEW_PW, confirm_password=NEW_PW)
    with pytest.raises(ServiceError) as missing:
        auth_service.change_password(
            999999, current_password='password123', new_password=NEW_PW, confirm_password=NEW_PW)
    assert str(inactive.value) == str(missing.value) == MSG_ACCOUNT_DISABLED
    assert _fresh(citizen.id).check_password('password123')


# ---------------------------------------------------------------------------
# セッションの印：User.get_id / load_user（パスワードを変えると古いセッションが無効になる）
# ---------------------------------------------------------------------------

def test_get_id_has_id_and_20_hex_fingerprint(citizen):
    # Act / Assert
    assert re.fullmatch(rf'{citizen.id}:[0-9a-f]{{20}}', citizen.get_id())
    # パスワードのハッシュそのものは含まれない
    assert citizen.password_hash not in citizen.get_id()


def test_get_id_changes_when_password_changes(citizen):
    # Arrange
    before = citizen.get_id()

    # Act
    citizen.set_password(NEW_PW)

    # Assert
    assert citizen.get_id() != before
    assert citizen.get_id().split(':')[0] == before.split(':')[0]


def test_load_user_restores_user_with_current_fingerprint(citizen):
    # Act / Assert
    assert load_user(citizen.get_id()).id == citizen.id


def test_load_user_rejects_old_fingerprint_after_password_change(citizen):
    # Arrange
    old_value = citizen.get_id()
    citizen.set_password(NEW_PW)
    db.session.commit()

    # Act / Assert
    assert load_user(old_value) is None
    assert load_user(citizen.get_id()).id == citizen.id


@pytest.mark.parametrize('make_value', [
    lambda user: str(user.id),                       # 2b より前の形式（印なし）
    lambda user: f'{user.id}:',                      # 印が空
    lambda user: f'{user.id}:{"0" * 20}',            # 印が違う
    lambda user: f'abc:{user.get_id().split(":")[1]}',   # ID が数字でない
    lambda user: f'-1:{user.get_id().split(":")[1]}',
    lambda user: f'{user.id + 1000}:{user.get_id().split(":")[1]}',   # 存在しない ID
    lambda user: '',
    lambda user: ':',
])
def test_load_user_rejects_malformed_values(citizen, make_value):
    # Act / Assert
    assert load_user(make_value(citizen)) is None


def test_load_user_rejects_inactive_user_even_with_valid_fingerprint(citizen):
    # Arrange
    value = citizen.get_id()
    citizen.is_active = False
    db.session.commit()

    # Act / Assert
    assert load_user(value) is None


def test_set_password_by_operator_invalidates_old_session_value(operator_staff):
    # Arrange
    old_value = operator_staff.get_id()

    # Act: flask set-password と同じ service
    auth_service.set_password_by_operator('staff01', NEW_PW)

    # Assert
    assert load_user(old_value) is None
