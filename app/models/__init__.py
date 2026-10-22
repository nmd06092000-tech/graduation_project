# 全モデルをここで import する（Flask-Migrate がテーブルを検出できるようにするため）
# 新しいモデルを追加したら、ここにも import を追加すること
from app.models.user import User  # noqa: F401
from app.models.department import Department  # noqa: F401
from app.models.category import Category  # noqa: F401
from app.models.duplicate_group import DuplicateGroup  # noqa: F401
from app.models.report import Report, ReportPhoto  # noqa: F401
from app.models.status_history import ReportStatusHistory  # noqa: F401
from app.models.ai_analysis import AiAnalysis  # noqa: F401
