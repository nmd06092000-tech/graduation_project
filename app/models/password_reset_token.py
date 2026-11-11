# パスワード再設定トークン（PasswordResetToken）モデル：市民のメールによる再設定（2b で使用）
# トークン本体は保存せず、SHA-256 などのハッシュ（token_hash）だけを保存する
import sqlalchemy as sa

from app.extensions import db


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.BigInteger, primary_key=True)
    # ユーザーを削除したらトークンも一緒に消える
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    # 使用済みにした日時（NULL = 未使用）
    used_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    user = db.relationship('User')
