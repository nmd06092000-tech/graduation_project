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
    # 職員が AI分析画面の「再実行」ボタンで実行した判定
    MANUAL = ('manual', '手動再実行')


# AI 判定失敗の種類（ai_analyses.error_message 先頭の [kind]）の画面表示ラベル。AIError.kind と対応
AI_ERROR_KIND_LABELS = {
    'config': '設定エラー',
    'client': '画像・入力エラー',
    'quota': '利用上限エラー',
    'auth': '認証エラー',
    'server': 'AIサーバーエラー',
    'timeout': 'タイムアウト',
    'network': '通信エラー',
    'blocked': '安全性によるブロック',
    'invalid_response': '応答形式エラー',
    'unknown': '不明なエラー',
}
# 報告詳細に出す案内文は種類ごとの固定文のみ（技術的な詳細は構成情報を含み得るため画面に出さない。DB とログには残る）
_AI_ERROR_CONFIG_MESSAGE = 'AI判定の設定に問題があるため、判定できませんでした。管理者へお問い合わせください。'
_AI_ERROR_RETRY_SUFFIX = '時間をおいて再実行してください。'
AI_ERROR_KIND_MESSAGES = {
    'config': _AI_ERROR_CONFIG_MESSAGE,
    'auth': _AI_ERROR_CONFIG_MESSAGE,
    'client': '添付写真または報告内容をAIが正常に読み取れず、判定できませんでした。',
    'quota': 'AIの利用上限に達したため、判定できませんでした。' + _AI_ERROR_RETRY_SUFFIX,
    'server': 'AIサーバーで障害が発生したため、判定できませんでした。' + _AI_ERROR_RETRY_SUFFIX,
    'timeout': 'AIの応答に時間がかかりすぎたため、判定できませんでした。' + _AI_ERROR_RETRY_SUFFIX,
    'network': 'AIモデルに接続できず、判定できませんでした。通信状況を確認して再実行してください。',
    'blocked': 'AIの安全性チェックにより判定できませんでした。報告内容を職員が確認してください。',
    'invalid_response': 'AIの判定結果を読み取れなかったため、判定できませんでした。再実行してください。',
    'unknown': 'AI判定に失敗しました。' + _AI_ERROR_RETRY_SUFFIX,
}


class CitizenNotificationType(LabeledEnum):
    """市民向けお知らせの種類（citizen_notifications.type）。お知らせ画面のバッジに表示する"""
    REPORT_RECEIVED = ('report_received', '報告受付')
    AI_JUDGED = ('ai_judged', 'AI判定')
    STAFF_UPDATE = ('staff_update', '職員から')


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


# 共通：CSRF トークンが無い・不正・セッション切れのとき（設計書にない業務外のエラー）
MSG_CSRF_EXPIRED = 'セッションの有効期限が切れました。もう一度操作してください。'


# 職員画面のメッセージ（ユースケース記述_行政職員 の文言どおり）
# 詳細・対応更新画面 基本フロー3・代替フロー2a-1
MSG_STATUS_UPDATED = '報告 #{report_id} のステータスを「{status}」に更新しました！'
MSG_COMMENT_REQUIRED = '担当者メモ・進捗コメントを入力してください。'
# 地図表示 代替フロー6a-1
MSG_MAP_NO_RESULT = '絞り込み結果が存在しません。'
# AI分析・自動判定ログ 代替フロー4a-1
MSG_AI_LOG_NOT_FOUND = 'AIログのタイトルが存在しません'
# AI分析・自動判定ログ「AIモデル再実行」（設計書にない新規の文言。計画 A20261124-123404 で承認済み）
MSG_AI_RERUN_DONE = '報告 #{report_id} のAI判定を再実行しました（緊急度「{urgency}」）。'
MSG_AI_RERUN_FALLBACK = '報告 #{report_id} はAIモデルが応答しなかったため、キーワード判定で再実行しました。'
MSG_AI_RERUN_FAILED = '報告 #{report_id} のAI判定に失敗しました。時間をおいて再度実行してください。'
MSG_AI_RERUN_CANCELLED = '取り消された報告はAI再実行の対象外です。'
MSG_AI_RERUN_NO_TARGET = '再実行が必要な報告はありません。'
MSG_AI_RERUN_BATCH_DONE = 'AIモデルを再実行しました（成功 {succeeded} 件 / キーワード判定 {fallback} 件 / 失敗 {failed} 件）。'
MSG_AI_RERUN_REMAINING = '対象が多いため今回は{executed}件のみ実行しました。残り{remaining}件は再度実行してください。'
# 対応管理・重複確認 代替フロー1a-1
MSG_NO_DUPLICATES = '現在重複した項目がありません'
# 代表案件に統合する 基本フロー5
MSG_GROUP_MERGED = '重複グループ #{group_id} を代表案件（#{report_id}）に統合しました！'
# 一括対応ステータス変更 基本フロー5
MSG_GROUP_STATUS_UPDATED = '重複グループ #{group_id} の対応ステータスを『{status}』に一括更新しました！'
# 重複なしにする（設計書にない操作。確認待ちのグループを重複なしにして一覧から外す）
MSG_GROUP_DISMISSED = '重複グループ {group_id} を重複なしにしました。'
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

