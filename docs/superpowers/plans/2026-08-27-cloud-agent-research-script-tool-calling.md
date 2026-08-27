# Cloud Agent Research Script Tool-Calling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a safe, provider-selectable Research Script mode that turns one to three operator-supplied public HTML/PDF sources into a durable draft in the existing Cloud Agent Script Editor.

**Architecture:** Add an app.services.cloud_agent.research subsystem behind the existing FastAPI router. It contains separate OpenRouter/AIHubMix OpenAI-compatible adapters, one shared public-web tool runtime, a bounded orchestration service, and separate Research SQLite tables in the configured Cloud Agent database. Standard Script and the Worker pipeline remain unchanged; both modes use the existing draft response and Script Editor state after a Research narration is accepted.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, existing OpenAI SDK, requests, Beautiful Soup 4, pypdf, SQLite, Streamlit, pytest, Ruff, uv.

**Spec:** docs/superpowers/specs/2026-08-27-cloud-agent-research-script-tool-calling-design.md

## Global Constraints

- Keep Standard Script behavior and imports unchanged; Research never falls back to it.
- Use only fetch_url(url) and read_pdf(url); do not implement search, OpenRouter server-side search, online suffixes, plugins, URL discovery, local knowledge bases, or Playwright.
- Require one to three public HTTP/HTTPS URLs, permit at most three tool executions and at most three provider API rounds per draft, and disable automatic paid retries.
- Use the existing FastAPI app, config.app, Cloud Agent SQLite path, factory composition, and Streamlit FastAPI client. Do not create a second app, worker, config loader, browser-profile manager, or browser session.
- Do not access Google Flow/Canva profiles, cookies, locks, VNC/noVNC, TTS, Flow, Canva, or paid APIs during automated tests.
- Provider keys are separate and write-only. Never log or return keys, authorization headers, cookies, signed URLs, raw provider payloads, or raw source content.
- Version one accepts only direct readable HTML/XHTML and PDF. Reject login, paywall, CAPTCHA, bot challenge, JavaScript-only, private/reserved network, unsupported content, and oversized sources with typed errors.
- Preserve the whole accepted normalized evidence packet. If it cannot fit the proved model context plus output reserve, return SOURCE_CONTEXT_TOO_LARGE; never silently truncate or summarize it.
- All production behavior is RED -> observed failure -> smallest GREEN -> focused tests -> relevant regression -> Ruff. Each task below is an independently reviewable commit.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| app/config/config.py | Research defaults and separate secret keys in existing config.app. |
| app/models/cloud_agent.py | Research Pydantic request/response/settings models and optional excluded research_draft_id. |
| app/services/cloud_agent/research/errors.py | Typed Research failures and safe Thai public-message map. |
| app/services/cloud_agent/research/models.py | Provider-neutral source, tool-call, accounting, capability, and provider result types. |
| app/services/cloud_agent/research/settings.py | Write-only provider-key and non-secret default persistence. |
| app/services/cloud_agent/research/store.py | Separate Research tables, provenance, lookup, and draft-to-job link. |
| app/services/cloud_agent/research/runtime.py | Public URL validation, pinned fetch, extraction, and exact deduplication. |
| app/services/cloud_agent/research/adapters.py | OpenRouter and AIHubMix protocol/capability adapters. |
| app/services/cloud_agent/research/service.py | Three-tool/three-round orchestration and common draft result. |
| app/services/cloud_agent/factory.py | Research construction from existing config.app. |
| app/controllers/v1/cloud_agent.py | Research routes, safe error mapping, optional Start association. |
| webui/cloud_agent.py | Research controls and existing Script Editor handoff. |
| test/services/cloud_agent/test_research_*.py | Mock-only Research unit, contract, and integration coverage. |

## Shared Interfaces

~~~python
class ResearchError(ValueError):
    def __init__(self, code: str, message: str, *, accounting: ResearchAccounting | None = None): ...

@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    canonical_url: str
    title: str
    content: str
    content_hash: str
    media_type: Literal["html", "pdf"]

@dataclass(frozen=True)
class ResearchAccounting:
    tool_calls: int = 0
    provider_rounds: int = 0
    usage: dict[str, int | float | str] | None = None
    cost: float | None = None

