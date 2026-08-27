# Cloud Agent Research Script Tool-Calling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

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
| app/services/cloud_agent/research/network.py | DNS/IP validation, address-pinned HTTP(S), redirects, timeouts, and body limit. |
| app/services/cloud_agent/research/runtime.py | Tool dispatch, HTML/PDF extraction, source hashing, and exact deduplication. |
| app/services/cloud_agent/research/adapters.py | OpenRouter and AIHubMix protocol/capability adapters. |
| app/services/cloud_agent/research/service.py | Three-tool/three-round orchestration and common draft result. |
| app/services/cloud_agent/factory.py | Research construction from existing config.app. |
| app/controllers/v1/cloud_agent.py | Research routes, safe error mapping, optional Start association. |
| webui/cloud_agent.py | Research controls and existing Script Editor handoff. |
| test/services/cloud_agent/test_research_*.py | Mock-only Research unit, contract, and integration coverage. |

## Shared Interfaces

~~~python
class ResearchError(ValueError):
    def __init__(self, code: str, diagnostic_message: str, *, accounting: ResearchAccounting | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostic_message = diagnostic_message
        self.accounting = accounting

@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    canonical_url: str
    title: str
    content: str
    content_hash: str
    media_type: Literal["html", "pdf"]

@dataclass(frozen=True)
class EvidenceBlock:
    text: str
    source_ids: tuple[str, ...]

@dataclass(frozen=True)
class EvidencePacket:
    sources: tuple[ResearchSource, ...]
    blocks: tuple[EvidenceBlock, ...]

@dataclass(frozen=True)
class ResearchAccounting:
    tool_calls: int = 0
    provider_rounds: int = 0
    usage: dict[str, int | float] | None = None
    cost: float | None = None
    def with_provider_round(self, usage: dict[str, int | float] | None, cost: float | None) -> "ResearchAccounting":
        totals = dict(self.usage or {})
        for name, value in (usage or {}).items():
            totals[name] = totals.get(name, 0) + value
        total_cost = None if self.cost is None and cost is None else (self.cost or 0.0) + (cost or 0.0)
        return replace(self, provider_rounds=self.provider_rounds + 1, usage=totals or None, cost=total_cost)
    def with_tool_call(self) -> "ResearchAccounting":
        return replace(self, tool_calls=self.tool_calls + 1)

@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    supports_tools: bool
    context_tokens: int

@dataclass(frozen=True)
class RequestedToolCall:
    call_id: str
    name: str
    arguments: dict[str, str]

@dataclass(frozen=True)
class ProviderRequest:
    model_id: str
    api_key: SecretStr
    messages: list[dict]
    tools: list[dict]
    max_output_tokens: int

class EvidenceClaim(BaseModel):
    claim: str
    source_id: str
    evidence_quote: str
    unstable: bool = False

class ProviderFinalPayload(BaseModel):
    script: str
    source_ids_used: list[str]
    model_knowledge_used: bool
    evidence_claims: list[EvidenceClaim]

@dataclass(frozen=True)
class ProviderResult:
    tool_calls: tuple[RequestedToolCall, ...] = ()
    final_payload: ProviderFinalPayload | None = None
    usage: dict[str, int | float] | None = None
    cost: float | None = None

class ResearchProviderMetadata(BaseModel):
    id: Literal["openrouter", "aihubmix"]
    models: list[str]
    default_model: str
    custom_model_id: str
    api_key_configured: bool

class ResearchDefaults(BaseModel):
    provider: Literal["openrouter", "aihubmix"]
    openrouter_model: str
    openrouter_custom_model_id: str
    aihubmix_model: str
    aihubmix_custom_model_id: str
    custom_system_prompt: str

class ResearchSettingsPatch(ResearchDefaults):
    pass

class ResearchAPIKeyPatch(BaseModel):
    api_key: str = Field(default="", max_length=4096)

class ConfirmedResearchKeyRemoval(BaseModel):
    confirmed: Literal[True]

class ResearchSourceMetadata(BaseModel):
    source_id: str
    url: str
    title: str
    content_hash: str

class ResearchDraftResponse(BaseModel):
    script: str
    master_prompt: str
    clip_plan: SixClipPlan
    research_draft_id: str
    sources: list[ResearchSourceMetadata]
    accounting: ResearchAccounting

class PersistedResearchDraft(BaseModel):
    research_draft_id: str
    script_hash: str
    provider: str
    effective_model_id: str
    sources: list[ResearchSourceMetadata]
    tool_calls: int
    provider_rounds: int
    usage: dict | None
    cost: float | None
    created_at: str
    editable_prompt_fingerprint: str
    invariant_prompt_fingerprint: str
    evidence_mode: Literal["source_evidence + model_knowledge"]

class ToolCallingAdapter(Protocol):
    provider_id: str
    def resolve_capability(self, model_id: str, api_key: SecretStr) -> ModelCapability:
        raise NotImplementedError
    def complete(self, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError

class ResearchToolRuntimeProtocol(Protocol):
    def preflight_urls(self, raw_urls: list[str]) -> tuple[str, ...]:
        raise NotImplementedError
    def execute(self, tool_name: str, supplied_url: str) -> ResearchSource:
        raise NotImplementedError
    def aggregate(self, sources: list[ResearchSource]) -> EvidencePacket:
        raise NotImplementedError

class ResearchScriptServiceProtocol(Protocol):
    def create_draft(self, request: ResearchDraftRequest) -> ResearchDraftResponse:
        raise NotImplementedError
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
def test_research_request_preserves_urls_and_defaults_citations_off():
    request = ResearchDraftRequest(
        subject="topic", language="", target_words=130, provider="openrouter",
        model_choice="openai/gpt-5.6-sol-pro", custom_model_id="",
        source_urls=[], custom_system_prompt="",
    )
    assert request.source_urls == []
    assert request.allow_citations is False
    assert request.model_dump(mode="json")["allow_citations"] is False

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
    model_choice: str = Field(max_length=256)
    custom_model_id: str = Field(default="", max_length=256)
    source_urls: list[str] = Field(default_factory=list)
    custom_system_prompt: str = Field(default="", max_length=8000)
    allow_citations: bool = False

    @model_validator(mode="after")
    def validate_urls_and_model(self):
        if not self.subject.strip() or not self.model_choice.strip():
            raise ValueError("subject and model_choice must not be blank")
        return self

class CloudJobCreate(BaseModel):
    # Add this field to the existing model; retain every current workflow field.
    research_draft_id: str = Field(default="", max_length=64, exclude=True)
~~~

Keep URL count, blank URL, scheme, and canonical-duplicate checks in the Task 5
domain preflight so the API returns `URL_REQUIRED`/`URL_INVALID` instead of the
application-wide generic Pydantic validation response. Add only Research
defaults: separate OpenRouter/AIHubMix keys, provider/model/custom-model defaults,
and Research prompt. Define all spec error codes and Thai safe-message mapping.
Do not change Standard/TTS defaults.

Use these exact default values and error inventory:

~~~python
RESEARCH_DEFAULTS = {
    "cloud_agent_research_default_provider": "openrouter",
    "cloud_agent_research_openrouter_model": "openai/gpt-5.6-sol-pro",
    "cloud_agent_research_openrouter_custom_model": "openai/gpt-5.6-sol-pro",
    "cloud_agent_research_aihubmix_model": "gpt-5.6-sol",
    "cloud_agent_research_aihubmix_custom_model": "gpt-5.6-sol",
    "cloud_agent_research_custom_system_prompt": "",
    "cloud_agent_research_openrouter_api_key": "",
    "cloud_agent_research_aihubmix_api_key": "",
}

RESEARCH_ERROR_CODES = {
    "URL_REQUIRED", "URL_INVALID", "URL_TARGET_NOT_PUBLIC",
    "URL_REDIRECT_REJECTED", "URL_FETCH_FAILED",
    "URL_CONTENT_UNSUPPORTED", "URL_CONTENT_TOO_LARGE", "PDF_INVALID",
    "PDF_TOO_LARGE", "PDF_TEXT_UNAVAILABLE", "SOURCE_EVIDENCE_EMPTY",
    "SOURCE_CONTEXT_TOO_LARGE", "PROVIDER_API_KEY_MISSING",
    "PROVIDER_AUTHENTICATION_FAILED", "PROVIDER_MODEL_UNSUPPORTED",
    "PROVIDER_TOOL_CALLING_UNSUPPORTED", "PROVIDER_TIMEOUT",
    "TOOL_CALL_LIMIT_EXCEEDED", "PROVIDER_ROUND_LIMIT_EXCEEDED",
    "RESEARCH_RESPONSE_INVALID",
}

RESEARCH_PUBLIC_MESSAGES = {
    "URL_REQUIRED": "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง",
    "URL_INVALID": "URL ไม่ถูกต้อง กรุณาตรวจสอบลิงก์และใส่ได้สูงสุด 3 แหล่ง",
    "URL_TARGET_NOT_PUBLIC": "URL นี้ไม่ใช่ปลายทางสาธารณะที่อนุญาต",
    "URL_REDIRECT_REJECTED": "URL เปลี่ยนเส้นทางไปยังปลายทางที่ไม่อนุญาต",
    "URL_FETCH_FAILED": "ไม่สามารถอ่านหน้าเว็บนี้ได้ กรุณาตรวจสอบว่าเปิดสาธารณะและลองใหม่",
    "URL_CONTENT_UNSUPPORTED": "แหล่งนี้ไม่ใช่หน้าเว็บหรือ PDF ที่ระบบรองรับ",
    "URL_CONTENT_TOO_LARGE": "หน้าเว็บนี้มีขนาดเกินขีดจำกัดความปลอดภัย",
    "PDF_INVALID": "ไฟล์ PDF ไม่ถูกต้องหรือเปิดอ่านไม่ได้",
    "PDF_TOO_LARGE": "ไฟล์ PDF มีขนาดหรือจำนวนหน้าเกินขีดจำกัดความปลอดภัย",
    "PDF_TEXT_UNAVAILABLE": "ไม่พบข้อความที่อ่านได้ใน PDF นี้",
    "SOURCE_EVIDENCE_EMPTY": "ไม่พบข้อมูลที่อ่านได้จากแหล่งที่ให้มา",
    "SOURCE_CONTEXT_TOO_LARGE": "ข้อมูลจากแหล่งอ้างอิงยาวเกินขีดจำกัดของโมเดลที่เลือก",
    "PROVIDER_API_KEY_MISSING": "ยังไม่ได้ตั้งค่า API key ของผู้ให้บริการที่เลือก",
    "PROVIDER_AUTHENTICATION_FAILED": "API key ของผู้ให้บริการไม่ถูกต้องหรือใช้งานไม่ได้",
    "PROVIDER_MODEL_UNSUPPORTED": "ไม่พบหรือไม่รองรับโมเดลที่เลือก",
    "PROVIDER_TOOL_CALLING_UNSUPPORTED": "โมเดลที่เลือกไม่รองรับ Tool Calling",
    "PROVIDER_TIMEOUT": "ผู้ให้บริการใช้เวลาตอบนานเกินกำหนด กรุณาลองใหม่ด้วยตนเอง",
    "TOOL_CALL_LIMIT_EXCEEDED": "โมเดลขออ่านแหล่งข้อมูลเกิน 3 ครั้ง งานจึงถูกหยุด",
    "PROVIDER_ROUND_LIMIT_EXCEEDED": "การสร้างสคริปต์เกิน 3 รอบ งานจึงถูกหยุด",
    "RESEARCH_RESPONSE_INVALID": "ผลลัพธ์ Research ไม่สมบูรณ์ จึงยังไม่เปลี่ยน Script Editor",
}
~~~

Keep `models.py` independent from `errors.py`; use a `TYPE_CHECKING` import for
the optional accounting annotation in `ResearchError` so the package has no
runtime circular import.

- [ ] **Step 4: Run focused verification**

Run: uv run pytest test/services/cloud_agent/test_research_contracts.py -q && uv run ruff check app/config/config.py app/models/cloud_agent.py app/services/cloud_agent/research test/services/cloud_agent/test_research_contracts.py

Expected: PASS; the request model preserves URL inputs for typed domain
validation and `research_draft_id` never enters the workflow payload.

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
    service.set_api_key("openrouter", "")
    assert service.get_provider("openrouter").api_key_configured is True
    assert "secret-value" not in service.get_provider("openrouter").model_dump_json()
    assert service.get_api_key_for_generation("openrouter").get_secret_value() == "secret-value"

def test_key_removal_requires_confirmation_and_then_clears_configured_state():
    service = ResearchSettingsService()
    service.set_api_key("aihubmix", "secret-value")
    with pytest.raises(ResearchError):
        service.remove_api_key("aihubmix", confirmed=False)
    assert service.remove_api_key("aihubmix", confirmed=True).api_key_configured is False

def test_store_persists_only_non_secret_provenance(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(make_successful_draft(script_hash=sha256_text("narration")))
    loaded = store.get(saved.research_draft_id)
    assert loaded.script_hash == sha256_text("narration")
    assert "source body" not in loaded.model_dump_json()

def test_association_rejects_changed_script(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(make_successful_draft())
    with pytest.raises(ResearchError, match="RESEARCH_RESPONSE_INVALID"):
        store.assert_script_matches(saved.research_draft_id, "edited narration")

def test_source_insert_failure_rolls_back_entire_successful_draft(tmp_path, monkeypatch):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    monkeypatch.setattr(store, "_insert_sources", raises_sqlite_error)
    with pytest.raises(sqlite3.Error):
        store.save_success(make_successful_draft())
    assert store.list_drafts() == []
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py -q

Expected: FAIL because Research settings and tables do not exist.

- [ ] **Step 3: Implement service and repository**

~~~python
class ResearchSettingsService:
    KEY_NAMES = {
        "openrouter": "cloud_agent_research_openrouter_api_key",
        "aihubmix": "cloud_agent_research_aihubmix_api_key",
    }

    def set_api_key(self, provider_id: str, value: str) -> ResearchProviderMetadata:
        key = self._require_provider(provider_id)
        if not value.strip():
            return self.get_provider(provider_id)
        with config.runtime_config_lock():
            config.app[key] = value.strip()
            config.save_config()
        return self.get_provider(provider_id)

    def remove_api_key(self, provider_id: str, confirmed: bool) -> ResearchProviderMetadata:
        key = self._require_provider(provider_id)
        if confirmed is not True:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "key removal not confirmed")
        with config.runtime_config_lock():
            config.app.pop(key, None)
            config.save_config()
        return self.get_provider(provider_id)

    def get_api_key_for_generation(self, provider_id: str) -> SecretStr:
        key_name = self._require_provider(provider_id)
        value = str(config.app.get(key_name, "") or "").strip()
        if not value:
            raise ResearchError("PROVIDER_API_KEY_MISSING", "provider key is not configured")
        return SecretStr(value)

class ResearchDraftStore:
    def assert_script_matches(self, research_draft_id: str, script: str) -> PersistedResearchDraft:
        draft = self.get(research_draft_id)
        if draft is None or not hmac.compare_digest(draft.script_hash, sha256_text(script)):
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "draft/script hash mismatch")
        return draft

    def link_job(self, research_draft_id: str, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO research_job_associations (research_draft_id, job_id, created_at) VALUES (?, ?, ?)",
                (research_draft_id, job_id, utc_now()),
            )
~~~

Create research_drafts, research_sources, and research_job_associations with
CREATE TABLE IF NOT EXISTS in the same configured SQLite file and the same
WAL/busy timeout as CloudJobStore. Store IDs, script/source hashes, URLs/titles,
provider/model, counts, sanitized usage/cost, timestamps, prompt fingerprints,
and evidence mode. Store the script hash, not the narration body;
`assert_script_matches` hashes the submitted Script Editor value and compares
that digest. Do not store source content, key, headers, cookies, signed URLs, or
raw provider data. A blank key save retains the old value; removal requires
confirmed is True.

`save_success` uses one `BEGIN IMMEDIATE` transaction for the draft and all
source rows, rolls back on any failure, and becomes visible only after complete
commit. `link_job` is idempotent for the same draft/job pair; one draft may be
associated with multiple jobs that start the same script hash.

Define `sha256_text(value)` as SHA-256 of `value.strip().encode("utf-8")` and
use that one helper at draft creation and Start validation, matching the existing
CloudJob script validator's trimmed value.

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
- Create: app/services/cloud_agent/research/network.py
- Create: app/services/cloud_agent/research/runtime.py
- Test: test/services/cloud_agent/test_research_network.py
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

def test_https_connection_is_pinned_to_validated_address(fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    client.get("https://public.example/article")
    assert fake_network.connect_calls == [
        {"ip": "93.184.216.34", "port": 443, "server_hostname": "public.example"}
    ]

def test_mixed_public_private_dns_answers_fail_closed(fake_network):
    fake_network.resolve("rebinding.example", ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(ResearchError) as captured:
        client.get("https://rebinding.example/")
    assert captured.value.code == "URL_TARGET_NOT_PUBLIC"

def test_decompressed_body_over_ten_mib_is_rejected(fake_network):
    fake_network.gzip_response(uncompressed_bytes=(10 * 1024 * 1024) + 1)
    with pytest.raises(ResearchError) as captured:
        client.get("https://public.example/large")
    assert captured.value.code == "URL_CONTENT_TOO_LARGE"

def test_html_strips_chrome_but_preserves_complete_readable_text(fake_http):
    source = runtime.execute("fetch_url", "https://public.example/article")
    assert source.content == "First fact.\n\nFinal fact."

def test_long_html_is_not_product_truncated(fake_http):
    fake_http.html("https://public.example/long", "A" * 50000)
    assert len(runtime.execute("fetch_url", "https://public.example/long").content) == 50000

def test_pdf_page_and_text_guards_are_typed(fake_pdf):
    with pytest.raises(ResearchError, match="PDF_TOO_LARGE"):
        runtime.execute("read_pdf", fake_pdf.url(pages=31, text="fact"))
    with pytest.raises(ResearchError, match="PDF_TEXT_UNAVAILABLE"):
        runtime.execute("read_pdf", fake_pdf.url(pages=3, text=""))

@pytest.mark.parametrize("html", [
    '<form><input type="password"></form>',
    '<div class="g-recaptcha"></div>',
    '<script>renderArticle()</script><div id="root"></div>',
])
def test_login_captcha_and_javascript_shells_are_rejected(fake_http, html):
    fake_http.html("https://public.example/blocked", html)
    with pytest.raises(ResearchError) as captured:
        runtime.execute("fetch_url", "https://public.example/blocked")
    assert captured.value.code == "URL_CONTENT_UNSUPPORTED"

def test_exact_duplicate_blocks_keep_all_source_ids():
    packet = runtime.aggregate([
        source("source-1", "Shared fact.\n\nUnique A."),
        source("source-2", "Shared fact.\n\nUnique B."),
    ])
    shared = next(block for block in packet.blocks if block.text == "Shared fact.")
    assert shared.source_ids == ("source-1", "source-2")
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q

Expected: FAIL because no runtime exists.

- [ ] **Step 3: Implement the public-only runtime**

~~~python
class ResearchToolRuntime:
    MAX_BODY_BYTES = 10 * 1024 * 1024
    MAX_PDF_PAGES = 30
    MAX_REDIRECTS = 5

    def preflight_urls(self, raw_urls: list[str]) -> tuple[str, ...]:
        return self.http_client.require_one_to_three_public_urls(raw_urls)

    def execute(self, tool_name: str, supplied_url: str) -> ResearchSource:
        if tool_name not in {"fetch_url", "read_pdf"}:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "unsupported tool")
        response = self._download_public_target(supplied_url)
        return self._extract_html(response) if tool_name == "fetch_url" else self._extract_pdf(response)

    def aggregate(self, sources: list[ResearchSource]) -> EvidencePacket:
        return build_exact_deduplicated_packet(sources)

class PinnedPublicHTTPClient:
    CONNECT_TIMEOUT_SECONDS = 5
    READ_TIMEOUT_SECONDS = 20
    TOTAL_TIMEOUT_SECONDS = 30
    MAX_HEADER_BYTES = 64 * 1024
    def _resolve_public_addresses(self, hostname: str, port: int) -> tuple[str, ...]:
        addresses = tuple(dict.fromkeys(
            answer[4][0] for answer in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        ))
        if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise ResearchError("URL_TARGET_NOT_PUBLIC", "DNS returned a prohibited address")
        return addresses

    def _connect_pinned(self, ip: str, port: int, *, server_hostname: str) -> socket.socket:
        raw_socket = socket.create_connection((ip, port), timeout=self.CONNECT_TIMEOUT_SECONDS)
        if port == 443:
            return self.ssl_context.wrap_socket(raw_socket, server_hostname=server_hostname)
        return raw_socket
~~~

Add beautifulsoup4==4.15.0 and pypdf==6.16.2, then run uv lock. In
`network.py`, implement the socket/TLS connection boundary explicitly: resolve
with `socket.getaddrinfo`, reject the entire answer set if any address is not
globally routable, connect the socket to one address from that checked tuple,
and for HTTPS wrap it with `ssl.create_default_context().wrap_socket` using the
original hostname as `server_hostname`. Send the original hostname in `Host`.
Do not validate and then let requests/urllib3 resolve the hostname a second time.
Canonicalize; accept HTTP(S) ports 80/443 only; reject credentials/signed query
parameters and URLs longer than 2,048 characters; disable auto-redirect and
revalidate every redirect, maximum five.
Allow identity, gzip, and deflate content encodings; reject other encodings.
Limit response headers to 64 KiB, use 5-second connect, 20-second read, and
30-second total deadlines, then stream decoded bytes and abort at 10 MiB. Close
sockets/responses in `finally` blocks.

`preflight_urls` enforces the raw one-to-three count, canonicalizes/deduplicates,
and performs the same public DNS/IP validation without downloading. `execute`
must resolve and validate again immediately before its address-pinned connection;
the earlier preflight is not reusable authorization and cannot create a DNS
rebinding window.

For HTML, remove scripts/styles/nav/footer/cookie banners/hidden content and normalize visible text. For PDFs, require MIME plus signature, reject encryption/malformed files, reject over 30 pages, extract text with PdfReader, and reject textless scans. Hash full normalized content. Exact duplicate URL/blocks collapse with retained source IDs. Do not truncate or summarize content; model-context rejection belongs to Task 5.

Reject 401/402/403 responses, password forms, recognized CAPTCHA/bot-challenge
markers, paywall-only bodies, and JavaScript shells that have no readable text
after script removal. Do not attempt a browser, login, consent, or challenge
bypass. Normalize extracted titles to at most 500 characters; this metadata cap
does not truncate evidence content.

- [ ] **Step 4: Run runtime and lock verification**

Run: uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q && uv lock --check && uv run ruff check app/services/cloud_agent/research/network.py app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add pyproject.toml uv.lock app/services/cloud_agent/research/network.py app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py
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

def test_final_message_requires_evidence_envelope():
    result = adapter.complete(provider_request(final_json={
        "script": "Narration",
        "source_ids_used": ["source-1"],
        "model_knowledge_used": True,
        "evidence_claims": [{
            "claim": "Verified fact", "source_id": "source-1",
            "evidence_quote": "exact words from source", "unstable": False,
        }],
    }))
    assert result.final_payload.script == "Narration"

def test_unknown_custom_model_fails_before_completion():
    with pytest.raises(ResearchError, match="PROVIDER_TOOL_CALLING_UNSUPPORTED"):
        adapter.resolve_capability("custom/unknown", SecretStr("key"))
    assert fake_client().calls == []

def test_generation_client_disables_sdk_retries(monkeypatch):
    adapter = OpenRouterToolCallingAdapter(client_factory=recording_openai_factory)
    adapter.complete(provider_request())
    assert recording_openai_factory.kwargs["max_retries"] == 0

def test_provider_exception_never_exposes_key_or_raw_payload(caplog):
    adapter = OpenRouterToolCallingAdapter(failing_client("key=secret raw-response"))
    with pytest.raises(ResearchError) as captured:
        adapter.complete(provider_request(api_key=SecretStr("secret")))
    assert "secret" not in str(captured.value)
    assert "raw-response" not in caplog.text
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

Use OpenAI(...).chat.completions.create with only the two owned tools. Translate
assistant tool-call IDs/name/JSON arguments and tool result messages. A final
assistant message must be JSON matching `ProviderFinalPayload`: narration,
successful source IDs used, whether model knowledge was used, and evidence
claims containing an exact supporting quote plus an unstable-fact flag. Parse
provider usage/cost into provider-neutral data. Missing/invalid JSON becomes
`RESEARCH_RESPONSE_INVALID`. Classify auth, timeout, unsupported model, and
malformed output without raw payloads.

Construct every generation and capability client with `max_retries=0` and an
explicit bounded timeout. Do not wrap `complete` or metadata lookup in another
automatic retry loop. A user clicking Generate again is a new attempt.
Keep credentials as `SecretStr` through settings/service/request objects and
call `get_secret_value()` only in the adapter expression that constructs the
OpenAI client. Do not include that local value in repr, diagnostics, or logs.

Catalog models use tested local capability metadata. On explicit Generate only, Custom Model ID capability lookup must prove tools and a usable context limit before completion. If not proved, return PROVIDER_TOOL_CALLING_UNSUPPORTED or PROVIDER_MODEL_UNSUPPORTED. Never run this lookup on load/refresh/save and never substitute model/provider.

Count only chat-completion generation requests as provider rounds. A model
metadata lookup is a preflight request, must be non-generative, and does not
consume the three generation rounds; its failure still stops the attempt before
generation.

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

def test_accounting_sums_provider_round_usage_and_cost():
    adapter.queue_round_usage([
        ({"prompt_tokens": 100, "completion_tokens": 20}, 0.01),
        ({"prompt_tokens": 200, "completion_tokens": 40}, 0.02),
    ])
    result = service.create_draft(request_with_one_url())
    assert result.accounting.usage == {"prompt_tokens": 300, "completion_tokens": 60}
    assert result.accounting.cost == pytest.approx(0.03)

def test_fourth_tool_is_rejected_without_partial_batch_execution():
    adapter.queue_tool_calls(["fetch_url", "fetch_url", "read_pdf", "fetch_url"])
    with pytest.raises(ResearchError, match="TOOL_CALL_LIMIT_EXCEEDED"):
        service.create_draft(request_with_three_urls())
    assert runtime.executed == []

def test_context_overflow_is_error_not_silent_truncation():
    adapter.set_context_limit(10)
    with pytest.raises(ResearchError, match="SOURCE_CONTEXT_TOO_LARGE"):
        service.create_draft(request_with_one_url())

def test_empty_urls_are_typed_before_provider_call():
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls([]))
    assert captured.value.code == "URL_REQUIRED"
    assert adapter.calls == []

def test_private_dns_target_is_rejected_before_provider_sees_url():
    runtime.reject_preflight("https://private-name.example", code="URL_TARGET_NOT_PUBLIC")
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls(["https://private-name.example"]))
    assert captured.value.code == "URL_TARGET_NOT_PUBLIC"
    assert adapter.calls == []

def test_more_than_three_supplied_urls_fail_before_provider_call():
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_urls(["https://a.example", "https://b.example", "https://c.example", "https://d.example"]))
    assert captured.value.code == "URL_INVALID"
    assert adapter.calls == []

