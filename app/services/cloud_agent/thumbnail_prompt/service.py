"""Generate one image prompt from a saved Cloud Agent job master prompt."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Mapping

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
_MARKER_SUFFIX_LABELS = frozenset(
    label.replace(" ", "") for label in _MARKER_TEXT_LABELS
)
_TEXT_LABEL_DELIMITERS = frozenset({":", ".", ")", "-", "–", "—"})
_LETTER_LABEL_DELIMITERS = frozenset({":", ".", ")", "-", "–", "—"})
_SIMPLE_SEGMENT_SEPARATORS = frozenset(
    {
        ";",
        "|",
        ",",
        ".",
        ":",
        "/",
        "–",
        "—",
        "•",
        "!",
        "?",
        "(",
        "[",
        "{",
        "؛",
        "。",
        "、",
        "，",
        "；",
        "：",
    }
)
_SPACED_SEGMENT_SEPARATORS = frozenset({"-"})
_MARKER_PREFIX_WRAPPERS = frozenset({'"', "'", "“", "”", "‘", "’", "(", "[", "{"})
_MAX_RATIO_COMPONENT = 1_000_000
_MAX_RATIO_IMBALANCE = 100
_MAX_NUMERIC_TOKEN_DIGITS = 12
# A deliberately narrow range keeps short ordinals and arbitrary numeric IDs labelled.
_MIN_YEAR_PREFIX = 1900
_MAX_YEAR_PREFIX = 2199
_MAX_OUTPUT_CHARACTERS = 8000
PROVIDER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)


def _canonical_validation_text(text: str) -> str:
    """Normalize compatibility forms and ignore invisible format separators."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _has_alternative_marker(text: str) -> bool:
    """Return whether a segment begins with a known text or numbered label."""
    validation_text = _canonical_validation_text(text)
    starts = tuple(dict.fromkeys(_segment_starts(validation_text)))
    return any(
        _segment_has_marker(validation_text, start) for start in starts
    ) or _has_sequential_colon_markers(validation_text)


def _segment_starts(text: str):
    yield 0
    for index, character in enumerate(text):
        if character == "." and _period_belongs_to_initialism(text, index):
            continue
        if character == ":" and _colon_belongs_to_ratio(text, index):
            continue
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


def _colon_belongs_to_ratio(text: str, colon_index: int) -> bool:
    lhs_cursor = colon_index - 1
    while lhs_cursor >= 0 and text[lhs_cursor].isspace():
        lhs_cursor -= 1
    lhs_end = lhs_cursor + 1
    while lhs_cursor >= 0 and text[lhs_cursor].isdecimal():
        lhs_cursor -= 1
    lhs_start = lhs_cursor + 1
    if lhs_start == lhs_end:
        return False
    lhs_token_end, lhs = _read_decimal_token(text, lhs_start)
    if lhs_token_end != lhs_end:
        return False
    rhs_start = colon_index + 1
    while rhs_start < len(text) and text[rhs_start].isspace():
        rhs_start += 1
    return _is_complete_ratio(text, rhs_start, lhs)


def _period_belongs_to_initialism(text: str, period_index: int) -> bool:
    if not _single_ascii_letter_before(text, period_index):
        return False
    cursor = period_index + 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return (
        cursor + 1 < len(text)
        and text[cursor].isascii()
        and text[cursor].isalpha()
        and text[cursor + 1] == "."
    )


def _single_ascii_letter_before(text: str, period_index: int) -> bool:
    cursor = period_index - 1
    while cursor >= 0 and text[cursor].isspace():
        cursor -= 1
    if cursor < 0 or not (text[cursor].isascii() and text[cursor].isalpha()):
        return False
    cursor -= 1
    while cursor >= 0 and text[cursor].isspace():
        cursor -= 1
    return cursor < 0 or not text[cursor].isalpha()


def _segment_has_marker(text: str, start: int) -> bool:
    cursor = _skip_marker_prefix(text, start)
    if cursor == len(text):
        return False
    if text[cursor].isdecimal():
        return _starts_with_numbered_marker(text, cursor)
    if _starts_with_letter_marker(text, cursor):
        return True
    return _starts_with_text_marker(text, cursor)


def _skip_marker_prefix(text: str, start: int) -> int:
    cursor = start
    while cursor < len(text):
        if text[cursor].isspace() or text[cursor] in _MARKER_PREFIX_WRAPPERS:
            cursor += 1
            continue
        break
    return cursor


