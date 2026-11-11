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
    CONTRACTOR = ('contractor', '業者対応中')
    CONFIRMING = ('confirming', '完了確認中')
    ON_HOLD = ('on_hold', '保留・他部署へ引継ぎ')
    WATCHING = ('watching', '保留・経過観察')
    RESOLVED = ('resolved', '対応完了')
    NOT_APPLICABLE = ('not_applicable', '対象外・対応不要')
    CANCELLED = ('cancelled', '取り消し')


class Urgency(LabeledEnum):
    HIGH = ('high', '高')
    MEDIUM = ('medium', '中')
    LOW = ('low', '低')


class AiAnalysisStatus(LabeledEnum):
    SUCCEEDED = ('succeeded', '判定完了・自動分類済み')
    FAILED = ('failed', '判定失敗')


class AiAnalysisTrigger(LabeledEnum):
    """AI 判定のきっかけ（ai_analyses.trigger_type）。AI分析画面の「再判定状況」に表示する"""
    INITIAL = ('initial', '初回判定')
    REJUDGE = ('rejudge', '再判定済み')


class DuplicateGroupStatus(LabeledEnum):
    OPEN = ('open', '確認待ち')
    MERGED = ('merged', '統合済み')
    DISMISSED = ('dismissed', '重複なし')


class LocationSource(LabeledEnum):
    """報告の緯度・経度の取得元（reports.location_source）。PHOTO・DEVICE が GPS"""
    PHOTO = ('photo', '写真の位置情報（GPS）')
    DEVICE = ('device', '端末の位置情報（GPS）')
    ADDRESS = ('address', '発生場所の住所から検索')

    @classmethod
    def gps_values(cls):
        return [cls.PHOTO.value, cls.DEVICE.value]


# 緊急度の補足説明（ダッシュボード・地図の凡例で共通利用）
URGENCY_DESCRIPTIONS = {
    Urgency.HIGH: '即時対応',
    Urgency.MEDIUM: '優先対応',
    Urgency.LOW: '通常対応',
}

# 未完了（職員の対応が必要）とみなすステータス（地図・優先対応案件・要人間チェックの対象）
OPEN_STATUSES = (
    ReportStatus.RECEIVED, ReportStatus.IN_PROGRESS, ReportStatus.CONTRACTOR, ReportStatus.CONFIRMING,
    ReportStatus.ON_HOLD, ReportStatus.WATCHING,
)
# 件数カードの「対応中」にまとめるステータス
WORKING_STATUSES = (ReportStatus.IN_PROGRESS, ReportStatus.CONTRACTOR, ReportStatus.CONFIRMING)
# 件数カードの「保留中」にまとめるステータス
HOLD_STATUSES = (ReportStatus.ON_HOLD, ReportStatus.WATCHING)

# 許可されるステータス遷移：{変更前: {変更後: 変更できるロール}}
# README の状態遷移に合わせる（取り消しは市民のみ、それ以外は職員のみ）
_S = ReportStatus
STATUS_TRANSITIONS = {
    _S.RECEIVED: {
        _S.IN_PROGRESS: {Role.STAFF},
        _S.ON_HOLD: {Role.STAFF},
        _S.WATCHING: {Role.STAFF},
        _S.NOT_APPLICABLE: {Role.STAFF},
        _S.RESOLVED: {Role.STAFF},
        _S.CANCELLED: {Role.CITIZEN},
    },
    _S.IN_PROGRESS: {
        _S.CONTRACTOR: {Role.STAFF},
        _S.CONFIRMING: {Role.STAFF},
        _S.RESOLVED: {Role.STAFF},
        _S.ON_HOLD: {Role.STAFF},
        _S.WATCHING: {Role.STAFF},
    },
    _S.CONTRACTOR: {
        _S.CONFIRMING: {Role.STAFF},
        _S.IN_PROGRESS: {Role.STAFF},
    },
    _S.CONFIRMING: {
        _S.RESOLVED: {Role.STAFF},
        _S.IN_PROGRESS: {Role.STAFF},
    },
    _S.ON_HOLD: {
        _S.IN_PROGRESS: {Role.STAFF},
    },
    _S.WATCHING: {
        _S.IN_PROGRESS: {Role.STAFF},
        _S.RESOLVED: {Role.STAFF},
        _S.NOT_APPLICABLE: {Role.STAFF},
    },
    _S.RESOLVED: {},
    _S.NOT_APPLICABLE: {},
    _S.CANCELLED: {},
}
del _S


def next_statuses(current, role):
    """現在のステータスから、指定ロールが変更できるステータスの一覧"""
    allowed = STATUS_TRANSITIONS.get(ReportStatus(current), {})
    return [status for status, roles in allowed.items() if Role(role) in roles]


