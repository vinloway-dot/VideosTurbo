from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import openai
from openai import OpenAI
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from app.services.cloud_agent.research.errors import ResearchError


URL_PARAMETERS = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2048,
        }
    },
    "required": ["url"],
    "additionalProperties": False,
}
FETCH_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "Fetch one approved public HTML page.",
        "parameters": URL_PARAMETERS,
    },
}
READ_PDF_TOOL = {
    "type": "function",
    "function": {
        "name": "read_pdf",
        "description": "Read one approved public PDF URL.",
        "parameters": URL_PARAMETERS,
    },
}
OWNED_TOOLS = [FETCH_URL_TOOL, READ_PDF_TOOL]


@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    supports_tools: bool
    context_tokens: int


@dataclass(frozen=True)
class RequestedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderRequest:
    model_id: str
    api_key: SecretStr
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_output_tokens: int


class EvidenceClaim(BaseModel):
    claim: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)
    unstable: bool = False

    @field_validator("claim", "source_id", "evidence_quote")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class ProviderFinalPayload(BaseModel):
    script: str = Field(min_length=1)
    source_ids_used: list[str]
    model_knowledge_used: bool
    evidence_claims: list[EvidenceClaim]

    @field_validator("script")
    @classmethod
    def _strip_script(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("script must not be blank")
        return normalized

    @field_validator("source_ids_used")
    @classmethod
    def _normalize_source_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            source_id = str(item or "").strip()
            if not source_id:
                raise ValueError("source_ids_used entries must not be blank")
            normalized.append(source_id)
        return normalized


@dataclass(frozen=True)
class ProviderResult:
    tool_calls: tuple[RequestedToolCall, ...] = ()
    final_payload: ProviderFinalPayload | None = None
    usage: dict[str, int | float] | None = None
    cost: float | None = None


class ToolCallingAdapter(Protocol):
    provider_id: str

    def resolve_capability(self, model_id: str, api_key: SecretStr) -> ModelCapability:
        raise NotImplementedError

    def complete(self, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError


def _default_openai_client_factory(**kwargs):
    return OpenAI(**kwargs)


class OpenAICompatibleToolCallingAdapter:
    provider_id = ""
    base_url = ""
    REQUEST_TIMEOUT_SECONDS = 45.0
    LOCAL_CAPABILITIES: dict[str, ModelCapability] = {}

    def __init__(
        self,
        client_factory: Callable[..., Any] | Any | None = None,
        *,
        request_timeout_seconds: float | None = None,
    ) -> None:
        self._client_factory = self._normalize_client_factory(client_factory)
        self.request_timeout_seconds = (
            self.REQUEST_TIMEOUT_SECONDS
            if request_timeout_seconds is None
            else float(request_timeout_seconds)
        )
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")

    def resolve_capability(self, model_id: str, api_key: SecretStr) -> ModelCapability:
        normalized_model_id = str(model_id or "").strip()
        if not normalized_model_id:
            raise ResearchError("PROVIDER_MODEL_UNSUPPORTED", "model id is required")
        local = self.LOCAL_CAPABILITIES.get(normalized_model_id)
        if local is not None:
            return local
        try:
            metadata = self._build_client(api_key).models.retrieve(normalized_model_id)
        except Exception as exc:  # pragma: no cover - exercised by classification tests
            raise self._classify_provider_error(exc, operation="capability lookup") from exc
        capability = self._capability_from_metadata(normalized_model_id, metadata)
        if not capability.supports_tools:
            raise ResearchError(
                "PROVIDER_TOOL_CALLING_UNSUPPORTED",
                "model metadata did not prove tool-calling support",
            )
        if capability.context_tokens <= 0:
            raise ResearchError(
                "PROVIDER_MODEL_UNSUPPORTED",
                "model metadata did not expose a usable context limit",
            )
        return capability

    def complete(self, request: ProviderRequest) -> ProviderResult:
        try:
            response = self._build_client(request.api_key).chat.completions.create(
                model=str(request.model_id or "").strip(),
                messages=list(request.messages),
                tools=OWNED_TOOLS,
                tool_choice="auto",
                max_tokens=max(1, int(request.max_output_tokens)),
            )
        except Exception as exc:
            raise self._classify_provider_error(exc, operation="completion") from exc
        try:
            return self._parse_response(response)
        except ResearchError as exc:
            if exc.code == "RESEARCH_RESPONSE_INVALID":
                exc.retryable = True
            raise

    def _build_client(self, api_key: SecretStr):
        return self._client_factory(
            api_key=api_key.get_secret_value(),
            base_url=self.base_url,
            max_retries=0,
            timeout=self.request_timeout_seconds,
        )

    def _normalize_client_factory(
        self, client_factory: Callable[..., Any] | Any | None
    ) -> Callable[..., Any]:
        if client_factory is None:
            return _default_openai_client_factory
        if callable(client_factory):
            return client_factory
        return lambda **_kwargs: client_factory

    def _parse_response(self, response: Any) -> ProviderResult:
        message = self._extract_primary_message(response)
        usage = self._parse_usage(response)
        cost = self._parse_cost(response)
        tool_calls = self._parse_tool_calls(message)
        if tool_calls:
            return ProviderResult(tool_calls=tool_calls, usage=usage, cost=cost)
        final_payload = self._parse_final_payload(message)
        return ProviderResult(final_payload=final_payload, usage=usage, cost=cost)

    def _extract_primary_message(self, response: Any) -> Any:
        choices = self._get_value(response, "choices")
        if not isinstance(choices, list) or not choices:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "provider returned no choices")
        first_choice = choices[0]
        message = self._get_value(first_choice, "message")
        if message is None:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "provider returned no message")
        return message

    def _parse_tool_calls(self, message: Any) -> tuple[RequestedToolCall, ...]:
        raw_tool_calls = self._get_value(message, "tool_calls")
        if not raw_tool_calls:
            return ()
        parsed_calls: list[RequestedToolCall] = []
        for raw_call in raw_tool_calls:
            function = self._get_value(raw_call, "function")
            name = str(self._get_value(function, "name") or "").strip()
            call_id = str(self._get_value(raw_call, "id") or "").strip() or name
            if name not in {"fetch_url", "read_pdf"}:
                raise ResearchError(
                    "RESEARCH_RESPONSE_INVALID",
                    "provider requested an unknown tool",
                )
            arguments = self._parse_json_object(
                self._get_value(function, "arguments"),
                error_detail=f"{name} arguments were not valid JSON",
            )
            if not str(arguments.get("url", "")).strip():
                raise ResearchError(
                    "RESEARCH_RESPONSE_INVALID",
                    f"{name} arguments did not include a URL",
                )
            parsed_calls.append(
                RequestedToolCall(call_id=call_id, name=name, arguments=arguments)
            )
        return tuple(parsed_calls)

    def _parse_final_payload(self, message: Any) -> ProviderFinalPayload:
        raw_content = self._content_to_text(self._get_value(message, "content"))
        payload = self._parse_json_object(
            raw_content,
            error_detail="final assistant message was not valid JSON",
        )
        try:
            return ProviderFinalPayload.model_validate(payload)
        except ValidationError as exc:
            raise ResearchError(
                "RESEARCH_RESPONSE_INVALID",
                f"final assistant message did not match the evidence envelope: {exc.error_count()} validation error(s)",
            ) from exc

    def _parse_usage(self, response: Any) -> dict[str, int | float] | None:
        usage = self._get_value(response, "usage")
        if usage is None:
            return None

        totals: dict[str, int | float] = {}
        input_tokens = self._as_non_negative_int(
            self._first_value(usage, "prompt_tokens", "input_tokens")
        )
        output_tokens = self._as_non_negative_int(
            self._first_value(usage, "completion_tokens", "output_tokens")
        )
        total_tokens = self._as_non_negative_int(self._get_value(usage, "total_tokens"))
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens

        if input_tokens or output_tokens or total_tokens:
            totals["input_tokens"] = input_tokens
            totals["output_tokens"] = output_tokens
            totals["total_tokens"] = total_tokens

        cached_tokens = self._as_non_negative_int(
            self._get_nested_value(usage, "prompt_tokens_details", "cached_tokens")
        )
        if cached_tokens:
            totals["cached_input_tokens"] = cached_tokens

        reasoning_tokens = self._as_non_negative_int(
            self._get_nested_value(usage, "completion_tokens_details", "reasoning_tokens")
        )
        if reasoning_tokens:
            totals["reasoning_output_tokens"] = reasoning_tokens

        return totals or None

    def _parse_cost(self, response: Any) -> float | None:
        for holder, name in (
            (response, "cost"),
            (response, "total_cost"),
            (self._get_value(response, "usage"), "cost"),
            (self._get_value(response, "usage"), "total_cost"),
        ):
            value = self._get_value(holder, name) if holder is not None else None
            if value is None:
                continue
            try:
                cost = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(cost) and cost >= 0:
                return cost
        return None

    def _capability_from_metadata(self, model_id: str, metadata: Any) -> ModelCapability:
        return ModelCapability(
            model_id=model_id,
            supports_tools=self._metadata_supports_tools(metadata),
            context_tokens=self._metadata_context_tokens(metadata),
        )

    def _metadata_supports_tools(self, metadata: Any) -> bool:
        explicit = self._first_value(metadata, "supports_tools", "tool_calling")
        if isinstance(explicit, bool):
            return explicit
        for field_name in ("supported_parameters", "capabilities", "features"):
            values = self._get_value(metadata, field_name)
            if not isinstance(values, list):
                continue
            normalized = {str(item or "").strip().lower() for item in values}
            if {"tools", "tool_choice"} & normalized:
                return True
            if "function_calling" in normalized or "tool_calling" in normalized:
                return True
        return False

    def _metadata_context_tokens(self, metadata: Any) -> int:
        raw_candidates = [
            self._get_value(metadata, "context_length"),
            self._get_value(metadata, "context_window"),
            self._get_value(metadata, "max_context_tokens"),
            self._get_value(metadata, "max_input_tokens"),
            self._get_nested_value(metadata, "top_provider", "context_length"),
            self._get_nested_value(metadata, "architecture", "context_length"),
        ]
        for candidate in raw_candidates:
            value = self._as_non_negative_int(candidate)
            if value > 0:
                return value
        return 0

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text_value = item.get("text")
                    if isinstance(text_value, dict):
                        text = str(text_value.get("value") or "").strip()
                    else:
                        text = str(text_value or "").strip()
                else:
                    text = str(self._get_value(item, "text") or "").strip()
                if text:
                    parts.append(text)
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _parse_json_object(self, raw_value: Any, *, error_detail: str) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            return raw_value
        text = self._strip_code_fence(str(raw_value or "").strip())
        if not text:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", error_detail)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", error_detail) from exc
        if not isinstance(parsed, dict):
            raise ResearchError("RESEARCH_RESPONSE_INVALID", error_detail)
        return parsed

    def _strip_code_fence(self, text: str) -> str:
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return text

    def _classify_provider_error(self, exc: Exception, *, operation: str) -> ResearchError:
        if isinstance(exc, ResearchError):
            return exc
        if isinstance(exc, (openai.APITimeoutError, TimeoutError, openai.APIConnectionError)):
            return ResearchError(
                "PROVIDER_TIMEOUT",
                f"{operation} timed out",
                retryable=True,
            )
        if isinstance(exc, openai.AuthenticationError):
            return ResearchError(
                "PROVIDER_AUTHENTICATION_FAILED",
                f"{operation} rejected the provider credentials",
            )
        if isinstance(exc, openai.NotFoundError):
            return ResearchError(
                "PROVIDER_MODEL_UNSUPPORTED",
                f"{operation} could not find the selected model",
            )
        if isinstance(exc, openai.BadRequestError):
            message = str(exc).lower()
            if "tool" in message or "function" in message:
                return ResearchError(
                    "PROVIDER_TOOL_CALLING_UNSUPPORTED",
                    f"{operation} rejected provider tool calling",
                )
            if "model" in message:
                return ResearchError(
                    "PROVIDER_MODEL_UNSUPPORTED",
                    f"{operation} rejected the selected model",
                )
            return ResearchError(
                "RESEARCH_RESPONSE_INVALID",
                f"{operation} returned a malformed provider response",
            )
        if isinstance(exc, openai.APIError):
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403}:
                return ResearchError(
                    "PROVIDER_AUTHENTICATION_FAILED",
                    f"{operation} rejected the provider credentials",
                )
            if status_code == 404:
                return ResearchError(
                    "PROVIDER_MODEL_UNSUPPORTED",
                    f"{operation} could not find the selected model",
                )
            if status_code in {408, 504}:
                return ResearchError(
                    "PROVIDER_TIMEOUT",
                    f"{operation} timed out",
                    retryable=True,
                )
            if status_code == 429 or (
                isinstance(status_code, int) and status_code >= 500
            ):
                return ResearchError(
                    "RESEARCH_RESPONSE_INVALID",
                    f"{operation} failed before a valid provider response was produced",
                    retryable=True,
                )
        if "timeout" in str(exc).lower():
            return ResearchError(
                "PROVIDER_TIMEOUT",
                f"{operation} timed out",
                retryable=True,
            )
        return ResearchError(
            "RESEARCH_RESPONSE_INVALID",
            f"{operation} failed before a valid provider response was produced",
        )

    def _first_value(self, holder: Any, *names: str) -> Any:
        for name in names:
            value = self._get_value(holder, name)
            if value is not None:
                return value
        return None

    def _get_nested_value(self, holder: Any, *names: str) -> Any:
        current = holder
        for name in names:
            current = self._get_value(current, name)
            if current is None:
                return None
        return current

    def _get_value(self, holder: Any, name: str) -> Any:
        if holder is None:
            return None
        if isinstance(holder, dict):
            return holder.get(name)
        return getattr(holder, name, None)

    def _as_non_negative_int(self, value: Any) -> int:
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        try:
            integer = int(value)
        except (OverflowError, TypeError, ValueError):
            return 0
        return integer if integer >= 0 else 0


class OpenRouterToolCallingAdapter(OpenAICompatibleToolCallingAdapter):
    provider_id = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    LOCAL_CAPABILITIES = {
        "openai/gpt-5.6-sol-pro": ModelCapability(
            model_id="openai/gpt-5.6-sol-pro",
            supports_tools=True,
            context_tokens=128_000,
        )
    }


class AIHubMixToolCallingAdapter(OpenAICompatibleToolCallingAdapter):
    provider_id = "aihubmix"
    base_url = "https://aihubmix.com/v1"
    LOCAL_CAPABILITIES = {
        "gpt-5.6-sol": ModelCapability(
            model_id="gpt-5.6-sol",
            supports_tools=True,
            context_tokens=128_000,
        )
    }


__all__ = [
    "AIHubMixToolCallingAdapter",
    "EvidenceClaim",
    "FETCH_URL_TOOL",
    "ModelCapability",
    "OpenRouterToolCallingAdapter",
    "OWNED_TOOLS",
    "ProviderFinalPayload",
    "ProviderRequest",
    "ProviderResult",
    "READ_PDF_TOOL",
    "RequestedToolCall",
    "ToolCallingAdapter",
    "URL_PARAMETERS",
]
