# 担当部署（Department）モデル
import sqlalchemy as sa

from app.extensions import db


class Department(db.Model):
    __tablename__ = 'departments'

    __table_args__ = (
        # 非表示（del_flag）と非表示日時・実行者の整合：非表示なら日時が必須、表示中なら日時・実行者は NULL
        sa.CheckConstraint(
            '(del_flag AND deleted_at IS NOT NULL) OR (NOT del_flag AND deleted_at IS NULL AND deleted_by_id IS NULL)',
            name='ck_departments_del_flag_consistent'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # 部署名（例: 道路管理課）。非表示の部署の名前も使い続ける（一意制約はそのまま）
    name = db.Column(db.String(50), nullable=False, unique=True)
    # 非表示（論理削除。3b）。新しい報告・職員登録・AI の選択肢から外れるだけで、過去のデータの表示は変わらない
    # del_flag・deleted_at・deleted_by_id は同じ書き込みで一緒に変える（CHECK ck_departments_del_flag_consistent）
    del_flag = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    deleted_at = db.Column(db.DateTime(timezone=True))
    # users ⇄ departments の循環参照があるため use_alter（テーブル作成後に FK を追加する）
    deleted_by_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT',
                                                           name='departments_deleted_by_id_fkey',
                                                           use_alter=True), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    @property
    def is_deleted(self):
        """非表示（論理削除）の部署か"""
        return bool(self.del_flag)

    def __repr__(self):
        return f'<Department {self.name}>'