def _starts_with_numbered_marker(text: str, start: int) -> bool:
    cursor, number = _read_decimal_token(text, start)
    number_end = cursor
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor == len(text) or text[cursor] not in _TEXT_LABEL_DELIMITERS:
        return False

    delimiter = text[cursor]
    content_start = cursor + 1
    while content_start < len(text) and text[content_start].isspace():
        content_start += 1
    if delimiter == ":":
        if content_start == len(text):
            return True
        if number is not None and _MIN_YEAR_PREFIX <= number <= _MAX_YEAR_PREFIX:
            return False
        return not _is_complete_ratio(text, content_start, number)
    if delimiter == ".":
        return not (
            cursor == number_end
            and cursor + 1 < len(text)
            and text[cursor + 1].isdecimal()
        )
    if delimiter in {"-", "–", "—"}:
        return cursor + 1 < len(text) and text[cursor + 1].isspace()
    return True


def _read_decimal_token(text: str, start: int) -> tuple[int, int | None]:
    cursor = start
    digits: list[str] = []
    while cursor < len(text) and text[cursor].isdecimal():
        if len(digits) < _MAX_NUMERIC_TOKEN_DIGITS:
            digits.append(str(unicodedata.decimal(text[cursor])))
        cursor += 1
    if cursor == start:
        return start, None
    if cursor - start > _MAX_NUMERIC_TOKEN_DIGITS:
        return cursor, None
    return cursor, int("".join(digits))


def _is_complete_ratio(text: str, rhs_start: int, lhs: int | None) -> bool:
    if lhs is None or not 1 <= lhs <= _MAX_RATIO_COMPONENT:
        return False
    rhs_end, rhs = _read_decimal_token(text, rhs_start)
    if (
        rhs is None
        or rhs_end == rhs_start
        or (rhs_end < len(text) and text[rhs_end].isalnum())
        or not 1 <= rhs <= _MAX_RATIO_COMPONENT
    ):
        return False
    smaller = min(lhs, rhs)
    larger = max(lhs, rhs)
    return larger <= smaller * _MAX_RATIO_IMBALANCE


def _starts_with_letter_marker(text: str, start: int) -> bool:
    if not (text[start].isascii() and text[start].isalpha()):
        return False
    cursor = start + 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor == len(text) or text[cursor] not in _LETTER_LABEL_DELIMITERS:
        return False
    if text[cursor] == ".":
        next_cursor = cursor + 1
        while next_cursor < len(text) and text[next_cursor].isspace():
            next_cursor += 1
        if (
            next_cursor + 1 < len(text)
            and text[next_cursor].isascii()
            and text[next_cursor].isalpha()
            and text[next_cursor + 1] == "."
        ):
            return False
    if text[cursor] in {"-", "–", "—"}:
        return cursor + 1 < len(text) and text[cursor + 1].isspace()
    return True


def _starts_with_text_marker(text: str, start: int) -> bool:
    cursor = start
    while cursor < len(text) and (
        text[cursor].isalnum() or text[cursor].isspace() or text[cursor] == "#"
    ):
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
        if suffix.startswith("#"):
            suffix = suffix[1:]
        if suffix.isdecimal() or (len(suffix) == 1 and suffix.isalpha()):
            return True
    return False


def _has_sequential_colon_markers(text: str) -> bool:
    values = [
        value
        for start in _segment_starts(text)
        if (value := _leading_numeric_colon_value(text, start)) is not None
    ]
    return len(values) >= 2 and values == list(range(1, len(values) + 1))


def _leading_numeric_colon_value(text: str, start: int) -> int | None:
    cursor = _skip_marker_prefix(text, start)
    token_end, value = _read_decimal_token(text, cursor)
    while token_end < len(text) and text[token_end].isspace():
        token_end += 1
    if value is None or token_end == len(text) or text[token_end] != ":":
        return None
    return value


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
        validation_text = _canonical_validation_text(normalized)
        if (
            not normalized
            or len(normalized) > _MAX_OUTPUT_CHARACTERS
            or "\n" in normalized
            or "\r" in normalized
            or any(unicodedata.category(character) == "Cc" for character in content)
            or _DISALLOWED_OUTPUT_MARKER.search(validation_text)
            or _DISALLOWED_INLINE_MARKDOWN.search(validation_text)
            or _has_alternative_marker(validation_text)
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
