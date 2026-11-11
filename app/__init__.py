from urllib.parse import quote, urlsplit

from flask import Flask, flash, jsonify, redirect, request, url_for
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from app.constants import MSG_CSRF_EXPIRED
from app.extensions import csrf, db, login_manager, migrate

# Referer の再エンコードで、そのまま残す文字（RFC 3986 の予約・非予約文字と、既存の %xx の %）
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + '?'


def _previous_page_path():
    """直前のページの「パス＋クエリ」を返す（Referer が無い・不正なら public.index）

    オープンリダイレクトを避けるため、Referer のホストは使わず自サイト内のパスだけを取り出す。
    """
    fallback = url_for('public.index')
    referer = request.headers.get('Referer', '')
    if not referer:
        return fallback
    parts = urlsplit(referer)
    path = parts.path
    # 「//host」「/\host」のようにほかのサイトへ解釈されうるパスは使わない
    if not path.startswith('/') or path.startswith('//') or '\\' in referer:
        return fallback
    # 制御文字（\x00 など）や空白が Location ヘッダにそのまま入らないよう再エンコードする。
    # 既に %xx の部分は二重にエンコードしないよう % を safe に含める（通常の Referer は変化しない）
    path = quote(path, safe=_PATH_SAFE)
    if parts.query:
        return f'{path}?{quote(parts.query, safe=_QUERY_SAFE)}'
    return path


def create_app(config_class=Config):
    """アプリケーションファクトリ：設定・拡張機能・Blueprint をまとめて初期化する"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Nginx などの背後で動かすとき、X-Forwarded-For / X-Forwarded-Proto を信頼して
    # request.remote_addr・is_secure を正しくする。X-Forwarded-Host は信頼しない（x_host=0）
    proxy_hops = app.config['PROXY_FIX_HOPS']
    if proxy_hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_hops, x_proto=proxy_hops, x_host=0)

    db.init_app(app)
    login_manager.init_app(app)
    # 全 POST に csrf_token を要求する（form は hidden の csrf_token、fetch は X-CSRFToken ヘッダ）
    csrf.init_app(app)
    # マイグレーション（flask db migrate / upgrade）
    migrate.init_app(app, db)

    # モデルを読み込み、user_loader を登録する
    from app import models  # noqa: F401

    from app.blueprints.public import public_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.citizen import citizen_bp
    from app.blueprints.staff import staff_bp
    from app.blueprints.admin import admin_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(citizen_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(admin_bp)

    # 定期実行するコマンド（flask --app run rejudge-urgency）
    from app.cli import register_commands
    register_commands(app)

    # CSRF トークンが無い・不正・セッション切れのとき：直前の画面へ戻して再操作を促す
    # （X-Replace-History 付きの送信は、下の after_request が移動先 URL の JSON に変換する）
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        app.logger.info('CSRF 検証エラー: %s %s (%s)', request.method, request.path, error.description)
        flash(MSG_CSRF_EXPIRED, 'warning')
        return redirect(_previous_page_path())

    # 履歴を増やさない form（static/js/navigation.js が fetch で送信する）には、
    # リダイレクトの代わりに移動先の URL を JSON で返す（flash は移動先の画面で表示される）
    @app.after_request
    def redirect_as_json(response):
        if request.headers.get('X-Replace-History') and 300 <= response.status_code < 400 and response.location:
            return jsonify(redirect=response.location)
        return response

    return app
