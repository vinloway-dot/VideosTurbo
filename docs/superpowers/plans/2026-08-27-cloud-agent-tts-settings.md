# Cloud Agent TTS Provider Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Cloud Agent the repaired SixClip TTS provider selector, compatible voice selector, and secure provider settings controls for all eight supported providers.

**Architecture:** A focused server-side `CloudTTSSettingsService` reads the existing `config` singleton and existing `voice` catalog functions. FastAPI exposes only safe catalogue/settings metadata and accepts write-only credential updates; Streamlit consumes these endpoints and never imports configuration or provider SDK code. `ExistingVoiceTTSClient` and `voice.tts()` remain the only synthesis router.

**Tech Stack:** FastAPI, Pydantic v2, Streamlit, existing TOML configuration (`app.config.config`), pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-27-cloud-agent-tts-settings-design.md`

## Global Constraints

- Support exactly `azure-tts-v1`, `azure-tts-v2`, `siliconflow`, `gemini-tts`, `mimo-tts`, `minimax-tts`, `elevenlabs`, and `chatterbox`.
- Reuse `app.services.voice.tts()` and existing catalog functions; do not create a second TTS router or config loader.
- Streamlit remains a thin FastAPI client: it must not import `app.config`, write TOML, open SQLite, or access browser profiles.
- Persist settings only through synchronized `config` sections plus one `config.save_config()` inside `config.runtime_config_lock()`.
- Never return, log, store in CloudJob/SQLite, or render a raw credential. A blank credential preserves the current value; only explicit confirmed removal clears it.
- Voice refresh is read-only and explicit for ElevenLabs and MiniMax. It must not synthesize TTS or start Flow/Canva work.
- Do not change Flow, Canva, checkpoints, paid-operation limits, legacy rendering, or background-music behavior.
- All tests use mocks/fakes; no paid TTS, Flow, or Canva action is allowed.

---

## File Structure

- Create `app/services/cloud_agent/tts_settings.py`: provider registry, safe setting metadata, config patch validation/persistence, catalog retrieval.
- Modify `app/models/cloud_agent.py`: request/response models for safe TTS metadata and write-only config updates.
- Modify `app/controllers/v1/cloud_agent.py`: thin routes and sanitized error translation.
- Modify `app/services/cloud_agent/factory.py`: build the settings service using existing composition.
- Modify `webui/cloud_agent.py`: provider/voice dropdowns, collapsible settings, explicit save/refresh/remove actions.
- Create `test/services/cloud_agent/test_tts_settings.py`: service tests.
- Modify `test/services/test_cloud_agent_controller.py` and `test/services/test_cloud_agent_webui.py`: endpoint/UI tests.

## Interfaces

```python
class TTSVoiceOption(BaseModel):
    id: str
    label: str

class TTSSettingField(BaseModel):
    name: str
    label: str
    kind: Literal["text", "password", "select", "voice_list"]
    value: str | list[str] | None = None  # never set for password
    configured: bool = False
    choices: list[str] = Field(default_factory=list)

class TTSProviderMetadata(BaseModel):
    id: str
    label: str
    voices: list[TTSVoiceOption]
    settings: list[TTSSettingField]
    requires_explicit_voice_refresh: bool = False

class TTSProviderSettingsPatch(BaseModel):
    settings: dict[str, str | list[str]] = Field(default_factory=dict)
    clear_secret_fields: list[str] = Field(default_factory=list)

```

The concrete service is `CloudTTSSettingsService` in
`app.services.cloud_agent.tts_settings`; it raises
`CloudTTSSettingsError(ValueError)`. Its exact public signatures are
`list_providers() -> list[TTSProviderMetadata]`,
`get_provider(provider_id: str, *, voices: list[str] | None = None) ->
TTSProviderMetadata`, `update_provider(provider_id: str,
patch: TTSProviderSettingsPatch) -> TTSProviderMetadata`, and
`refresh_voices(provider_id: str) -> TTSProviderMetadata`.

Routes:

```text
GET  /api/v1/cloud-agent/tts/providers
GET  /api/v1/cloud-agent/tts/providers/{provider_id}
PUT  /api/v1/cloud-agent/tts/providers/{provider_id}/settings
POST /api/v1/cloud-agent/tts/providers/{provider_id}/voices/refresh
```

### Task 1: Safe models and fixed provider registry

**Files:**
- Create: `app/services/cloud_agent/tts_settings.py`
- Modify: `app/models/cloud_agent.py`
- Test: `test/services/cloud_agent/test_tts_settings.py`

**Produces:** safe provider metadata for exactly eight providers; catalog calls that never make remote requests on ordinary metadata reads.

- [ ] **Step 1: Write RED tests**

```python
def test_list_providers_returns_exactly_the_eight_supported_ids(settings_service):
    assert [item.id for item in settings_service.list_providers()] == [
        "azure-tts-v1", "azure-tts-v2", "siliconflow", "gemini-tts",
        "mimo-tts", "minimax-tts", "elevenlabs", "chatterbox",
    ]

