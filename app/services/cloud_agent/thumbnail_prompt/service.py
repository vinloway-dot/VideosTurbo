"""Generate one image prompt from a saved Cloud Agent job master prompt."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping
import unicodedata

import httpx
import openai
from openai import OpenAI

from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
    ensure_thumbnail_prompt_platform_supported,
)


_DISALLOWED_OUTPUT_MARKER = re.compile(
    r"(?im)^\s*(?:#{1,6}\s+\S|[-+*]\s+\S|>\s*\S|`{3}|~{3}|"
    r"(?:-{3,}|\*{3,}|_{3,})\s*$|"
    r"(?:your\s+prompt|here(?:\s+is|'s|’s)\s+(?:your\s+prompt|"
    r"the\s+thumbnail\s+prompt|an?\s+image\s+prompt))\s*:|"
    r"thumbnail\s+prompt(?:\s*:|\s+[—–-]\s*)|"
    r"(?:พรอมต์หน้าปก|นี่คือพรอมต์หน้าปก|นี่คือ\s+prompt\s+หน้าปก)\s*:|"
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
_MARKER_TEXT_LABELS = frozenset(
    {
        "primary",
        "alternative",
        "option",
        "choice",
        "prompt",
        "response",
        "result",
        "your prompt",
        "thumbnail prompt",
    }
)
_MARKER_SUFFIX_LABELS = frozenset({"primary", "alternative", "option", "choice"})
_TEXT_LABEL_DELIMITERS = frozenset({":", ".", ")", "-"})
_SIMPLE_SEGMENT_SEPARATORS = frozenset({";", "|"})
_SPACED_SEGMENT_SEPARATORS = frozenset({"-", "–", "—", "/"})
_MAX_OUTPUT_CHARACTERS = 8000
PROVIDER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)


def _has_alternative_marker(text: str) -> bool:
    """Return whether a segment begins with a known text or numbered label."""
    return any(_segment_has_marker(text, start) for start in _segment_starts(text))


def _segment_starts(text: str):
    yield 0
    for index, character in enumerate(text):
        if character in _SIMPLE_SEGMENT_SEPARATORS:
            yield index + 1
        elif (
            character in _SPACED_SEGMENT_SEPARATORS
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isspace()
            and text[index + 1].isspace()
        ):
            yield index + 1


def _segment_has_marker(text: str, start: int) -> bool:
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor == len(text):
        return False
    if text[cursor].isdigit():
        return _starts_with_numbered_marker(text, cursor)
    return _starts_with_text_marker(text, cursor)


def _starts_with_numbered_marker(text: str, start: int) -> bool:
    cursor = start
    while cursor < len(text) and text[cursor].isdigit():
        cursor += 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor == len(text) or text[cursor] not in _TEXT_LABEL_DELIMITERS:
        return False

    delimiter = text[cursor]
    content_start = cursor + 1
    while content_start < len(text) and text[content_start].isspace():
        content_start += 1
    if delimiter == ":":
        return content_start == len(text) or not text[content_start].isdigit()
    if delimiter == "-":
        return cursor + 1 < len(text) and text[cursor + 1].isspace()
    return True


def _starts_with_text_marker(text: str, start: int) -> bool:
    cursor = start
    while cursor < len(text) and (text[cursor].isalnum() or text[cursor].isspace()):
        cursor += 1
    if cursor == len(text) or text[cursor] not in _TEXT_LABEL_DELIMITERS:
        return False

    label = " ".join(text[start:cursor].casefold().split())
    if label in _MARKER_TEXT_LABELS:
        return True
    compact_label = label.replace(" ", "")
    for base_label in _MARKER_SUFFIX_LABELS:
        if not compact_label.startswith(base_label):
            continue
        suffix = compact_label[len(base_label) :]
        if suffix.isdigit() or (len(suffix) == 1 and suffix.isalpha()):
            return True
    return False


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
        ensure_thumbnail_prompt_platform_supported()
        try:
            video_master_prompt = self._storage.read_master_prompt(job_id)
        except ValueError as exc:
            raise ThumbnailPromptError(
                "JOB_MASTER_PROMPT_UNAVAILABLE", str(exc)
            ) from exc

        generation_settings = self._settings.get_generation_snapshot()

        instruction = _instruction(
            video_master_prompt, generation_settings.master_prompt
        )
        client = self._clients.get(generation_settings.provider_id)
        try:
            if client is None:
                client = self._client_factory(
                    api_key=generation_settings.api_key.get_secret_value(),
                    base_url=generation_settings.base_url,
                    timeout=PROVIDER_TIMEOUT,
                    max_retries=0,
                )
            response = client.chat.completions.create(
                model=generation_settings.model_id,
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
            or len(normalized) > _MAX_OUTPUT_CHARACTERS
            or "\n" in normalized
            or "\r" in normalized
            or any(unicodedata.category(character) == "Cc" for character in content)
            or _DISALLOWED_OUTPUT_MARKER.search(normalized)
            or _DISALLOWED_INLINE_MARKDOWN.search(normalized)
            or _has_alternative_marker(normalized)
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
