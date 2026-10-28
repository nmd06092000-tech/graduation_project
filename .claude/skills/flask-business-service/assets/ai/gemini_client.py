"""Gemini 呼び出しの共通ラッパー（google-genai SDK / generate_content）。

方針
- API キー・モデル名・タイムアウトは Flask の config から受け取る（ハードコードしない）
- 1 回完結の呼び出し（stateless）。会話履歴やサーバー側保存は使わない
- 出力は JSON スキーマ指定の構造化出力で受け取り、Pydantic で検証する
- SDK の例外はすべて AIError に変換して呼び出し元（service）へ渡す
  → service は AIError を捕捉し、業務処理（保存など）は続行する
- ログに API キー・プロンプト全文・個人情報を出さない

配置先の例: app/ai/gemini_client.py
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Generic, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

try:  # 画像の縮小・EXIF 除去（任意。Pillow が無ければそのまま送る）
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
RETRY_STATUS = [408, 429, 500, 502, 503, 504]


class AIError(Exception):
    """AI 呼び出しの失敗。kind で原因を区別する。"""

    def __init__(self, message: str, *, kind: str = "unknown", status_code: int | None = None):
        super().__init__(message)
        # config / quota / auth / client / server / timeout / network / blocked / invalid_response / unknown
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class AIResult(Generic[T]):
    value: T                    # 検証済みの構造化結果
    model: str                  # 実際に応答したモデル（response.model_version）
    raw_text: str               # 応答 JSON（ai_analyses に保存して後で検証できるように）
    latency_ms: int
    prompt_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str
    model: str
    timeout_ms: int = 20_000
    max_retries: int = 2
    temperature: float = 0.2
    max_output_tokens: int = 2048
    thinking_level: str | None = None   # "minimal" / "low" など。対応モデルのみ。空なら送らない
    max_images: int = 3
    image_max_side: int = 1600          # 長辺をこのピクセルまで縮小（Pillow がある場合）
    image_max_bytes: int = 5 * 1024 * 1024

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "GeminiSettings":
        api_key = (config.get("GEMINI_API_KEY") or "").strip()
        model = (config.get("GEMINI_MODEL") or "").strip()
        if not api_key:
            raise AIError("GEMINI_API_KEY が設定されていません", kind="config")
        if not model:
            raise AIError("GEMINI_MODEL が設定されていません", kind="config")
        return cls(
            api_key=api_key,
            model=model,
            timeout_ms=int(config.get("GEMINI_TIMEOUT_MS", 20_000)),
            max_retries=int(config.get("GEMINI_MAX_RETRIES", 2)),
            temperature=float(config.get("GEMINI_TEMPERATURE", 0.2)),
            max_output_tokens=int(config.get("GEMINI_MAX_OUTPUT_TOKENS", 2048)),
            thinking_level=(config.get("GEMINI_THINKING_LEVEL") or None),
        )


def prepare_image(data: bytes, mime_type: str, *, max_side: int = 1600, max_bytes: int = 5 * 1024 * 1024) -> ImageInput:
    """送信前に画像を整える。Pillow があれば向き補正・縮小・EXIF（位置情報）除去を行う。"""
    if mime_type not in ALLOWED_IMAGE_MIME:
        raise AIError(f"未対応の画像形式です: {mime_type}", kind="client")
    if Image is not None and mime_type in {"image/jpeg", "image/png", "image/webp"}:
        try:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=85)   # 保存し直すことで EXIF は付かない
            data, mime_type = buf.getvalue(), "image/jpeg"
        except Exception:  # 壊れた画像はそのまま判定に回さない
            raise AIError("画像を読み込めませんでした", kind="client")
    if len(data) > max_bytes:
        raise AIError("画像サイズが大きすぎます", kind="client")
    return ImageInput(data=data, mime_type=mime_type)


class GeminiClient:
    """generate_content を JSON 構造化出力で呼ぶ薄いラッパー。アプリ全体で 1 つを共有する。"""

    def __init__(self, settings: GeminiSettings, client: Any | None = None):
        self.settings = settings
        self._client = client or genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(
                timeout=settings.timeout_ms,  # ミリ秒
                retry_options=types.HttpRetryOptions(
                    attempts=settings.max_retries + 1,
                    http_status_codes=RETRY_STATUS,
                ),
            ),
        )

    def generate_json(
        self,
        *,
        schema: type[T],
        system_instruction: str,
        prompt: str,
        images: Sequence[ImageInput] = (),
    ) -> AIResult[T]:
        if len(images) > self.settings.max_images:
            images = list(images)[: self.settings.max_images]
        contents: list[Any] = [types.Part.from_bytes(data=i.data, mime_type=i.mime_type) for i in images]
        contents.append(prompt)

        config_kwargs: dict[str, Any] = dict(
            system_instruction=system_instruction,
            temperature=self.settings.temperature,
            max_output_tokens=self.settings.max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        )
        if self.settings.thinking_level:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=self.settings.thinking_level.upper()
            )
        config = types.GenerateContentConfig(**config_kwargs)

        started = time.monotonic()
        try:
            response = self._client.models.generate_content(
                model=self.settings.model, contents=contents, config=config
            )
        except genai_errors.ClientError as e:
            kind = {429: "quota", 401: "auth", 403: "auth"}.get(e.code, "client")
            logger.warning("Gemini client error code=%s status=%s", e.code, e.status)
            raise AIError(f"Gemini API エラー ({e.code} {e.status})", kind=kind, status_code=e.code) from e
        except genai_errors.ServerError as e:
            logger.warning("Gemini server error code=%s status=%s", e.code, e.status)
            raise AIError(f"Gemini サーバーエラー ({e.code})", kind="server", status_code=e.code) from e
        except genai_errors.APIError as e:
            raise AIError(f"Gemini API エラー ({e.code})", kind="unknown", status_code=e.code) from e
        except Exception as e:  # タイムアウト・接続エラー（httpx など）
            name = type(e).__name__.lower()
            kind = "timeout" if "timeout" in name else "network" if ("connect" in name or "network" in name) else "unknown"
            logger.warning("Gemini call failed: %s", type(e).__name__)
            raise AIError(f"Gemini 呼び出しに失敗しました ({type(e).__name__})", kind=kind) from e
        latency_ms = int((time.monotonic() - started) * 1000)

        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise AIError(f"入力がブロックされました ({feedback.block_reason})", kind="blocked")

        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        finish_name = getattr(finish, "name", str(finish)) if finish is not None else None
        text = getattr(response, "text", None)
        if not text:
            if finish_name == "MAX_TOKENS":
                raise AIError("出力が上限に達しました（GEMINI_MAX_OUTPUT_TOKENS を確認）", kind="invalid_response")
            raise AIError(f"応答が空です (finish_reason={finish_name})",
                          kind="blocked" if finish_name and finish_name != "STOP" else "invalid_response")

        try:
            value = schema.model_validate_json(text)
        except ValidationError as e:
            raise AIError("AI の応答が想定の形式ではありません", kind="invalid_response") from e

        usage = getattr(response, "usage_metadata", None)
        return AIResult(
            value=value,
            model=getattr(response, "model_version", None) or self.settings.model,
            raw_text=text,
            latency_ms=latency_ms,
            prompt_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        )