def test_metadata_never_serializes_stored_secret(settings_service, monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "api_key", "do-not-return-me")
    payload = settings_service.get_provider("elevenlabs").model_dump(mode="json")
    assert "do-not-return-me" not in repr(payload)
    field = next(item for item in payload["settings"] if item["name"] == "api_key")
    assert field["configured"] is True
    assert field["value"] is None

def test_azure_catalogs_split_by_existing_v2_rule(settings_service, monkeypatch):
    monkeypatch.setattr(voice, "get_all_azure_voices", lambda **_: [
        "en-US-JennyNeural-Female", "en-US-AvaMultilingualNeural-V2-Female",
    ])
    assert [v.id for v in settings_service.get_provider("azure-tts-v1").voices] == ["en-US-JennyNeural-Female"]
    assert [v.id for v in settings_service.get_provider("azure-tts-v2").voices] == ["en-US-AvaMultilingualNeural-V2-Female"]
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/cloud_agent/test_tts_settings.py -k 'providers or secret or azure' -v`

Expected: fail because the models/service do not exist, not due to test setup.

- [ ] **Step 3: Implement minimal models and static registry**

Use a static registry mapping each provider to its display name, config-section/key allowlist, secret fields, standard catalog function, and whether it supports explicit remote refresh. Build password fields only through:

```python
def _password_field(name: str, label: str, configured: bool) -> TTSSettingField:
    return TTSSettingField(name=name, label=label, kind="password", value=None, configured=configured)
```

Filter Azure using `bool(voice.is_azure_v2_voice(item)) is expected_v2`. Use existing static catalog functions for SiliconFlow, Gemini, MiMo, and configured Chatterbox voices. Do not call ElevenLabs or MiniMax remotely from `get_provider()`.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest test/services/cloud_agent/test_tts_settings.py -k 'providers or secret or azure' -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/models/cloud_agent.py app/services/cloud_agent/tts_settings.py test/services/cloud_agent/test_tts_settings.py
git commit -m "feat: add cloud tts provider metadata"
```

### Task 2: Secure mutation and explicit remote catalogue refresh

**Files:**
- Modify: `app/services/cloud_agent/tts_settings.py`
- Test: `test/services/cloud_agent/test_tts_settings.py`

**Produces:** allowlisted updates that persist once under the existing lock, blank-key preservation, confirmed secret clearing, and safe refresh for ElevenLabs/MiniMax.

- [ ] **Step 1: Write RED tests**

```python
def test_blank_secret_preserves_value_but_explicit_clear_removes_it(settings_service, monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "api_key", "stored-secret")
    settings_service.update_provider("elevenlabs", TTSProviderSettingsPatch(settings={"api_key": ""}))
    assert config.elevenlabs["api_key"] == "stored-secret"
    settings_service.update_provider("elevenlabs", TTSProviderSettingsPatch(clear_secret_fields=["api_key"]))
    assert config.elevenlabs.get("api_key", "") == ""

def test_rejects_unrelated_or_nonsecret_clear(settings_service):
    with pytest.raises(CloudTTSSettingsError, match="not supported"):
        settings_service.update_provider("gemini-tts", TTSProviderSettingsPatch(settings={"base_url": "x"}))
    with pytest.raises(CloudTTSSettingsError, match="explicit"):
        settings_service.update_provider("elevenlabs", TTSProviderSettingsPatch(clear_secret_fields=["model_id"]))

def test_refresh_elevenlabs_uses_key_without_returning_it(settings_service, monkeypatch):
    monkeypatch.setitem(config.elevenlabs, "api_key", "configured-secret")
    monkeypatch.setattr(voice, "get_elevenlabs_voices", lambda key: ["elevenlabs:id:Name"])
    result = settings_service.refresh_voices("elevenlabs")
    assert [item.id for item in result.voices] == ["elevenlabs:id:Name"]
    assert "configured-secret" not in repr(result.model_dump())
```

