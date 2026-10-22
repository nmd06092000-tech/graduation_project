# 報告（Report）と添付写真（ReportPhoto）モデル
import sqlalchemy as sa

from app.constants import ReportStatus, Urgency
from app.extensions import db


class Report(db.Model):
    __tablename__ = 'reports'

    __table_args__ = (
        sa.CheckConstraint(ReportStatus.check_sql('status'), name='ck_reports_status'),
        sa.CheckConstraint(f"urgency IS NULL OR {Urgency.check_sql('urgency')}", name='ck_reports_urgency'),
        sa.CheckConstraint('latitude BETWEEN -90 AND 90', name='ck_reports_latitude'),
        sa.CheckConstraint('longitude BETWEEN -180 AND 180', name='ck_reports_longitude'),
        sa.CheckConstraint('duplicate_of_id IS NULL OR duplicate_of_id <> id', name='ck_reports_duplicate_of_self'),
        # 市民の報告一覧・マイページ（市民ごと・新しい順）
        sa.Index('ix_reports_citizen_id_created_at', 'citizen_id', 'created_at'),
        # 職員の報告一覧・ダッシュボード（ステータス別の件数・未完了の一覧）
        sa.Index('ix_reports_status_created_at', 'status', 'created_at'),
        # 重複チェック（同カテゴリ・未完了・最近）と地図のカテゴリ絞り込み
        sa.Index('ix_reports_category_id_status_created_at', 'category_id', 'status', 'created_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    # 報告した市民
    citizen_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    # 件名
    title = db.Column(db.String(100), nullable=False)
    # 詳細内容
    description = db.Column(db.Text, nullable=False)
    # 位置情報（緯度・経度）と表示用の住所
    latitude = db.Column(db.Double, nullable=False)
    longitude = db.Column(db.Double, nullable=False)
    address = db.Column(db.String(255), nullable=False)
    # 現在のステータス（変更は status_service のみ）
    status = db.Column(db.String(20), nullable=False,
                       default=ReportStatus.RECEIVED.value, server_default=ReportStatus.RECEIVED.value)
    # 確定カテゴリ・緊急度・担当部署（AI の提案を初期値とし、職員が変更できる）。NULL = 未分類・未判定・未設定
    category_id = db.Column(db.BigInteger, db.ForeignKey('categories.id', ondelete='RESTRICT'))
    urgency = db.Column(db.String(20))
    department_id = db.Column(db.BigInteger, db.ForeignKey('departments.id', ondelete='RESTRICT'), index=True)
    # 所属する重複候補グループ
    duplicate_group_id = db.Column(
        db.BigInteger, db.ForeignKey('duplicate_groups.id', ondelete='RESTRICT'), index=True)
    # 統合先の代表案件。NULL = 代表または単独の報告
    duplicate_of_id = db.Column(db.BigInteger, db.ForeignKey('reports.id', ondelete='RESTRICT'), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=sa.func.now(), onupdate=sa.func.now())

    citizen = db.relationship('User')
    category = db.relationship('Category')
    department = db.relationship('Department')
    duplicate_group = db.relationship('DuplicateGroup', back_populates='reports')
    duplicate_of = db.relationship('Report', remote_side=[id], back_populates='duplicates')
    duplicates = db.relationship('Report', back_populates='duplicate_of')
    photos = db.relationship('ReportPhoto', back_populates='report', order_by='ReportPhoto.id',
                             cascade='all, delete-orphan', passive_deletes=True)
    # now() は同一トランザクション内で同じ値になるため、id でも並べて順序を確定させる
    histories = db.relationship('ReportStatusHistory', back_populates='report',
                                order_by='[ReportStatusHistory.created_at, ReportStatusHistory.id]')
    ai_analyses = db.relationship('AiAnalysis', back_populates='report',
                                  order_by='[AiAnalysis.created_at.desc(), AiAnalysis.id.desc()]')

    @property
    def latest_ai_analysis(self):
        """最新の AI 判定結果（なければ None）"""
        return self.ai_analyses[0] if self.ai_analyses else None

    def __repr__(self):
        return f'<Report {self.id} {self.status}>'


class ReportPhoto(db.Model):
    __tablename__ = 'report_photos'

    id = db.Column(db.BigInteger, primary_key=True)
    report_id = db.Column(db.BigInteger, db.ForeignKey('reports.id', ondelete='CASCADE'),
                          nullable=False, index=True)
    # 保存ファイル名（ランダム名、UPLOAD_FOLDER からの相対パス）
    file_path = db.Column(db.String(255), nullable=False, unique=True)
    # MIME タイプ（配信時の Content-Type）
    content_type = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    report = db.relationship('Report', back_populates='photos')
