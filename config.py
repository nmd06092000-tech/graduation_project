import os
from datetime import timedelta

from dotenv import load_dotenv

# .env ファイルから環境変数を読み込む
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # 未設定の場合は起動時にエラーにする（秘密情報をコードに直書きしない）
    SECRET_KEY = os.environ['SECRET_KEY']
    SQLALCHEMY_DATABASE_URI = os.environ['DATABASE_URL']
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 「ログイン状態を保持する」の有効期間
    REMEMBER_COOKIE_DURATION = timedelta(days=14)

    # AI分析画面「要人間チェック（低信頼度）」とみなす信頼度のしきい値（0〜1）
    AI_LOW_CONFIDENCE_THRESHOLD = float(os.environ.get('AI_LOW_CONFIDENCE_THRESHOLD', '0.7'))

    # 写真・アイコンの保存先（個人情報を含むため static ではなく instance/uploads に置く）
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'instance', 'uploads'))
    # 1 リクエストの上限（写真 3 枚 × 10MB ＋ 入力欄）
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024

    # AI（Gemini）。GEMINI_API_KEY を設定すると Gemini を使い、未設定ならキーワード判定（rule_based）になる
    AI_PROVIDER = os.environ.get('AI_PROVIDER', 'gemini')
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
    GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    GEMINI_TIMEOUT_MS = int(os.environ.get('GEMINI_TIMEOUT_MS', '20000'))
    GEMINI_MAX_RETRIES = int(os.environ.get('GEMINI_MAX_RETRIES', '2'))
    GEMINI_TEMPERATURE = float(os.environ.get('GEMINI_TEMPERATURE', '0.2'))
    GEMINI_MAX_OUTPUT_TOKENS = int(os.environ.get('GEMINI_MAX_OUTPUT_TOKENS', '2048'))
    # 空なら送らない。対応モデルのみ minimal / low など
    GEMINI_THINKING_LEVEL = os.environ.get('GEMINI_THINKING_LEVEL', '')

    # 地図表示（Google Maps JavaScript API）。キー未設定なら従来の地図画像を表示する
    # キーはブラウザに渡るため、Google Cloud 側で HTTP リファラー制限をかけること
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    # AdvancedMarker に必要なマップ ID（DEMO_MAP_ID は開発用）
    GOOGLE_MAPS_MAP_ID = os.environ.get('GOOGLE_MAPS_MAP_ID', 'DEMO_MAP_ID')


class TestingConfig(Config):
    # pytest 用。開発 DB とは別の PostgreSQL（テストのたびにテーブルを作り直す）
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL', '')
    # テストでは Gemini を呼ばない
    AI_PROVIDER = 'rule_based'
    GEMINI_API_KEY = ''
