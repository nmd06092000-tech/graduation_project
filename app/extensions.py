from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
# 全 POST リクエストに csrf_token を要求する（テストでは WTF_CSRF_ENABLED=False）
csrf = CSRFProtect()