class ToolCallingAdapter(Protocol):
    provider_id: str
    def resolve_capability(self, model_id: str, api_key: str) -> ModelCapability: ...
    def complete(self, request: ProviderRequest) -> ProviderResult: ...

class ResearchToolRuntime:
    def execute(self, tool_name: str, supplied_url: str) -> ResearchSource: ...

class ResearchScriptService:
    def create_draft(self, request: ResearchDraftRequest) -> ResearchDraftResponse: ...
~~~

### Task 1: Define Research Contracts, Errors, and Configuration

**Files:**

- Create: app/services/cloud_agent/research/__init__.py
- Create: app/services/cloud_agent/research/errors.py
- Create: app/services/cloud_agent/research/models.py
- Modify: app/config/config.py
- Modify: app/models/cloud_agent.py
- Test: test/services/cloud_agent/test_research_contracts.py

**Consumes:** Existing config.app persistence and Cloud Agent Pydantic models.

**Produces:** All shared models and typed errors used by later tasks.

- [ ] **Step 1: Write failing contract/configuration tests**

~~~python
def test_research_request_requires_one_to_three_unique_urls():
    with pytest.raises(ValidationError, match="source_urls"):
        ResearchDraftRequest(
            subject="topic", language="", target_words=130, provider="openrouter",
            model_choice="openai/gpt-5.6-sol-pro", custom_model_id="",
            source_urls=[], custom_system_prompt="",
        )

def test_error_exposes_code_but_not_internal_detail():
    error = ResearchError("URL_TARGET_NOT_PUBLIC", "socket connected to 127.0.0.1")
    assert error.code == "URL_TARGET_NOT_PUBLIC"
    assert public_research_message(error.code) == "URL นี้ไม่ใช่ปลายทางสาธารณะที่อนุญาต"

def test_job_research_id_is_excluded_from_workflow_payload():
    request = CloudJobCreate(**valid_job_payload(), research_draft_id="draft-1")
    assert "research_draft_id" not in request.model_dump(mode="json")
~~~

- [ ] **Step 2: Run the test to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_contracts.py -q

Expected: FAIL because Research contracts and defaults do not exist.

- [ ] **Step 3: Implement the smallest common contract**

~~~python
class ResearchDraftRequest(BaseModel):
    subject: str
    language: str = ""
    target_words: int = Field(default=130, ge=40, le=400)
    provider: Literal["openrouter", "aihubmix"]
    model_choice: str
    custom_model_id: str = Field(default="", max_length=256)
    source_urls: list[str] = Field(min_length=1, max_length=3)
    custom_system_prompt: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def validate_urls_and_model(self):
        if not self.subject.strip() or not self.model_choice.strip():
            raise ValueError("subject and model_choice must not be blank")
        if len({url.strip() for url in self.source_urls}) != len(self.source_urls):
            raise ValueError("source_urls must be unique")
        return self

class CloudJobCreate(BaseModel):
    research_draft_id: str = Field(default="", max_length=64, exclude=True)
~~~

Add only Research defaults: separate OpenRouter/AIHubMix keys, provider/model/custom-model defaults, and Research prompt. Define all spec error codes and Thai safe-message mapping. Do not change Standard/TTS defaults.

- [ ] **Step 4: Run focused verification**

Run: uv run pytest test/services/cloud_agent/test_research_contracts.py -q && uv run ruff check app/config/config.py app/models/cloud_agent.py app/services/cloud_agent/research test/services/cloud_agent/test_research_contracts.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add app/config/config.py app/models/cloud_agent.py app/services/cloud_agent/research/__init__.py app/services/cloud_agent/research/errors.py app/services/cloud_agent/research/models.py test/services/cloud_agent/test_research_contracts.py
git commit -m "feat: define research script contracts"
~~~

### Task 2: Persist Research Settings, Provenance, and Job Links

**Files:**

- Create: app/services/cloud_agent/research/settings.py
- Create: app/services/cloud_agent/research/store.py
- Modify: app/services/cloud_agent/factory.py
- Test: test/services/cloud_agent/test_research_settings.py
- Test: test/services/cloud_agent/test_research_store.py

**Consumes:** Task 1 models/errors plus config.runtime_config_lock(), config.save_config(), and the configured Cloud Agent SQLite path.