Add concrete tests for MiniMax endpoint/model choices and shared-key fallback, Chatterbox comma-list normalization, static Gemini/MiMo/SiliconFlow catalogs, and refresh exceptions preserving every config value.

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/cloud_agent/test_tts_settings.py -k 'blank_secret or refresh or unrelated or minimax or chatterbox' -v`

Expected: fail because update/refresh behavior is absent.

- [ ] **Step 3: Implement locked allowlisted changes**

```python
def update_provider(self, provider_id: str, patch: TTSProviderSettingsPatch) -> TTSProviderMetadata:
    spec = self._spec(provider_id)
    self._validate_patch(spec, patch)
    with config.runtime_config_lock():
        self._apply_nonblank_settings(spec, patch.settings)
        self._clear_only_explicit_secrets(spec, patch.clear_secret_fields)
        config.save_config()
    return self.get_provider(provider_id)
```

For ElevenLabs call `voice.get_elevenlabs_voices(voice.get_elevenlabs_api_key())`; for MiniMax call `voice.get_minimax_voice_catalog()` and map each entry to `minimax:<voice_id>`. Reject remote refresh for the other six providers. Do not log keys, request bodies, exception response text, or catalog authorization data.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest test/services/cloud_agent/test_tts_settings.py -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/tts_settings.py test/services/cloud_agent/test_tts_settings.py
git commit -m "feat: add secure cloud tts settings"
```

### Task 3: FastAPI contract

**Files:**
- Modify: `app/controllers/v1/cloud_agent.py`
- Modify: `app/services/cloud_agent/factory.py`
- Modify: `test/services/test_cloud_agent_controller.py`

**Produces:** four routes, dependency-overridable service construction, and sanitized typed failures.

- [ ] **Step 1: Write RED controller tests**

```python
def test_tts_provider_routes_are_registered():
    assert ("GET", "/api/v1/cloud-agent/tts/providers") in EXPECTED_CLOUD_AGENT_PATHS
    assert ("PUT", "/api/v1/cloud-agent/tts/providers/{provider_id}/settings") in EXPECTED_CLOUD_AGENT_PATHS

def test_settings_api_redacts_stored_key_and_preserves_blank_update(client, fake_tts_settings):
    assert "stored-secret" not in client.get("/api/v1/cloud-agent/tts/providers/elevenlabs").text
    response = client.put("/api/v1/cloud-agent/tts/providers/elevenlabs/settings", json={"settings": {"api_key": ""}})
    assert response.status_code == 200
    assert "stored-secret" not in response.text

def test_tts_settings_routes_do_not_start_synthesis_or_browser(client, monkeypatch):
    monkeypatch.setattr(voice, "tts", lambda *_a, **_k: pytest.fail("no synthesis"))
    monkeypatch.setattr(PersistentBrowserManager, "open", lambda *_a, **_k: pytest.fail("no browser"))
    assert client.post("/api/v1/cloud-agent/tts/providers/elevenlabs/voices/refresh").status_code == 200
```

Add route tests for unknown provider/field (422), explicit clearing, refresh-error sanitization, and no response text containing a sentinel secret.

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/test_cloud_agent_controller.py -k 'tts_provider or settings_api or tts_settings' -v`

Expected: fail because the routes/dependency are absent.

- [ ] **Step 3: Implement thin routes**

```python
def get_cloud_tts_settings_service() -> CloudTTSSettingsService:
    return build_cloud_tts_settings_service()

@router.get("/cloud-agent/tts/providers")
def list_cloud_tts_providers(service: CloudTTSSettingsService = Depends(get_cloud_tts_settings_service)):
    return utils.get_response(200, [item.model_dump(mode="json") for item in service.list_providers()])

@router.put("/cloud-agent/tts/providers/{provider_id}/settings")
def update_cloud_tts_provider_settings(provider_id: str, body: TTSProviderSettingsPatch, service: CloudTTSSettingsService = Depends(get_cloud_tts_settings_service)):
    return utils.get_response(200, service.update_provider(provider_id, body).model_dump(mode="json"))
```

Add matching provider-detail and refresh routes. Translate only `CloudTTSSettingsError` to the repository-standard sanitized `HttpException`; do not log `body.model_dump()`.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest test/services/test_cloud_agent_controller.py -k 'tts_provider or settings_api or tts_settings' -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/controllers/v1/cloud_agent.py app/services/cloud_agent/factory.py test/services/test_cloud_agent_controller.py
git commit -m "feat: expose cloud tts settings api"
```

### Task 4: Cloud Agent provider/voice/settings UI

**Files:**
- Modify: `webui/cloud_agent.py`
- Modify: `test/services/test_cloud_agent_webui.py`

**Produces:** provider dropdown, provider-compatible voice dropdown, per-provider expander, explicit settings persistence and catalog refresh, with no secrets in CloudJob creation.

