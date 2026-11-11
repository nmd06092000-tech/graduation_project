"""初期データ投入スクリプト（担当部署・カテゴリのマスタ。開発用テストアカウントは任意）

テーブルは Flask-Migrate で作成するため、先にマイグレーションを適用しておくこと。
実行方法（task-app 直下で）:
    flask --app run db upgrade
    python -m scripts.seed                    # マスタのみ（本番・EC2 はこちら）
    python -m scripts.seed --with-test-users  # マスタ＋テストアカウント（開発のみ）

テストアカウントは既定では登録しない（公開済みのパスワードで本番に入られないようにするため）。
何度実行しても同じデータが重複して登録されることはない。
"""
import argparse

from app import create_app
from app.constants import TEST_ACCOUNT_PW
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

# 開発用テストアカウント（本番環境では使用しないこと）。パスワードは remove-test-users と共通の TEST_ACCOUNT_PW
TEST_USERS = [
    ('taro@example.com', '山田 太郎', User.ROLE_CITIZEN, TEST_ACCOUNT_PW),
    ('staff01', '鈴木 健一', User.ROLE_STAFF, TEST_ACCOUNT_PW),
    ('admin01', 'システム管理者', User.ROLE_ADMIN, TEST_ACCOUNT_PW),
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


def seed(with_test_users=False):
    """現在のアプリケーションコンテキストの DB にマスタを登録して commit する

    with_test_users=True のときだけテストアカウントも登録する（開発・テスト用）。
    """
    seed_master()
    if with_test_users:
        seed_users()
    db.session.commit()


def main(argv=None):
    """コマンドライン入口：アプリを作成して seed() を呼ぶ"""
    parser = argparse.ArgumentParser(description='担当部署・カテゴリのマスタを登録する')
    parser.add_argument('--with-test-users', action='store_true',
                        help='開発用テストアカウント（taro@example.com / staff01 / admin01）も登録する')
    args = parser.parse_args(argv)

    app = create_app()
    with app.app_context():
        seed(with_test_users=args.with_test_users)
    if args.with_test_users:
        print('マスタデータとテストユーザーを登録しました')
    else:
        print('マスタデータを登録しました（テストユーザーは登録していません）')


if __name__ == '__main__':
    main()
