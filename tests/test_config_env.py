# 環境変数の整数読み込み（config._env_int）と、公開パスワード定数の一元化のテスト（GĐ2a 修正後）
# _env_int は関数を直接呼ぶ（モジュールを reload して他のテストを壊さない）。DB は使わない
from pathlib import Path

import pytest

from app.constants import TEST_ACCOUNT_PW
from config import _env_int

NAME = 'TASKAPP_TEST_INT'


def test_env_int_unset_returns_default(monkeypatch):
    # Arrange
    monkeypatch.delenv(NAME, raising=False)

    # Act / Assert
    assert _env_int(NAME, 5, 1) == 5


@pytest.mark.parametrize('raw', ['', '   '])
def test_env_int_blank_returns_default(monkeypatch, raw):
    # Arrange: 空・空白だけは未設定と同じ
    monkeypatch.setenv(NAME, raw)

    # Act / Assert
    assert _env_int(NAME, 15, 1) == 15


def test_env_int_strips_whitespace(monkeypatch):
    # Arrange
    monkeypatch.setenv(NAME, ' 3 ')

    # Act / Assert
    assert _env_int(NAME, 5, 1) == 3


@pytest.mark.parametrize('raw', ['abc', '1.5', '3 4', '0x10'])
def test_env_int_non_integer_raises_with_message(monkeypatch, raw):
    # Arrange
    monkeypatch.setenv(NAME, raw)

    # Act
    with pytest.raises(ValueError) as excinfo:
        _env_int(NAME, 5, 1)

    # Assert
    assert str(excinfo.value) == f'環境変数 {NAME} の値 {raw!r} は不正です。整数を指定してください'


@pytest.mark.parametrize('raw, minimum', [('0', 1), ('-1', 1), ('-1', 0)])
def test_env_int_below_minimum_raises_with_message(monkeypatch, raw, minimum):
    # Arrange
    monkeypatch.setenv(NAME, raw)

    # Act
    with pytest.raises(ValueError) as excinfo:
        _env_int(NAME, 5, minimum)

    # Assert
    assert str(excinfo.value) == f'環境変数 {NAME} の値 {int(raw)} は不正です。{minimum} 以上の整数を指定してください'


def test_env_int_equal_to_minimum_is_accepted(monkeypatch):
    # Arrange: 境界値（minimum ちょうど）は通る。PROXY_FIX_HOPS は 0 が有効
    monkeypatch.setenv(NAME, '0')

    # Act / Assert
    assert _env_int(NAME, 7, 0) == 0


def test_config_defaults_for_lock_and_proxy(app):
    # Assert: 既定値（.env で上書きしていない前提。テスト用 Config は親の値を継承する）
    assert app.config['LOGIN_MAX_FAILURES'] >= 1
    assert app.config['LOGIN_LOCK_MINUTES'] >= 1
    assert app.config['PROXY_FIX_HOPS'] >= 0


def test_test_account_pw_is_single_shared_value():
    # Assert: user_service / validators / seed が同じ app.constants.TEST_ACCOUNT_PW を使う
    from app.services import user_service
    from app.utils import validators
    assert TEST_ACCOUNT_PW == 'password123'
    assert user_service.TEST_ACCOUNT_PW is TEST_ACCOUNT_PW
    assert validators.TEST_ACCOUNT_PW is TEST_ACCOUNT_PW
    # seed は実行せず（開発 DB に書くため）ソースだけ確認する：定数を import し、リテラルを重ねて持たない
    seed_source = (Path(__file__).resolve().parent.parent / 'scripts' / 'seed.py').read_text(encoding='utf-8')
    assert 'from app.constants import TEST_ACCOUNT_PW' in seed_source
    assert "'password123'" not in seed_source
