"""開発用の初期データ投入スクリプト

実行方法（task-app 直下で）:
    python -m scripts.seed
"""
from app import create_app
from app.extensions import db
from app.models import User

# 開発用テストアカウント（本番環境では使用しないこと）
TEST_USERS = [
    ('taro@example.com', '山田 太郎', User.ROLE_CITIZEN, 'password123'),
    ('staff01', '鈴木 健一', User.ROLE_STAFF, 'password123'),
    ('admin01', 'システム管理者', User.ROLE_ADMIN, 'password123'),
]

app = create_app()

with app.app_context():
    db.create_all()
    for login_id, name, role, password in TEST_USERS:
        if User.query.filter_by(login_id=login_id).first():
            continue
        user = User(login_id=login_id, name=name, role=role)
        user.set_password(password)
        db.session.add(user)
    db.session.commit()
    print('テーブルとテストユーザーを作成しました')
