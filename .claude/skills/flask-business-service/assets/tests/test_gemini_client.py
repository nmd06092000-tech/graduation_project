"""Gemini ラッパーと判定クラスのテスト（実 API は呼ばない）。

配置先の例: tests/test_gemini_client.py
SDK の Client の代わりに FakeClient を渡し、応答・例外を差し替える。
"""
import json

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from app.ai.gemini_client import AIError, GeminiClient, GeminiSettings, ImageInput
from app.ai.gemini_classifier import Choice, GeminiReportClassifier, JudgeChoices, ReportJudgement


def make_response(payload, *, finish=types.FinishReason.STOP, model="gemini-test-001"):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text=text)] if text else []),
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
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result):
        self.models = FakeModels(result)


SETTINGS = GeminiSettings(api_key="test-key", model="gemini-test")
CHOICES = JudgeChoices(
    categories=[Choice("road", "道路"), Choice("park", "公園")],
    urgencies=[Choice("high", "高"), Choice("medium", "中"), Choice("low", "低")],
    departments=[Choice("doboku", "土木課")],
)
GOOD = {"category_code": "road", "urgency_code": "high", "department_code": "doboku",
        "summary": "道路に大きな穴", "reason": "車両事故の恐れ", "confidence": 0.82}


def test_classify_success_and_request_shape():
    fake = FakeClient(make_response(GOOD))
    clf = GeminiReportClassifier(GeminiClient(SETTINGS, client=fake))
    res = clf.classify("家の前の道路に穴があります", [ImageInput(b"\xff\xd8fake", "image/jpeg")], choices=CHOICES)

    assert (res.category_code, res.urgency_code, res.department_code) == ("road", "high", "doboku")
    assert res.model == "gemini-test-001" and res.prompt_tokens == 120 and res.warnings == []
    call = fake.models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["config"].response_mime_type == "application/json"
    assert isinstance(call["contents"][0], types.Part)          # 画像が先
    assert "<report>" in call["contents"][-1]                   # 本文はタグで囲む
    assert "road: 道路" in call["contents"][-1]                 # 候補は DB から渡したもの


def test_unknown_codes_are_dropped():
    bad = {**GOOD, "category_code": "river", "department_code": ""}
    clf = GeminiReportClassifier(GeminiClient(SETTINGS, client=FakeClient(make_response(bad))))
    res = clf.classify("川があふれそう", choices=CHOICES)
    assert res.category_code is None and res.department_code is None
    assert any("river" in w for w in res.warnings)


def test_confidence_is_clamped():
    clf = GeminiReportClassifier(GeminiClient(SETTINGS, client=FakeClient(make_response({**GOOD, "confidence": 3}))))
    assert clf.classify("x", choices=CHOICES).confidence == 1.0


@pytest.mark.parametrize("code,kind", [(429, "quota"), (403, "auth"), (400, "client")])
def test_client_errors_map_to_ai_error(code, kind):
    err = genai_errors.ClientError(code, {"error": {"code": code, "message": "x", "status": "S"}})
    client = GeminiClient(SETTINGS, client=FakeClient(err))
    with pytest.raises(AIError) as ei:
        client.generate_json(schema=ReportJudgement, system_instruction="s", prompt="p")
    assert ei.value.kind == kind and ei.value.status_code == code


def test_server_error_and_timeout():
    err = genai_errors.ServerError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
    with pytest.raises(AIError) as ei:
        GeminiClient(SETTINGS, client=FakeClient(err)).generate_json(schema=ReportJudgement, system_instruction="s", prompt="p")
    assert ei.value.kind == "server"

    class ReadTimeout(Exception):
        pass
    with pytest.raises(AIError) as ei:
        GeminiClient(SETTINGS, client=FakeClient(ReadTimeout())).generate_json(schema=ReportJudgement, system_instruction="s", prompt="p")
    assert ei.value.kind == "timeout"


def test_invalid_json_and_max_tokens():
    with pytest.raises(AIError) as ei:
        GeminiClient(SETTINGS, client=FakeClient(make_response('{"category_code": 1'))).generate_json(
            schema=ReportJudgement, system_instruction="s", prompt="p")
    assert ei.value.kind == "invalid_response"

    with pytest.raises(AIError) as ei:
        GeminiClient(SETTINGS, client=FakeClient(make_response("", finish=types.FinishReason.MAX_TOKENS))).generate_json(
            schema=ReportJudgement, system_instruction="s", prompt="p")
    assert "上限" in str(ei.value)


def test_settings_require_key_and_model():
    with pytest.raises(AIError) as ei:
        GeminiSettings.from_config({"GEMINI_MODEL": "m"})
    assert ei.value.kind == "config"
    s = GeminiSettings.from_config({"GEMINI_API_KEY": "k", "GEMINI_MODEL": "m", "GEMINI_TIMEOUT_MS": "5000"})
    assert s.timeout_ms == 5000