**Produces:** Write-only settings, separate durable Research data, and script-hash validation for a Start association.

- [ ] **Step 1: Write failing settings/store tests**

~~~python
def test_settings_readback_redacts_key_and_blank_save_retains_it():
    service = ResearchSettingsService()
    service.set_api_key("openrouter", "secret-value")
    assert service.get_provider("openrouter").api_key_configured is True
    assert "secret-value" not in service.get_provider("openrouter").model_dump_json()
    assert service.get_api_key_for_generation("openrouter") == "secret-value"

def test_store_persists_only_non_secret_provenance(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(make_successful_draft())
    assert store.get(saved.research_draft_id).script_hash == sha256_text(saved.script)
    assert "source body" not in store.get(saved.research_draft_id).model_dump_json()

def test_association_rejects_changed_script(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(make_successful_draft())
    with pytest.raises(ResearchError, match="RESEARCH_RESPONSE_INVALID"):
        store.assert_script_matches(saved.research_draft_id, "edited narration")
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py -q

Expected: FAIL because Research settings and tables do not exist.

- [ ] **Step 3: Implement service and repository**

~~~python
class ResearchSettingsService:
    def get_defaults(self) -> ResearchDefaults: ...
    def update_defaults(self, patch: ResearchSettingsPatch) -> ResearchDefaults: ...
    def get_provider(self, provider_id: str) -> ResearchProviderMetadata: ...
    def set_api_key(self, provider_id: str, value: str) -> ResearchProviderMetadata: ...
    def remove_api_key(self, provider_id: str, confirmed: bool) -> ResearchProviderMetadata: ...
    def get_api_key_for_generation(self, provider_id: str) -> str: ...

class ResearchDraftStore:
    def save_success(self, draft: PersistedResearchDraft) -> PersistedResearchDraft: ...
    def get(self, research_draft_id: str) -> PersistedResearchDraft | None: ...
    def assert_script_matches(self, research_draft_id: str, script: str) -> PersistedResearchDraft: ...
    def link_job(self, research_draft_id: str, job_id: str) -> None: ...
~~~

Create research_drafts, research_sources, and research_job_associations with CREATE TABLE IF NOT EXISTS in the same configured SQLite file and the same WAL/busy timeout as CloudJobStore. Store IDs, script/source hashes, URLs/titles, provider/model, counts, sanitized usage/cost, timestamps, prompt fingerprints, and evidence mode. Do not store content, key, headers, cookies, signed URLs, or raw provider data. A blank key save retains the old value; removal requires confirmed is True.

Add factory builders using only config.app["cloud_agent_db_path"].

- [ ] **Step 4: Run focused verification**

Run: uv run pytest test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py -q && uv run ruff check app/services/cloud_agent/factory.py app/services/cloud_agent/research test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add app/services/cloud_agent/factory.py app/services/cloud_agent/research/settings.py app/services/cloud_agent/research/store.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py
git commit -m "feat: persist research script settings and drafts"
~~~

### Task 3: Add Guarded Public-Web Tools

**Files:**

- Modify: pyproject.toml
- Modify: uv.lock
- Create: app/services/cloud_agent/research/runtime.py
- Test: test/services/cloud_agent/test_research_runtime.py

**Consumes:** Task 1 source/error models.

**Produces:** The only executable fetch_url/read_pdf boundary, independent of providers and browsers.

- [ ] **Step 1: Write failing runtime tests**

~~~python
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private", "http://[::1]/", "http://169.254.169.254/",
    "http://user:pass@example.com/", "file:///etc/passwd", "https://example.com:8080/",
])
def test_runtime_rejects_non_public_or_unsupported_target(url):
    with pytest.raises(ResearchError, match="URL_TARGET_NOT_PUBLIC|URL_INVALID"):
        runtime.execute("fetch_url", url)

def test_redirect_is_revalidated_before_connection(fake_http):
    fake_http.redirect("https://public.example/a", "http://127.0.0.1/private")
    with pytest.raises(ResearchError, match="URL_REDIRECT_REJECTED"):
        runtime.execute("fetch_url", "https://public.example/a")