def test_custom_choice_requires_non_blank_custom_model_before_provider_call():
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_model(model_choice="custom", custom_model_id=""))
    assert captured.value.code == "PROVIDER_MODEL_UNSUPPORTED"
    assert adapter.calls == []

def test_canonical_duplicate_urls_collapse_before_tool_call():
    request = request_with_urls([
        "https://EXAMPLE.com:443/article#top", "https://example.com/article",
    ])
    service.create_draft(request)
    assert runtime.executed_urls == ["https://example.com/article"]

def test_repeated_model_tool_call_reuses_source_but_consumes_budget():
    adapter.queue_tool_rounds(["fetch_url", "fetch_url"])
    result = service.create_draft(request_with_one_url())
    assert runtime.executed_urls == ["https://example.com/article"]
    assert result.accounting.tool_calls == 2

def test_evidence_claim_quote_must_exist_in_successful_source():
    adapter.queue_final(evidence_quote="invented words")
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())
    assert captured.value.code == "RESEARCH_RESPONSE_INVALID"

def test_model_only_final_is_rejected_even_after_source_read():
    adapter.queue_final(source_ids_used=[], evidence_claims=[])
    with pytest.raises(ResearchError) as captured:
        service.create_draft(request_with_one_url())
    assert captured.value.code == "SOURCE_EVIDENCE_EMPTY"
