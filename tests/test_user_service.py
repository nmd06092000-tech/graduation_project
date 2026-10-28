# 市民のプロフィール設定（お名前・メールアドレス・アイコン）のテスト
import pytest

from app.constants import (
    MSG_EMAIL_NO_AT, MSG_EMAIL_REQUIRED, MSG_EMAIL_TAKEN, MSG_ICON_REQUIRED, MSG_NAME_EMAIL_REQUIRED,
    MSG_NAME_REQUIRED,
)
from app.services import ServiceError, ValidationError, storage_service, user_service
from tests.conftest import jpeg_bytes, upload


# プロフィール設定 基本フロー2〜3：メールアドレスは小文字で保存する
def test_update_profile(citizen):
    user_service.update_profile(citizen.id, name=' 山田 花子 ', email='Hanako@Example.com')

    assert citizen.name == '山田 花子'
    assert citizen.login_id == 'hanako@example.com'


# 代替フロー2b-1（赤）・2c-1・2d-1（オレンジ）、＠なし・重複
@pytest.mark.parametrize('name, email, expected', [
    ('', '', ('danger', MSG_NAME_EMAIL_REQUIRED)),
    ('', 'taro@example.com', ('warning', MSG_NAME_REQUIRED)),
    ('山田 太郎', '', ('warning', MSG_EMAIL_REQUIRED)),
    ('山田 太郎', 'taro.example.com', ('warning', MSG_EMAIL_NO_AT.format(email='taro.example.com'))),
    ('山田 太郎', 'staff01', ('warning', MSG_EMAIL_NO_AT.format(email='staff01'))),
])
def test_update_profile_validation(citizen, name, email, expected):
    with pytest.raises(ValidationError) as e:
        user_service.update_profile(citizen.id, name=name, email=email)
    assert e.value.messages == [expected]
    assert citizen.login_id == 'taro@example.com'


def test_update_profile_email_taken(citizen):
    from tests.conftest import _make_user
    _make_user('hanako@example.com', '山田 花子', 'citizen')

    with pytest.raises(ValidationError) as e:
        user_service.update_profile(citizen.id, name='山田 太郎', email='HANAKO@example.com')
    assert e.value.messages == [('warning', MSG_EMAIL_TAKEN)]


# アイコンを保存し、変更したら古いファイルは消す
def test_update_icon(citizen):
    user_service.update_icon(citizen.id, upload(jpeg_bytes()))
    first = citizen.icon_path
    user_service.update_icon(citizen.id, upload(jpeg_bytes()))

    assert citizen.icon_path.startswith('icons/') and storage_service.exists(citizen.icon_path)
    assert not storage_service.exists(first)


def test_update_icon_requires_file(citizen):
    with pytest.raises(ServiceError, match=MSG_ICON_REQUIRED):
        user_service.update_icon(citizen.id, None)
