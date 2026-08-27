from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import SecretStr

from app.services.cloud_agent.research.errors import ResearchError


@dataclass
class _RecordingFactory:
    response_client: object
    kwargs_history: list[dict] = field(default_factory=list)

    def __call__(self, **kwargs):
        self.kwargs_history.append(kwargs)
        return self.response_client


class _FakeChatCompletions:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _FakeModelsResource:
    def __init__(self, retrieved=None, error: Exception | None = None):
        self.retrieved = retrieved
        self.error = error
        self.calls: list[str] = []

    def retrieve(self, model_id: str):
        self.calls.append(model_id)
        if self.error is not None:
            raise self.error
        return self.retrieved


class _FakeClient:
    def __init__(self, completion_response=None, *, completion_error=None, retrieved_model=None):
        self.chat = SimpleNamespace(
            completions=_FakeChatCompletions(completion_response, completion_error)
        )
        self.models = _FakeModelsResource(retrieved_model)


def _provider_request(*, api_key: SecretStr | None = None, model_id: str = "openai/gpt-5.6-sol-pro"):
    return SimpleNamespace(
        model_id=model_id,
        api_key=api_key or SecretStr("provider-secret"),
        messages=[{"role": "user", "content": "research prompt"}],
        tools=[
            {"type": "function", "function": {"name": "fetch_url"}},
            {"type": "function", "function": {"name": "web_search"}},
        ],
        max_output_tokens=512,
    )


def _completion_response(
    *,
    content: str | None = None,
    tool_name: str | None = None,
    tool_arguments: dict | None = None,
    usage=None,
    cost: float | None = None,
):
    message = SimpleNamespace(content=content)
    if tool_name is not None:
        message.tool_calls = [
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name=tool_name,
                    arguments=json.dumps(tool_arguments or {"url": "https://example.com/a"}),
                ),
            )
        ]
    else:
        message.tool_calls = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage
        or SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=40,
            total_tokens=160,
            prompt_tokens_details=SimpleNamespace(cached_tokens=11),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=7),
        ),
    )
    if cost is not None:
        response.cost = cost
    return response


def _final_payload_json():
    return json.dumps(
        {
            "script": "Narration",
            "source_ids_used": ["source-1"],
            "model_knowledge_used": True,
            "evidence_claims": [
                {
                    "claim": "Verified fact",
                    "source_id": "source-1",
                    "evidence_quote": "exact words from source",
                    "unstable": False,
                }
            ],
        }
    )


def test_openrouter_declares_only_owned_tools():
    from app.services.cloud_agent.research.adapters import (
        FETCH_URL_TOOL,
        READ_PDF_TOOL,
        OpenRouterToolCallingAdapter,
    )

    client = _FakeClient(completion_response=_completion_response(content=_final_payload_json()))
    adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(client))

    adapter.complete(_provider_request())

    assert client.chat.completions.calls[0]["tools"] == [FETCH_URL_TOOL, READ_PDF_TOOL]
    assert "web_search" not in repr(client.chat.completions.calls)


def test_aihubmix_parses_tool_call_and_final_usage():
    from app.services.cloud_agent.research.adapters import AIHubMixToolCallingAdapter

    usage = {
        "prompt_tokens": 90,
        "completion_tokens": 12,
        "total_tokens": 102,
        "prompt_tokens_details": {"cached_tokens": 5},
    }
    client = _FakeClient(
        completion_response=_completion_response(
            tool_name="fetch_url",
            tool_arguments={"url": "https://example.com/a"},
            usage=usage,
            cost=0.03,
        )
    )

    result = AIHubMixToolCallingAdapter(client_factory=_RecordingFactory(client)).complete(
        _provider_request(model_id="gpt-5.6-sol")
    )

    assert result.tool_calls[0].arguments == {"url": "https://example.com/a"}
    assert result.usage == {
        "input_tokens": 90,
        "output_tokens": 12,
        "total_tokens": 102,
        "cached_input_tokens": 5,
    }
    assert result.cost == 0.03


def test_final_message_requires_evidence_envelope():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    client = _FakeClient(completion_response=_completion_response(content=_final_payload_json()))
    adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(client))

    result = adapter.complete(_provider_request())

    assert result.final_payload is not None
    assert result.final_payload.script == "Narration"


