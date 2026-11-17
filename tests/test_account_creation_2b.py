# アカウント作成（GĐ2b）のテスト：user_service.create_staff / create_admin / list_departments / find_department、
# validators.validate_operator_password、メール送信（utils/mail.py）、設定（PUBLIC_BASE_URL・本番の警告）
import smtplib
from unittest import mock

import pytest

from app.constants import (
    MSG_DEPARTMENT_INVALID, MSG_DEPARTMENT_REQUIRED, MSG_ID_HAS_AT, MSG_ID_REQUIRED, MSG_ID_TAKEN, MSG_ID_TOO_LONG,
    MSG_NAME_REQUIRED, MSG_NAME_TOO_LONG, MSG_PASSWORD_MISMATCH, MSG_PASSWORD_PUBLIC, MSG_PASSWORD_REQUIRED,
    MSG_PASSWORD_TOO_SHORT,
)
from app.extensions import db
from app.models import Department, User
from app.services import ServiceError, ValidationError, auth_service, user_service
from app.utils import mail
from app.utils.mail import MailError
from app.utils.validators import validate_operator_password
from app import create_app
from config import TestingConfig
from tests.conftest import _make_user

PW = 'Secret-pw1'
# SMTP の認証情報（テスト用のダミー値。実際のサーバーにはつながない）
SMTP_TEST_CREDENTIAL = 's3cret-mail-pw'


# ---------------------------------------------------------------------------
# create_staff
# ---------------------------------------------------------------------------

def test_create_staff_saves_staff_with_department(department):
    # Act
    user = user_service.create_staff(login_id=' staff10 ', name=' 田中 次郎 ', password=PW, department_id=department.id,
                                     confirm_password=PW)

    # Assert
    saved = User.query.filter_by(login_id='staff10').one()
    assert saved.id == user.id
    assert saved.role == User.ROLE_STAFF and saved.name == '田中 次郎'
    assert saved.department_id == department.id and saved.is_active is True
    assert saved.check_password(PW) and saved.password_hash != PW


def test_create_staff_accepts_department_id_as_string(department):
    # Act: 画面の選択値は文字列
    user = user_service.create_staff(login_id='staff10', name='田中', password=PW, department_id=f' {department.id} ')

    # Assert
    assert user.department_id == department.id


def test_create_staff_new_account_can_log_in(department):
    # Arrange
    user_service.create_staff(login_id='staff10', name='田中', password=PW, department_id=department.id)

    # Act / Assert
    assert auth_service.authenticate(User.ROLE_STAFF, 'staff10', PW).login_id == 'staff10'


@pytest.mark.parametrize('department_id, message', [
    (None, MSG_DEPARTMENT_REQUIRED), ('', MSG_DEPARTMENT_REQUIRED), ('  ', MSG_DEPARTMENT_REQUIRED),
    ('abc', MSG_DEPARTMENT_INVALID), ('999999', MSG_DEPARTMENT_INVALID), ('0', MSG_DEPARTMENT_INVALID),
    ('-1', MSG_DEPARTMENT_INVALID), ('1.5', MSG_DEPARTMENT_INVALID), ('9' * 30, MSG_DEPARTMENT_INVALID),
    ('１', MSG_DEPARTMENT_INVALID), ('1; DROP TABLE users', MSG_DEPARTMENT_INVALID),
])
def test_create_staff_rejects_missing_or_invalid_department(department, department_id, message):
    # Act
    with pytest.raises(ValidationError) as e:
        user_service.create_staff(login_id='staff10', name='田中', password=PW, department_id=department_id)

    # Assert
    assert e.value.messages == [('warning', message)]
    assert User.query.filter_by(login_id='staff10').count() == 0