# 市民のお知らせ（設計書にない新規の文言。計画 A20261118-123834 で承認済み）
# 一覧に出す1行の文言（message）と、開いたときに表示する詳細（detail）。職員名・内部メモ・緊急度の理由は含めない
# 報告を受け付けたとき
MSG_CNOTICE_RECEIVED = '報告 #{report_id}「{title}」を受け付けました。'
MSG_CNOTICE_RECEIVED_DETAIL = ('報告 #{report_id}「{title}」を受け付けました。'
                               '内容は職員が確認します。対応状況は報告一覧で確認できます。')
# AI の判定（初回）が終わったとき
MSG_CNOTICE_AI_JUDGED = '報告 #{report_id}「{title}」のAI判定が完了しました（緊急度：{urgency}）。'
MSG_CNOTICE_AI_JUDGED_DETAIL = ('報告 #{report_id}「{title}」をAIが判定しました。カテゴリ：{category}、緊急度：{urgency}。'
                                'この判定は目安で、最終的な緊急度は職員が内容を確認して決定します。')
# 職員が対応状況を変更したとき（公開メッセージがあれば detail の末尾に付ける）
MSG_CNOTICE_STATUS = '報告 #{report_id}「{title}」の対応状況が「{status}」になりました。'
MSG_CNOTICE_STATUS_DETAIL = ('報告 #{report_id}「{title}」の対応状況が「{from_status}」から「{status}」に変更されました。')
MSG_CNOTICE_STAFF_MESSAGE = '職員からのメッセージ：{message}'


# ログイン・アカウント作成・パスワード（ユースケース記述の文言どおり）
# {label} は ID 欄の名前（市民: メールアドレス / 職員: 職員ID / 管理者: 管理者ID）
# ログイン 代替フロー（全て未入力）
MSG_ID_OR_PASSWORD_REQUIRED = '{label}またはパスワードを入力してください'
# ログイン 代替フロー（ID 未入力・パスワード未入力・8文字未満・職員/管理者 ID に ＠）
MSG_ID_REQUIRED = '{label}を入力してください。'
MSG_PASSWORD_REQUIRED = 'パスワードを入力してください。'
MSG_PASSWORD_TOO_SHORT = 'このテキストは8文字以上で入力してください（現在は{length}文字です）。'
MSG_ID_HAS_AT = "{label}に'＠'は使用できません。"
# 設計書にない業務エラー（ログイン失敗・利用停止・一時ロック）
MSG_LOGIN_FAILED = '{label}またはパスワードが正しくありません'
MSG_ACCOUNT_DISABLED = 'このアカウントは利用停止されています'
MSG_LOGIN_LOCKED = 'ログインに連続して失敗したため、アカウントを一時的にロックしました。{minutes}分後にもう一度お試しください。'
# 設計書にない入力チェック・業務エラー（パスワード確認・flask コマンドによるアカウント操作）
MSG_PASSWORD_MISMATCH = 'パスワードが一致しません。'
MSG_ID_TAKEN = 'この{label}は既に登録されています。'
MSG_USER_NOT_FOUND = '「{login_id}」のアカウントが見つかりません。'
MSG_NO_ADMIN_LEFT = '有効な管理者アカウントが他に残らないため、実行できません。先に create-admin で管理者を作成してください。'
# 以下 3 つは設計書にない新規の文言（flask コマンドの create-admin / set-password）
MSG_PASSWORD_PUBLIC = 'このパスワードは公開されているため使用できません。'
MSG_ID_TOO_LONG = '{label}は255文字以内で入力してください。'
MSG_ACCOUNT_SAVE_FAILED = 'アカウントを保存できませんでした。しばらくしてからもう一度お試しください。'