def test_html_strips_chrome_but_preserves_complete_readable_text(fake_http):
    source = runtime.execute("fetch_url", "https://public.example/article")
    assert source.content == "First fact.\n\nFinal fact."
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_runtime.py -q

Expected: FAIL because no runtime exists.

- [ ] **Step 3: Implement the public-only runtime**

~~~python
class ResearchToolRuntime:
    MAX_BODY_BYTES = 10 * 1024 * 1024
    MAX_PDF_PAGES = 30
    MAX_REDIRECTS = 5

    def canonicalize_supplied_url(self, raw_url: str) -> str: ...
    def execute(self, tool_name: str, supplied_url: str) -> ResearchSource:
        if tool_name not in {"fetch_url", "read_pdf"}:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "unsupported tool")
        response = self._download_public_target(supplied_url)
        return self._extract_html(response) if tool_name == "fetch_url" else self._extract_pdf(response)
~~~

Add beautifulsoup4==4.15.0 and pypdf==6.16.2, then run uv lock. Implement a pinned HTTP client: canonicalize; accept HTTP(S) ports 80/443 only; reject credentials/signed query parameters; resolve before every connection; reject all non-public/mixed answers; connect only to the checked address with original Host/SNI; disable auto-redirect and revalidate every redirect, maximum five. Enforce 10 MiB decoded-body cap.

For HTML, remove scripts/styles/nav/footer/cookie banners/hidden content and normalize visible text. For PDFs, require MIME plus signature, reject encryption/malformed files, reject over 30 pages, extract text with PdfReader, and reject textless scans. Hash full normalized content. Exact duplicate URL/blocks collapse with retained source IDs. Do not truncate or summarize content; model-context rejection belongs to Task 5.

- [ ] **Step 4: Run runtime and lock verification**

Run: uv run pytest test/services/cloud_agent/test_research_runtime.py -q && uv lock --check && uv run ruff check app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_runtime.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add pyproject.toml uv.lock app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_runtime.py
git commit -m "feat: add guarded research source tools"
~~~

### Task 4: Implement OpenRouter and AIHubMix Tool-Calling Adapters

**Files:**

- Create: app/services/cloud_agent/research/adapters.py
- Test: test/services/cloud_agent/test_research_adapters.py

**Consumes:** Tasks 1-2 models/settings and the repository's existing OpenAI SDK.

**Produces:** Provider protocol conversion, strict capability checks, and sanitized accounting.

- [ ] **Step 1: Write failing adapter fixture tests**

~~~python
def test_openrouter_declares_only_owned_tools():
    adapter = OpenRouterToolCallingAdapter(fake_client())
    adapter.complete(provider_request())
    assert fake_client().calls[0]["tools"] == [FETCH_URL_TOOL, READ_PDF_TOOL]
    assert "web_search" not in repr(fake_client().calls)

def test_aihubmix_parses_tool_call_and_final_usage():
    result = AIHubMixToolCallingAdapter(fake_client(tool_call="fetch_url")).complete(provider_request())
    assert result.tool_calls[0].arguments == {"url": "https://example.com/a"}

def test_unknown_custom_model_fails_before_completion():
    with pytest.raises(ResearchError, match="PROVIDER_TOOL_CALLING_UNSUPPORTED"):
        adapter.resolve_capability("custom/unknown", "key")
    assert fake_client().calls == []
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_adapters.py -q

Expected: FAIL because adapters do not exist.

- [ ] **Step 3: Implement adapters**

~~~python
FETCH_URL_TOOL = {"type": "function", "function": {"name": "fetch_url", "parameters": URL_PARAMETERS}}
READ_PDF_TOOL = {"type": "function", "function": {"name": "read_pdf", "parameters": URL_PARAMETERS}}

class OpenRouterToolCallingAdapter(OpenAICompatibleToolCallingAdapter):
    provider_id = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

class AIHubMixToolCallingAdapter(OpenAICompatibleToolCallingAdapter):
    provider_id = "aihubmix"
    base_url = "https://aihubmix.com/v1"
~~~

Use OpenAI(...).chat.completions.create with only the two owned tools. Translate assistant tool-call IDs/name/JSON arguments, tool result messages, final narration, and provider usage/cost fields into provider-neutral data. Classify auth, timeout, unsupported model, and malformed output without raw payloads.