~~~

- [ ] **Step 2: Run tests to verify RED**

Run: uv run pytest test/services/cloud_agent/test_research_service.py -q

Expected: FAIL because the orchestration service does not exist.

- [ ] **Step 3: Implement the bounded service**

~~~python
class ResearchScriptService:
    MAX_TOOL_EXECUTIONS = 3
    MAX_PROVIDER_ROUNDS = 3
    CONTEXT_PROTOCOL_RESERVE_TOKENS = 2048
    OUTPUT_RESERVE_TOKENS = 2048

    def create_draft(self, request: ResearchDraftRequest) -> ResearchDraftResponse:
        canonical_urls = self.runtime.preflight_urls(request.source_urls)
        settings = self.settings.require_generation_settings(request)
        capability = self.adapters[request.provider].resolve_capability(settings.model_id, settings.api_key)
        messages = self._initial_messages(request, canonical_urls, capability)
        accounting = ResearchAccounting()
        sources: list[ResearchSource] = []
        for round_number in range(1, self.MAX_PROVIDER_ROUNDS + 1):
            result = self.adapters[request.provider].complete(self._provider_request(messages, settings, capability))
            accounting = accounting.with_provider_round(result.usage, result.cost)
            if result.tool_calls:
                self._require_synthesis_round(round_number)
                sources, messages, accounting = self._execute_tool_batch(
                    result.tool_calls, canonical_urls, sources, messages, accounting
                )
                continue
            if result.final_payload is None:
                raise ResearchError("RESEARCH_RESPONSE_INVALID", "missing final payload", accounting=accounting)
            return self._persist_valid_final(result.final_payload, sources, request, accounting)
        raise ResearchError("PROVIDER_ROUND_LIMIT_EXCEEDED", "provider round limit reached", accounting=accounting)
