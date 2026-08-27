from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

import pytest
from pydantic import SecretStr

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services.cloud_agent.research.adapters import (
    EvidenceClaim,
    ModelCapability,
    ProviderFinalPayload,
    ProviderResult,
    RequestedToolCall,
)
from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.models import ResearchDraftRequest
from app.services.cloud_agent.research.runtime import (
    EvidenceBlock,
    EvidencePacket,
    ResearchSource,
)
from app.services.cloud_agent.research.store import ResearchDraftStore


def _canonical_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ResearchError("URL_INVALID", "invalid URL")
    port = parsed.port
    host = parsed.hostname.lower()
    netloc = host
    if port is not None and not (
        (parsed.scheme == "https" and port == 443)
        or (parsed.scheme == "http" and port == 80)
    ):
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    if path == "/" and not parsed.query:
        path = ""
    return urlunsplit((parsed.scheme, netloc, path, parsed.query, ""))


def _source(url: str, *, source_id: str = "source-1", content: str | None = None):
    body = content or "Exact source fact appears here."
    return ResearchSource(
        source_id=source_id,
        url=url,
        final_url=url,
        title="Example source",
        content=body,
        content_hash=sha256(body.encode("utf-8")).hexdigest(),
        mime_type="text/html",
    )


@dataclass
class FakeRuntime:
    rejected_preflights: dict[str, str] = field(default_factory=dict)
    failures: dict[tuple[str, str], ResearchError] = field(default_factory=dict)
    sources_by_url: dict[str, ResearchSource] = field(default_factory=dict)
    executed: list[tuple[str, str]] = field(default_factory=list)
    executed_urls: list[str] = field(default_factory=list)

    def reject_preflight(self, url: str, *, code: str) -> None:
        self.rejected_preflights[url] = code

    def fail_tool(self, tool_name: str, url: str, *, code: str) -> None:
        self.failures[(tool_name, _canonical_url(url))] = ResearchError(code, "failed")

    def preflight_urls(self, raw_urls: list[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw_url in raw_urls:
            if raw_url in self.rejected_preflights:
                raise ResearchError(self.rejected_preflights[raw_url], "blocked")
            canonical = _canonical_url(raw_url)
            if canonical not in normalized:
                normalized.append(canonical)
        return tuple(normalized)

    def execute(self, tool_name: str, supplied_url: str) -> ResearchSource:
        canonical = _canonical_url(supplied_url)
        self.executed.append((tool_name, canonical))
        self.executed_urls.append(canonical)
        error = self.failures.get((tool_name, canonical))
        if error is not None:
            raise error
        return self.sources_by_url.get(canonical) or _source(canonical)

    def aggregate(self, sources: list[ResearchSource]) -> EvidencePacket:
        blocks_by_text: dict[str, list[str]] = {}
        ordered: list[str] = []
        for source in sources:
            for block in source.content.split("\n\n"):
                text = " ".join(block.split())
                if not text:
                    continue
                if text not in blocks_by_text:
                    blocks_by_text[text] = [source.source_id]
                    ordered.append(text)
                elif source.source_id not in blocks_by_text[text]:
                    blocks_by_text[text].append(source.source_id)
        return EvidencePacket(
            sources=tuple(sources),
            blocks=tuple(
                EvidenceBlock(text=text, source_ids=tuple(blocks_by_text[text]))
                for text in ordered
            ),
        )


@dataclass
class FakeSettings:
    api_key: SecretStr = field(default_factory=lambda: SecretStr("provider-key"))
    calls: list[str] = field(default_factory=list)

    def get_api_key_for_generation(self, provider_id: str) -> SecretStr:
        self.calls.append(provider_id)
        assert provider_id in {"openrouter", "aihubmix"}
        return self.api_key


@dataclass
class FakeAdapter:
    context_limit: int = 128_000
    rounds: list[ProviderResult] = field(default_factory=list)
    calls: list[object] = field(default_factory=list)
    capabilities: list[tuple[str, SecretStr]] = field(default_factory=list)

    def queue_round_usage(self, rounds: list[tuple[dict[str, int], float]]) -> None:
        self.rounds = [
            ProviderResult(
                tool_calls=(RequestedToolCall(f"call-{index}", "fetch_url", {"url": "https://example.com/article"}),),
                usage=usage,
                cost=cost,
            )
            for index, (usage, cost) in enumerate(rounds[:-1], start=1)
        ]
        final_usage, final_cost = rounds[-1]
        self.rounds.append(ProviderResult(final_payload=_final_payload(), usage=final_usage, cost=final_cost))

    def queue_tool_calls(self, names: list[str]) -> None:
        self.rounds = [
            ProviderResult(
                tool_calls=tuple(
                    RequestedToolCall(f"call-{index}", name, {"url": "https://example.com/article"})
                    for index, name in enumerate(names, start=1)
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 2},
                cost=0.001,
            )
        ]

    def queue_tool_rounds(self, names: list[str]) -> None:
        self.rounds = [
            ProviderResult(
                tool_calls=(RequestedToolCall(f"call-{index}", name, {"url": "https://example.com/article"}),),
                usage={"prompt_tokens": 10, "completion_tokens": 2},
                cost=0.001,
            )
            for index, name in enumerate(names, start=1)
        ]
        self.rounds.append(ProviderResult(final_payload=_final_payload(), usage={"prompt_tokens": 10, "completion_tokens": 2}, cost=0.001))

    def queue_final(
        self,
        *,
        script: str = "Narration from research.",
        source_ids_used: list[str] | None = None,
        evidence_claims: list[EvidenceClaim] | None = None,
        evidence_quote: str = "Exact source fact appears here.",
        unstable: bool = False,
    ) -> None:
        if evidence_claims is None:
            evidence_claims = [
                EvidenceClaim(
                    claim="Verified fact",
                    source_id="source-1",
                    evidence_quote=evidence_quote,
                    unstable=unstable,
                )
            ]
        final_payload = ProviderFinalPayload.model_construct(
            script=script,
            source_ids_used=["source-1"] if source_ids_used is None else source_ids_used,
            model_knowledge_used=True,
            evidence_claims=evidence_claims,
        )
        self.rounds = [
            ProviderResult(
                tool_calls=(RequestedToolCall("call-1", "fetch_url", {"url": "https://example.com/article"}),),
                usage={"prompt_tokens": 10, "completion_tokens": 2},
                cost=0.001,
            ),
            ProviderResult(
                final_payload=final_payload,
                usage={"prompt_tokens": 20, "completion_tokens": 4},
                cost=0.002,
            ),
        ]

    def set_context_limit(self, value: int) -> None:
        self.context_limit = value

    def resolve_capability(self, model_id: str, api_key: SecretStr) -> ModelCapability:
        self.capabilities.append((model_id, api_key))
        return ModelCapability(
            model_id=model_id,
            supports_tools=True,
            context_tokens=self.context_limit,
        )

    def complete(self, request) -> ProviderResult:
        self.calls.append(request)
        if not self.rounds:
            self.queue_final()
        return self.rounds.pop(0)


def _final_payload() -> ProviderFinalPayload:
    return ProviderFinalPayload(
        script="Narration from research.",
        source_ids_used=["source-1"],
        model_knowledge_used=True,
        evidence_claims=[
            EvidenceClaim(
                claim="Verified fact",
                source_id="source-1",
                evidence_quote="Exact source fact appears here.",
                unstable=False,
            )
        ],
    )


def _clip_plan(target_words: int = 130) -> SixClipPlan:
    return SixClipPlan(
        target_words=target_words,
        segments=[
            SixClipSegment(
                index=index,
                start_sec=(index - 1) * 10,
                end_sec=index * 10,
                title=f"Clip {index}",
                narration_context=f"Narration {index}",
                video_prompt=(
                    "Create a realistic 10-second vertical 9:16 video. "
                    "0-3 seconds: open. 3-6 seconds: continue. "
                    "6-10 seconds: finish."
                ),
            )
            for index in range(1, 7)
        ],
    )


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def runtime():
    return FakeRuntime()


@pytest.fixture
def store(tmp_path):
    return ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")


@pytest.fixture
def settings():
    return FakeSettings()


@pytest.fixture
def service(adapter, runtime, store, settings):
    from app.services.cloud_agent.research.service import ResearchScriptService

    return ResearchScriptService(
        runtime=runtime,
        settings=settings,
        store=store,
        adapters={"openrouter": adapter},
        clip_plan_generator=lambda script, language, target_words: _clip_plan(target_words),
    )


def request_with_urls(urls: list[str]) -> ResearchDraftRequest:
    return ResearchDraftRequest(
        subject="Research topic",
        language="English",
        target_words=130,
        provider="openrouter",
        model_choice="openai/gpt-5.6-sol-pro",
        custom_model_id="",
        source_urls=urls,
        custom_system_prompt="Use a neutral educational tone.",
    )


def request_with_one_url() -> ResearchDraftRequest:
    return request_with_urls(["https://example.com/article"])


def request_with_three_urls() -> ResearchDraftRequest:
    return request_with_urls(
        [
            "https://example.com/article",
            "https://example.com/second",
            "https://example.com/third",
        ]
    )


def request_with_model(model_choice: str, custom_model_id: str) -> ResearchDraftRequest:
    request = request_with_one_url()
    return request.model_copy(
        update={"model_choice": model_choice, "custom_model_id": custom_model_id}
    )


def test_success_returns_existing_draft_shape_and_provenance(service, store):
    result = service.create_draft(request_with_one_url())

    assert {"script", "master_prompt", "clip_plan", "research_draft_id", "sources", "accounting"} <= set(result.model_dump())
    assert result.accounting.provider_rounds == 2
    assert store.get(result.research_draft_id).evidence_mode == "source_evidence + model_knowledge"


def test_accounting_sums_provider_round_usage_and_cost(service, adapter):
    adapter.queue_round_usage([
        ({"prompt_tokens": 100, "completion_tokens": 20}, 0.01),
        ({"prompt_tokens": 200, "completion_tokens": 40}, 0.02),
    ])

    result = service.create_draft(request_with_one_url())

    assert result.accounting.usage == {"prompt_tokens": 300, "completion_tokens": 60}
    assert result.accounting.cost == pytest.approx(0.03)


def test_missing_cost_in_any_provider_round_remains_unavailable(service, adapter):
    adapter.rounds = [
        ProviderResult(
            tool_calls=(
                RequestedToolCall(
                    "call-1", "fetch_url", {"url": "https://example.com/article"}
                ),
            ),
            usage={"prompt_tokens": 10},
            cost=0.01,
        ),
        ProviderResult(
            final_payload=_final_payload(),
            usage={"completion_tokens": 4},
            cost=None,
        ),
    ]

    result = service.create_draft(request_with_one_url())

    assert result.accounting.cost is None


def test_fourth_tool_is_rejected_without_partial_batch_execution(service, adapter, runtime):
    adapter.queue_tool_calls(["fetch_url", "fetch_url", "read_pdf", "fetch_url"])

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_three_urls())

    assert captured.value.code == "TOOL_CALL_LIMIT_EXCEEDED"
    assert runtime.executed == []


def test_context_overflow_is_error_not_silent_truncation(service, adapter):
    adapter.set_context_limit(10)

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())

    assert captured.value.code == "SOURCE_CONTEXT_TOO_LARGE"

