# パスワードの規則、職員・管理者IDに @ を使わない規則などの共通チェック
# ここでは例外を投げず、エラー（(flash カテゴリ, 文言) のリスト）を返す。呼び出し側が ValidationError にまとめる
from app.constants import (
    MSG_PASSWORD_MISMATCH, MSG_PASSWORD_PUBLIC, MSG_PASSWORD_REQUIRED, MSG_PASSWORD_TOO_SHORT, TEST_ACCOUNT_PW,
)

PASSWORD_MIN_LENGTH = 8


def validate_new_password(password, confirm=None):
    """パスワードの入力チェック。問題がなければ空のリストを返す

    未入力 → MSG_PASSWORD_REQUIRED、8文字未満 → MSG_PASSWORD_TOO_SHORT（どちらも設計書の文言）。
    confirm（確認用パスワード）を渡したときだけ、一致するかも調べる（flask コマンドなど確認欄がない場合は省略）。
    """
    if not password:
        return [('warning', MSG_PASSWORD_REQUIRED)]
    if len(password) < PASSWORD_MIN_LENGTH:
        return [('warning', MSG_PASSWORD_TOO_SHORT.format(length=len(password)))]
    if confirm is not None and password != confirm:
        return [('warning', MSG_PASSWORD_MISMATCH)]
    return []


def validate_operator_password(password):
    """運用者（flask コマンド）が設定するパスワードの入力チェック

    validate_new_password の規則に加え、README で公開されていたテストアカウントのパスワードは使えない。
    （ログイン画面の入力チェックには使わない：公開パスワードでも「ログイン失敗」として数える必要があるため）
    """
    errors = validate_new_password(password)
    if not errors and password == TEST_ACCOUNT_PW:
        errors.append(('warning', MSG_PASSWORD_PUBLIC))
    return errors
