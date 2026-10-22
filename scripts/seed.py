"""開発用の初期データ投入スクリプト（担当部署・カテゴリのマスタとテストアカウント）

テーブルは Flask-Migrate で作成するため、先にマイグレーションを適用しておくこと。
実行方法（task-app 直下で）:
    flask --app run db upgrade
    python -m scripts.seed

何度実行しても同じデータが重複して登録されることはない。
"""
from app import create_app
from app.extensions import db
from app.models import Category, Department, User

# 担当部署（暫定。必要に応じて追加する）
DEPARTMENTS = ['道路管理課', '道路照明課', '公園緑地課', '環境課', '市民生活課']

# カテゴリ（内部コード, 表示名, 既定の担当部署）
CATEGORIES = [
    ('road', '道路', '道路管理課'),
    ('streetlight', '街灯', '道路照明課'),
    ('park', '公園', '公園緑地課'),
    ('illegal_dumping', '不法投棄', '環境課'),
    ('other', 'その他', '市民生活課'),
]

# 開発用テストアカウント（本番環境では使用しないこと）
TEST_USERS = [
    ('taro@example.com', '山田 太郎', User.ROLE_CITIZEN, 'password123'),
    ('staff01', '鈴木 健一', User.ROLE_STAFF, 'password123'),
    ('admin01', 'システム管理者', User.ROLE_ADMIN, 'password123'),
]


def seed_master():
    """担当部署とカテゴリを登録する（登録済みのものはスキップ）"""
    departments = {d.name: d for d in Department.query.all()}
    for name in DEPARTMENTS:
        if name not in departments:
            departments[name] = Department(name=name)
            db.session.add(departments[name])
    db.session.flush()

    existing_codes = {c.code for c in Category.query.all()}
    for code, name, department_name in CATEGORIES:
        if code in existing_codes:
            continue
        db.session.add(Category(code=code, name=name, default_department=departments[department_name]))


def seed_users():
    """テストアカウントを登録する（登録済みのものはスキップ）"""
    for login_id, name, role, password in TEST_USERS:
        if User.query.filter_by(login_id=login_id).first():
            continue
        user = User(login_id=login_id, name=name, role=role)
        user.set_password(password)
        db.session.add(user)


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        seed_master()
        seed_users()
        db.session.commit()
        print('マスタデータとテストユーザーを登録しました')
