# Cloud Agent Research Script Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an isolated OpenRouter-powered Research Script mode that produces source-verified drafts in the existing Cloud Agent Script Editor while leaving Standard Script and the TTS → Google Flow → Canva workflow unchanged.

**Architecture:** Keep `POST /cloud-agent/draft` and its legacy generator untouched. Add a separate Research controller, settings service, OpenRouter adapter, citation validator, orchestration service, and durable research-draft store. The Streamlit page selects one of the two draft engines, but both write the same existing draft state before entering the existing production workflow.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, Requests, SQLite, Streamlit, Pytest, Ruff, OpenRouter Chat Completions with the `openrouter:web_search` server tool.

**Spec:** `docs/superpowers/specs/2026-08-27-cloud-agent-research-script-design.md`

## Global Constraints

- Follow strict RED → GREEN: no production behavior before a focused test fails for the intended missing behavior.
- Do not change the behavior or implementation of `POST /cloud-agent/draft` or `generate_script()`.
- Do not make VideosTurbo fetch, crawl, or scrape source pages. Only OpenRouter performs web search.
- Send one OpenRouter generation request per click. Configure `max_results=5`, `max_total_results=5`, and `max_tool_calls=5`; never retry a paid request automatically.
- Never fall back from Research Script to Standard Script.
- Never log or return API keys, authorization headers, raw provider payloads, signed URLs, or provider request identifiers.
- Use the existing `config.app`, `config.runtime_config_lock()`, and `config.save_config()`; do not create another config loader.
- No OpenRouter request may occur on page load, refresh, settings save, prompt restore, or job start.
- Preserve all TTS, Flow, Canva, worker, legacy-rendering, and stock-media behavior.
- Unit and integration tests must use fakes; a paid live request requires separate authorization.
- If an unexpected test failure occurs, use `superpowers:systematic-debugging` before modifying production code.
- Before claiming completion, use `superpowers:verification-before-completion` and run the full gates in Task 10.

---

## Task 1: Define Research contracts and persisted settings

**Files:**

- Create: `app/models/cloud_agent_research.py`
- Create: `app/services/cloud_agent/research_prompts.py`
- Create: `app/services/cloud_agent/research_settings.py`
- Modify: `app/config/config.py`
- Test: `test/services/cloud_agent/test_research_settings.py`
- Test: `test/services/cloud_agent/test_models.py`

- [ ] **Step 1: Write RED tests for models, defaults, secret retention, and prompt reset**

Add tests proving:

- `ScriptCreationMode` accepts only `standard` and `research`.
- Research settings default to Standard mode and `openai/gpt-5.6-sol-pro`.
- `api_key_configured` is returned, but the key value is never serialized.
- A blank API-key patch retains the saved key.
- Explicit `clear_api_key=True` removes the key.
- Research Rules and Research Writing Prompt save independently.
- Each prompt can be restored independently to the exact approved default text.
- A custom model is effective only when `model_choice == "custom"` and the custom ID is nonblank.

Use a patched in-memory `config.app` and spies for `config.save_config()` and `config.runtime_config_lock()`.

- [ ] **Step 2: Run RED and preserve the intended failure**

Run:

```bash
uv run pytest \
  test/services/cloud_agent/test_research_settings.py \
  test/services/cloud_agent/test_models.py \
  -k 'research' -v
```

Expected RED: imports for `cloud_agent_research` and `research_settings` fail because the Research contracts do not exist yet. Syntax or fixture failures do not count.

- [ ] **Step 3: Add the minimum Pydantic contracts**

