import os
from datetime import timedelta

from dotenv import load_dotenv

# .env ファイルから環境変数を読み込む
load_dotenv()


class Config:
    # 未設定の場合は起動時にエラーにする（秘密情報をコードに直書きしない）
    SECRET_KEY = os.environ['SECRET_KEY']
    SQLALCHEMY_DATABASE_URI = os.environ['DATABASE_URL']
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 「ログイン状態を保持する」の有効期間
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