~~~

Require one to three submitted list entries before canonicalization. Then
canonicalize and collapse canonical duplicates for the tool allowlist. Raise
`URL_REQUIRED` for none and `URL_INVALID` for blank, malformed, over-three, or
unsupported entries before capability/provider calls.
Resolve `model_choice == "custom"` only from the trimmed `custom_model_id`;
otherwise use `model_choice` itself. A blank Custom Model ID fails before
capability/provider calls with `PROVIDER_MODEL_UNSUPPORTED`.
Allow tool URLs only if their canonical form is in that supplied allowlist.
Require a final synthesis round after any tool round. Reject batches exceeding
remaining tool budget before executing any call. Send a sanitized failed-tool
result for a source read failure and continue the bounded conversation if at
least one other source succeeds; accept no final result unless at least one
source succeeded. Build immutable security/evidence/model-knowledge instructions
before editable prompt and wrap source contents as untrusted data.

Cache a successful tool result by `(tool_name, canonical_url)` within this one
attempt only. A repeated model request returns that verified cached result but
still increments `tool_calls`; it cannot bypass the limit of three. Do not share
source bodies across attempts or persist this request-local cache.

After each tool batch, call `runtime.aggregate` over every successful source and
format the tool-result messages from its `EvidencePacket`. Emit each exact
normalized block once, carrying all contributing source IDs; later duplicate
blocks contain only a cross-reference to the already emitted block. Preserve all
unique differing text and all successful source metadata.