Catalog models use tested local capability metadata. On explicit Generate only, Custom Model ID capability lookup must prove tools and a usable context limit before completion. If not proved, return PROVIDER_TOOL_CALLING_UNSUPPORTED or PROVIDER_MODEL_UNSUPPORTED. Never run this lookup on load/refresh/save and never substitute model/provider.

- [ ] **Step 4: Run focused verification**

Run: uv run pytest test/services/cloud_agent/test_research_adapters.py -q && uv run ruff check app/services/cloud_agent/research/adapters.py test/services/cloud_agent/test_research_adapters.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add app/services/cloud_agent/research/adapters.py test/services/cloud_agent/test_research_adapters.py
git commit -m "feat: add research tool calling adapters"
~~~

### Task 5: Build the Three-Tool/Three-Round Research Service

**Files:**

- Create: app/services/cloud_agent/research/service.py
- Modify: app/services/cloud_agent/factory.py
- Test: test/services/cloud_agent/test_research_service.py

**Consumes:** Tasks 1-4 plus existing generate_six_clip_plan and build_master_prompt.

**Produces:** Atomic common draft response, provenance, evidence policy enforcement, and safe accounting.

- [ ] **Step 1: Write failing state-machine tests**

~~~python
def test_success_returns_existing_draft_shape_and_provenance():
    result = service.create_draft(request_with_one_url())
    assert {"script", "master_prompt", "clip_plan", "research_draft_id", "sources", "accounting"} <= set(result.model_dump())
    assert result.accounting.provider_rounds == 2
    assert store.get(result.research_draft_id).evidence_mode == "source_evidence + model_knowledge"

def test_fourth_tool_is_rejected_without_partial_batch_execution():
    adapter.queue_tool_calls(["fetch_url", "fetch_url", "read_pdf", "fetch_url"])
    with pytest.raises(ResearchError, match="TOOL_CALL_LIMIT_EXCEEDED"):
        service.create_draft(request_with_three_urls())
    assert runtime.executed == []

def test_context_overflow_is_error_not_silent_truncation():
    adapter.set_context_limit(10)
    with pytest.raises(ResearchError, match="SOURCE_CONTEXT_TOO_LARGE"):
        service.create_draft(request_with_one_url())
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_service.py -q

Expected: FAIL because the orchestration service does not exist.

- [ ] **Step 3: Implement the bounded service**

~~~python
class ResearchScriptService:
    MAX_TOOL_EXECUTIONS = 3
    MAX_PROVIDER_ROUNDS = 3

    def create_draft(self, request: ResearchDraftRequest) -> ResearchDraftResponse:
        settings = self.settings.require_generation_settings(request)
        capability = self.adapters[request.provider].resolve_capability(settings.model_id, settings.api_key)
        messages = self._initial_messages(request, capability)
        accounting = ResearchAccounting()
        for round_number in range(1, self.MAX_PROVIDER_ROUNDS + 1):
            result = self.adapters[request.provider].complete(self._provider_request(messages, settings, capability))
            accounting = accounting.with_provider_round(result.usage, result.cost)
            if result.tool_calls:
                self._require_synthesis_round(round_number)
                sources, messages, accounting = self._execute_tool_batch(result.tool_calls, request, messages, accounting)
                continue
            return self._persist_valid_final(result.final_text, sources, request, accounting)
        raise ResearchError("PROVIDER_ROUND_LIMIT_EXCEEDED", "provider round limit reached", accounting=accounting)
~~~

Allow tool URLs only if their canonical form is among supplied URLs. Require a final synthesis round after any tool round. Reject batches exceeding remaining tool budget before executing any call. Require at least one successful source. Build immutable security/evidence/model-knowledge instructions before editable prompt and wrap source contents as untrusted data.

Calculate complete evidence plus output reserve against adapter-proved context before each completion. Raise SOURCE_CONTEXT_TOO_LARGE if it cannot fit. Never chunk-select, summarize, or truncate. Validate final narration and source attribution, then create existing six-clip plan/master prompt, save provenance last, and return common draft result. On any error, persist no successful draft and create no job.

- [ ] **Step 4: Run service and dependency regression**

