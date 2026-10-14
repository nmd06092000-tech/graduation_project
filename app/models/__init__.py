# 全モデルをここで import する（Flask-Migrate がテーブルを検出できるようにするため）
# 新しいモデルを追加したら、ここにも import を追加すること
from app.models.user import User  # noqa: F401
