"""Generate one image prompt from a saved Cloud Agent job master prompt."""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import unicodedata
from typing import Any, Callable, Mapping

import httpx
import openai
from openai import AsyncOpenAI

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
        "alternate",
        "alt",
        "option",
        "choice",
        "variant",
        "variation",
        "version",
        "concept",
        "secondary",
        "backup",
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
_TEXT_MARKER_INITIALS = frozenset(label[0] for label in _MARKER_TEXT_LABELS)
_INLINE_TEXT_LABEL_DELIMITERS = frozenset(
    {":", ")", "]", "}", "=", "|", "–", "—", "→", "⇒", "⟶", "⟹"}
)
_WEAK_INLINE_TEXT_LABEL_DELIMITERS = frozenset({",", ";", "/", "…"})
_LETTER_LABEL_DELIMITERS = frozenset({":", ".", ")", "]", "}", "-", "–", "—"})
_MARKER_PREFIX_WRAPPERS = frozenset({'"', "'", "“", "”", "‘", "’", "(", "[", "{"})
_ALLOWED_FORMAT_CONTROLS = frozenset({"\u200c", "\u200d"})
_ROMAN_NUMERAL_SUFFIX = re.compile(r"[ivxlcdm]{1,8}\Z")
_NUMERIC_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "∶": ":",
        "⁄": "/",
        "∕": "/",
        "٬": ",",
    }
)
_MAX_TEXT_MARKER_SPAN = 48
_MAX_NUMERIC_TOKEN_DIGITS = 12
_MAX_RATIO_COMPONENT = (10**_MAX_NUMERIC_TOKEN_DIGITS) - 1
_MAX_RATIO_IMBALANCE = 100
_YEAR_TOKEN_DIGITS = 4
_MAX_OUTPUT_CHARACTERS = 8000
PROVIDER_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
PROVIDER_DEADLINE_SECONDS = 45.0


class _ProviderDeadlineExceeded(Exception):
    """Internal sentinel used to sanitize a hard provider deadline."""


def _canonical_validation_text(text: str) -> str:
    """Normalize safe compatibility forms without changing returned prompt text."""
    normalized = unicodedata.normalize("NFKD", text.replace("…", ";")).translate(
        _NUMERIC_PUNCTUATION_TRANSLATION
    )
    result: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category == "Cf":
            continue
        if (
            category.startswith("M")
            and result
            and result[-1].isascii()
            and result[-1].isalnum()
        ):
            continue
        result.append(character)
    return "".join(result)


def _contains_disallowed_unicode(text: str) -> bool:
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cc", "Cs", "Zl", "Zp"}:
            return True
        if category == "Cf" and character not in _ALLOWED_FORMAT_CONTROLS:
            return True
    return False


def _is_marker_delimiter(character: str) -> bool:
    return unicodedata.category(character)[0] in {"P", "S"}


def _has_alternative_marker(text: str) -> bool:
    """Return whether the output contains a known text or numbered label."""
    validation_text = _canonical_validation_text(text)
    starts = tuple(dict.fromkeys(_segment_starts(validation_text)))
    return (
        any(_segment_has_marker(validation_text, start) for start in starts)
        or _has_inline_text_marker(validation_text)
        or _has_sequential_colon_markers(validation_text)
    )


def _segment_starts(text: str):
    yield 0
    for index, character in enumerate(text):
        if character == "." and (
            _period_belongs_to_initialism(text, index)
            or _period_belongs_to_decimal(text, index)
        ):
            continue
        if character == ":" and _colon_belongs_to_ratio(text, index):
            continue
        if _is_marker_delimiter(character):
            yield index + 1


def _period_belongs_to_decimal(text: str, period_index: int) -> bool:
    return (
        period_index > 0
        and period_index + 1 < len(text)
        and text[period_index - 1].isdecimal()
        and text[period_index + 1].isdecimal()
    )


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
    if not _single_letter_before(text, period_index):
        return False
    cursor = period_index + 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor + 1 < len(text) and text[cursor].isalpha() and text[cursor + 1] == "."