Define these public types in `app/models/cloud_agent_research.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ScriptCreationMode = Literal["standard", "research"]
ResearchModelChoice = Literal[
    "openai/gpt-5.6-sol-pro",
    "openai/gpt-5.6-sol",
    "custom",
]


class ResearchSettingsView(BaseModel):
    default_script_mode: ScriptCreationMode = "standard"
    model_choice: ResearchModelChoice = "openai/gpt-5.6-sol-pro"
    custom_model_id: str = "openai/gpt-5.6-sol-pro"
    research_rules_prompt: str
    research_writing_prompt: str
    api_key_configured: bool = False


class ResearchSettingsPatch(BaseModel):
    default_script_mode: ScriptCreationMode
    model_choice: ResearchModelChoice
    custom_model_id: str = Field(max_length=300)
    research_rules_prompt: str = Field(min_length=1, max_length=20000)
    research_writing_prompt: str = Field(min_length=1, max_length=20000)
    api_key: str = Field(default="", exclude=True, max_length=1000)
    clear_api_key: bool = False


class ResearchPromptResetRequest(BaseModel):
    prompt_name: Literal["research_rules_prompt", "research_writing_prompt"]
```

Also define `ResearchDraftRequest`, `ResearchSource`, `ResearchMetadata`, and `ResearchDraftResponse` here for later tasks. `ResearchDraftResponse` must expose exactly the spec fields and must not contain provider internals. Put the two exact approved default prompt strings in the dependency-free `research_prompts.py` module.

- [ ] **Step 4: Add config defaults and the settings service**

Add these keys to `CLOUD_AGENT_DEFAULTS` in `app/config/config.py`:

```python
"cloud_agent_default_script_mode": "standard",
"cloud_agent_research_model_choice": "openai/gpt-5.6-sol-pro",
"cloud_agent_research_custom_model_id": "openai/gpt-5.6-sol-pro",
"cloud_agent_research_rules_prompt": DEFAULT_RESEARCH_RULES_PROMPT,
"cloud_agent_research_writing_prompt": DEFAULT_RESEARCH_WRITING_PROMPT,
"cloud_agent_openrouter_api_key": "",
```

To avoid a config-to-service import cycle, import the two approved prompt constants from the dependency-free `app/services/cloud_agent/research_prompts.py` module into both config and the settings service.

Implement `ResearchSettingsService` with:

```python
def get(self) -> ResearchSettingsView: ...
def update(self, patch: ResearchSettingsPatch) -> ResearchSettingsView: ...
def reset_prompt(self, prompt_name: str) -> ResearchSettingsView: ...
def require_api_key(self) -> str: ...
def effective_model(self, settings: ResearchSettingsView) -> str: ...
```

All writes occur under `config.runtime_config_lock()`, call `config.save_config()`, and return `self.get()` for readback. `get()` returns only `api_key_configured`; only `require_api_key()` may read the secret.

- [ ] **Step 5: Run GREEN**

Run the focused command from Step 2. Expected: all Research model/settings tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/models/cloud_agent_research.py app/services/cloud_agent/research_prompts.py app/services/cloud_agent/research_settings.py app/config/config.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_models.py
git commit -m "feat: add research script settings contracts"
```

---

## Task 2: Implement the isolated OpenRouter server-tool adapter

**Files:**

- Create: `app/services/cloud_agent/research_errors.py`
- Create: `app/services/cloud_agent/openrouter_research.py`
- Test: `test/services/cloud_agent/test_openrouter_research.py`

- [ ] **Step 1: Write RED transport tests**

With a fake `requests.Session`, assert one call to `https://openrouter.ai/api/v1/chat/completions` with:

```python
{
    "model": effective_model,
    "messages": expected_messages,
    "tools": [{
        "type": "openrouter:web_search",
        "parameters": {
            "max_results": 5,
            "max_total_results": 5,
        },
    }],
    "max_tool_calls": 5,
}
```

Also assert:

- the bearer secret is sent only in the header and absent from logs/exceptions;
- `choices[0].message.content` and `choices[0].message.annotations` are parsed;
- `usage.server_tool_use.web_search_requests` is recorded as a non-secret count;
- `401/403` maps to `OPENROUTER_AUTHENTICATION_FAILED`;
- unsupported model/tool `400` maps to `RESEARCH_MODEL_UNSUPPORTED`;
- timeout maps to `RESEARCH_PROVIDER_TIMEOUT`;
- missing/invalid content or annotations maps to `RESEARCH_RESPONSE_INVALID`;
- no retry occurs for any failure.

