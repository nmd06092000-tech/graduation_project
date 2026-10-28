# 分類器（キーワード版）のテスト
# Gemini の呼び出し（gemini_client・llm_classifier）は SDK の Client の代わりに FakeClient を渡して確認する（実 API は呼ばない）
import json

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from app.ai.base import AIError, Choice, ClassificationChoices, ImageInput
from app.ai.gemini_client import GeminiClient, GeminiSettings, prepare_image
from app.ai.llm_classifier import (
    GeminiAddressEstimator, GeminiPhotoChecker, GeminiReportClassifier, ReportJudgement,
)
from app.ai.rule_based import RuleBasedClassifier
from tests.conftest import jpeg_bytes

CHOICES = ClassificationChoices(
    categories=[Choice('road', '道路'), Choice('streetlight', '街灯'), Choice('park', '公園'),
                Choice('illegal_dumping', '不法投棄'), Choice('other', 'その他')],
    urgencies=[Choice('high', '高'), Choice('medium', '中'), Choice('low', '低')],
    departments=[Choice('1', '道路管理課'), Choice('2', '道路照明課')],
)


# ---- キーワード判定 ----

@pytest.mark.parametrize('text, category, urgency', [
    ('道路に大きな穴が開いていて危険です', 'road', 'high'),
    ('交差点の街灯が点灯していません', 'streetlight', 'medium'),
    ('公園のブランコが破損しています', 'park', 'high'),
    ('空き地にゴミが捨てられています', 'illegal_dumping', 'medium'),
    ('壁に落書きがあります', 'other', 'low'),
])
def test_rule_based(text, category, urgency):
    result = RuleBasedClassifier().classify(text, choices=CHOICES)
    assert (result.category_code, result.urgency_code) == (category, urgency)
    assert result.department_code is None and result.model_name == 'rule_based'


# ---- Gemini（FakeClient） ----

def make_response(payload, *, finish=types.FinishReason.STOP, model='gemini-test-001'):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(
            content=types.Content(role='model', parts=[types.Part(text=text)] if text else []),
            finish_reason=finish,
        )],
        model_version=model,
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=120, candidates_token_count=40),
    )


class FakeModels:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({'model': model, 'contents': contents, 'config': config})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result):
        self.models = FakeModels(result)


SETTINGS = GeminiSettings(api_key='test-key', model='gemini-test')
GOOD = {'category_code': 'road', 'urgency_code': 'high', 'department_code': '1',
        'reason': '車両事故の恐れ', 'confidence': 0.82}


def _client(result):
    return GeminiClient(SETTINGS, client=FakeClient(result))


def test_classify_success_and_request_shape():
    fake = FakeClient(make_response(GOOD))
    classifier = GeminiReportClassifier(GeminiClient(SETTINGS, client=fake))

    result = classifier.classify('家の前の道路に穴があります', [ImageInput(b'\xff\xd8fake', 'image/jpeg')],
                                 choices=CHOICES)

    assert (result.category_code, result.urgency_code, result.department_code) == ('road', 'high', '1')
    assert result.model_name == 'gemini-test-001' and result.warnings == []
    assert result.raw_response['prompt_tokens'] == 120
    call = fake.models.calls[0]
    assert call['model'] == 'gemini-test'
    assert call['config'].response_mime_type == 'application/json'
    assert isinstance(call['contents'][0], types.Part)        # 画像が先
    assert '<report>' in call['contents'][-1]                 # 本文はタグで囲む
    assert 'road: 道路' in call['contents'][-1]               # 候補は DB から渡したもの


def test_unknown_codes_are_dropped_and_confidence_clamped():
    bad = {**GOOD, 'category_code': 'river', 'department_code': '', 'confidence': 3}
    result = GeminiReportClassifier(_client(make_response(bad))).classify('川があふれそう', choices=CHOICES)

    assert result.category_code is None and result.department_code is None
    assert any('river' in w for w in result.warnings)
    assert result.confidence == 1.0


def test_photo_checker_and_address_estimator():
    checker = GeminiPhotoChecker(_client(make_response({'in_focus': False, 'appropriate': True})))
    assert checker.check(ImageInput(b'x', 'image/jpeg')).in_focus is False

    estimator = GeminiAddressEstimator(_client(make_response({'address': ' 埼玉県さいたま市大宮区桜木町1丁目 付近 '})))
    assert estimator.estimate(35.906, 139.624).address == '埼玉県さいたま市大宮区桜木町1丁目 付近'
    empty = GeminiAddressEstimator(_client(make_response({'address': ''})))
    assert empty.estimate(0, 0).address is None


@pytest.mark.parametrize('code, kind', [(429, 'quota'), (403, 'auth'), (400, 'client')])
def test_client_errors_map_to_ai_error(code, kind):
    error = genai_errors.ClientError(code, {'error': {'code': code, 'message': 'x', 'status': 'S'}})
    with pytest.raises(AIError) as e:
        _client(error).generate_json(schema=ReportJudgement, system_instruction='s', prompt='p')
    assert e.value.kind == kind and e.value.status_code == code


def test_server_error_and_timeout():
    error = genai_errors.ServerError(503, {'error': {'code': 503, 'message': 'x', 'status': 'UNAVAILABLE'}})
    with pytest.raises(AIError) as e:
        _client(error).generate_json(schema=ReportJudgement, system_instruction='s', prompt='p')
    assert e.value.kind == 'server'

    class ReadTimeout(Exception):
        pass
    with pytest.raises(AIError) as e:
        _client(ReadTimeout()).generate_json(schema=ReportJudgement, system_instruction='s', prompt='p')
    assert e.value.kind == 'timeout'


def test_invalid_json_and_max_tokens():
    with pytest.raises(AIError) as e:
        _client(make_response('{"category_code": 1')).generate_json(
            schema=ReportJudgement, system_instruction='s', prompt='p')
    assert e.value.kind == 'invalid_response'

    with pytest.raises(AIError) as e:
        _client(make_response('', finish=types.FinishReason.MAX_TOKENS)).generate_json(
            schema=ReportJudgement, system_instruction='s', prompt='p')
    assert '上限' in str(e.value)


def test_settings_require_key_and_model():
    with pytest.raises(AIError) as e:
        GeminiSettings.from_config({'GEMINI_MODEL': 'm'})
    assert e.value.kind == 'config'
    settings = GeminiSettings.from_config({'GEMINI_API_KEY': 'k', 'GEMINI_MODEL': 'm', 'GEMINI_TIMEOUT_MS': '5000'})
    assert settings.timeout_ms == 5000


# 送信前の画像は JPEG に変換し、EXIF（GPS 情報）を除く
def test_prepare_image_strips_exif():
    from app.services.storage_service import extract_gps
    image = prepare_image(jpeg_bytes(gps=(35.9, 139.6)), 'image/jpeg')
    assert image.mime_type == 'image/jpeg'
    assert extract_gps(image.data) is None