def test_empty_urls_are_typed_before_provider_call(service, adapter):
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls([]))

    assert captured.value.code == "URL_REQUIRED"
    assert adapter.calls == []


def test_private_dns_target_is_rejected_before_provider_sees_url(service, runtime, adapter):
    runtime.reject_preflight("https://private-name.example", code="URL_TARGET_NOT_PUBLIC")

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls(["https://private-name.example"]))

    assert captured.value.code == "URL_TARGET_NOT_PUBLIC"
    assert adapter.calls == []


def test_private_dns_target_is_rejected_before_api_key_lookup(
    service,
    runtime,
    settings,
):
    runtime.reject_preflight("https://private-name.example", code="URL_TARGET_NOT_PUBLIC")

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls(["https://private-name.example"]))

    assert captured.value.code == "URL_TARGET_NOT_PUBLIC"
    assert settings.calls == []


def test_more_than_three_supplied_urls_fail_before_provider_call(service, adapter):
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls(["https://a.example", "https://b.example", "https://c.example", "https://d.example"]))

    assert captured.value.code == "URL_INVALID"
    assert adapter.calls == []


def test_custom_choice_requires_non_blank_custom_model_before_provider_call(service, adapter):
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_model(model_choice="custom", custom_model_id=""))

    assert captured.value.code == "PROVIDER_MODEL_UNSUPPORTED"
    assert adapter.calls == []