Run: uv run pytest test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py -q && uv run ruff check app/services/cloud_agent/factory.py app/services/cloud_agent/research/service.py test/services/cloud_agent/test_research_service.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add app/services/cloud_agent/factory.py app/services/cloud_agent/research/service.py test/services/cloud_agent/test_research_service.py
git commit -m "feat: orchestrate bounded research script drafts"
~~~

### Task 6: Expose FastAPI Routes and Safe Start Association

**Files:**

- Modify: app/controllers/v1/cloud_agent.py
- Modify: test/services/test_cloud_agent_controller.py
- Create: test/services/cloud_agent/test_research_controller.py

**Consumes:** Tasks 1-5 and existing controller dependency override patterns.

**Produces:** Existing-router Research API, safe typed failures, settings/key operations, and optional post-create job link.

- [ ] **Step 1: Write failing controller tests**

~~~python
def test_research_draft_route_is_on_existing_cloud_agent_router(tmp_path):
    response = research_client(tmp_path).post("/api/v1/cloud-agent/research/drafts", json=research_payload())
    assert response.status_code == 200
    assert response.json()["data"]["research_draft_id"] == "draft-1"

def test_research_failure_is_typed_safe_and_creates_no_job(tmp_path):
    response = research_client(tmp_path).post("/api/v1/cloud-agent/research/drafts", json={**research_payload(), "source_urls": []})
    assert response.status_code == 422
    assert response.json()["code"] == "URL_REQUIRED"
    assert "secret" not in response.text

def test_start_requires_research_script_hash_match(tmp_path):
    payload = {**valid_job_payload(), "research_draft_id": "draft-1", "script": "different"}
    assert research_client(tmp_path).post("/api/v1/cloud-agent/jobs", json=payload).status_code == 422
~~~

- [ ] **Step 2: Run controller tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py -q

Expected: FAIL because Research dependencies/routes are absent.

- [ ] **Step 3: Add routes to the existing controller only**

~~~python
def get_research_service() -> ResearchScriptService:
    return build_research_script_service()

def get_research_draft_store() -> ResearchDraftStore:
    return build_research_draft_store()

@router.post("/cloud-agent/research/drafts")
def create_research_draft(body: ResearchDraftRequest, service: ResearchScriptService = Depends(get_research_service)):
    try:
        return utils.get_response(200, service.create_draft(body).model_dump(mode="json"))
    except ResearchError as exc:
        raise _research_http_exception(exc) from exc
~~~

Implement specified provider catalog, get/update settings, set/remove key, create draft, and draft metadata routes. Map ResearchError to stable status/code and Thai message, never internal text. GET/settings/key paths do not invoke adapters/providers.

Before normal job creation, non-empty excluded research_draft_id calls assert_script_matches. After normal job creation, link the IDs through ResearchDraftStore. Do not add Research fields to CloudJob storage or Worker behavior.

- [ ] **Step 4: Run controller verification**

Run: uv run pytest test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py -q && uv run ruff check app/controllers/v1/cloud_agent.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add app/controllers/v1/cloud_agent.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py
git commit -m "feat: expose research script API"
~~~

### Task 7: Add Research Mode to the Streamlit Thin Client

**Files:**

- Modify: webui/cloud_agent.py
- Modify: test/services/test_cloud_agent_webui.py

**Consumes:** Task 6 route/response contracts and existing _api, _store_draft, and Start controls.

**Produces:** Explicit mode selection and successful Research result handoff into unchanged Script Editor.

- [ ] **Step 1: Write failing WebUI tests**

~~~python
def test_research_mode_is_fastapi_only_and_has_owned_controls():
    source = UI_SOURCE.read_text(encoding="utf-8")
    for label in ("Standard Script", "Research Script", "Source URLs", "Generate Research Script", "Sources"):
        assert label in source
    assert "sqlite3" not in source.lower()
    assert "PersistentBrowserManager" not in source

def test_research_failure_never_stores_draft(monkeypatch):
    monkeypatch.setattr(cloud_agent, "_prepare_research_draft", raises_url_required)
    monkeypatch.setattr(cloud_agent, "_store_draft", lambda _draft: pytest.fail("must preserve editor"))
    render_research_generate_click(monkeypatch)
~~~

