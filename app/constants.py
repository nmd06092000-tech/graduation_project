# ロール・報告ステータス・緊急度の定数、許可されるステータス遷移、画面表示用の日本語ラベル
# DB には英語のコード（value）を保存し、画面には label の日本語を表示する
from enum import Enum


class LabeledEnum(str, Enum):
    """コードと日本語ラベルを持つ Enum の共通クラス"""

    def __new__(cls, value, label):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    # テンプレートや f-string ではコード（例: received）として出力する
    def __str__(self):
        return self.value

    def __format__(self, format_spec):
        return self.value.__format__(format_spec)

    @classmethod
    def values(cls):
        return [m.value for m in cls]

    @classmethod
    def label_of(cls, value, default=''):
        """コードから日本語ラベルを返す（未設定・不明なコードは default）"""
        try:
            return cls(value).label
        except ValueError:
            return default

    @classmethod
    def check_sql(cls, column):
        """CHECK 制約用の SQL（例: status IN ('received', ...)）"""
        values = ', '.join(f"'{v}'" for v in cls.values())
        return f'{column} IN ({values})'


class Role(LabeledEnum):
    CITIZEN = ('citizen', '市民')
    STAFF = ('staff', '職員')
    ADMIN = ('admin', '管理者')


class ReportStatus(LabeledEnum):
    RECEIVED = ('received', '未対応')
    IN_PROGRESS = ('in_progress', '対応中')
    RESOLVED = ('resolved', '対応済み')
    CANCELLED = ('cancelled', '取り消し')


class Urgency(LabeledEnum):
    HIGH = ('high', '高')
    MEDIUM = ('medium', '中')
    LOW = ('low', '低')


class AiAnalysisStatus(LabeledEnum):
    SUCCEEDED = ('succeeded', '判定完了・自動分類済み')
    FAILED = ('failed', '判定失敗')


class DuplicateGroupStatus(LabeledEnum):
    OPEN = ('open', '確認待ち')
    MERGED = ('merged', '統合済み')
    DISMISSED = ('dismissed', '重複なし')


# 緊急度の補足説明（ダッシュボード・地図の凡例で共通利用）
URGENCY_DESCRIPTIONS = {
    Urgency.HIGH: '即時対応',
    Urgency.MEDIUM: '優先対応',
    Urgency.LOW: '通常対応',
}

# 未完了（職員の対応が必要）とみなすステータス
OPEN_STATUSES = (ReportStatus.RECEIVED, ReportStatus.IN_PROGRESS)

# 許可されるステータス遷移：{変更前: {変更後: 変更できるロール}}
# README の状態遷移（received → in_progress → resolved、received → cancelled は市民のみ）に合わせる
STATUS_TRANSITIONS = {
    ReportStatus.RECEIVED: {
        ReportStatus.IN_PROGRESS: {Role.STAFF},
        ReportStatus.CANCELLED: {Role.CITIZEN},
    },
    ReportStatus.IN_PROGRESS: {
        ReportStatus.RESOLVED: {Role.STAFF},
    },
    ReportStatus.RESOLVED: {},
    ReportStatus.CANCELLED: {},
}


def next_statuses(current, role):
    """現在のステータスから、指定ロールが変更できるステータスの一覧"""
    allowed = STATUS_TRANSITIONS.get(ReportStatus(current), {})
    return [status for status, roles in allowed.items() if Role(role) in roles]