def test_canonical_duplicate_urls_collapse_before_tool_call(service, runtime):
    request = request_with_urls([
        "https://EXAMPLE.com:443/article#top", "https://example.com/article",
    ])

    service.create_draft(request)

    assert runtime.executed_urls == ["https://example.com/article"]


def test_repeated_model_tool_call_reuses_source_but_consumes_budget(service, adapter, runtime):
    adapter.queue_tool_rounds(["fetch_url", "fetch_url"])

    result = service.create_draft(request_with_one_url())

    assert runtime.executed_urls == ["https://example.com/article"]
    assert result.accounting.tool_calls == 2


def test_evidence_block_is_not_repeated_across_provider_rounds(service, adapter):
    adapter.queue_tool_rounds(["fetch_url", "fetch_url"])

    service.create_draft(request_with_one_url())

    final_request_tool_content = "\n".join(
        str(message.get("content", ""))
        for message in adapter.calls[-1].messages
        if message.get("role") == "tool"
    )
    assert final_request_tool_content.count("Exact source fact appears here.") == 1
    assert "already_emitted" in final_request_tool_content


def test_multi_tool_batch_emits_each_evidence_block_once(service, adapter, runtime):
    runtime.sources_by_url["https://example.com/article"] = _source(
        "https://example.com/article",
        source_id="source-1",
        content="Shared fact.\n\nUnique first fact.",
    )
    runtime.sources_by_url["https://example.com/second"] = _source(
        "https://example.com/second",
        source_id="source-2",
        content="Shared fact.\n\nUnique second fact.",
    )
    adapter.rounds = [
        ProviderResult(
            tool_calls=(
                RequestedToolCall("call-1", "fetch_url", {"url": "https://example.com/article"}),
                RequestedToolCall("call-2", "fetch_url", {"url": "https://example.com/second"}),
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 2},
            cost=0.001,
        ),
        ProviderResult(
            final_payload=ProviderFinalPayload(
                script="Narration from research.",
                source_ids_used=["source-1", "source-2"],
                model_knowledge_used=False,
                evidence_claims=[
                    EvidenceClaim(
                        claim="Verified first fact",
                        source_id="source-1",
                        evidence_quote="Unique first fact.",
                        unstable=False,
                    ),
                    EvidenceClaim(
                        claim="Verified second fact",
                        source_id="source-2",
                        evidence_quote="Unique second fact.",
                        unstable=False,
                    ),
                ],
            ),
            usage={"prompt_tokens": 20, "completion_tokens": 4},
            cost=0.002,
        ),
    ]

    service.create_draft(
        request_with_urls(["https://example.com/article", "https://example.com/second"])
    )

    second_request_messages = adapter.calls[1].messages
    tool_contents = "\n".join(
        str(message.get("content", ""))
        for message in second_request_messages
        if message.get("role") == "tool"
    )
    assert tool_contents.count("Shared fact.") == 1
    assert "sources=source-1,source-2" in tool_contents


