# LLM（Gemini・画像対応）を使う AI 機能：報告の分類、撮影写真のチェック、緯度経度からの住所推定
# JSON 応答を検証して base.py の結果クラスに変換する。候補外のコードは破棄する
import json

from pydantic import BaseModel, Field

from app.ai.base import AddressEstimate, ClassificationResult, PhotoCheckResult
from app.ai.prompts import (
    ADDRESS_INSTRUCTION, CLASSIFY_INSTRUCTION, PHOTO_CHECK_INSTRUCTION, PROMPT_VERSION,
    build_address_prompt, build_classify_prompt,
)


class ReportJudgement(BaseModel):
    """Gemini に返させる JSON の形（値の検証は _to_result で行う）"""
    category_code: str = Field(description='カテゴリ候補のコード。判断できなければ空文字')
    urgency_code: str = Field(description='緊急度候補のコード')
    department_code: str = Field(description='担当部署候補のコード。判断できなければ空文字')
    reason: str = Field(description='判定理由（職員向け、100字以内、個人情報を含めない）')
    confidence: float = Field(description='判定の確からしさ（0.0〜1.0）')


class PhotoCheck(BaseModel):
    in_focus: bool = Field(description='ピントが合っていれば true')
    appropriate: bool = Field(description='地域の困りごとの報告に使える写真なら true')


class AddressAnswer(BaseModel):
    address: str = Field(description='推定した住所（町名・丁目まで）。推定できなければ空文字')


def _raw(res):
    """ai_analyses.raw_response に保存する内容（応答 JSON・プロンプト版・処理時間・トークン数）"""
    try:
        response = json.loads(res.raw_text)
    except ValueError:
        response = res.raw_text
    return {'response': response, 'prompt_version': PROMPT_VERSION, 'latency_ms': res.latency_ms,
            'prompt_tokens': res.prompt_tokens, 'output_tokens': res.output_tokens}


class GeminiReportClassifier:
    """新規報告 基本フロー3：報告の分類・緊急度判定"""

    def __init__(self, client):
        self._client = client
        self.model_name = client.settings.model

    def classify(self, text, photos=(), *, choices):
        res = self._client.generate_json(
            schema=ReportJudgement,
            system_instruction=CLASSIFY_INSTRUCTION,
            prompt=build_classify_prompt(text, choices),
            images=photos,
        )
        return self._to_result(res, choices)

    @staticmethod
    def _to_result(res, choices):
        v, warnings = res.value, []

        def pick(code, items, label):
            code = (code or '').strip()
            if not code:
                return None
            if code in {c.code for c in items}:
                return code
            warnings.append(f"{label}: 候補外のコード '{code}' を破棄")
            return None

        raw = _raw(res)
        raw['warnings'] = warnings
        return ClassificationResult(
            category_code=pick(v.category_code, choices.categories, 'category'),
            urgency_code=pick(v.urgency_code, choices.urgencies, 'urgency'),
            department_code=pick(v.department_code, choices.departments, 'department'),
            confidence=min(max(float(v.confidence), 0.0), 1.0),
            model_name=res.model,
            raw_response=raw,
            warnings=warnings,
        )


class GeminiPhotoChecker:
    """写真撮影 代替フロー4b-1・4c-1：ピントと、報告に適した写真かを確認する"""

    def __init__(self, client):
        self._client = client

    def check(self, photo):
        res = self._client.generate_json(
            schema=PhotoCheck,
            system_instruction=PHOTO_CHECK_INSTRUCTION,
            prompt='この写真を確認してください。',
            images=[photo],
        )
        return PhotoCheckResult(in_focus=res.value.in_focus, appropriate=res.value.appropriate,
                                model_name=res.model)


class GeminiAddressEstimator:
    """位置情報の確認 基本フロー3：緯度・経度から住所を推定する"""

    def __init__(self, client):
        self._client = client

    def estimate(self, latitude, longitude):
        res = self._client.generate_json(
            schema=AddressAnswer,
            system_instruction=ADDRESS_INSTRUCTION,
            prompt=build_address_prompt(latitude, longitude),
        )
        address = res.value.address.strip()[:255]
        return AddressEstimate(address=address or None, model_name=res.model)