- [ ] **Step 1: Write RED WebUI tests**

```python
def test_cloud_agent_ui_uses_tts_metadata_api_not_config_or_voice_router():
    source = UI_SOURCE.read_text(encoding="utf-8")
    assert '"tts/providers"' in source
    assert "app.config" not in source
    assert "app.services.voice" not in source

def test_start_payload_has_provider_and_voice_but_no_provider_secret(monkeypatch):
    captured = {}
    monkeypatch.setattr(cloud_agent, "_api", lambda _method, _path, **kw: captured.update(kw.get("json", {})) or {"id": "job"})
    cloud_agent._start_job(subject="s", target_words=130, language="", script="x", master_prompt="m", clip_plan={}, tts_provider="elevenlabs", voice_id="elevenlabs:id:Name", voice_speed=1.0)
    assert captured["tts_provider"] == "elevenlabs"
    assert captured["voice_id"] == "elevenlabs:id:Name"
    assert "api_key" not in captured
```

Use the existing fake-Streamlit style to assert a configured password field is blank in the widget and ordinary Save omits it from the PUT request; assert only confirmed Remove emits `clear_secret_fields=["api_key"]`.

- [ ] **Step 2: Run RED**

Run: `uv run pytest test/services/test_cloud_agent_webui.py -k 'tts_metadata or start_payload or blank_secret' -v`

Expected: fail because the UI has free-text provider/voice fields and no settings API calls.

- [ ] **Step 3: Implement the dynamic thin-client block**

```python
def _save_tts_settings(provider_id, settings, clear_secret_fields):
    return _api("PUT", f"tts/providers/{provider_id}/settings", json={
        "settings": {key: value for key, value in settings.items() if value not in ("", [])},
        "clear_secret_fields": clear_secret_fields,
    })
```

Fetch provider metadata via FastAPI. Render the dropdown using metadata IDs/labels; voice choices come only from selected metadata or explicit cached refresh results. Put provider settings below it in an expander. Render credentials as empty `type="password"` fields plus configured status. Render a confirmation checkbox and button for each removal. ElevenLabs/MiniMax load only when the user clicks `Load Voices`; cache only returned voice identifiers in Streamlit session state. Preserve the exact existing `_start_job()` payload fields and never include settings/keys.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest test/services/test_cloud_agent_webui.py -k 'tts_metadata or start_payload or blank_secret' -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add webui/cloud_agent.py test/services/test_cloud_agent_webui.py
git commit -m "feat: add cloud tts provider controls"
```

### Task 5: Regression and static verification

**Files:**
- Modify only if a new RED-tested correction is required: files from Tasks 1–4.
- Test: `test/services/cloud_agent/test_tts.py`, `test/services/cloud_agent/test_tts_settings.py`, `test/services/test_cloud_agent_controller.py`, `test/services/test_cloud_agent_webui.py`

- [ ] **Step 1: Run focused/full TTS and Cloud Agent regression**

```bash
uv run pytest \
  test/services/cloud_agent/test_tts.py \
  test/services/cloud_agent/test_tts_settings.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_webui.py -v
uv run pytest test/services/cloud_agent -v
```

Expected: pass; mocks prove no paid TTS/Flow/Canva work.

- [ ] **Step 2: Run Ruff**

```bash
uv run ruff check \
  app/models/cloud_agent.py \
  app/services/cloud_agent/tts_settings.py \
  app/services/cloud_agent/factory.py \
  app/controllers/v1/cloud_agent.py \
  webui/cloud_agent.py \
  test/services/cloud_agent/test_tts_settings.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_webui.py
```

Expected: no findings.

- [ ] **Step 3: Inspect security surface and commit/push**

```bash
git diff --check
rg -n "api_key|speech_key" app/models/cloud_agent.py app/services/cloud_agent/tts_settings.py app/controllers/v1/cloud_agent.py webui/cloud_agent.py
git status --short
git push origin feature/cloud-video-agent
```

Confirm no raw credential is serialized, logged, included in a job, or returned by the UI API. Do not run paid smoke tests for this feature.

## Plan Self-Review

- **Spec coverage:** Tasks 1–4 cover eight providers, existing catalog reuse, safe persisted settings, blank/key-removal semantics, explicit ElevenLabs/MiniMax refresh, compatible voices, and a thin WebUI. Task 5 covers regression, lint, and credential-surface inspection.
- **Completeness scan:** The fake Streamlit test follows the existing fixture style and has exact required capture assertions.
- **Type consistency:** Every route and UI helper uses `TTSProviderSettingsPatch` and `TTSProviderMetadata`; every provider uses the same eight IDs from the static registry.