- [ ] **Step 2: Run RED**

```bash
uv run pytest test/services/cloud_agent/test_openrouter_research.py -v
```

Expected RED: missing adapter/error modules, not malformed fake responses.

- [ ] **Step 3: Implement typed errors and one-request adapter**

Define:

```python
class ResearchScriptError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422): ...
```

Implement:

```python
class OpenRouterResearchClient:
    def __init__(self, *, session=None, timeout_seconds: float = 300.0): ...

    def generate(
        self,
        *,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
    ) -> OpenRouterResearchResult: ...
```

The adapter may sanitize known error status/text for classification, but user-facing errors must be fixed safe messages. Do not include the response body in raised exceptions. Keep all beta API details inside this adapter so future OpenRouter schema changes do not leak into workflow/UI code.

- [ ] **Step 4: Run GREEN and Ruff**

```bash
uv run pytest test/services/cloud_agent/test_openrouter_research.py -v
uv run ruff check \
  app/services/cloud_agent/research_errors.py \
  app/services/cloud_agent/openrouter_research.py \
  test/services/cloud_agent/test_openrouter_research.py
```

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/research_errors.py app/services/cloud_agent/openrouter_research.py test/services/cloud_agent/test_openrouter_research.py
git commit -m "feat: add openrouter research adapter"
```

---

## Task 3: Validate URLs and provider-backed citations

**Files:**

- Create: `app/services/cloud_agent/research_sources.py`
- Test: `test/services/cloud_agent/test_research_sources.py`

- [ ] **Step 1: Write RED normalization and validation tests**

Cover:

- extraction of only public `http`/`https` URLs from the subject;
- rejection of credentials in URLs, localhost, loopback, link-local, and private/reserved IP targets;
- lowercase host, removal of fragments and tracking parameters (`utm_*`, `fbclid`, `gclid`), default-port removal, and consistent trailing-slash handling;
- Unicode domain normalization;
- exact canonical host/path matching for user-supplied primary URLs;
- duplicate citations after normalization;
- at least two distinct accepted citations;
- missing title/URL;
- structured source entries not backed by an OpenRouter `url_citation` annotation;
- unresolved `cited_claims` source references;
- all supplied primary URLs, not only the first, must be cited.

- [ ] **Step 2: Run RED**

```bash
uv run pytest test/services/cloud_agent/test_research_sources.py -v
```

Expected RED: missing source validator.

- [ ] **Step 3: Implement pure source functions**

Expose:

```python
def extract_subject_urls(subject: str) -> list[str]: ...
def normalize_source_url(url: str) -> str: ...
def citation_sources(annotations: list[dict]) -> dict[str, ProviderCitation]: ...
def validate_research_sources(
    *,
    subject: str,
    annotations: list[dict],
    declared_sources: list[ResearchSource],
) -> list[ResearchSource]: ...
```

Use `urllib.parse` and `ipaddress`; do not issue network requests. Accepted returned source URLs must come from `url_citation` annotations. Raise `RESEARCH_PRIMARY_URL_NOT_VERIFIED`, `RESEARCH_SOURCES_INSUFFICIENT`, or `RESEARCH_RESPONSE_INVALID` as appropriate.

- [ ] **Step 4: Run GREEN and Ruff**

```bash
uv run pytest test/services/cloud_agent/test_research_sources.py -v
uv run ruff check app/services/cloud_agent/research_sources.py test/services/cloud_agent/test_research_sources.py
```

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/research_sources.py test/services/cloud_agent/test_research_sources.py
git commit -m "feat: verify research citations"
```

---

## Task 4: Persist Research draft provenance separately

**Files:**

- Create: `app/services/cloud_agent/research_store.py`
- Test: `test/services/cloud_agent/test_research_store.py`

- [ ] **Step 1: Write RED persistence and migration tests**

Use a temporary SQLite path and assert initialization creates:

