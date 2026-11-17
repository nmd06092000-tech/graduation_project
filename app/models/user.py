import hashlib
import hmac

import sqlalchemy as sa
from flask import current_app
from flask_login import UserMixin
from sqlalchemy.ext.hybrid import hybrid_property
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
        # 削除済み（del_flag）と削除日時・削除者の整合：削除なら日時が必須、未削除なら日時・削除者は NULL
        sa.CheckConstraint(
            '(del_flag AND deleted_at IS NOT NULL) OR (NOT del_flag AND deleted_at IS NULL AND deleted_by_id IS NULL)',
            name='ck_users_del_flag_consistent'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # 市民: メールアドレス（小文字で保存） / 職員・管理者: ID（@なし）
    login_id = db.Column(db.String(255), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # False の場合はログイン不可（利用停止。flask --app run remove-test-users だけが立てる別の旗）
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=sa.true())
    # 論理削除（管理者が削除したアカウント）。ID は残したまま使えなくする。復元は flask --app run restore-user
    # del_flag・deleted_at・deleted_by_id は同じ書き込みで一緒に変える（CHECK ck_users_del_flag_consistent）
    del_flag = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT',
                                                           name='users_deleted_by_id_fkey'), index=True)
    # 市民の郵便番号（「123-4567」の形で保存）と住所（埼玉県内）。職員・管理者は NULL
    postal_code = db.Column(db.String(8))
    address = db.Column(db.String(255))
    # アイコン画像の保存パス（UPLOAD_FOLDER からの相対パス）。NULL の場合は既定アイコン
    icon_path = db.Column(db.String(255))
    last_login_at = db.Column(db.DateTime(timezone=True))
    # 連続ログイン失敗回数（成功・ロック発動で 0 に戻す。加算は auth_service が UPDATE 文で原子的に行う）
    failed_login_count = db.Column(db.Integer, nullable=False, default=0, server_default=sa.text('0'))
    # この日時までログイン不可（NULL またはすでに過ぎていればロックなし）
    locked_until = db.Column(db.DateTime(timezone=True))
    # 職員の担当部署（旧データ・市民・管理者は NULL。使うのは職員アカウントの管理画面から）
    department_id = db.Column(db.BigInteger, db.ForeignKey('departments.id', ondelete='RESTRICT'), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    department = db.relationship('Department')

    @property
    def is_deleted(self):
        """管理者に削除（論理削除）されたアカウントか"""
        return bool(self.del_flag)

    @hybrid_property
    def is_usable(self):
        """使えるアカウントか（利用停止でも削除済みでもない）。「使える」の判定は必ずこれを使う（is_active 単独は不可）"""
        return bool(self.is_active) and not self.del_flag

    @is_usable.expression
    def is_usable(cls):
        return sa.and_(cls.is_active.is_(True), cls.del_flag.is_(False))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def _password_fingerprint(self):
        """パスワードのハッシュに紐づく印（SECRET_KEY による HMAC の先頭20文字。ハッシュそのものは外に出さない）"""
        digest = hmac.new(current_app.config['SECRET_KEY'].encode(), self.password_hash.encode(), hashlib.sha256)
        return digest.hexdigest()[:20]

    def get_id(self):
        """セッション・「ログイン状態を保持する」Cookie に入れる値：「ID:パスワードの印」

        パスワードを変えると印が変わるので、変更前に発行された Cookie（ほかの端末のセッションを含む）は無効になる。
        """
        return f'{self.id}:{self._password_fingerprint()}'


# セッションに保存された値からユーザーを復元する（利用停止中・削除済み・パスワード変更後の古いセッションは復元しない）
@login_manager.user_loader
def load_user(session_value):
    user_id, _, fingerprint = str(session_value).partition(':')
    if not (user_id.isascii() and user_id.isdigit()) or not fingerprint:
        # 印のない値（2b より前のセッション）や不正な値は復元しない
        return None
    user = db.session.get(User, int(user_id))
    if user is None or not user.is_usable:
        return None
    if not hmac.compare_digest(fingerprint.encode(), user._password_fingerprint().encode()):
        return None
    return user