# 職員画面のメッセージ（ユースケース記述_行政職員 の文言どおり）
# 詳細・対応更新画面 基本フロー3・代替フロー2a-1
MSG_STATUS_UPDATED = '報告 #{report_id} のステータスを「{status}」に更新しました！'
MSG_COMMENT_REQUIRED = '担当者メモ・進捗コメントを入力してください。'
# 地図表示 代替フロー6a-1
MSG_MAP_NO_RESULT = '絞り込み結果が存在しません。'
# AI分析・自動判定ログ 代替フロー4a-1
MSG_AI_LOG_NOT_FOUND = 'AIログのタイトルが存在しません'
# 対応管理・重複確認 代替フロー1a-1
MSG_NO_DUPLICATES = '現在重複した項目がありません'
# 代表案件に統合する 基本フロー5
MSG_GROUP_MERGED = '重複グループ #{group_id} を代表案件（#{report_id}）に統合しました！'
# 一括対応ステータス変更 基本フロー5
MSG_GROUP_STATUS_UPDATED = '重複グループ #{group_id} の対応ステータスを『{status}』に一括更新しました！'
# 設計書にない業務エラー（不正な遷移・処理済みグループなど）
MSG_INVALID_STATUS = '「{current}」から「{status}」には変更できません。'
# AI緊急度の再判定の通知（ダッシュボード）
MSG_NOTICE_REJUDGED_CHANGED = ('報告 #{report_id}「{title}」をAIが再判定し、緊急度を「{ai_urgency}」と判定しました'
                               '（現在の緊急度：{urgency}）。必要に応じて緊急度を見直してください。')
MSG_NOTICE_REJUDGED_SAME = '報告 #{report_id}「{title}」をAIが再判定しました（緊急度「{ai_urgency}」で変更なし）。'
MSG_NOTICE_REJUDGE_FAILED = '報告 #{report_id}「{title}」のAI再判定に失敗しました。報告詳細で緊急度を確認してください。'
# 報告詳細「緊急度の判定」：AI の判定と異なる緊急度にする場合は理由が必要
MSG_URGENCY_REASON_REQUIRED = 'AIの判定と異なる緊急度にする場合は、判断変更理由を入力してください。'
MSG_INVALID_URGENCY = '緊急度の値が正しくありません。'
MSG_GROUP_NOT_OPEN = 'この重複グループは既に処理済みです。'
MSG_PRIMARY_NOT_IN_GROUP = '代表案件はグループ内の報告から選択してください。'
MSG_GROUP_INVALID_STATUS = '次の報告は「{status}」に変更できません：{report_ids}'


# 市民画面のメッセージ（ユースケース記述_県民 の文言どおり）
# 新規報告 代替フロー2a-1〜2d-1
MSG_REPORT_ALL_REQUIRED = '写真の撮影、位置情報、詳細内容を入力してください。'
MSG_PHOTO_REQUIRED = '写真を撮影してください。'
MSG_LOCATION_REQUIRED = '位置情報を入力してください。'
MSG_DESCRIPTION_REQUIRED = '詳細内容を入力してください。'
# 写真撮影 代替フロー4b-1
MSG_PHOTO_OUT_OF_FOCUS = 'ピントを合わせてください。'
# プロフィール設定 基本フロー3・代替フロー2a-1〜2d-1
MSG_PROFILE_UPDATED = 'プロフィールを更新しました'
MSG_ICON_NOT_SAVED = 'アイコンが保存されていません。'
MSG_NAME_EMAIL_REQUIRED = 'お名前またはメールアドレスを入力してください。'
MSG_NAME_REQUIRED = 'お名前を入力してください。'
MSG_EMAIL_REQUIRED = 'メールアドレスを入力してください。'
# アカウント作成・ログイン 代替フロー（メールアドレスに ＠ がない場合）
MSG_EMAIL_NO_AT = "メールアドレスに'＠'を挿入してください。'{email}'内に'＠'がありません。"
# 設計書にない入力チェック・業務エラー
MSG_TITLE_REQUIRED = '件名を入力してください。'
MSG_TITLE_TOO_LONG = '件名は100文字以内で入力してください。'
MSG_DESCRIPTION_TOO_LONG = '詳細内容は1000文字以内で入力してください。'
# 報告画面の「位置情報」欄（reports.address）。画面の項目名に合わせる
MSG_ADDRESS_TOO_LONG = '位置情報は255文字以内で入力してください。'
# GPS 情報がなく、位置情報欄の住所からも位置を検索できない場合
MSG_ADDRESS_NOT_FOUND = '入力した位置情報の住所から位置を特定できませんでした。住所を確認してください。'
MSG_PHOTO_TOO_MANY = '写真は{max}枚まで添付できます。'
MSG_PHOTO_INVALID = '写真は JPEG・PNG・WebP 形式の画像ファイルを選択してください。'
MSG_PHOTO_TOO_LARGE = '写真は1枚{max_mb}MB以下にしてください。'
MSG_UPLOAD_TOO_LARGE = '送信できるファイルの合計サイズを超えています。'
# 写真撮影 代替フロー4c-1（不適切な写真は報告画面に戻す）
MSG_PHOTO_INAPPROPRIATE = 'この写真は報告に使用できません。困りごとの場所を撮影してください。'
# 位置情報の確認 代替フロー3a-1（写真から GPS 情報を取得できない場合）
MSG_GPS_NOT_FOUND = '写真から位置情報を取得できませんでした。報告画面で位置情報を入力してください。'
MSG_ADDRESS_NOT_ESTIMATED = '住所を推定できませんでした。報告画面で位置情報を入力してください。'
MSG_NAME_TOO_LONG = 'お名前は100文字以内で入力してください。'
MSG_EMAIL_TAKEN = 'このメールアドレスは既に登録されています。'
MSG_ICON_REQUIRED = '画像ファイルを選択してください。'
MSG_ICON_UPDATED = 'プロフィールアイコンを更新しました。'
MSG_ICON_DELETED = 'プロフィールアイコンを削除しました。'