```sql
CREATE TABLE cloud_agent_research_drafts (
    id TEXT PRIMARY KEY,
    script_sha256 TEXT NOT NULL,
    model_id TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    research_rules_sha256 TEXT NOT NULL,
    research_writing_sha256 TEXT NOT NULL
);

CREATE TABLE cloud_agent_job_research (
    job_id TEXT PRIMARY KEY,
    research_draft_id TEXT NOT NULL,
    associated_at TEXT NOT NULL,
    FOREIGN KEY (research_draft_id)
        REFERENCES cloud_agent_research_drafts(id)
);
```

Test atomic save/read, source JSON round-trip, prompt fingerprints rather than prompt bodies, existing-database compatible initialization, idempotent association, rejection of an unknown draft ID, and rejection when the current script hash differs from the saved Research draft.

- [ ] **Step 2: Run RED**

```bash
uv run pytest test/services/cloud_agent/test_research_store.py -v
```

Expected RED: missing store.

- [ ] **Step 3: Implement the isolated SQLite store**

Expose:

```python
class ResearchDraftStore:
    def __init__(self, db_path: str): ...
    def save_draft(...) -> StoredResearchDraft: ...
    def get_draft(self, research_draft_id: str) -> StoredResearchDraft | None: ...
    def associate_job(
        self,
        *,
        job_id: str,
        research_draft_id: str,
        script: str,
    ) -> None: ...
    def get_job_research_draft_id(self, job_id: str) -> str: ...
```

Follow the connection/transaction conventions in `job_store.py`, but do not alter `cloud_agent_jobs`. Store source metadata as stable JSON and SHA-256 hashes as lowercase hex.

- [ ] **Step 4: Run GREEN and existing store regression**

```bash
uv run pytest \
  test/services/cloud_agent/test_research_store.py \
  test/services/cloud_agent/test_job_store.py \
  -v
uv run ruff check app/services/cloud_agent/research_store.py test/services/cloud_agent/test_research_store.py
```

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/research_store.py test/services/cloud_agent/test_research_store.py
git commit -m "feat: persist research draft provenance"
```

---

## Task 5: Orchestrate one source-grounded Research draft

**Files:**

- Create: `app/services/cloud_agent/research_script.py`
- Test: `test/services/cloud_agent/test_research_script.py`

- [ ] **Step 1: Write RED service tests with a fake OpenRouter client**

Test that the service:

- requires a configured key before calling the client;
- resolves curated/custom model deterministically;
- combines Research Rules, Research Writing Prompt, subject, language, target words, and supplied URLs into the request messages;
- calls the OpenRouter client exactly once;
- parses a strict JSON payload containing narration and declared source references;
- validates real provider annotations through `research_sources.py`;
- calls `generate_six_clip_plan()` and `build_master_prompt()` only after source validation passes;
- persists `research_draft_id`, script hash, model, accepted sources, timestamp, and prompt fingerprints;
- returns the shared `script`, `master_prompt`, and `clip_plan` plus Research metadata;
- never imports or calls legacy `generate_script()`;
- does not persist, update Script Editor state, or fall back after a Research failure.

- [ ] **Step 2: Run RED**

```bash
uv run pytest test/services/cloud_agent/test_research_script.py -v
```

Expected RED: missing orchestration service.

- [ ] **Step 3: Implement the minimum service**

Use dependency injection:

```python
class ResearchScriptService:
    def __init__(
        self,
        *,
        settings: ResearchSettingsService,
        client: OpenRouterResearchClient,
        store: ResearchDraftStore,
        clip_plan_builder=generate_six_clip_plan,
        master_prompt_builder=build_master_prompt,
    ): ...

    def create_draft(self, body: ResearchDraftRequest) -> ResearchDraftResponse: ...
