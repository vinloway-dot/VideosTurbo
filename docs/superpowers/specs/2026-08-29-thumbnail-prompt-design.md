# Thumbnail Prompt Design

## Purpose

Give each completed Cloud Agent video a **Prompt หน้าปก** action.  The action
uses the complete, immutable master prompt already saved with that video's job
to return one ready-to-use image-generation prompt.  It does not generate an
image itself.

## Scope

- Add a dedicated Thumbnail Prompt subsystem, independent of the existing
  Script, Research and TTS configuration and provider code.
- Add a Settings section named **Thumbnail Master Prompt**.
- Add a **Prompt หน้าปก** control beside the delete control in the completed
  video library.
- Support only AIHubMix and OpenRouter in the first release.
- Return one plain-text prompt per request.  Do not persist generated output
  and do not queue thumbnail work.

## Non-goals

- Image generation, image storage, image upload, image preview or image
  editing.
- Changes to the Google Flow, Canva, TTS, script, research or final-video
  workflow.
- Per-video provider or model selection.
- Reusing the existing `app.services.llm` provider registry, settings or
  credentials.

## Configuration

Thumbnail Prompt owns a separate configuration namespace and separate
credentials.  It must never read, write, expose or mutate the existing LLM
provider settings.

The Settings page exposes:

| Setting | Default | Rules |
|---|---|---|
| Thumbnail Master Prompt | empty | Required before generation; global for every video. |
| Default provider | `aihubmix` | May be changed only to a configured supported provider. |
| AIHubMix API key / base URL | empty | Dedicated Thumbnail Prompt credentials. |
| AIHubMix model | `gpt-5.6-sol` | Preset plus custom-model input. |
| OpenRouter API key / base URL | empty | Dedicated Thumbnail Prompt credentials. |
| OpenRouter model | `openai/gpt-5.6-sol` | Preset plus custom-model input. |

Secrets are write-only from the UI.  Settings read endpoints return whether a
secret is configured, never its value.

## Provider boundary

A new Thumbnail Prompt provider registry owns two clients:

- `aihubmix`
- `openrouter`

The registry validates the selected default provider, its credentials, its
base URL and its resolved model before a request is sent.  It is implemented
separately from the existing LLM registry even when both use compatible HTTP
protocols.  Custom model IDs are accepted only after non-empty validation.

## Request flow

1. The user opens the completed-video library and presses **Prompt หน้าปก**
   on a visible completed video.
2. The API verifies that the job is library-visible and resolves its job
   storage path safely.
3. The service reads that job's full `input/master_prompt.txt`; it does not
   accept a master prompt supplied by the browser.
4. The service reads the Thumbnail Prompt settings and invokes the configured
   default provider/model.
5. The model receives the complete video master prompt and the global
   Thumbnail Master Prompt.  It must analyse the full video context internally
   and output exactly one self-contained image-generation prompt.
6. The API returns that plain-text prompt.  The browser displays it in a
   copyable field.  It is not written to the database or job storage.

The instruction supplied to the provider requires no explanation, headings,
markdown, alternatives or analysis in the output.

## API

The Cloud Agent API gains a dedicated Thumbnail Prompt settings resource and a
single generation action:

- `GET /cloud-agent/thumbnail-prompt/settings`
- `PUT /cloud-agent/thumbnail-prompt/settings`
- `POST /cloud-agent/videos/{job_id}/thumbnail-prompt`

The generate endpoint has no client-supplied provider, model, master prompt or
system prompt fields.  This preserves the global-default rule and prevents a
browser client from reading arbitrary files.

## UI

Settings contains a separate **Thumbnail Master Prompt** card with the global
prompt, default-provider selector and dedicated AIHubMix/OpenRouter settings.
It shows configuration errors next to the relevant provider without exposing
secrets.

Each completed-video card gets **Prompt หน้าปก** beside its delete action.
Pressing it requests one prompt using the global default.  The card displays a
busy state, a copyable result field on success, and a retryable error message
on failure.  There is no provider/model selector on the card.

## Failure handling

- Missing master prompt, invalid visible job or unavailable final video: do
  not call a provider; return a scoped client error.
- Empty global prompt, unsupported default provider, missing credential or
  missing model: reject before a network request with an actionable settings
  error.
- Provider timeout, HTTP error or empty/non-text completion: return a
  sanitized retryable error for this action only.
- No Thumbnail Prompt failure changes job status, final media, Google Flow,
  Canva or worker queue state.

## Verification

Tests cover provider isolation, default and custom model resolution, secret
redaction, safe master-prompt lookup, request construction, output validation,
all expected failures and completed-video UI state.  Existing completed-video,
Cloud Agent workflow, Google Flow and Canva tests remain part of regression
verification.
