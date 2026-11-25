# 報告カテゴリ（Category）モデル：道路・街灯・公園・不法投棄など。既定の担当部署を持つ
import sqlalchemy as sa

from app.extensions import db


class Category(db.Model):
    __tablename__ = 'categories'

    __table_args__ = (
        # 非表示（del_flag）と非表示日時・実行者の整合：非表示なら日時が必須、表示中なら日時・実行者は NULL
        sa.CheckConstraint(
            '(del_flag AND deleted_at IS NOT NULL) OR (NOT del_flag AND deleted_at IS NULL AND deleted_by_id IS NULL)',
            name='ck_categories_del_flag_consistent'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # 内部コード（例: road）。ルールベース分類・プロンプトで使い、表示名を変えても変わらない
    # 画面から作るカテゴリは「cat_」＋ランダム 8 文字で自動採番（管理者は入力しない）
    code = db.Column(db.String(30), nullable=False, unique=True)
    # 表示名（例: 道路）
    name = db.Column(db.String(50), nullable=False, unique=True)
    # 既定の担当部署
    default_department_id = db.Column(
        db.BigInteger, db.ForeignKey('departments.id', ondelete='RESTRICT'), nullable=False, index=True)
    # 非表示（論理削除。3b）。新しい報告・AI の選択肢から外れるだけで、過去の報告の表示は変わらない
    # del_flag・deleted_at・deleted_by_id は同じ書き込みで一緒に変える（CHECK ck_categories_del_flag_consistent）
    del_flag = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT',
                                                           name='categories_deleted_by_id_fkey'), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    default_department = db.relationship('Department')

    @property
    def is_deleted(self):
        """非表示（論理削除）のカテゴリか"""
        return bool(self.del_flag)

    def __repr__(self):
        return f'<Category {self.code}>'
