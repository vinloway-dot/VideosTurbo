# Thumbnail Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate Thumbnail Prompt subsystem that turns a completed video's full master prompt into one copyable image-generation prompt using a globally configured AIHubMix or OpenRouter provider.

**Architecture:** A new `app/services/cloud_agent/thumbnail_prompt/` package owns configuration, provider metadata, OpenAI-compatible clients and prompt generation.  It reads the server-side job master prompt through `CloudJobStorage`, has dedicated API endpoints, and is used by isolated Settings and completed-video UI controls.  The generated text is returned only to the browser; it does not alter jobs, queues, Google Flow, Canva or the existing LLM subsystem.

**Tech Stack:** FastAPI, Pydantic, OpenAI Python SDK, TOML-backed `config.app`, Streamlit, pytest.

**Spec:** `docs/superpowers/specs/2026-08-29-thumbnail-prompt-design.md`

## Global Constraints

- Support exactly `aihubmix` and `openrouter` in v1; default to `aihubmix`.
- Use dedicated Thumbnail Prompt configuration and credentials; do not import or mutate `app.services.llm` or Research settings.
- AIHubMix defaults to model `gpt-5.6-sol`; OpenRouter defaults to `openai/gpt-5.6-sol`; both allow a non-empty custom model ID.
- Read only the full server-side `input/master_prompt.txt` for a completed, library-visible job; do not accept prompt, provider or model data from the generation request.
- Return one plain-text ready-to-use image prompt, without markdown, explanation, alternatives or analysis.
- Do not create images, store generated prompts, change the job schema, enqueue work, or change Google Flow, Canva, TTS, script or research behavior.
- Return configuration state but never API-key values; sanitize upstream errors.
- Use test doubles for all provider calls; tests must not send paid requests.

---

## File Structure

- Modify: `app/services/cloud_agent/video_library.py` — public completed-video lookup that shares library visibility rules.
- Create `app/services/cloud_agent/thumbnail_prompt/errors.py` — stable public error codes and sanitized Thai messages.
- Create `app/services/cloud_agent/thumbnail_prompt/models.py` — Pydantic request/response and provider metadata models.
- Create `app/services/cloud_agent/thumbnail_prompt/settings.py` — dedicated config keys, provider catalog, validation and secret persistence.
- Create `app/services/cloud_agent/thumbnail_prompt/service.py` — full-master-prompt reader, instruction builder and OpenAI-compatible completion clients.
- Create `app/services/cloud_agent/thumbnail_prompt/__init__.py` — explicit public subsystem exports.
- Modify `app/config/config.py` — defaults for the independent thumbnail namespace.
- Modify `app/services/cloud_agent/storage.py` — safe `read_master_prompt(job_id)` helper.
- Modify `app/services/cloud_agent/factory.py` — dependency factories only for the new settings and generation services.
- Modify `app/controllers/v1/cloud_agent.py` — settings/provider routes and the library-visible generate route.
- Modify `webui/pages/3_Settings.py` and `webui/cloud_agent.py` — Thumbnail Master Prompt settings controls using the internal API.
- Modify `webui/completed_videos.py` and `webui/cloud_agent_ui.py` — per-card prompt action, result and retry state.
- Create focused `test/services/cloud_agent/test_thumbnail_prompt_*.py`, `test/services/test_cloud_agent_thumbnail_prompt_controller.py`, and extend existing Settings/library UI tests.

### Task 1: Dedicated configuration and settings boundary

**Files:**
- Create: `app/services/cloud_agent/thumbnail_prompt/__init__.py`
- Create: `app/services/cloud_agent/thumbnail_prompt/errors.py`
- Create: `app/services/cloud_agent/thumbnail_prompt/models.py`
- Create: `app/services/cloud_agent/thumbnail_prompt/settings.py`
- Modify: `app/config/config.py`
- Test: `test/services/cloud_agent/test_thumbnail_prompt_settings.py`

**Interfaces:**
- Produces `ThumbnailPromptSettingsService`, `ThumbnailPromptProviderMetadata`, `ThumbnailPromptSettingsPayload`, and `ThumbnailPromptError`.
- Consumed by the API controller, provider-generation service and Settings UI.

- [ ] **Step 1: Write failing settings tests**