def test_create_staff_collects_all_errors(department):
    # Act
    with pytest.raises(ValidationError) as e:
        user_service.create_staff(login_id='', name='', password='short', department_id='', confirm_password='x')

    # Assert
    assert e.value.messages == [
        ('warning', MSG_ID_REQUIRED.format(label='職員ID')), ('warning', MSG_NAME_REQUIRED),
        ('warning', MSG_DEPARTMENT_REQUIRED), ('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]


# ---------------------------------------------------------------------------
# create_admin
# ---------------------------------------------------------------------------

def test_create_admin_saves_admin_without_department(app):
    # Act
    user = user_service.create_admin(login_id='root', name='管理者', password=PW, confirm_password=PW)

    # Assert
    saved = db.session.get(User, user.id)
    assert saved.role == User.ROLE_ADMIN and saved.department_id is None and saved.is_active is True
    assert auth_service.authenticate(User.ROLE_ADMIN, 'root', PW).id == user.id


def test_create_admin_keeps_signature_without_confirm(app):
    # Act: 確認欄なし（CLI の create-admin）でも作れる
    user = user_service.create_admin(login_id='root', name='管理者', password=PW)

    # Assert
    assert user.check_password(PW)


# ---------------------------------------------------------------------------
# 共通の入力チェック（職員・管理者）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('create', ['staff', 'admin'])
@pytest.mark.parametrize('kwargs, expected', [
    ({'login_id': ''}, ('warning', MSG_ID_REQUIRED)),
    ({'login_id': '   '}, ('warning', MSG_ID_REQUIRED)),
    ({'login_id': 'a@b'}, ('warning', MSG_ID_HAS_AT)),
    ({'login_id': 'a' * 256}, ('warning', MSG_ID_TOO_LONG)),
    ({'name': ''}, ('warning', MSG_NAME_REQUIRED)),
    ({'name': '名' * 101}, ('warning', MSG_NAME_TOO_LONG)),
    ({'password': ''}, ('warning', MSG_PASSWORD_REQUIRED)),
    ({'password': 'short', 'confirm_password': 'short'}, ('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))),
    ({'confirm_password': 'Different-pw'}, ('warning', MSG_PASSWORD_MISMATCH)),
    ({'password': 'password123', 'confirm_password': 'password123'}, ('warning', MSG_PASSWORD_PUBLIC)),
])
def test_create_account_input_errors(department, create, kwargs, expected):
    # Arrange
    label = '職員ID' if create == 'staff' else '管理者ID'
    values = {'login_id': 'newuser', 'name': '田中', 'password': PW, 'confirm_password': PW, **kwargs}
    if create == 'staff':
        values['department_id'] = department.id
    function = user_service.create_staff if create == 'staff' else user_service.create_admin

    # Act
    with pytest.raises(ValidationError) as e:
        function(**values)

    # Assert
    category, text = expected
    assert e.value.messages == [(category, text.format(label=label) if '{label}' in text else text)]
    assert User.query.filter_by(name=values['name']).count() == 0


def test_create_account_name_100_chars_and_id_255_chars_are_accepted(department):
    # Act
    user = user_service.create_staff(login_id='a' * 255, name='名' * 100, password=PW, department_id=department.id)

    # Assert
    assert len(user.login_id) == 255 and len(user.name) == 100


@pytest.mark.parametrize('existing_role', [User.ROLE_STAFF, User.ROLE_ADMIN, User.ROLE_CITIZEN])
def test_create_account_rejects_duplicate_id_of_any_role(department, existing_role):
    # Arrange: 役割が違っても login_id は一意
    _make_user('dup01', '先に登録', existing_role)

    # Act
    with pytest.raises(ValidationError) as e:
        user_service.create_staff(login_id='dup01', name='田中', password=PW, department_id=department.id)

    # Assert
    assert e.value.messages == [('warning', MSG_ID_TAKEN.format(label='職員ID'))]
    assert User.query.filter_by(login_id='dup01').count() == 1


def test_create_account_integrity_error_becomes_validation_error(department):
    # Arrange: チェックのあと、保存の直前に別のリクエストが同じ ID を作った状況（コミットで一意制約違反）
    _make_user('race01', '先に登録', User.ROLE_STAFF)
    with mock.patch.object(user_service, '_login_id_exists', side_effect=[False, True]):
        # Act
        with pytest.raises(ValidationError) as e:
            user_service.create_staff(login_id='race01', name='田中', password=PW, department_id=department.id)

    # Assert
    assert e.value.messages == [('warning', MSG_ID_TAKEN.format(label='職員ID'))]
    assert User.query.filter_by(login_id='race01').count() == 1


# ---------------------------------------------------------------------------
# list_departments / find_department
# ---------------------------------------------------------------------------

def test_list_departments_returns_id_and_name_in_id_order(app):
    # Arrange
    first = Department(name='道路管理課')
    second = Department(name='公園緑地課')
    db.session.add_all([first, second])
    db.session.commit()

    # Act
    rows = user_service.list_departments()

    # Assert
    assert [(row[0], row[1]) for row in rows] == [(first.id, '道路管理課'), (second.id, '公園緑地課')]
    assert rows[0].id == first.id and rows[0].name == '道路管理課'


def test_list_departments_empty(app):
    # Act / Assert
    assert list(user_service.list_departments()) == []


def test_find_department_by_id_and_by_name(department):
    # Act / Assert
    assert user_service.find_department(str(department.id)).id == department.id
    assert user_service.find_department(f' {department.id} ').id == department.id
    assert user_service.find_department('道路管理課').id == department.id


def test_find_department_numeric_name_falls_back_to_name(app):
    # Arrange: 名前が数字だけで、同じ数字の ID が存在しない部署
    value = Department(name='999999')
    db.session.add(value)
    db.session.commit()

    # Act / Assert
    assert user_service.find_department('999999').id == value.id


@pytest.mark.parametrize('value', ['', '  ', None, 'なし', '999999', '0', '-1', '9' * 30, '道路'])
def test_find_department_not_found(department, value):
    # Act
    with pytest.raises(ServiceError) as e:
        user_service.find_department(value)

    # Assert: 部分一致は使わず、見つからなければ同じ文言
    assert str(e.value) == MSG_DEPARTMENT_INVALID


# ---------------------------------------------------------------------------
# validate_operator_password
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('password, confirm, expected', [
    (PW, None, []),
    (PW, PW, []),
    ('', None, [('warning', MSG_PASSWORD_REQUIRED)]),
    ('short', None, [('warning', MSG_PASSWORD_TOO_SHORT.format(length=5))]),
    (PW, 'other-pw-123', [('warning', MSG_PASSWORD_MISMATCH)]),
    ('password123', None, [('warning', MSG_PASSWORD_PUBLIC)]),
    ('password123', 'password123', [('warning', MSG_PASSWORD_PUBLIC)]),
    ('12345678', '12345678', []),
])
def test_validate_operator_password(password, confirm, expected):
    # Act / Assert
    assert validate_operator_password(password, confirm) == expected


# ---------------------------------------------------------------------------
# utils/mail.py：SMTP には実際につながない（smtplib.SMTP を差し替える）
# ---------------------------------------------------------------------------

@pytest.fixture
def smtp(app):
    """MAIL_SERVER を設定し、smtplib.SMTP を差し替えたモック（接続しない）"""
    app.config.update(MAIL_SERVER='smtp.example.test', MAIL_PORT=2525, MAIL_TIMEOUT=7, MAIL_FROM='no-reply@example.test',
                      MAIL_USERNAME='mailer', MAIL_PASSWORD=SMTP_TEST_CREDENTIAL)
    with mock.patch('app.utils.mail.smtplib.SMTP') as smtp_class:
        yield smtp_class


def test_send_mail_without_server_only_logs(app, caplog):
    # Arrange: TestingConfig は MAIL_SERVER を空にしている
    assert app.config['MAIL_SERVER'] == ''

    # Act
    with mock.patch('app.utils.mail.smtplib.SMTP') as smtp_class, caplog.at_level('WARNING'):
        result = mail.send_mail('taro@example.com', '件名', '本文 http://localhost:5000/x')

    # Assert: 接続せず、宛先・件名・本文を WARNING でログに出す
    assert result is None
    smtp_class.assert_not_called()
    assert 'taro@example.com' in caplog.text and '本文 http://localhost:5000/x' in caplog.text


def test_send_mail_uses_starttls_login_and_sends_message(smtp):
    # Act
    mail.send_mail('taro@example.com', '再設定のご案内', '本文です')

    # Assert
    smtp.assert_called_once_with('smtp.example.test', 2525, timeout=7)
    server = smtp.return_value.__enter__.return_value
    server.starttls.assert_called_once()
    server.login.assert_called_once_with('mailer', SMTP_TEST_CREDENTIAL)
    message = server.send_message.call_args.args[0]
    assert message['To'] == 'taro@example.com' and message['From'] == 'no-reply@example.test'
    assert message['Subject'] == '再設定のご案内'
    assert '本文です' in message.get_content()
    # STARTTLS のあとにログインする
    names = [call[0] for call in server.method_calls]
    assert names.index('starttls') < names.index('login') < names.index('send_message')


def test_send_mail_skips_login_without_username(app, smtp):
    # Arrange
    app.config['MAIL_USERNAME'] = ''

    # Act
    mail.send_mail('taro@example.com', '件名', '本文')

    # Assert
    smtp.return_value.__enter__.return_value.login.assert_not_called()
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()


@pytest.mark.parametrize('error', [smtplib.SMTPAuthenticationError(535, b'bad creds ' + SMTP_TEST_CREDENTIAL.encode()),
                                   smtplib.SMTPServerDisconnected('x'), ConnectionRefusedError('refused'),
                                   TimeoutError('timeout')])
def test_send_mail_wraps_failures_in_mail_error_without_details(smtp, error):
    # Arrange
    smtp.return_value.__enter__.return_value.send_message.side_effect = error

    # Act
    with pytest.raises(MailError) as e:
        mail.send_mail('taro@example.com', '件名', '本文')

    # Assert: 例外の種類名だけ（認証情報・宛先・本文は入らない）
    assert str(e.value) == type(error).__name__
    assert SMTP_TEST_CREDENTIAL not in str(e.value) and 'taro@example.com' not in str(e.value)


def test_send_mail_connection_error_is_mail_error(smtp):
    # Arrange
    smtp.side_effect = OSError('network down')

    # Act / Assert
    with pytest.raises(MailError):
        mail.send_mail('taro@example.com', '件名', '本文')


def test_send_mail_header_injection_is_mail_error(smtp):
    # Act: 件名に改行を入れてヘッダを増やそうとしても、送らずに MailError
    with pytest.raises(MailError):
        mail.send_mail('taro@example.com', '件名\nBcc: evil@example.com', '本文')

    # Assert
    smtp.return_value.__enter__.return_value.send_message.assert_not_called()


# 宛先は「ちょうど 1 件のアドレス」だけ許す（複数宛先・空白・改行・空・文字列以外は送らない）
INVALID_RECIPIENTS = ['a@x.com,b@y.com', 'a@x.com; b@y.com', 'a b@x.com', 'a@x.com\nBcc: c@z', 'a@x.com\r\nBcc: c@z',
                      '', None, 123]


@pytest.mark.parametrize('to', INVALID_RECIPIENTS)
def test_send_mail_invalid_recipient_is_mail_error_without_smtp(smtp, to):
    # Act
    with pytest.raises(MailError) as e:
        mail.send_mail(to, '件名', '本文')

    # Assert: SMTP につながず、例外メッセージに宛先を含めない
    assert str(e.value) == 'InvalidRecipient'
    smtp.assert_not_called()


@pytest.mark.parametrize('to', INVALID_RECIPIENTS)
def test_send_mail_invalid_recipient_is_mail_error_in_log_mode(app, caplog, to):
    # Arrange: ログ出力モード（MAIL_SERVER が空）
    assert app.config['MAIL_SERVER'] == ''

    # Act
    with mock.patch('app.utils.mail.smtplib.SMTP') as smtp_class, caplog.at_level('WARNING'):
        with pytest.raises(MailError):
            mail.send_mail(to, '件名', '本文-secret-link')

    # Assert: 送らず、本文もログに出さない
    smtp_class.assert_not_called()
    assert '本文-secret-link' not in caplog.text


def test_send_mail_plain_address_still_sends(smtp):
    # Act
    mail.send_mail('taro@example.com', '件名', '本文')

    # Assert
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()


def test_testing_config_never_sends_real_mail(app):
    # Assert: テスト用設定は SMTP サーバーを持たない（実メールの事故を防ぐ）
    assert app.config['MAIL_SERVER'] == ''
    assert app.config['PUBLIC_BASE_URL'] == 'http://localhost:5000'
    assert app.config['PASSWORD_RESET_TOKEN_MINUTES'] == 30


# ---------------------------------------------------------------------------
# 設定：リンクは PUBLIC_BASE_URL から作る・本番で MAIL_SERVER 未設定なら警告
# ---------------------------------------------------------------------------

def test_reset_link_follows_public_base_url_config(app, citizen, outbox):
    # Arrange
    app.config['PUBLIC_BASE_URL'] = 'https://team3app.example.test'

    # Act
    auth_service.request_password_reset('taro@example.com')

    # Assert
    body = outbox.call_args.args[2]
    assert 'https://team3app.example.test/reset-password/confirm/' in body
    assert 'localhost' not in body


def test_production_without_mail_server_logs_warning(app, caplog):
    # Arrange: 本番相当（Cookie に Secure）で MAIL_SERVER が空
    class ProdLikeConfig(TestingConfig):
        SESSION_COOKIE_SECURE = True
        REMEMBER_COOKIE_SECURE = True

    # Act
    with caplog.at_level('WARNING'):
        create_app(ProdLikeConfig)

    # Assert
    assert 'MAIL_SERVER が未設定です' in caplog.text


def test_no_warning_when_mail_server_is_configured(app, caplog):
    # Arrange
    class ProdMailConfig(TestingConfig):
        SESSION_COOKIE_SECURE = True
        REMEMBER_COOKIE_SECURE = True
        MAIL_SERVER = 'smtp.example.test'

    # Act
    with caplog.at_level('WARNING'):
        create_app(ProdMailConfig)

    # Assert
    assert 'MAIL_SERVER が未設定です' not in caplog.text