```

The model response JSON contract is:

```json
{
  "script": "narration only",
  "sources": [
    {
      "url": "https://source.example/article",
      "title": "Source title",
      "publisher": "Publisher",
      "cited_claims": ["claim supported by this source"]
    }
  ]
}
```

Reject markdown fences, blank narration, malformed JSON, or narration that exceeds the existing request model limits. Keep accepted sources separate from spoken narration.

- [ ] **Step 4: Run GREEN and Ruff**

```bash
uv run pytest test/services/cloud_agent/test_research_script.py -v
uv run ruff check app/services/cloud_agent/research_script.py test/services/cloud_agent/test_research_script.py
```

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/research_script.py test/services/cloud_agent/test_research_script.py
git commit -m "feat: generate source grounded research drafts"
```

---

## Task 6: Expose a separate Research API and safe settings controls

**Files:**

- Create: `app/controllers/v1/cloud_agent_research.py`
- Modify: `app/services/cloud_agent/factory.py`
- Modify: `app/router.py`
- Test: `test/services/test_cloud_agent_research_controller.py`
- Test: `test/services/test_cloud_agent_controller.py`

- [ ] **Step 1: Write RED controller tests**

Cover these routes:

```text
GET  /api/v1/cloud-agent/research/settings
PUT  /api/v1/cloud-agent/research/settings
POST /api/v1/cloud-agent/research/settings/prompts/reset
POST /api/v1/cloud-agent/research-draft
```

Assert:

- GET never exposes the stored API key.
- PUT blank key retains, explicit clear removes, and returned settings are readback values.
- prompt reset changes only the requested prompt.
- research draft returns the complete response contract.
- each typed failure returns the correct safe code/message without provider data.
- Research failure does not call `/cloud-agent/draft` or the legacy generator.
- importing the router still leaves existing Cloud Agent controller tests unchanged.

- [ ] **Step 2: Run RED**

```bash
uv run pytest \
  test/services/test_cloud_agent_research_controller.py \
  test/services/test_cloud_agent_controller.py \
  -k 'research or draft' -v
```

Expected RED: Research routes return 404 because they are not registered.

- [ ] **Step 3: Add composition builders and the separate router**

Add factory functions that all reuse `config.app`:

```python
def build_research_settings_service() -> ResearchSettingsService: ...
def build_research_draft_store() -> ResearchDraftStore: ...
def build_openrouter_research_client() -> OpenRouterResearchClient: ...
def build_research_script_service() -> ResearchScriptService: ...
```

Implement controller dependencies wrapping these builders, return `utils.get_response(...)`, and map `ResearchScriptError` to `HttpException` using only `code` and safe `message`. Include the new router in `app/router.py` without modifying legacy route bodies.

- [ ] **Step 4: Run GREEN and controller regression**

```bash
uv run pytest \
  test/services/test_cloud_agent_research_controller.py \
  test/services/test_cloud_agent_controller.py \
  -v
uv run ruff check \
  app/controllers/v1/cloud_agent_research.py \
  app/services/cloud_agent/factory.py \
  app/router.py \
  test/services/test_cloud_agent_research_controller.py
```

- [ ] **Step 5: Commit**

```bash
git add app/controllers/v1/cloud_agent_research.py app/services/cloud_agent/factory.py app/router.py test/services/test_cloud_agent_research_controller.py test/services/test_cloud_agent_controller.py
git commit -m "feat: expose research script api"
```

---

## Task 7: Associate optional Research provenance when starting a job

**Files:**

- Modify: `app/models/cloud_agent.py`
- Modify: `app/controllers/v1/cloud_agent.py`
- Test: `test/services/cloud_agent/test_models.py`
- Test: `test/services/test_cloud_agent_controller.py`

- [ ] **Step 1: Write RED request/association tests**

Add tests proving:

- existing job payloads without Research data remain valid and behave identically;
- `research_draft_id` is accepted only as an optional start-request field and does not become a `CloudJobRecord` workflow field;
- a valid Research ID with a matching script is associated after the DRAFT job is durably created and before it becomes QUEUED;
- unknown ID or script-hash mismatch leaves the created job FAILED/not claimable and returns 422;
- legacy starts never initialize or query Research provenance.