```python
def test_defaults_are_dedicated_and_aihubmix_is_selected(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))
    service = ThumbnailPromptSettingsService()
    assert service.get_settings().default_provider == "aihubmix"
    assert service.get_provider("aihubmix").default_model == "gpt-5.6-sol"
    assert service.get_provider("openrouter").default_model == "openai/gpt-5.6-sol"

def test_settings_hide_api_key_and_allow_custom_model(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))
    service = ThumbnailPromptSettingsService()
    service.set_api_key("aihubmix", "secret-value")
    updated = service.update_settings(
        ThumbnailPromptSettingsPayload(
            master_prompt="Create a striking thumbnail.",
            default_provider="aihubmix",
            aihubmix_model="custom",
            aihubmix_custom_model_id="my-thumbnail-model",
            openrouter_model="openai/gpt-5.6-sol",
            openrouter_custom_model_id="",
        )
    )
    assert updated.aihubmix_model == "custom"
    assert "secret-value" not in updated.model_dump_json()
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest test/services/cloud_agent/test_thumbnail_prompt_settings.py -v`

Expected: FAIL because the Thumbnail Prompt package and defaults do not exist.

- [ ] **Step 3: Add defaults, models, errors and service implementation**

```python
THUMBNAIL_PROMPT_DEFAULTS = {
    "cloud_agent_thumbnail_prompt_master_prompt": "",
    "cloud_agent_thumbnail_prompt_default_provider": "aihubmix",
    "cloud_agent_thumbnail_prompt_aihubmix_model": "gpt-5.6-sol",
    "cloud_agent_thumbnail_prompt_aihubmix_custom_model": "",
    "cloud_agent_thumbnail_prompt_openrouter_model": "openai/gpt-5.6-sol",
    "cloud_agent_thumbnail_prompt_openrouter_custom_model": "",
    "cloud_agent_thumbnail_prompt_aihubmix_api_key": "",
    "cloud_agent_thumbnail_prompt_openrouter_api_key": "",
    "cloud_agent_thumbnail_prompt_aihubmix_base_url": "https://aihubmix.com/v1",
    "cloud_agent_thumbnail_prompt_openrouter_base_url": "https://openrouter.ai/api/v1",
}

class ThumbnailPromptSettingsPayload(BaseModel):
    master_prompt: str = Field(min_length=1, max_length=8000)
    default_provider: Literal["aihubmix", "openrouter"]
    aihubmix_model: str
    aihubmix_custom_model_id: str = Field(default="", max_length=256)
    openrouter_model: str
    openrouter_custom_model_id: str = Field(default="", max_length=256)
```

Implement provider metadata with `models=[default_model, "custom"]`, write API keys only under the new keys while holding `config.runtime_config_lock()`, and return `api_key_configured: bool` rather than any secret value.  Reject a `custom` selection without a custom model ID and reject unsupported providers/models with `ThumbnailPromptError`.

- [ ] **Step 4: Run focused tests and configuration regression tests**

Run: `pytest test/services/cloud_agent/test_thumbnail_prompt_settings.py test/services/test_cloud_agent_controller.py -v`