def test_evidence_claim_quote_must_exist_in_successful_source(service, adapter):
    adapter.queue_final(evidence_quote="invented words")

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())

    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"


def test_model_only_final_is_rejected_even_after_source_read(service, adapter):
    adapter.queue_final(source_ids_used=[], evidence_claims=[])

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())

    assert captured.value.code == "SOURCE_EVIDENCE_EMPTY"


def test_claim_sources_must_match_declared_source_ids_used(service, adapter, runtime):
    runtime.sources_by_url["https://example.com/article"] = _source(
        "https://example.com/article",
        source_id="source-1",
        content="First verified fact.",
    )
    runtime.sources_by_url["https://example.com/second"] = _source(
        "https://example.com/second",
        source_id="source-2",
        content="Second verified fact.",
    )
    adapter.rounds = [
        ProviderResult(
            tool_calls=(
                RequestedToolCall(
                    "call-1", "fetch_url", {"url": "https://example.com/article"}
                ),
                RequestedToolCall(
                    "call-2", "fetch_url", {"url": "https://example.com/second"}
                ),
            )
        ),
        ProviderResult(
            final_payload=ProviderFinalPayload(
                script="Narration from research.",
                source_ids_used=["source-1"],
                model_knowledge_used=True,
                evidence_claims=[
                    EvidenceClaim(
                        claim="Second fact",
                        source_id="source-2",
                        evidence_quote="Second verified fact.",
                    )
                ],
            )
        ),
    ]

    with pytest.raises(ResearchError) as captured:
        service.create_draft(
            request_with_urls(
                ["https://example.com/article", "https://example.com/second"]
            )
        )

    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"


