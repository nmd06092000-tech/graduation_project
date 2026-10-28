from flask import Flask, jsonify, request

from config import Config
from app.extensions import db, login_manager, migrate


def create_app(config_class=Config):
    """アプリケーションファクトリ：設定・拡張機能・Blueprint をまとめて初期化する"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
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

    # 履歴を増やさない form（static/js/navigation.js が fetch で送信する）には、
    # リダイレクトの代わりに移動先の URL を JSON で返す（flash は移動先の画面で表示される）
    @app.after_request
    def redirect_as_json(response):
        if request.headers.get('X-Replace-History') and 300 <= response.status_code < 400 and response.location:
            return jsonify(redirect=response.location)
        return response

    return app
