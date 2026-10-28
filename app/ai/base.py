# 分類器のインターフェースと共通の結果クラス ClassificationResult
# AI 層は DB・service を import しない。候補（カテゴリ・緊急度・担当部署）は service から受け取る
from dataclasses import dataclass, field
from typing import Protocol, Sequence


class AIError(Exception):
    """AI 呼び出しの失敗。kind で原因を区別する（service はこれを捕捉して業務処理を続ける）"""

    def __init__(self, message, *, kind='unknown', status_code=None):
        super().__init__(message)
        # config / quota / auth / client / server / timeout / network / blocked / invalid_response / unknown
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True)
class ImageInput:
    """AI に渡す画像（送信前に縮小・EXIF 除去したもの）"""
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class Choice:
    """AI が選べる候補（code は DB に保存する値、label は表示名）"""
    code: str
    label: str


@dataclass(frozen=True)
class ClassificationChoices:
    categories: Sequence[Choice]
    urgencies: Sequence[Choice]
    departments: Sequence[Choice] = ()


@dataclass
class ClassificationResult:
    """報告の分類結果。コードは候補内のものだけ（候補外は None）"""
    category_code: str | None
    urgency_code: str | None
    department_code: str | None
    confidence: float
    model_name: str
    raw_response: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


@dataclass
class PhotoCheckResult:
    """撮影写真のチェック結果（ピント・報告に適した写真か）"""
    in_focus: bool
    appropriate: bool
    model_name: str


@dataclass
class AddressEstimate:
    """緯度・経度から推定した住所（推定できなければ address は None）"""
    address: str | None
    model_name: str


class Classifier(Protocol):
    model_name: str

    def classify(self, text: str, photos: Sequence[ImageInput] = (), *,
                 choices: ClassificationChoices) -> ClassificationResult:
        ...