@pytest.mark.parametrize(
    ("script", "custom_system_prompt"),
    [
        ("Verified fact (source-1).", "Use a neutral educational tone."),
        ("Verified fact [1].", "Use a neutral educational tone."),
        ("Read more at https://example.com/article.", "Use a neutral educational tone."),
        ("Read more at www.example.com/article.", "Use a neutral educational tone."),
        ("Read more at https://example.com/article.", "Do not cite sources or include URLs."),
        ("Verified fact [1].", "Use sources only to verify factual accuracy."),
        ("Verified fact [1].", "No need to include citations."),
        ("Verified fact [1].", "You need not include citations."),
        ("Verified fact [1].", "Under no circumstances include citations."),
        (
            "Verified fact [1].",
            "Under no circumstances should the narration cite factual claims.",
        ),
        ("Verified fact [1].", "Do not explain how to cite factual claims."),
        ("Verified fact [1].", "Explain how to cite factual claims."),
        ("Verified fact [1].", "Include a reference implementation."),
        ("Verified fact [1].", "Display the source language accurately."),
    ],
)
def test_unrequested_or_negated_citation_forms_are_rejected(
    service,
    adapter,
    script,
    custom_system_prompt,
):
    adapter.queue_final(script=script)
    request = request_with_one_url().model_copy(
        update={"custom_system_prompt": custom_system_prompt}
    )

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request)

    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"


def test_affirmative_citation_request_allows_citations(service, adapter):
    adapter.queue_final(script="Read more at https://example.com/article [1].")
    request = request_with_one_url().model_copy(
        update={"custom_system_prompt": "Include source citations and URLs."}
    )

    result = service.create_draft(request)

    assert result.script == "Read more at https://example.com/article [1]."


def test_cite_factual_claims_authorizes_citations(service, adapter):
    adapter.queue_final(script="Verified fact [1].")
    request = request_with_one_url().model_copy(
        update={"custom_system_prompt": "Cite factual claims."}
    )

    result = service.create_draft(request)

    assert result.script == "Verified fact [1]."


def test_ordinary_non_citation_use_of_source_is_allowed(service, adapter):
    adapter.queue_final(script="A mountain spring is the village water source.")

    result = service.create_draft(request_with_one_url())

    assert result.script == "A mountain spring is the village water source."


def test_tool_urls_must_match_supplied_allowlist(service, adapter, runtime):
    del runtime
    adapter.queue_tool_calls(["fetch_url"])
    adapter.rounds[0] = ProviderResult(
        tool_calls=(RequestedToolCall("call-1", "fetch_url", {"url": "https://evil.example/article"}),),
        usage={"prompt_tokens": 10, "completion_tokens": 2},
        cost=0.001,
    )

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())

    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"


def test_failed_tool_result_can_continue_when_another_source_succeeds(service, adapter, runtime):
    runtime.fail_tool("fetch_url", "https://example.com/article", code="URL_FETCH_FAILED")
    runtime.sources_by_url["https://example.com/second"] = _source(
        "https://example.com/second",
        content="Second exact fact.",
    )
    adapter.rounds = [
        ProviderResult(
            tool_calls=(
                RequestedToolCall("call-1", "fetch_url", {"url": "https://example.com/article"}),
                RequestedToolCall("call-2", "fetch_url", {"url": "https://example.com/second"}),
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 2},
            cost=0.001,
        ),
        ProviderResult(
            final_payload=ProviderFinalPayload(
                script="Narration from research.",
                source_ids_used=["source-1"],
                model_knowledge_used=False,
                evidence_claims=[
                    EvidenceClaim(
                        claim="Verified fact",
                        source_id="source-1",
                        evidence_quote="Second exact fact.",
                        unstable=True,
                    )
                ],
            ),
            usage={"prompt_tokens": 20, "completion_tokens": 4},
            cost=0.002,
        ),
    ]

    result = service.create_draft(
        request_with_urls(["https://example.com/article", "https://example.com/second"])
    )

    assert result.script == "Narration from research."
    assert len(result.sources) == 1


def test_later_recoverable_failure_batch_continues_with_prior_source(
    service,
    adapter,
    runtime,
):
    runtime.fail_tool("fetch_url", "https://example.com/second", code="URL_FETCH_FAILED")
    adapter.rounds = [
        ProviderResult(
            tool_calls=(
                RequestedToolCall(
                    "call-1",
                    "fetch_url",
                    {"url": "https://example.com/article"},
                ),
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 2},
            cost=0.001,
        ),
        ProviderResult(
            tool_calls=(
                RequestedToolCall(
                    "call-2",
                    "fetch_url",
                    {"url": "https://example.com/second"},
                ),
            ),
            usage={"prompt_tokens": 11, "completion_tokens": 3},
            cost=0.002,
        ),
        ProviderResult(
            final_payload=_final_payload(),
            usage={"prompt_tokens": 20, "completion_tokens": 4},
            cost=0.003,
        ),
    ]

    result = service.create_draft(
        request_with_urls(["https://example.com/article", "https://example.com/second"])
    )

    assert result.script == "Narration from research."
    assert result.accounting.provider_rounds == 3
    assert result.accounting.tool_calls == 2
    assert runtime.executed_urls == [
        "https://example.com/article",
        "https://example.com/second",
    ]
    final_request_messages = adapter.calls[2].messages
    assert any(
        message.get("role") == "tool" and "URL_FETCH_FAILED" in message.get("content", "")
        for message in final_request_messages
    )


