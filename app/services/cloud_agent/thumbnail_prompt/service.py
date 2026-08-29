"""Generate one image prompt from a saved Cloud Agent job master prompt."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

import openai
from openai import OpenAI

from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)


_DISALLOWED_OUTPUT_MARKER = re.compile(
    r"(?im)^\s*(?:#{1,6}\s+\S|[-+*]\s+\S|>\s*\S|`{3}|~{3}|"
    r"(?:-{3,}|\*{3,}|_{3,})\s*$|(?:option|alternative|choice)\b"
    r"(?:\s*(?:\d+|[a-z]))?\s*[:.)-]|(?:prompt|response|result)\s*:|"
    r"(?:[a-z]|\d+)[.)]\s+\S)"
)
_DISALLOWED_INLINE_MARKDOWN = re.compile(
    r"(?:`|!?\[[^\r\n]*\](?:\([^\)\r\n]*\)|\[[^\]\r\n]*\])|"
    r"^\s*\[[^\]\r\n]+\]:\s*\S+|"
    r"<(?:[a-z][a-z0-9+.-]*:[^>\r\n]+|[^<>\s@]+@[^<>\s@]+)>|"
    r"</?[a-z][^<>\r\n]*>|<!--[^\r\n]*-->|<!\[CDATA\[[^\r\n]*\]\]>|"
    r"<![a-z][^<>\r\n]*>|"
    r"<\?[^<>\r\n]*\?>|"
    r"\*\*[^*\r\n]+\*\*|__[^_\r\n]+__|"
    r"~~[^~\r\n]+~~|(?<![\w*])\*[^*\r\n]+\*(?!\*)|"
    r"(?<![\w_])_[^_\r\n]+_(?!\w))",
    re.IGNORECASE,
)
PROVIDER_TIMEOUT_SECONDS = 45


def _instruction(master_prompt: str, thumbnail_master_prompt: str) -> str:
    return "\n\n".join(
        (
            "Create exactly one ready-to-use image-generation prompt.",
            "Return only that prompt: no heading, markdown, explanation, analysis, or alternatives.",
            "Analyse the complete video master prompt internally and follow the thumbnail master prompt.",
            f"<thumbnail_master_prompt>\n{thumbnail_master_prompt}\n</thumbnail_master_prompt>",
            f"<video_master_prompt>\n{master_prompt}\n</video_master_prompt>",
        )
    )


class ThumbnailPromptService:
    """Uses only the Thumbnail Prompt configuration namespace and clients."""

    def __init__(
        self,
        *,
        storage: CloudJobStorage,
        settings: ThumbnailPromptSettingsService,
        clients: Mapping[str, Any] | None = None,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._clients = dict(clients or {})
        self._client_factory = client_factory

    def generate_for_job(self, job_id: str) -> str:
        try:
            video_master_prompt = self._storage.read_master_prompt(job_id)
        except ValueError as exc:
            raise ThumbnailPromptError(
                "JOB_MASTER_PROMPT_UNAVAILABLE", str(exc)
            ) from exc

        provider_id = self._settings.get_configured_provider_id()
        api_key = self._settings.get_api_key_for_generation(provider_id)
        model_id = self._settings.resolve_model(provider_id)
        base_url = self._settings.get_base_url_for_generation(provider_id)
        thumbnail_master_prompt = str(
            self._settings.get_settings().master_prompt or ""
        ).strip()
        if not thumbnail_master_prompt:
            raise ThumbnailPromptError(
                "THUMBNAIL_MASTER_PROMPT_MISSING",
                "ยังไม่ได้ตั้งค่า Thumbnail Master Prompt",
            )

        instruction = _instruction(video_master_prompt, thumbnail_master_prompt)
        client = self._clients.get(provider_id)
        try:
            if client is None:
                client = self._client_factory(
                    api_key=api_key.get_secret_value(),
                    base_url=base_url,
                    timeout=PROVIDER_TIMEOUT_SECONDS,
                    max_retries=0,
                )
            response = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": instruction}],
            )
        except Exception as exc:
            raise self._provider_error(exc) from exc
        return self._normalize_completion(response)

    @staticmethod
    def _provider_error(exc: Exception) -> ThumbnailPromptError:
        if isinstance(exc, openai.APITimeoutError):
            return ThumbnailPromptError(
                "PROVIDER_TIMEOUT", "ผู้ให้บริการใช้เวลาตอบนานเกินกำหนด กรุณาลองใหม่"
            )
        if isinstance(exc, openai.AuthenticationError):
            return ThumbnailPromptError(
                "PROVIDER_AUTHENTICATION_FAILED", "ไม่สามารถยืนยันตัวตนกับผู้ให้บริการได้"
            )
        if isinstance(exc, (openai.APIStatusError, openai.APIError)):
            return ThumbnailPromptError(
                "PROVIDER_REQUEST_FAILED", "ผู้ให้บริการไม่สามารถสร้างผลลัพธ์ได้ กรุณาลองใหม่"
            )
        return ThumbnailPromptError(
            "PROVIDER_REQUEST_FAILED", "ผู้ให้บริการไม่สามารถสร้างผลลัพธ์ได้ กรุณาลองใหม่"
        )

    @classmethod
    def _normalize_completion(cls, response: Any) -> str:
        choices = cls._value(response, "choices")
        if not isinstance(choices, (list, tuple)) or len(choices) != 1:
            raise cls._invalid_response()
        message = cls._value(choices[0], "message")
        content = cls._value(message, "content")
        if not isinstance(content, str):
            raise cls._invalid_response()
        normalized = content.strip()
        if (
            not normalized
            or "\n" in normalized
            or "\r" in normalized
            or _DISALLOWED_OUTPUT_MARKER.search(normalized)
            or _DISALLOWED_INLINE_MARKDOWN.search(normalized)
        ):
            raise cls._invalid_response()
        return normalized

    @staticmethod
    def _value(value: Any, key: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(key)
        return getattr(value, key, None)

    @staticmethod
    def _invalid_response() -> ThumbnailPromptError:
        return ThumbnailPromptError(
            "THUMBNAIL_PROMPT_RESPONSE_INVALID",
            "ผลลัพธ์ Prompt หน้าปกไม่ถูกต้อง กรุณาลองใหม่",
        )
