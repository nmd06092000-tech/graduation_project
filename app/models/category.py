# 報告カテゴリ（Category）モデル：道路・街灯・公園・不法投棄など。既定の担当部署を持つ
import sqlalchemy as sa

from app.extensions import db


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.BigInteger, primary_key=True)
    # 内部コード（例: road）。ルールベース分類・プロンプトで使い、表示名を変えても変わらない
    code = db.Column(db.String(30), nullable=False, unique=True)
    # 表示名（例: 道路）
    name = db.Column(db.String(50), nullable=False, unique=True)
    # 既定の担当部署
    default_department_id = db.Column(
        db.BigInteger, db.ForeignKey('departments.id', ondelete='RESTRICT'), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    default_department = db.relationship('Department')

    def __repr__(self):
        return f'<Category {self.code}>'
