# 担当部署（Department）モデル
import sqlalchemy as sa

from app.extensions import db


class Department(db.Model):
    __tablename__ = 'departments'

    id = db.Column(db.BigInteger, primary_key=True)
    # 部署名（例: 道路管理課）
    name = db.Column(db.String(50), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    def __repr__(self):
        return f'<Department {self.name}>'