def test_final_message_allows_empty_evidence_lists_for_service_validation():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    payload = {
        "script": "Narration",
        "source_ids_used": [],
        "model_knowledge_used": True,
        "evidence_claims": [],
    }
    client = _FakeClient(
        completion_response=_completion_response(content=json.dumps(payload))
    )
    adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(client))

    result = adapter.complete(_provider_request())

    assert result.final_payload is not None
    assert result.final_payload.source_ids_used == []
    assert result.final_payload.evidence_claims == []


def test_invalid_final_message_raises_research_response_invalid():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    client = _FakeClient(completion_response=_completion_response(content='{"script":"missing"}'))
    adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(client))

    with pytest.raises(ResearchError) as captured:
        adapter.complete(_provider_request())

    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("script", "   "),
        ("source_ids_used", ["source-1", "   "]),
        ("source_ids_used", ["   "]),
        (
            "evidence_claims",
            [
                {
                    "claim": "   ",
                    "source_id": "source-1",
                    "evidence_quote": "exact words from source",
                    "unstable": False,
                }
            ],
        ),
        (
            "evidence_claims",
            [
                {
                    "claim": "Verified fact",
                    "source_id": "   ",
                    "evidence_quote": "exact words from source",
                    "unstable": False,
                }
            ],
        ),
        (
            "evidence_claims",
            [
                {
                    "claim": "Verified fact",
                    "source_id": "source-1",
                    "evidence_quote": "   ",
                    "unstable": False,
                }
            ],
        ),
    ],
)
def test_final_message_rejects_whitespace_only_required_fields(field_name, field_value):
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    payload = json.loads(_final_payload_json())
    payload[field_name] = field_value
    client = _FakeClient(
        completion_response=_completion_response(content=json.dumps(payload))
    )
    adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(client))

    with pytest.raises(ResearchError) as captured:
        adapter.complete(_provider_request())

    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"


def test_unknown_custom_model_fails_before_completion():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    metadata = {"id": "custom/unknown", "supported_parameters": ["temperature"], "context_length": 0}
    client = _FakeClient(completion_response=_completion_response(content=_final_payload_json()), retrieved_model=metadata)
    adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(client))

    with pytest.raises(ResearchError) as captured:
        adapter.resolve_capability("custom/unknown", SecretStr("key"))

    assert captured.value.code == "PROVIDER_TOOL_CALLING_UNSUPPORTED"
    assert client.chat.completions.calls == []
    assert client.models.calls == ["custom/unknown"]


def test_generation_client_disables_sdk_retries_and_sets_timeout():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    client = _FakeClient(completion_response=_completion_response(content=_final_payload_json()))
    recorder = _RecordingFactory(client)
    adapter = OpenRouterToolCallingAdapter(client_factory=recorder)

    adapter.complete(_provider_request())

    assert recorder.kwargs_history[0]["max_retries"] == 0
    assert recorder.kwargs_history[0]["timeout"] > 0


def test_timeout_and_auth_failures_are_classified_without_leaking_secrets():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    timeout_client = _FakeClient(
        completion_error=openai.APITimeoutError(request=httpx.Request("POST", "https://example.com"))
    )
    timeout_adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(timeout_client))

    with pytest.raises(ResearchError) as timeout_error:
        timeout_adapter.complete(_provider_request())

    assert timeout_error.value.code == "PROVIDER_TIMEOUT"

    auth_error = openai.AuthenticationError(
        "key=secret raw-response",
        response=httpx.Response(401, request=httpx.Request("POST", "https://example.com")),
        body={"message": "raw-response"},
    )
    auth_client = _FakeClient(completion_error=auth_error)
    auth_adapter = OpenRouterToolCallingAdapter(client_factory=_RecordingFactory(auth_client))

    with pytest.raises(ResearchError) as auth_captured:
        auth_adapter.complete(_provider_request(api_key=SecretStr("secret")))

    assert auth_captured.value.code == "PROVIDER_AUTHENTICATION_FAILED"
    assert "secret" not in str(auth_captured.value)
    assert "raw-response" not in str(auth_captured.value)


def test_non_finite_provider_usage_and_cost_are_ignored_safely():
    from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter

    usage = {
        "prompt_tokens": float("inf"),
        "completion_tokens": float("nan"),
        "total_tokens": float("-inf"),
    }
    response = _completion_response(
        content=_final_payload_json(),
        usage=usage,
        cost=float("inf"),
    )
    adapter = OpenRouterToolCallingAdapter(
        client_factory=_RecordingFactory(_FakeClient(completion_response=response))
    )

    result = adapter.complete(_provider_request())

    assert result.usage is None
    assert result.cost is None