Expected: PASS; Research settings remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/config/config.py app/services/cloud_agent/thumbnail_prompt test/services/cloud_agent/test_thumbnail_prompt_settings.py
git commit -m "feat: add thumbnail prompt settings boundary"
```

### Task 2: Prompt-generation service and safe master-prompt access

**Files:**
- Modify: `app/services/cloud_agent/storage.py`
- Create: `app/services/cloud_agent/thumbnail_prompt/service.py`
- Test: `test/services/cloud_agent/test_thumbnail_prompt_service.py`
- Test: `test/services/cloud_agent/test_storage.py`

**Interfaces:**
- Consumes `ThumbnailPromptSettingsService.get_generation_settings()` and `CloudJobStorage.read_master_prompt(job_id)`.
- Produces `ThumbnailPromptService.generate_for_job(job_id: str) -> str`.

- [ ] **Step 1: Write failing service tests**

```python
def test_generate_uses_full_saved_master_prompt_and_returns_one_plain_prompt(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-1")
    paths.master_prompt_file.write_text("FULL VIDEO MASTER PROMPT", encoding="utf-8")
    client = FakeCompletionClient("Solar flare over Earth, dramatic golden light, 16:9")
    service = ThumbnailPromptService(storage=storage, settings=ready_settings(), clients={"aihubmix": client})
    result = service.generate_for_job("job-1")
    assert result == "Solar flare over Earth, dramatic golden light, 16:9"
    assert "FULL VIDEO MASTER PROMPT" in client.messages[-1]["content"]
    assert "analysis" not in result.lower()

def test_generate_rejects_empty_or_multichoice_provider_output(tmp_path):
    service = service_with_completion(tmp_path, "Option 1: one\nOption 2: two")
    with pytest.raises(ThumbnailPromptError, match="ผลลัพธ์"):
        service.generate_for_job("job-1")
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest test/services/cloud_agent/test_thumbnail_prompt_service.py -v`

Expected: FAIL because `read_master_prompt` and `ThumbnailPromptService` do not exist.

- [ ] **Step 3: Implement safe reader and isolated OpenAI-compatible clients**

```python
def read_master_prompt(self, job_id: str) -> str:
    path = self._paths(job_id).master_prompt_file
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError("job master prompt is unavailable") from exc
    if not value:
        raise ValueError("job master prompt is empty")
    return value

def _instruction(master_prompt: str, thumbnail_master_prompt: str) -> str:
    return "\n\n".join((
        "Create exactly one ready-to-use image-generation prompt.",
        "Return only that prompt: no heading, markdown, explanation, analysis, or alternatives.",
        "Analyse the complete video master prompt internally and follow the thumbnail master prompt.",
        f"<thumbnail_master_prompt>\n{thumbnail_master_prompt}\n</thumbnail_master_prompt>",
        f"<video_master_prompt>\n{master_prompt}\n</video_master_prompt>",
    ))
```

Use a package-local `OpenAI(api_key=..., base_url=...)` client for each provider.  Call `chat.completions.create(model=..., messages=[{"role": "user", "content": instruction}])`, normalize non-empty string output, strip surrounding whitespace, reject output containing multiple labelled options, and convert timeout/auth/HTTP/empty-response exceptions to sanitized `ThumbnailPromptError` codes.  Do not import `app.services.llm`.

- [ ] **Step 4: Run focused tests and existing storage tests**

Run: `pytest test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/cloud_agent/test_storage.py -v`

Expected: PASS without network access.

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/storage.py app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/cloud_agent/test_storage.py
git commit -m "feat: generate thumbnail prompts from job masters"
```

### Task 3: Cloud Agent API surface

**Files:**
- Modify: `app/services/cloud_agent/factory.py`
- Modify: `app/services/cloud_agent/video_library.py`
- Modify: `app/controllers/v1/cloud_agent.py`
- Test: `test/services/test_cloud_agent_thumbnail_prompt_controller.py`

**Interfaces:**
- Consumes `ThumbnailPromptSettingsService`, `ThumbnailPromptService`, `CloudVideoLibraryService.get_visible_job(job_id)` and `CloudJobStorage`.
- Produces the three API routes in the approved spec.

- [ ] **Step 1: Write failing controller tests**

```python
def test_thumbnail_prompt_endpoint_only_generates_for_visible_completed_job(client, services):
    response = client.post("/api/v1/cloud-agent/videos/visible/thumbnail-prompt")
    assert response.status_code == 200
    assert response.json()["data"] == {"prompt": "ready image prompt"}
    services.thumbnail.generate_for_job.assert_called_once_with("visible")

def test_thumbnail_prompt_endpoint_rejects_queued_job_without_calling_provider(client, services):
    response = client.post("/api/v1/cloud-agent/videos/queued/thumbnail-prompt")
    assert response.status_code == 404
    services.thumbnail.generate_for_job.assert_not_called()
```

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `pytest test/services/test_cloud_agent_thumbnail_prompt_controller.py -v`

Expected: FAIL because the dependencies and routes do not exist.

- [ ] **Step 3: Add factories, schemas and routes**

```python
@router.post("/cloud-agent/videos/{job_id}/thumbnail-prompt")
def generate_thumbnail_prompt(
    job_id: str,
    request: Request,
    store: CloudJobStore = Depends(get_cloud_job_store),
    library: CloudVideoLibraryService = Depends(get_cloud_video_library_service),
    thumbnails: ThumbnailPromptService = Depends(get_thumbnail_prompt_service),
):
    del request
    job = library.get_visible_job(job_id)
    if job is None:
        raise HttpException(task_id="thumbnail-prompt", status_code=404, message="completed video not found")
    try:
        return utils.get_response(200, {"prompt": thumbnails.generate_for_job(job.id)})
    except ThumbnailPromptError as exc:
        raise _thumbnail_prompt_http_exception(exc) from exc
```

Add this public helper without changing existing list or delete behavior:

```python
def get_visible_job(self, job_id: str) -> CloudJobRecord | None:
    job = self._store.get_job(job_id)
    return job if job is not None and self._is_visible(job) else None
```

Expose `GET`/`PUT /cloud-agent/thumbnail-prompt/settings`, `GET /cloud-agent/thumbnail-prompt/providers`, and provider API-key update/removal endpoints under `/cloud-agent/thumbnail-prompt/providers/{provider_id}/api-key`.  Use purpose-specific request models with input-length validators, map missing configuration to 422, authentication to 401, timeout to 504 and provider response failures to 502.  Ensure settings responses are redacted.

- [ ] **Step 4: Run controller and Cloud Agent regression tests**

Run: `pytest test/services/test_cloud_agent_thumbnail_prompt_controller.py test/services/test_cloud_agent_controller.py -v`

Expected: PASS; no generated prompt is persisted and existing video routes retain their behavior.

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/factory.py app/services/cloud_agent/video_library.py app/controllers/v1/cloud_agent.py test/services/test_cloud_agent_thumbnail_prompt_controller.py
git commit -m "feat: expose thumbnail prompt API"
```

### Task 4: Settings UI for the independent subsystem

**Files:**
- Modify: `webui/cloud_agent.py`
- Modify: `webui/pages/3_Settings.py`
- Test: `test/services/test_cloud_agent_ui.py`
- Test: `test/services/test_completed_videos_page.py`

**Interfaces:**
- Consumes the Thumbnail Prompt settings/provider/API-key endpoints from Task 3.
- Produces `_render_thumbnail_prompt_settings(...)` and page-local session-state keys prefixed `thumbnail_prompt_`.

- [ ] **Step 1: Write failing UI tests**

```python
def test_settings_page_renders_thumbnail_prompt_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(cloud_agent, "_render_thumbnail_prompt_settings", lambda **kwargs: calls.append(kwargs))
    runpy.run_path("webui/pages/3_Settings.py", run_name="settings_test")
    assert calls
    assert calls[0]["settings"]["default_provider"] == "aihubmix"

def test_thumbnail_settings_payload_uses_dedicated_default_only():
    payload = cloud_agent._thumbnail_prompt_settings_payload({
        "thumbnail_prompt_default_provider": "openrouter",
        "thumbnail_prompt_master_prompt": "Style guide",
        "thumbnail_prompt_aihubmix_model": "gpt-5.6-sol",
        "thumbnail_prompt_openrouter_model": "openai/gpt-5.6-sol",
    })
    assert payload["default_provider"] == "openrouter"
    assert "llm_provider" not in payload
```

- [ ] **Step 2: Run focused UI tests and verify they fail**

Run: `pytest test/services/test_cloud_agent_ui.py -k thumbnail_prompt -v`

Expected: FAIL because the Settings renderer and payload helper do not exist.

- [ ] **Step 3: Implement the Settings card**

```python
with st.container(key="thumbnail_prompt_settings", border=True):
    st.subheader("Thumbnail Master Prompt")
    st.text_area("Thumbnail Master Prompt", key="thumbnail_prompt_master_prompt")
    st.selectbox("Default Thumbnail Provider", options=("aihubmix", "openrouter"), key="thumbnail_prompt_default_provider")
```

Load settings and provider metadata only through the new API.  Render selected provider model choice plus custom-model field, and dedicated API-key write/remove controls.  Preserve the user-entered key only for the one save request, clear it from session state afterward, and never render the persisted secret.  Saving this card must not invoke existing research, script or TTS helpers.

- [ ] **Step 4: Run Settings-page regression tests**

Run: `pytest test/services/test_cloud_agent_ui.py test/services/test_completed_videos_page.py -v`

Expected: PASS; existing Settings cards remain intact.

- [ ] **Step 5: Commit**

```bash
git add webui/cloud_agent.py webui/pages/3_Settings.py test/services/test_cloud_agent_ui.py test/services/test_completed_videos_page.py
git commit -m "feat: add thumbnail prompt settings UI"
```

### Task 5: Completed-video prompt action and end-to-end verification

**Files:**
- Modify: `webui/completed_videos.py`
- Modify: `webui/cloud_agent_ui.py`
- Test: `test/services/test_cloud_agent_ui.py`
- Test: `test/services/test_completed_videos_page.py`
- Test: `test/services/test_cloud_agent_thumbnail_prompt_controller.py`

**Interfaces:**
- Consumes `POST videos/{job_id}/thumbnail-prompt` and returns `{ "prompt": str }`.
- Produces card-local session state `thumbnail_prompt_result_by_job` and `thumbnail_prompt_error_by_job`.

- [ ] **Step 1: Write failing completed-library UI tests**

```python
def test_prompt_action_requests_one_prompt_and_keeps_video_card_unchanged(monkeypatch):
    result = completed_videos.generate_thumbnail_prompt("job-1")
    assert result == "ready image prompt"
    assert completed_videos._api.call_args.args == ("POST", "videos/job-1/thumbnail-prompt")

def test_prompt_action_error_is_scoped_to_its_job(monkeypatch):
    state = {"thumbnail_prompt_error_by_job": {}}
    completed_videos.store_thumbnail_prompt_error(state, "job-1", "provider timeout")
    assert state["thumbnail_prompt_error_by_job"] == {"job-1": "provider timeout"}
```

- [ ] **Step 2: Run focused UI tests and verify they fail**

Run: `pytest test/services/test_cloud_agent_ui.py -k 'thumbnail_prompt or video_library' -v`

Expected: FAIL because the completed-library action and card state do not exist.

- [ ] **Step 3: Add the card action and copyable result**

```python
if action_column.button("Prompt หน้าปก", key=f"thumbnail_prompt_{item.job_id}"):
    prompt = on_thumbnail_prompt(item.job_id)
    st.session_state["thumbnail_prompt_result_by_job"][item.job_id] = prompt

if prompt := thumbnail_prompt_results.get(item.job_id):
    st.text_area("Thumbnail Prompt", value=prompt, key=f"thumbnail_prompt_output_{item.job_id}", disabled=True)
```

Add a request helper in `completed_videos.py`, keep result/error maps keyed by job ID, disable only the clicked action while it is pending, and render retryable errors without changing delete-confirmation state.  Keep the action adjacent to deletion controls and do not add provider/model controls to a video card.

- [ ] **Step 4: Run focused suite, full Cloud Agent suite and static checks**

Run: `pytest test/services/cloud_agent/test_thumbnail_prompt_settings.py test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py test/services/test_cloud_agent_ui.py test/services/test_completed_videos_page.py -v`

Expected: PASS with no network calls.

Run: `pytest test/services/cloud_agent test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_video_library.py test/services/test_cloud_agent_ui.py -v`

Expected: PASS.

Run: `ruff check app/services/cloud_agent/thumbnail_prompt app/controllers/v1/cloud_agent.py webui/cloud_agent.py webui/completed_videos.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webui/completed_videos.py webui/cloud_agent_ui.py test/services/test_cloud_agent_ui.py test/services/test_completed_videos_page.py test/services/test_cloud_agent_thumbnail_prompt_controller.py
git commit -m "feat: add completed video thumbnail prompt action"
```

## Final verification

- [ ] Run `git diff --check` and inspect `git status --short`; do not stage user-owned config backups or `.superpowers` artifacts.
- [ ] Run the focused and Cloud Agent regression commands from Task 5 and record their complete pass/fail totals.
- [ ] Verify `GET /api/v1/cloud-agent/thumbnail-prompt/settings` redacts API keys using a test configuration; do not use a production key.
- [ ] Verify a completed-video generation request in tests returns one prompt and leaves the job row, final media and job storage unchanged.