- [ ] **Step 2: Run WebUI tests to verify RED**

Run: uv run pytest test/services/test_cloud_agent_webui.py -q

Expected: FAIL because current UI has only Standard Script controls.

- [ ] **Step 3: Implement mode-specific controls before shared editor**

~~~python
def _prepare_research_draft(**payload):
    return _api("POST", "research/drafts", json=payload, timeout=DRAFT_TIMEOUT_SECONDS)

mode = st.radio("Script Creation Mode", ["Standard Script", "Research Script"], key="cloud_agent_script_mode")
if mode == "Research Script":
    st.caption("Research generation may call the selected provider up to 3 rounds.")
else:
    # retain existing Standard Generate Script and Refresh Draft behavior
~~~

Keep existing Standard keys/controls and behavior unchanged. Render Research provider/model/custom model/key/prompt/URL controls; save only through FastAPI and verify non-secret readback. On success pass common response to _store_draft and retain only research_draft_id in session state so _start_job sends it. On failure show Thai API error and never call _store_draft.

Show busy state while Generate runs, no fake progress. Show maximum three rounds before request and actual rounds/usage/cost afterward; mark unavailable metadata unavailable. Render Sources only from successful response sources. Load/refresh/settings/prompt saves make no provider/draft request.

- [ ] **Step 4: Run UI regression**

Run: uv run pytest test/services/test_cloud_agent_webui.py test/services/cloud_agent/test_research_controller.py -q && uv run ruff check webui/cloud_agent.py test/services/test_cloud_agent_webui.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add webui/cloud_agent.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add research script mode to cloud agent"
~~~

### Task 8: Complete Non-Paid Regression Verification

**Files:**

- Create: docs/cloud-agent-research-script-verification.md
- Test: all Research test files and existing Cloud Agent controller/WebUI regressions

**Consumes:** Tasks 1-7.

**Produces:** Committed evidence that the new draft subsystem did not affect Standard Script or paid production services.

- [ ] **Step 1: Add final route-inventory regression**

~~~python
def test_research_route_inventory_uses_only_cloud_agent_prefix():
    routes = registered_cloud_agent_routes()
    assert "POST /api/v1/cloud-agent/research/drafts" in routes
    assert not any("web_search" in route or ":online" in route for route in routes)
~~~

- [ ] **Step 2: Run inventory test**

Run: uv run pytest test/services/cloud_agent/test_research_controller.py::test_research_route_inventory_uses_only_cloud_agent_prefix -q

Expected: PASS. If it fails, return to owning task; do not patch around inventory.

- [ ] **Step 3: Run complete non-paid matrix**

~~~bash
uv run pytest \
  test/services/cloud_agent/test_research_contracts.py \
  test/services/cloud_agent/test_research_settings.py \
  test/services/cloud_agent/test_research_store.py \
  test/services/cloud_agent/test_research_runtime.py \
  test/services/cloud_agent/test_research_adapters.py \
  test/services/cloud_agent/test_research_service.py \
  test/services/cloud_agent/test_research_controller.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_webui.py -q
uv run ruff check app webui test
uv lock --check
~~~

Expected: all commands PASS. Tests use fixtures/mocks only; no provider, TTS, Flow, Canva, browser, or remote media action occurs.

- [ ] **Step 4: Record verification evidence**

~~~markdown
# Cloud Agent Research Script Verification

## Automated non-paid verification

- Research contract, settings, store, runtime, adapter, service, controller, and WebUI tests: PASS
- Existing Cloud Agent controller/WebUI regressions: PASS
- Ruff and uv lock check: PASS

## Live-operation scope

No OpenRouter/AIHubMix generation, TTS synthesis, Google Flow generation, Canva mutation, browser session, or paid retry was executed.
~~~

- [ ] **Step 5: Commit**

~~~bash
git add docs/cloud-agent-research-script-verification.md test/services/cloud_agent/test_research_controller.py
git commit -m "test: verify research script mode"
~~~

## Post-Plan Gate

Do not execute this plan until the operator explicitly approves it. After automated verification, request separate approval before any live provider smoke. An approved smoke is one bounded Research attempt only; it must not start TTS, Google Flow, Canva, or a CloudJob unless separately authorized.
