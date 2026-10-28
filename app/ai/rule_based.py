# キーワードによる分類器（開発・テスト用、AI障害時のフォールバック）
from app.ai.base import ClassificationResult

MODEL_NAME = 'rule_based'

# カテゴリのコード（categories.code）と判定に使うキーワード。上から順に最初に当たったものを採用する
CATEGORY_KEYWORDS = [
    ('streetlight', ['街灯', '電灯', '照明', '球切れ', '点灯', '消灯']),
    ('park', ['公園', '遊具', 'ブランコ', '滑り台', 'すべり台', '砂場', 'ベンチ']),
    ('illegal_dumping', ['不法投棄', 'ゴミ', 'ごみ', '粗大', '廃棄', '捨て']),
    ('road', ['道路', '穴', '陥没', '舗装', 'ひび', '段差', 'ガードレール', '歩道', 'マンホール']),
]
OTHER_CATEGORY = 'other'

# 緊急度「高」とみなすキーワード（人命・安全に関わるもの）
HIGH_URGENCY_KEYWORDS = ['危険', '事故', 'けが', '怪我', '陥没', '倒木', '冠水', '大きな穴', '破損', '転倒', '至急']
# 緊急度「低」とみなすキーワード
LOW_URGENCY_KEYWORDS = ['落書き', '汚れ', '要望', '見た目']

# キーワード判定の確からしさ（AI分析画面の「要人間チェック」に入るよう低めにする）
CONFIDENCE = 0.5


class RuleBasedClassifier:
    """件名・詳細内容のキーワードでカテゴリと緊急度を決める。担当部署は service がカテゴリの既定部署にする"""

    model_name = MODEL_NAME

    def classify(self, text, photos=(), *, choices):
        category_codes = {c.code for c in choices.categories}
        category = next((code for code, words in CATEGORY_KEYWORDS
                         if code in category_codes and any(w in text for w in words)), None)
        if category is None and OTHER_CATEGORY in category_codes:
            category = OTHER_CATEGORY

        if any(w in text for w in HIGH_URGENCY_KEYWORDS):
            urgency = 'high'
        elif any(w in text for w in LOW_URGENCY_KEYWORDS):
            urgency = 'low'
        else:
            urgency = 'medium'

        return ClassificationResult(
            category_code=category,
            urgency_code=urgency,
            department_code=None,
            confidence=CONFIDENCE,
            model_name=MODEL_NAME,
            raw_response={'category_code': category, 'urgency_code': urgency},
        )
