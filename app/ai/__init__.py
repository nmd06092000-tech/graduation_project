# 設定 AI_PROVIDER に応じて分類器を返す get_classifier()
# AI_PROVIDER=gemini かつ GEMINI_API_KEY がある場合は Gemini、それ以外はキーワード判定（rule_based）
from flask import current_app

from app.ai.base import AIError
from app.ai.rule_based import RuleBasedClassifier


def gemini_enabled():
    """Gemini を使う設定になっているか（キー未設定なら使わない）"""
    config = current_app.config
    return config.get('AI_PROVIDER') == 'gemini' and bool((config.get('GEMINI_API_KEY') or '').strip())


def _gemini_client():
    # HTTP 接続を使い回すため、アプリごとに 1 つだけ作る
    from app.ai.gemini_client import GeminiClient, GeminiSettings
    extensions = current_app.extensions
    if 'gemini_client' not in extensions:
        extensions['gemini_client'] = GeminiClient(GeminiSettings.from_config(current_app.config))
    return extensions['gemini_client']


def get_classifier():
    """報告の分類器（新規報告 基本フロー3）"""
    if gemini_enabled():
        try:
            from app.ai.llm_classifier import GeminiReportClassifier
            return GeminiReportClassifier(_gemini_client())
        except AIError as e:
            current_app.logger.warning('Gemini を使えないため rule_based を使用: %s', e)
    return RuleBasedClassifier()


def get_fallback_classifier():
    """Gemini が失敗したときに使う分類器"""
    return RuleBasedClassifier()


def get_photo_checker():
    """撮影写真のチェック（写真撮影 4b-1・4c-1）。Gemini を使わない設定では None（チェックしない）"""
    if not gemini_enabled():
        return None
    from app.ai.llm_classifier import GeminiPhotoChecker
    return GeminiPhotoChecker(_gemini_client())


def get_address_estimator():
    """緯度経度からの住所推定（位置情報の確認 3）。Gemini を使わない設定では None（推定しない）"""
    if not gemini_enabled():
        return None
    from app.ai.llm_classifier import GeminiAddressEstimator
    return GeminiAddressEstimator(_gemini_client())
