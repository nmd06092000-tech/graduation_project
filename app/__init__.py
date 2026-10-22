from flask import Flask

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

    return app
