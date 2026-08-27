# Cloud Agent TTS Provider Settings Design

**Status:** Approved design; implementation has not started.

## Goal

Give the Cloud Agent page the same selectable TTS provider and provider-setting
capabilities as the repaired SixClip UI, while retaining the Cloud Agent
architecture: Streamlit is a thin FastAPI client and the existing
`app.services.voice.tts()` router remains the only synthesis implementation.

The UI must let an operator select a provider, choose a compatible voice, and
manage that provider's server-side configuration without exposing a stored
credential in a browser response, CloudJob record, job history, or logs.

## Scope

The TTS Provider control supports exactly these eight existing providers:

1. Azure TTS V1
2. Azure TTS V2
3. SiliconFlow TTS
4. Google Gemini TTS
5. Xiaomi MiMo TTS
6. MiniMax TTS
7. ElevenLabs TTS
8. Chatterbox TTS

The existing Cloud Agent `Speed` control remains the canonical voice rate.
This change does not add a separate preview workflow, uploaded narration mode,
background music configuration, a new TTS implementation, or changes to Flow,
Canva, job checkpoints, or paid-operation rules.

## Existing Components Reused

- `app.services.voice.tts()` remains the authoritative provider router.
- Existing voice catalog functions remain authoritative:
  `get_all_azure_voices`, `get_siliconflow_voices`, `get_gemini_voices`,
  `get_mimo_voices`, `get_elevenlabs_voices`, `get_minimax_voice_catalog`,
  and `get_chatterbox_voices`.
- Existing `config` synchronized sections and `config.save_config()` remain
  the only configuration source and persistence mechanism.
- `ExistingVoiceTTSClient` retains its provider/voice consistency check before
  it calls the existing voice router.

No TTS provider behavior is copied into a Cloud Agent-specific implementation.

## Settings API

FastAPI owns all configuration reads and writes. Streamlit never imports the
server-side config module and never writes `config.toml` directly.

The API exposes a provider catalogue endpoint that returns only safe metadata:

- provider identifier and display name;
- compatible voice identifiers and display labels;
- visible non-secret settings and supported choices;
- a boolean `credential_configured` for each credential field.

It never returns a raw API key, credential-derived signature, environment value,
or private configuration path.

A provider settings update accepts only the fields supported by that provider.
Secret fields are write-only. An omitted or blank secret value preserves the
currently configured secret. Removing a secret is a separate explicit request
which requires the UI's "Remove stored key" confirmation; it cannot happen as
a side effect of a normal blank password field rerun.

Writes validate provider and field names against a static allowlist, update the
existing synchronized config sections, and call `config.save_config()` once.
They use the existing runtime configuration locking/persistence behavior, so
concurrent WebUI requests cannot produce partially written TOML.

Error responses are sanitized: they state the setting or provider that is
invalid, unavailable, or unconfigured, but never echo a submitted secret.

## Provider-Specific Contract

| Provider | Voice source | Settings shown/edited |
| --- | --- | --- |
| Azure TTS V1 | Existing Azure/Edge non-V2 catalog | No provider credential required for Edge routing |
| Azure TTS V2 | Existing Azure V2 catalog | Speech region, Speech key |
| SiliconFlow | Existing SiliconFlow catalog | API key |
| Google Gemini | Existing Gemini catalog | Gemini API key |
| Xiaomi MiMo | Existing MiMo catalog | MiMo API key |
| MiniMax | Existing loaded MiniMax catalogue | TTS API key (or existing shared fallback), endpoint, model, load voices action |
| ElevenLabs | Existing remote ElevenLabs catalogue after credential exists | API key, model, load/refresh voices action |
| Chatterbox | Existing configured Chatterbox list | Base URL, API key, model, comma-separated voice list |

Voice results must retain the identifiers expected by `voice.tts()` (including
provider prefixes where the existing router requires them). A selected provider
may not submit a voice belonging to another provider; the existing Cloud Agent
adapter remains the final enforcement layer.

MiniMax and ElevenLabs catalogue refreshes are explicit user actions. They may
perform the existing provider read-only catalog request but must not synthesize
speech or initiate a Flow/Canva action. A failed catalog request leaves the
stored settings intact and produces a sanitized UI error.

## WebUI Flow

1. The Cloud Agent panel fetches safe provider metadata from FastAPI.
2. The operator chooses one of the eight providers from a dropdown.
3. The UI renders the provider's visible settings and write-only password
   inputs. It uses a collapsed settings area to keep the normal form compact.
4. On an explicit save/load action, it sends only changed values to FastAPI.
5. The Voice dropdown is populated from the API response for the selected
   provider. If the previously selected voice is incompatible, the UI requires
   a compatible new selection rather than silently retaining it.
6. Creating a CloudJob sends the selected provider, compatible voice, and
   existing speed. It never sends any provider secret in the job request.

The normal default remains `azure-tts-v1` with the existing default voice when
the operator has not chosen another provider.

## Failure Handling

- Unknown provider, unknown setting, invalid endpoint/model, or incompatible
  voice returns a typed HTTP 422/409 response with a sanitized message.
- A missing required credential is shown as not configured and blocks only
  selection/use of that provider; it does not alter stored settings or a job.
- A transient remote voice-catalog failure does not clear a valid existing
  catalog choice or credential and does not initiate TTS.
- The API does not log request bodies containing secrets. Tests must verify
  redaction in serialized responses and error messages.

## Tests and Verification

TDD begins with RED tests before production code:

1. API returns all eight providers and only a provider-compatible voice list.
2. API configuration responses redact every credential while reporting its
   configured state.
3. Blank secret update preserves a stored secret; explicit confirmed removal
   removes only that requested secret.
4. Each provider accepts only its allowlisted settings and rejects unrelated
   fields.
5. Azure version voice filtering, Gemini/MiMo/SiliconFlow catalogs, Chatterbox
   configured voices, and provider/voice mismatch behavior match existing
   `voice.py` semantics.
6. ElevenLabs and MiniMax load catalog only after usable configuration and
   retain configuration across an empty WebUI rerun.
7. WebUI renders the dropdown, provider-specific controls, and sends no secret
   in job creation; it displays sanitized configuration/catalog errors.
8. Existing Cloud Agent TTS adapter and controller tests remain green.

Required verification after implementation includes focused controller/WebUI/
TTS tests, the Cloud Agent regression suite, and Ruff on every modified Python
file. No paid TTS, Flow, or Canva operation is permitted for this feature's
tests.

## Non-Goals and Security Invariants

- Do not add a second configuration loader or a second TTS router.
- Do not store raw secrets in SQLite, a CloudJob, Streamlit session state
  beyond the transient password widget submission, logs, or API responses.
- Do not expose browser profiles, tokens, signed URLs, or provider credentials.
- Do not change legacy six-clip rendering or background-music behavior.