Serialize the full message/tool-schema packet deterministically and use its UTF-8
byte count as a conservative token upper bound, plus 2,048 tokens for hidden
chat-protocol overhead and 2,048 tokens reserved for the final response, against
the adapter-proved context limit. Raise
SOURCE_CONTEXT_TOO_LARGE if it cannot fit. Never chunk-select, summarize, or
truncate.

Validate the final envelope at request time: every `source_ids_used` and
`EvidenceClaim.source_id` must refer to a successful source; each normalized
`evidence_quote` must occur verbatim in that source's normalized content; and
at least one successful source must be listed in `source_ids_used`. A result that
uses model knowledge but no source evidence fails with `SOURCE_EVIDENCE_EMPTY`.
Every claim marked unstable must have a verified source/quote. The spoken script
does not include those internal references unless the per-draft citation toggle
is enabled. `request.allow_citations` is the sole citation authority; never infer
permission from `custom_system_prompt`. Then create the existing six-clip
plan/master prompt, save provenance last, and return common draft result. On any
error, persist no successful draft and create no job.

Failure atomicity is mandatory: an exception may return only the typed code,
Thai public message, and sanitized accounting. It may not persist a successful
Research draft/source/link, invoke Standard Script, build/queue a CloudJob, or
change the Script Editor/master prompt/clip plan/draft snapshot.

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
    client = research_client(tmp_path)
    response = client.post("/api/v1/cloud-agent/research/drafts", json={**research_payload(), "source_urls": []})
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "URL_REQUIRED"
    assert response.json()["message"] == "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง"
    assert "secret" not in response.text
    assert client.app.state.job_store.list_jobs() == []