- [ ] **Step 2: Run RED**

```bash
uv run pytest \
  test/services/cloud_agent/test_models.py \
  test/services/test_cloud_agent_controller.py \
  -k 'research_draft or create_cloud_agent_job' -v
```

Expected RED: the request rejects or ignores `research_draft_id`, and no association occurs.

- [ ] **Step 3: Add the narrow integration seam**

Add:

```python
class CloudJobStartRequest(CloudJobCreate):
    research_draft_id: str = Field(default="", max_length=64)
```

Change only the create-job endpoint body type. Build a plain `CloudJobCreate` with `model_dump(exclude={"research_draft_id"})`, create DRAFT as today, associate when the ID is nonblank, then continue the existing prepared-voice and QUEUED transitions. Do not add Research fields to `CloudJobRecord` or the workflow.

- [ ] **Step 4: Run GREEN and full controller regression**

```bash
uv run pytest \
  test/services/cloud_agent/test_models.py \
  test/services/test_cloud_agent_controller.py \
  -v
uv run ruff check app/models/cloud_agent.py app/controllers/v1/cloud_agent.py
```

- [ ] **Step 5: Commit**

```bash
git add app/models/cloud_agent.py app/controllers/v1/cloud_agent.py test/services/cloud_agent/test_models.py test/services/test_cloud_agent_controller.py
git commit -m "feat: link research drafts to cloud jobs"
```

---

## Task 8: Add an isolated Research WebUI that converges on Script Editor

**Files:**

- Create: `webui/cloud_agent_research.py`
- Modify: `webui/cloud_agent.py`
- Create: `test/services/test_cloud_agent_research_webui.py`
- Modify: `test/services/test_cloud_agent_webui.py`

- [ ] **Step 1: Write RED WebUI tests**

Using the existing fake Streamlit/UI pattern, assert:

- `Script Creation Mode` shows Standard and Research and loads the saved server default.
- Save as Default performs PUT then GET and displays success only after equality is verified.
- Standard selection renders the existing controls and calls only `POST draft` on Generate Script.
- Research selection renders key/configured status, curated/custom model controls, both editable prompts, save/readback/restore actions, Generate Research Script, and Sources.
- opening or refreshing the page performs no OpenRouter generation request.
- Generate Research Script shows only a busy animation/message, calls only `POST research-draft`, and disables duplicate submission during the synchronous call.
- Research failure leaves `cloud_agent_script`, `cloud_agent_master_prompt`, and `cloud_agent_clip_plan` unchanged and shows an actionable error.
- successful Standard and Research results both call the same `_store_draft()` and populate the same Script Editor state.
- a successful Research result additionally stores `research_draft_id`, `sources`, and `research_metadata` in session state.
- generating a Standard draft clears stale Research provenance.
- editing the Script Editor and using Refresh Draft calls the legacy draft endpoint with the current nonblank script only to rebuild the clip plan; it does not call Research again and preserves the existing Research ID.
- Start includes `research_draft_id` only when Research provenance exists.

- [ ] **Step 2: Run RED**

```bash
uv run pytest \
  test/services/test_cloud_agent_research_webui.py \
  test/services/test_cloud_agent_webui.py \
  -v
```

Expected RED: Research component/mode controls do not exist. Existing Standard tests must remain green where not asserting the new mode selector.

- [ ] **Step 3: Implement the separate Research component**

Keep provider-specific code in `webui/cloud_agent_research.py`. Its public boundary is:

```python
def render_research_script_controls(
    *,
    api,
    subject: str,
    language: str,
    target_words: int,
    store_draft,
) -> None: ...
```

The component may use Streamlit session state keys prefixed `cloud_agent_research_`. API key fields use `type="password"`; returned settings never refill the secret. Settings save and prompt restore must read back from the server before showing success.

- [ ] **Step 4: Add the mode branch and shared convergence point**

In `webui/cloud_agent.py`:

- render the mode selector after Language and before the mode-specific prompt area;
- keep the existing Standard controls in their current branch;
- call `render_research_script_controls()` only in Research mode;
- keep one shared Script Editor and all existing TTS/session/job controls below the branch;
- extend `_store_draft()` to accept optional Research provenance;
- include the optional Research ID in `_start_job()` without changing any other payload values.

Do not import the OpenRouter client or SQLite store into Streamlit.

- [ ] **Step 5: Run GREEN and WebUI regressions**

```bash
uv run pytest \
  test/services/test_cloud_agent_research_webui.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_webui_startup.py \
  -v
uv run ruff check \
  webui/cloud_agent_research.py \
  webui/cloud_agent.py \
  test/services/test_cloud_agent_research_webui.py \
  test/services/test_cloud_agent_webui.py
```

- [ ] **Step 6: Commit**

```bash
git add webui/cloud_agent_research.py webui/cloud_agent.py test/services/test_cloud_agent_research_webui.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add research script web controls"
```

---

## Task 9: Prove isolation and downstream compatibility end to end with fakes

**Files:**

- Create: `test/services/cloud_agent/test_research_script_integration.py`
- Modify only if a verified gap requires it: files introduced in Tasks 1–8

- [ ] **Step 1: Write RED integration tests before any correction**

Build the FastAPI app with fake Research dependencies and assert:

1. Standard Generate → shared Script Editor → job create works with no Research calls or provenance row.
2. Research Generate → verified sources → shared Script Editor → job create creates one provenance association.
3. The resulting Research job record has the same script/master-prompt/clip-plan/TTS fields as a Standard job and remains consumable by the unchanged workflow.
4. Research authentication, source, timeout, and malformed-response failures create no job, make no Standard request, and preserve the previous Script Editor state.
5. Page load/settings operations make zero paid generation calls.
6. One Generate click results in exactly one OpenRouter HTTP call even if the provider reports multiple internal web searches.

- [ ] **Step 2: Run RED and diagnose any mismatch**

```bash
uv run pytest test/services/cloud_agent/test_research_script_integration.py -v
```

Expected RED only for a real cross-component contract gap. If it passes immediately, do not add production code.

- [ ] **Step 3: Make one minimum correction per proven failure**

Keep corrections inside the new Research boundary or the explicit shared UI/job seams. Do not refactor legacy script generation, TTS, Flow, Canva, or worker code.

- [ ] **Step 4: Run GREEN**

```bash
uv run pytest test/services/cloud_agent/test_research_script_integration.py -v
```

- [ ] **Step 5: Commit**

```bash
git add test/services/cloud_agent/test_research_script_integration.py
git add app/models/cloud_agent_research.py app/services/cloud_agent/research_script.py app/controllers/v1/cloud_agent_research.py app/controllers/v1/cloud_agent.py webui/cloud_agent_research.py webui/cloud_agent.py
git commit -m "test: verify research script integration"
```

Before committing, use `git diff --cached --name-only` and unstage every unchanged or unrelated path; the command lists broad candidate paths only to capture a minimum verified correction if one was required.

---

## Task 10: Full verification, sanitized manual smoke, and deployment checkpoint

**Files:**

- Modify only if needed for documentation accuracy: `deploy/cloud-agent/README.md`

- [ ] **Step 1: Run the focused Research suite**

```bash
uv run pytest \
  test/services/cloud_agent/test_research_settings.py \
  test/services/cloud_agent/test_openrouter_research.py \
  test/services/cloud_agent/test_research_sources.py \
  test/services/cloud_agent/test_research_store.py \
  test/services/cloud_agent/test_research_script.py \
  test/services/cloud_agent/test_research_script_integration.py \
  test/services/test_cloud_agent_research_controller.py \
  test/services/test_cloud_agent_research_webui.py \
  -v
```

- [ ] **Step 2: Run Cloud Agent and WebUI regression**

```bash
uv run pytest \
  test/services/cloud_agent \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_webui.py \
  test/services/test_webui_startup.py \
  test/services/test_legacy_retirement.py \
  -v
```