# 設計書にない新規の文言（2b：パスワード再設定・パスワード変更・職員アカウント登録）
# 市民のパスワード再設定（メール）：メールアドレスが登録されているかどうかに関わらず、同じ文言を表示する
MSG_RESET_MAIL_SENT = ('入力したメールアドレスが登録されている場合、確認メールを送信しました。'
                       'メールのリンクを開き、パスワードの変更を確定してください。')
MSG_RESET_LINK_INVALID = 'このリンクは無効か、有効期限が切れています。もう一度メールアドレスを入力して、再設定をやり直してください。'
# 職員・管理者のパスワード再設定（運用者が発行する再設定コード）。ID が存在するかどうかが分からないよう、原因は区別しない
MSG_RESET_CODE_REQUIRED = '再設定コードを入力してください。'
MSG_RESET_CODE_INVALID = 'IDまたは再設定コードが正しくありません。コードの有効期限が切れている場合は、管理者に再発行を依頼してください。'
# issue-reset-code（flask コマンド）と、管理者画面の再設定コード発行（user_service）で使う
MSG_RESET_CODE_NOT_ALLOWED = '「{login_id}」は有効な職員・管理者アカウントではないため、再設定コードを発行できません。'
# 市民のパスワード変更
MSG_PASSWORD_CHANGED = 'パスワードを変更しました'
MSG_CURRENT_PASSWORD_REQUIRED = '現在のパスワードを入力してください。'
MSG_CURRENT_PASSWORD_WRONG = '現在のパスワードが正しくありません。'
MSG_PASSWORD_SAME_AS_CURRENT = '新しいパスワードは、現在のパスワードと異なるものにしてください。'
# 管理者によるアカウント登録（職員は所属部署が必須）
MSG_ROLE_REQUIRED = 'アカウント権限を選択してください。'
MSG_DEPARTMENT_REQUIRED = '所属部署を選択してください。'
MSG_DEPARTMENT_INVALID = '所属部署が見つかりません。'

# 設計書にない新規の文言（3a：管理者によるアカウント管理・削除済みアカウントの復元）
# 画面の操作（削除・ロック解除・再設定コード発行）の対象が存在しない・削除済み・市民のとき。存在の有無は区別しない
MSG_ACCOUNT_TARGET_NOT_FOUND = '対象のアカウントが見つかりません。'
MSG_CANNOT_DELETE_SELF = '自分自身のアカウントは削除できません。'
MSG_CANNOT_DELETE_LAST_ADMIN = '有効な管理者が他にいなくなるため、このアカウントは削除できません。'
MSG_ACCOUNT_DELETED = '「{name}」（{login_id}）のアカウントを削除しました。'
MSG_ACCOUNT_UNLOCKED = '「{name}」（{login_id}）のロックを解除しました。'
# 発行直後の表示時間（120秒）が過ぎた、または再読み込みで消えたとき（警告）
MSG_RESET_CODE_GONE = '再設定コードの表示時間が過ぎました。必要な場合は、もう一度発行してください。'
# flask コマンド（restore-user / set-password）専用
MSG_ACCOUNT_NOT_DELETED = '「{login_id}」は削除されていないアカウントです。'
MSG_ACCOUNT_DELETED_RESTORE_FIRST = '「{login_id}」は削除済みのアカウントです。先に restore-user で復元してください。'
MSG_ACCOUNT_RESTORED = '「{login_id}」を復元しました。'