def test_start_requires_research_script_hash_match(tmp_path):
    payload = {**valid_job_payload(), "research_draft_id": "draft-1", "script": "different"}
    assert research_client(tmp_path).post("/api/v1/cloud-agent/jobs", json=payload).status_code == 422

def test_link_failure_never_queues_job(tmp_path):
    client, store = research_client_with_failing_link(tmp_path)
    response = client.post("/api/v1/cloud-agent/jobs", json=matching_research_job_payload())
    assert response.status_code == 422
    assert store.list_jobs()[0].status is CloudJobStatus.FAILED
    assert store.list_jobs()[0].error_code == "RESEARCH_DRAFT_ASSOCIATION_FAILED"

def test_standard_draft_does_not_resolve_or_call_research_service(tmp_path):
    client = research_client(tmp_path, research_service=lambda: pytest.fail("standard path imported Research behavior"))
    response = client.post("/api/v1/cloud-agent/draft", json=standard_draft_payload())
    assert response.status_code == 200

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/cloud-agent/research/providers"),
    ("GET", "/api/v1/cloud-agent/research/settings"),
    ("PUT", "/api/v1/cloud-agent/research/settings"),
])
def test_load_refresh_and_save_never_call_provider(method, path, tmp_path):
    client = research_client(tmp_path, adapter=forbidden_adapter())
    response = client.request(method, path, json=valid_settings_payload() if method == "PUT" else None)
    assert response.status_code == 200
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

def _research_http_exception(exc: ResearchError) -> HttpException:
    return HttpException(
        task_id="cloud-agent-research",
        status_code=_research_http_status(exc.code),
        message=public_research_message(exc.code),
        data={"code": exc.code, "accounting": safe_accounting(exc.accounting)},
    )

RESEARCH_HTTP_STATUS = {
    "PROVIDER_API_KEY_MISSING": 422,
    "PROVIDER_AUTHENTICATION_FAILED": 401,
    "PROVIDER_TIMEOUT": 504,
    "URL_FETCH_FAILED": 502,
}

def _research_http_status(code: str) -> int:
    return RESEARCH_HTTP_STATUS.get(code, 422)

def safe_accounting(value: ResearchAccounting | None) -> dict:
    value = value or ResearchAccounting()
    return {
        "tool_calls": value.tool_calls,
        "provider_rounds": value.provider_rounds,
        "usage": value.usage,
        "cost": value.cost,
    }
~~~

Implement these exact routes on the existing router:

- `GET /cloud-agent/research/providers`
- `GET /cloud-agent/research/settings`
- `PUT /cloud-agent/research/settings`
- `PUT /cloud-agent/research/providers/{provider_id}/api-key`
- `DELETE /cloud-agent/research/providers/{provider_id}/api-key`
- `POST /cloud-agent/research/drafts`
- `GET /cloud-agent/research/drafts/{research_draft_id}`

The DELETE body is `{"confirmed": true}`. Put the stable error code and
sanitized accounting in the existing response `data` object and the Thai public
message in `message`; do not change application-wide `utils.get_response` or
the `HttpException` handler. Never expose `diagnostic_message`. GET/settings/key
paths do not invoke adapters/providers.

Before normal job creation, non-empty excluded research_draft_id calls
assert_script_matches. After normal job creation, link the IDs through
ResearchDraftStore. If linking fails, patch the still-DRAFT job to FAILED with
`RESEARCH_DRAFT_ASSOCIATION_FAILED` and do not queue it. Do not add Research
fields to CloudJob storage or Worker behavior.

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
    for label in (
        "Standard Script",
        "Research Script",
        "Source URLs",
        "อนุญาตให้ใส่อ้างอิงในสคริปต์",
        "Generate Research Script",
        "Sources",
    ):
        assert label in source
    assert "sqlite3" not in source.lower()
    assert "PersistentBrowserManager" not in source

def test_research_failure_never_stores_draft(monkeypatch):
    monkeypatch.setattr(cloud_agent, "_prepare_research_draft", raises_url_required)
    monkeypatch.setattr(cloud_agent, "_store_draft", lambda _draft: pytest.fail("must preserve editor"))
    render_research_generate_click(monkeypatch)

def test_research_error_reads_code_and_accounting_from_existing_api_shape():
    response = fake_response(422, {
        "status": 422,
        "message": "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง",
        "data": {"code": "URL_REQUIRED", "accounting": {"provider_rounds": 0}},
    })
    assert cloud_agent._research_error_data(response)["code"] == "URL_REQUIRED"

def test_research_settings_save_requires_exact_server_readback(monkeypatch):
    monkeypatch.setattr(cloud_agent, "_api", mismatching_research_settings_api())
    assert cloud_agent._save_and_verify_research_settings(valid_research_settings()) == (
        False, "Could not verify saved research settings. Reload the page and try again."
    )

def test_blank_research_key_is_not_sent_as_replacement():
    assert cloud_agent._research_key_payload("") is None
    assert cloud_agent._research_key_payload("new-key") == {"api_key": "new-key"}

def test_edit_then_refresh_clears_research_association_but_keeps_shared_workflow(monkeypatch):
    monkeypatch.setattr(cloud_agent.st, "session_state", {
        "cloud_agent_script": "Edited narration",
        "cloud_agent_draft_script": "Original research narration",
        "cloud_agent_research_draft_id": "draft-1",
    })
    cloud_agent._store_refreshed_draft(refreshed_draft(script="Edited narration"))
    assert "cloud_agent_research_draft_id" not in cloud_agent.st.session_state
    assert cloud_agent.st.session_state["cloud_agent_script"] == "Edited narration"

def test_unchanged_research_script_sends_optional_draft_id(monkeypatch):
    recorded = recording_api(monkeypatch)
    cloud_agent._start_job(**valid_start_fields(), research_draft_id="draft-1")
    assert recorded.json["research_draft_id"] == "draft-1"

def test_unchanged_refresh_retains_research_association(monkeypatch):
    monkeypatch.setattr(cloud_agent.st, "session_state", {
        "cloud_agent_draft_script": "Research narration",
        "cloud_agent_research_draft_id": "draft-1",
    })
    cloud_agent._store_refreshed_draft(refreshed_draft(script="Research narration"))
    assert cloud_agent.st.session_state["cloud_agent_research_draft_id"] == "draft-1"

def test_standard_generation_clears_stale_research_provenance(monkeypatch):
    st.session_state["cloud_agent_research_draft_id"] = "draft-1"
    cloud_agent._store_draft(standard_draft())
    assert "cloud_agent_research_draft_id" not in st.session_state
~~~

- [ ] **Step 2: Run WebUI tests to verify RED**

Run: uv run pytest test/services/test_cloud_agent_webui.py -q

Expected: FAIL because current UI has only Standard Script controls.

- [ ] **Step 3: Implement mode-specific controls before shared editor**

~~~python
def _prepare_research_draft(**payload):
    return _api("POST", "research/drafts", json=payload, timeout=DRAFT_TIMEOUT_SECONDS)

def _research_error_data(response: requests.Response) -> dict:
    payload = response.json()
    return {"message": payload.get("message", "Research request failed."), **payload.get("data", {})}

def _research_key_payload(value: str) -> dict | None:
    return {"api_key": value.strip()} if value.strip() else None

def _research_job_fields(research_draft_id: str) -> dict:
    return {"research_draft_id": research_draft_id} if research_draft_id else {}

def _store_research_result(draft: dict) -> None:
    _store_draft(draft)
    st.session_state["cloud_agent_research_draft_id"] = draft["research_draft_id"]
    st.session_state["cloud_agent_research_sources"] = draft["sources"]
    st.session_state["cloud_agent_research_accounting"] = draft["accounting"]

def _store_refreshed_draft(draft: dict) -> None:
    prior_script = str(st.session_state.get("cloud_agent_draft_script", "")).strip()
    retained = {
        key: st.session_state[key]
        for key in ("cloud_agent_research_draft_id", "cloud_agent_research_sources", "cloud_agent_research_accounting")
        if key in st.session_state
    }
    _store_draft(draft)
    if retained and draft["script"].strip() == prior_script:
        st.session_state.update(retained)

def _save_and_verify_research_settings(payload: dict) -> tuple[bool, str]:
    saved = _api("PUT", "research/settings", json=payload)
    readback = _api("GET", "research/settings")
    if all(saved.get(name) == value and readback.get(name) == value for name, value in payload.items()):
        return True, "Saved and verified."
    return False, "Could not verify saved research settings. Reload the page and try again."