def _single_letter_before(text: str, period_index: int) -> bool:
    cursor = period_index - 1
    while cursor >= 0 and text[cursor].isspace():
        cursor -= 1
    if cursor < 0 or not text[cursor].isalpha():
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
    if cursor == len(text) or not _is_marker_delimiter(text[cursor]):
        return False

    delimiter = text[cursor]
    content_start = cursor + 1
    while content_start < len(text) and text[content_start].isspace():
        content_start += 1
    if delimiter == ":":
        if content_start == len(text):
            return True
        if _looks_like_year_token(text, start, number_end):
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
    if delimiter == "," and _looks_like_grouped_number(text, cursor):
        return False
    if delimiter == "/" and _looks_like_fraction(text, cursor):
        return False
    return True


def _looks_like_year_token(text: str, start: int, end: int) -> bool:
    return end - start == _YEAR_TOKEN_DIGITS and all(
        character.isdecimal() for character in text[start:end]
    )


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


def _looks_like_grouped_number(text: str, delimiter_index: int) -> bool:
    cursor = delimiter_index
    saw_group = False
    while cursor < len(text) and text[cursor] == ",":
        group_start = cursor + 1
        group_end = group_start
        while (
            group_end < len(text)
            and text[group_end].isdecimal()
            and group_end - group_start < 3
        ):
            group_end += 1
        if group_end - group_start != 3:
            return False
        if group_end < len(text) and text[group_end].isdecimal():
            return False
        saw_group = True
        cursor = group_end
    return saw_group


def _looks_like_fraction(text: str, delimiter_index: int) -> bool:
    rhs_start = delimiter_index + 1
    while rhs_start < len(text) and text[rhs_start].isspace():
        rhs_start += 1
    rhs_end, rhs = _read_decimal_token(text, rhs_start)
    return (
        rhs is not None
        and rhs_end > rhs_start
        and (rhs_end == len(text) or not text[rhs_end].isalnum())
    )


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
    return (
        larger <= smaller * _MAX_RATIO_IMBALANCE
        or _has_explicit_contrast_ratio_context(text, rhs_end)
    )


def _has_explicit_contrast_ratio_context(text: str, rhs_end: int) -> bool:
    cursor = rhs_end
    while cursor < len(text) and (text[cursor].isspace() or text[cursor] in {",", ";"}):
        cursor += 1
    return text[cursor:].casefold().startswith("contrast ratio")


def _starts_with_letter_marker(text: str, start: int) -> bool:
    if not text[start].isalpha():
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
            and text[next_cursor].isalpha()
            and text[next_cursor + 1] == "."
        ):
            return False
    if text[cursor] in {"-", "–", "—"}:
        return cursor + 1 < len(text) and text[cursor + 1].isspace()
    return True


def _starts_with_text_marker(text: str, start: int) -> bool:
    return _text_marker(text, start) is not None


def _text_marker(text: str, start: int) -> tuple[int, str] | None:
    cursor = start
    limit = min(len(text), start + _MAX_TEXT_MARKER_SPAN)
    while cursor < limit and (
        text[cursor].isalnum() or text[cursor].isspace() or text[cursor] == "#"
    ):
        cursor += 1
    if cursor == len(text):
        return None
    if cursor == limit and (
        text[cursor].isalnum() or text[cursor].isspace() or text[cursor] == "#"
    ):
        return None
    if not _is_marker_delimiter(text[cursor]):
        return None

    label = " ".join(text[start:cursor].casefold().split())
    if not _matches_marker_label(label):
        return None
    return cursor, text[cursor]


def _matches_marker_label(label: str) -> bool:
    if label in _MARKER_TEXT_LABELS:
        return True
    compact_label = label.replace(" ", "")
    for base_label in _MARKER_SUFFIX_LABELS:
        if not compact_label.startswith(base_label):
            continue
        if _is_marker_suffix(compact_label[len(base_label) :]):
            return True
    return False


def _is_marker_suffix(suffix: str) -> bool:
    if suffix.startswith("#"):
        suffix = suffix[1:]
    if not suffix:
        return False
    if suffix.isdecimal():
        return True
    if len(suffix) == 1 and suffix.isalpha():
        return True
    if suffix.startswith("no") and suffix[2:].isdecimal():
        return True
    return _ROMAN_NUMERAL_SUFFIX.fullmatch(suffix) is not None