# 設計書にない新規の文言（3b：マスタ管理＝部署・カテゴリの追加・名称変更・非表示／再表示）
# {label} は 部署名 / カテゴリ名、{item} は 部署 / カテゴリ
MSG_DEPARTMENT_NAME_REQUIRED = '部署名を入力してください。'
# カテゴリ追加で名前・既定の担当部署の両方が空のとき（赤）。片方だけ空ならそれぞれ個別の文言（オレンジ）
MSG_CATEGORY_NAME_DEPARTMENT_REQUIRED = 'カテゴリ名と既定の担当部署を入力してください。'
MSG_CATEGORY_NAME_REQUIRED = 'カテゴリ名を入力してください。'
MSG_CATEGORY_DEFAULT_DEPARTMENT_REQUIRED = '既定の担当部署を選択してください。'
MSG_MASTER_NAME_TOO_LONG = '{label}は50文字以内で入力してください。'
MSG_MASTER_NAME_INVALID = '{label}に使用できない文字が含まれています。'
MSG_MASTER_NAME_TAKEN = 'この{label}は既に登録されています。'
MSG_MASTER_NAME_TAKEN_HIDDEN = 'この{label}は非表示の{item}として登録されています。'
# 画面の操作の対象が存在しない（範囲外の ID を含む）とき
MSG_MASTER_TARGET_NOT_FOUND = '対象の{item}が見つかりません。'
MSG_CATEGORY_DEFAULT_DEPARTMENT_INVALID = '既定の担当部署が見つかりません。'
MSG_DEPARTMENT_IN_USE_BY_CATEGORY = ('既定の担当部署として使われているカテゴリ（{names}）があるため、非表示にできません。'
                                     '先にカテゴリの既定の担当部署を変更してください。')
MSG_CATEGORY_OTHER_PROTECTED = '「{name}」はAI判定の既定の分類として使うため、非表示にできません。'
MSG_CATEGORY_RESTORE_DEPARTMENT_HIDDEN = ('既定の担当部署「{department}」が非表示のため、表示できません。'
                                          '先に部署を表示してください。')
MSG_MASTER_SAVE_FAILED = '保存できませんでした。しばらくしてからもう一度お試しください。'
MSG_DEPARTMENT_CREATED = '部署「{name}」を登録しました。'
MSG_DEPARTMENT_UPDATED = '部署「{name}」を更新しました。'
MSG_DEPARTMENT_HIDDEN = '部署「{name}」を非表示にしました。'
MSG_DEPARTMENT_RESTORED = '部署「{name}」を表示に戻しました。'
MSG_CATEGORY_CREATED = 'カテゴリ「{name}」を登録しました。'
MSG_CATEGORY_UPDATED = 'カテゴリ「{name}」を更新しました。'
MSG_CATEGORY_HIDDEN = 'カテゴリ「{name}」を非表示にしました。'
MSG_CATEGORY_RESTORED = 'カテゴリ「{name}」を表示に戻しました。'

# パスワード再設定メール（件名・本文。{name} はお名前、{url} は再設定ページのリンク、{minutes} は有効期限（分））
MAIL_RESET_SUBJECT = '【困りごと報告システム】パスワード再設定のご案内'
MAIL_RESET_BODY = (
    '{name} 様\n'
    '\n'
    'パスワード再設定のご依頼を受け付けました。\n'
    '下記のリンクを開き、表示された画面で「パスワードの変更を確定する」ボタンを押すと、パスワードが変更されます'
    '（{minutes}分間有効・1回のみ使えます）。リンクを開くだけでは変更されません。\n'
    '\n'
    '{url}\n'
    '\n'
    '心当たりがない場合はリンクを開かないでください。パスワードは変更されません。\n'
)

# 開発用テストアカウントの公開パスワード（README に載っていた値。秘密ではない）。
# remove-test-users の対象判定と scripts/seed.py のテストアカウント作成で共用し、
# create-admin / set-password ではこの値を新しいパスワードとして受け付けない
TEST_ACCOUNT_PW = 'password123'