- [ ] **Step 3: Run Ruff on every modified Python file**

```bash
uv run ruff check \
  app/config/config.py \
  app/models/cloud_agent.py \
  app/models/cloud_agent_research.py \
  app/router.py \
  app/controllers/v1/cloud_agent.py \
  app/controllers/v1/cloud_agent_research.py \
  app/services/cloud_agent/factory.py \
  app/services/cloud_agent/openrouter_research.py \
  app/services/cloud_agent/research_errors.py \
  app/services/cloud_agent/research_prompts.py \
  app/services/cloud_agent/research_script.py \
  app/services/cloud_agent/research_settings.py \
  app/services/cloud_agent/research_sources.py \
  app/services/cloud_agent/research_store.py \
  webui/cloud_agent.py \
  webui/cloud_agent_research.py \
  test/services/cloud_agent/test_models.py \
  test/services/cloud_agent/test_openrouter_research.py \
  test/services/cloud_agent/test_research_script.py \
  test/services/cloud_agent/test_research_script_integration.py \
  test/services/cloud_agent/test_research_settings.py \
  test/services/cloud_agent/test_research_sources.py \
  test/services/cloud_agent/test_research_store.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_research_controller.py \
  test/services/test_cloud_agent_research_webui.py \
  test/services/test_cloud_agent_webui.py
```

- [ ] **Step 4: Verify repository hygiene and architecture invariants**

Run:

```bash
rg -n "generate_script|cloud-agent/draft" \
  app/services/cloud_agent/openrouter_research.py \
  app/services/cloud_agent/research_script.py \
  app/controllers/v1/cloud_agent_research.py
rg -n "requests\.(get|post)|Session\(" app/services/cloud_agent/research_sources.py
rg -n "api_key|Authorization" webui/cloud_agent_research.py
git status --short
```

Expected:

- no legacy generator call in Research production modules;
- no source-page network fetch in the citation validator;
- no key value exposed in UI responses/logging;
- only intended tracked changes plus the operator's pre-existing untracked config backups.

- [ ] **Step 5: Run a non-paid deployed UI smoke**

On the Ubuntu host, deploy through the existing service workflow and verify:

- Standard is selected by default on existing config.
- Standard Generate remains functional.
- Research settings and API-key configured state survive refresh.
- no provider request occurs until Generate Research Script is clicked.
- no paid click is performed without separate authorization.

- [ ] **Step 6: Paid smoke requires a new explicit gate**

After automated and non-paid checks pass, stop at:

```text
READY_FOR_AUTHORIZED_OPENROUTER_RESEARCH_SMOKE
```

Report the selected model, one-request budget, five-result cap, configured-key state, and that no paid request has run. With later authorization, execute exactly one Research generation and verify citations, Script Editor convergence, durable provenance, and downstream job creation without starting TTS/Flow/Canva unless separately requested.

- [ ] **Step 7: Final commit and push**

If README changed:

```bash
git add deploy/cloud-agent/README.md
git commit -m "docs: describe research script deployment"
```

Then verify, push, and wait for CI:

```bash
git status --short
git push origin feature/cloud-video-agent
git rev-parse HEAD
git ls-remote origin refs/heads/feature/cloud-video-agent
```

Do not mark the feature complete until relevant CI checks pass.

---

## Completion Checklist

- [ ] Standard Script code path and tests are unchanged and green.
- [ ] Research settings are durable, read back after save, and secrets remain write-only.
- [ ] OpenRouter receives one bounded server-tool request only after an explicit click.
- [ ] Every accepted Research draft has at least two real citation annotations and all supplied URLs are verified.
- [ ] Research failures stop without fallback or Script Editor mutation.
- [ ] Both modes populate the same Script Editor and existing downstream contract.
- [ ] Research provenance is durable and optionally associated with the job.
- [ ] No page fetcher/crawler, second config loader, or workflow coupling was introduced.
- [ ] Focused tests, full Cloud Agent regressions, Ruff, deployed non-paid smoke, push, and CI all pass.
