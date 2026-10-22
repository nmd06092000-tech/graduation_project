import sqlalchemy as sa
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.constants import Role
from app.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    # ロールのコードは constants.Role から取る（既存コードの User.ROLE_xxx はそのまま使える）
    ROLE_CITIZEN = Role.CITIZEN.value
    ROLE_STAFF = Role.STAFF.value
    ROLE_ADMIN = Role.ADMIN.value

    __table_args__ = (
        sa.CheckConstraint(Role.check_sql('role'), name='ck_users_role'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # 市民: メールアドレス（小文字で保存） / 職員・管理者: ID（@なし）
    login_id = db.Column(db.String(255), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # False の場合はログイン不可（利用停止）
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=sa.true())
    # アイコン画像の保存パス（UPLOAD_FOLDER からの相対パス）。NULL の場合は既定アイコン
    icon_path = db.Column(db.String(255))
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# セッションに保存されたIDからユーザーを復元する（利用停止中のユーザーは復元しない）
@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    if user is None or not user.is_active:
        return None
    return user