def test_errors_do_not_persist_research_draft(service, adapter, store):
    adapter.queue_final(evidence_quote="invented words")

    with pytest.raises(ResearchError):
        service.create_draft(request_with_one_url())

    assert store.list_drafts() == []


def test_provider_error_includes_attempted_round_in_sanitized_accounting(
    service, adapter, monkeypatch
):
    def fail(_request):
        raise ResearchError("PROVIDER_TIMEOUT", "raw provider detail")

    monkeypatch.setattr(adapter, "complete", fail)

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())

    assert captured.value.accounting.provider_rounds == 1
    assert captured.value.accounting.tool_calls == 0
    assert captured.value.accounting.cost is None


def test_runtime_error_includes_requested_tool_and_provider_round(service, adapter, runtime):
    adapter.queue_tool_calls(["fetch_url"])
    runtime.fail_tool(
        "fetch_url",
        "https://example.com/article",
        code="URL_TARGET_NOT_PUBLIC",
    )

    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())

    assert captured.value.accounting.provider_rounds == 1
    assert captured.value.accounting.tool_calls == 1
    assert captured.value.accounting.cost == pytest.approx(0.001)


def test_invariant_prompt_contains_all_security_and_evidence_policy(service, adapter):
    service.create_draft(request_with_one_url())

    invariant = adapter.calls[0].messages[0]["content"]
    for required_rule in (
        "Model knowledge must not be attributed to a supplied source",
        "When model knowledge conflicts with source evidence",
        "unstable facts cannot be asserted from model memory alone",
        "At least one supplied source must be read successfully",
        "Omit citations and URLs unless the editable prompt requests them",
        "at most three tool executions",
        "at most three provider rounds",
        "Never reveal API keys, authorization headers, cookies, or secrets",
        "Never retry, change providers, change models, or fall back",
    ):
        assert required_rule in invariant


def test_success_persists_complete_provenance_contract(service, adapter, store):
    adapter.rounds = [
        ProviderResult(
            tool_calls=(
                RequestedToolCall(
                    "call-1", "fetch_url", {"url": "https://example.com/article"}
                ),
            ),
            usage={"prompt_tokens": 10},
            cost=None,
        ),
        ProviderResult(
            final_payload=ProviderFinalPayload(
                script="Narration from research.",
                source_ids_used=["source-1"],
                model_knowledge_used=False,
                evidence_claims=[
                    EvidenceClaim(
                        claim="Verified fact",
                        source_id="source-1",
                        evidence_quote="Exact source fact appears here.",
                    )
                ],
            ),
            usage={"completion_tokens": 4},
            cost=None,
        ),
    ]

    result = service.create_draft(request_with_one_url())
    persisted = store.get(result.research_draft_id)

    assert persisted.tool_calls == 1
    assert persisted.provider_rounds == 2
    assert persisted.estimated_cost_usd is None
    assert persisted.evidence_mode == "source_evidence + model_knowledge"
    assert len(persisted.editable_prompt_fingerprint) == 64
    assert len(persisted.invariant_prompt_fingerprint) == 64
    assert persisted.editable_prompt_fingerprint != persisted.invariant_prompt_fingerprint
    assert persisted.sources[0].content_hash == result.sources[0].content_hash


def test_success_uses_custom_model_id(service, adapter):
    service.create_draft(
        request_with_model(model_choice="custom", custom_model_id=" custom/model ")
    )

    assert adapter.capabilities[0][0] == "custom/model"


def test_factory_builds_research_script_service(monkeypatch, tmp_path):
    from app.services.cloud_agent import factory
    from app.services.cloud_agent.research.service import ResearchScriptService

    monkeypatch.setitem(
        factory.config.app,
        "cloud_agent_db_path",
        str(tmp_path / "cloud-agent.sqlite3"),
    )

    service = factory.build_research_script_service()

    assert isinstance(service, ResearchScriptService)