mode = st.radio("Script Creation Mode", ["Standard Script", "Research Script"], key="cloud_agent_script_mode")
if mode == "Research Script":
    st.caption("Research generation may call the selected provider up to 3 rounds.")
else:
    standard_generate_clicked = st.button("Generate Script", key="cloud_agent_generate_script")

# Keep the existing Refresh Draft action shared below both mode-specific
# Generate actions. It receives Script Editor text and does not generate a
# Standard script when that text is non-empty.

try:
    draft = _prepare_research_draft(**research_payload)
except requests.HTTPError as exc:
    error = _research_error_data(exc.response)
    st.error(error["message"])
    _render_research_accounting(error.get("accounting", {}))
else:
    _store_research_result(draft)
    _render_research_accounting(draft.get("accounting", {}))
~~~

Render `st.checkbox("อนุญาตให้ใส่อ้างอิงในสคริปต์", value=False,
key="cloud_agent_research_allow_citations")` only in Research mode and include
its boolean as `allow_citations` in the Research draft request JSON. Do not add
the field or control to Standard mode.

Keep existing Standard keys/controls and behavior unchanged. Render Research
provider/model/custom model/key/prompt/URL controls; save only through FastAPI
and verify non-secret readback. On success pass common response to `_store_draft`
and retain only `research_draft_id` in session state so `_start_job` sends it.
Keep the existing Refresh Draft action after the shared Script Editor for both
modes. If the refreshed script differs from the persisted Research script
snapshot, remove `cloud_agent_research_draft_id`; the edited script can then use
the normal downstream workflow without claiming stale Research provenance. On
failure show Thai API error and never call `_store_draft`.

At the start of the existing `_store_draft`, clear stale Research draft ID,
Sources, and accounting keys. `_store_research_result` calls `_store_draft`
first and then sets the new Research metadata, so switching back to Standard can
never attach old provenance to a new script.

Extend `_start_job` with `research_draft_id: str = ""` and merge
`_research_job_fields(research_draft_id)` into its existing JSON payload. All
Standard calls omit the field and retain the exact current request body.

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
  test/services/cloud_agent/test_research_network.py \
  test/services/cloud_agent/test_research_runtime.py \
  test/services/cloud_agent/test_research_adapters.py \
  test/services/cloud_agent/test_research_service.py \
  test/services/cloud_agent/test_research_controller.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_webui.py -q
uv run pytest \
  test/services/cloud_agent \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_llm.py \
  test/services/test_six_clip_plan.py -q
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

### Task 9: Push, Verify CI, and Run a Non-Paid Deployed Smoke

**Files:**

- Modify: docs/cloud-agent-research-script-verification.md

**Consumes:** Task 8 green local matrix and the existing Draft PR/branch workflow.

**Produces:** Remote CI evidence plus a deployed API/WebUI smoke that makes no
provider or production-media request.

- [ ] **Step 1: Verify the push scope and preserve user files**

Run: `git status --short && git log --oneline origin/feature/cloud-video-agent..HEAD`

Expected: only the protected untracked `config.toml.backup-*` and
`config.toml.save*` files remain; all intended code/docs/tests are committed.
Never add, remove, print, or modify those files.

- [ ] **Step 2: Push the feature branch and wait for Draft PR CI**

~~~bash
git push origin feature/cloud-video-agent
gh pr checks 4 --watch --interval 20
~~~

Expected: Python 3.11, Python 3.13, Windows smoke, lock, Ruff, compile, tests,
and coverage gates pass. If CI fails, use systematic debugging, add a new RED
regression for the proven cause, fix minimally, rerun focused/full verification,
commit, push, and wait for CI again.

- [ ] **Step 3: Deploy only the tested API/WebUI code and dependencies**

~~~bash
uv sync --frozen
sudo systemctl restart videosturbo-api videosturbo-webui
systemctl is-active videosturbo-api videosturbo-webui videosturbo-worker
~~~

Expected: all three services report `active`. Do not restart or interact with
Xvfb/Openbox/x11vnc/noVNC, Google Flow, Canva, or the Worker unless a proven
deployment issue specifically requires the normal reversible service action.

- [ ] **Step 4: Run safe local HTTP and loopback checks**

~~~bash
curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/health | jq '{status, enabled: .data.enabled, worker_online: .data.worker_online, storage_writable: .data.storage_writable}'
curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/research/providers | jq '{status, providers: [.data[] | {id, api_key_configured}]}'
curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/research/settings | jq '{status, provider: .data.provider}'
curl -fsSI http://127.0.0.1:8501/ | head -n 1
ss -ltn | awk '$4 ~ /:(5900|6080)$/ {print $4}'
~~~

Expected: health/settings/provider catalog and WebUI respond successfully;
provider output contains configured booleans only; ports 5900/6080, if present,
are bound only to 127.0.0.1. Do not call POST research/drafts, expose prompt/key
values, create a CloudJob, check browser sessions, or invoke any paid/media path.

- [ ] **Step 5: Record evidence, commit, push, and verify final CI**

Append only sanitized CI run identifiers/status and the safe smoke results to
the verification document, then run:

~~~bash
git add docs/cloud-agent-research-script-verification.md
git commit -m "docs: record research script deployment smoke"
git push origin feature/cloud-video-agent
gh pr checks 4 --watch --interval 20
~~~

Expected: final CI passes and the Draft PR remains Draft. Do not perform a live
OpenRouter/AIHubMix generation without a new explicit operator approval.

## Implementation References

- OpenRouter tool calling: https://openrouter.ai/docs/guides/features/tool-calling
- OpenRouter model capability metadata: https://openrouter.ai/docs/guides/overview/models
- Beautiful Soup package/release: https://pypi.org/project/beautifulsoup4/4.15.0/
- pypdf package/release: https://pypi.org/project/pypdf/6.16.2/
- Existing AIHubMix OpenAI-compatible behavior: app/services/llm.py and
  test/services/test_llm.py

## Post-Plan Gate

Do not execute this plan until the operator explicitly approves it. After automated verification, request separate approval before any live provider smoke. An approved smoke is one bounded Research attempt only; it must not start TTS, Google Flow, Canva, or a CloudJob unless separately authorized.