def _has_inline_text_marker(text: str) -> bool:
    for start, character in enumerate(text):
        if not (character.isascii() and character.isalpha()):
            continue
        if character.casefold() not in _TEXT_MARKER_INITIALS:
            continue
        if start > 0 and (text[start - 1].isalnum() or text[start - 1] == "#"):
            continue
        marker = _text_marker(text, start)
        if marker is None:
            continue
        delimiter_index, delimiter = marker
        if delimiter in _INLINE_TEXT_LABEL_DELIMITERS:
            return True
        if (
            delimiter in _WEAK_INLINE_TEXT_LABEL_DELIMITERS
            and _weak_inline_marker_is_structural(
                text,
                start,
                delimiter_index,
                delimiter,
            )
        ):
            return True
        if (
            delimiter == "-"
            and delimiter_index + 1 < len(text)
            and text[delimiter_index + 1].isspace()
        ):
            return True
    return False


def _weak_inline_marker_is_structural(
    text: str,
    start: int,
    delimiter_index: int,
    delimiter: str,
) -> bool:
    raw_label = " ".join(text[start:delimiter_index].split())
    folded_label = raw_label.casefold()
    compact_label = folded_label.replace(" ", "")
    if any(
        compact_label.startswith(base_label)
        and _is_marker_suffix(compact_label[len(base_label) :])
        for base_label in _MARKER_SUFFIX_LABELS
    ):
        return True
    if raw_label[:1].isupper():
        return True
    if delimiter in {";", "…"}:
        return True
    if delimiter == ",":
        return _previous_word(text, start).casefold() not in {"a", "an"}
    if delimiter == "/":
        if (
            folded_label == "alternative"
            and _next_word(text, delimiter_index + 1).casefold() == "original"
        ):
            return False
        return True
    return False


def _previous_word(text: str, start: int) -> str:
    cursor = start - 1
    while cursor >= 0 and text[cursor].isspace():
        cursor -= 1
    end = cursor + 1
    while cursor >= 0 and text[cursor].isalpha():
        cursor -= 1
    return text[cursor + 1 : end]


def _next_word(text: str, start: int) -> str:
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    end = cursor
    while end < len(text) and text[end].isalpha():
        end += 1
    return text[cursor:end]


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
        client_factory: Callable[..., Any] = AsyncOpenAI,
        provider_deadline_seconds: float = PROVIDER_DEADLINE_SECONDS,
    ) -> None:
        deadline = float(provider_deadline_seconds)
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError(
                "provider_deadline_seconds must be a positive finite value"
            )
        self._storage = storage
        self._settings = settings
        self._clients = dict(clients or {})
        self._client_factory = client_factory
        self._provider_deadline_seconds = deadline

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
        owns_client = client is None
        try:
            if client is None:
                client = self._client_factory(
                    api_key=generation_settings.api_key.get_secret_value(),
                    base_url=generation_settings.base_url,
                    timeout=PROVIDER_TIMEOUT,
                    max_retries=0,
                )
            response = asyncio.run(
                self._request_completion(
                    client,
                    model=generation_settings.model_id,
                    instruction=instruction,
                    close_client=owns_client,
                )
            )
        except Exception as exc:
            raise self._provider_error(exc) from exc
        return self._normalize_completion(response)

    async def _request_completion(
        self,
        client: Any,
        *,
        model: str,
        instruction: str,
        close_client: bool,
    ) -> Any:
        try:
            async with asyncio.timeout(self._provider_deadline_seconds):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": instruction}],
                    )
                    if inspect.isawaitable(response):
                        response = await response
                    return response
                finally:
                    if close_client:
                        close = getattr(client, "close", None)
                        if callable(close):
                            close_result = close()
                            if inspect.isawaitable(close_result):
                                await close_result
        except TimeoutError as exc:
            raise _ProviderDeadlineExceeded from exc

    @staticmethod
    def _provider_error(exc: Exception) -> ThumbnailPromptError:
        if isinstance(exc, (_ProviderDeadlineExceeded, openai.APITimeoutError)):
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
        if len(content) > _MAX_OUTPUT_CHARACTERS or _contains_disallowed_unicode(
            content
        ):
            raise cls._invalid_response()

        normalized = content.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            raise cls._invalid_response()

        validation_text = _canonical_validation_text(normalized)
        if (
            _DISALLOWED_OUTPUT_MARKER.search(normalized)
            or _DISALLOWED_OUTPUT_MARKER.search(validation_text)
            or _DISALLOWED_INLINE_MARKDOWN.search(normalized)
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
