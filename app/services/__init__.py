# 業務ロジック層。request や render_template をここで使わないこと


class ServiceError(Exception):
    """業務エラー（message は画面にそのまま表示できる日本語）。ルートで受けて flash する"""


class ValidationError(ServiceError):
    """入力チェックのエラー。messages は (flash カテゴリ, 文言) のリスト

    設計書の色分け：赤（全て未入力など）= 'danger'、オレンジ（項目ごと）= 'warning'
    """

    def __init__(self, messages):
        super().__init__(' / '.join(text for _, text in messages))
        self.messages = messages
