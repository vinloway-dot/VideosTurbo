# Cloud Agent Research Script Tool-Calling Design

**Date:** 2026-08-27

**Status:** Approved in chat; replacement written specification awaiting operator review.

**Replaces:** `2026-08-27-cloud-agent-research-script-design.md` and its
implementation plan. Those files remain historical and superseded because they
describe the cancelled OpenRouter server-side Web Search design.

**Scope:** Research-assisted script drafting only. The existing Script Editor,
master-prompt and six-clip-plan contract, TTS, Google Flow, Canva, worker,
checkpoints, and final validation remain the production path.

## 1. Purpose

Add a separate Research Script mode to the Cloud Agent. It reads one to three
public URLs supplied by the operator through VideosTurbo-owned tools, lets the
selected provider use those tool results together with model knowledge, and
places an accepted narration into the same Script Editor used by Standard
Script.

Research Script is additive. It must not refactor, import into, replace, or
silently fall back to Standard Script. A Research failure leaves the current
Script Editor and downstream draft state unchanged.

## 2. Confirmed Product Decisions

- Providers are OpenRouter and AIHubMix.
- OpenRouter defaults to `openai/gpt-5.6-sol-pro`.
- AIHubMix defaults to `gpt-5.6-sol`.
- Each provider also offers a `Custom Model ID` choice initialized to its
  provider default.
- A Research request requires one to three operator-supplied URLs.
- The only tools are `fetch_url(url)` and `read_pdf(url)`.
- A draft permits at most three tool executions in total and at most three
  provider API rounds in total.
- There is no automatic paid retry and no automatic provider/model fallback.
- Version one reads only public HTML and PDF resources directly over HTTP or
  HTTPS. It has no Playwright or browser fallback.
- Evidence has no product-level character truncation. The full accepted,
  sanitized evidence is sent when it fits the selected model. Technical safety
  and provider-context guardrails reject an oversized request explicitly
  instead of silently cutting evidence.
- The WebUI shows the maximum of three provider rounds before execution. After
  success or failure it shows the actual rounds used and provider-supplied
  usage/cost metadata when available.
- Research has an editable, persisted Custom System Prompt, but code-enforced
  security and evidence invariants always take precedence.
- A successful Research draft has durable provenance separate from the
  CloudJob workflow record.

## 3. Goals

- Preserve Standard Script and the validated Cloud Agent path without behavior
  changes.
- Keep provider-specific protocol code out of evidence fetching and policy.
- Apply one security implementation to both providers.
- Prove that every displayed source was successfully read by VideosTurbo.
- Allow source evidence and model knowledge without false source attribution.
- Fail closed with stable typed errors and useful Thai WebUI messages.
- Reuse the existing FastAPI application, `config.app`, Script Editor state,
  draft preparation contract, and CloudJob factory composition.

## 4. Non-goals

- Web search, URL discovery, SearXNG, Brave Search, or another search API.
- OpenRouter `openrouter:web_search`, `:online`, or legacy search plugins.
- Playwright, authenticated browsing, login, paywall or bot-challenge bypass.
- Local knowledge-base ingestion or indexing.
- Changes to TTS, Google Flow, Canva, browser profiles, worker behavior, leases,
  checkpoints, or final validation.
- A second FastAPI app, configuration loader, worker, browser-profile manager,
  or hidden fallback script generator.

## 5. Architecture

```text
Streamlit Cloud Agent panel (thin FastAPI client)
                 |
                 v
Cloud Agent Research Script API
                 |
                 v
ResearchScriptService
  |-- OpenRouterToolCallingAdapter
  |-- AIHubMixToolCallingAdapter
  `-- ResearchToolRuntime
       |-- fetch_url
       `-- read_pdf
                 |
                 v
ResearchDraftRepository (research records, not workflow state)
                 |
                 v
existing Script Editor -> existing draft/master prompt/clip plan -> Start
```

### 5.1 Research service

The shared service owns request validation, the bounded provider/tool state
machine, prompt construction, evidence aggregation, final-response validation,
provenance, and the shared draft response. It depends on a provider-adapter
interface and the shared tool runtime, not on provider implementations.

It calls the existing six-clip-plan and master-prompt preparation only after a
Research narration is accepted, so a successful Standard or Research request
returns the same downstream draft shape.

### 5.2 Provider adapters

Each adapter owns only:

- endpoint and authentication protocol;
- provider request and tool-declaration format;
- model selection and capability resolution;
- assistant tool-call parsing;
- tool-result message formatting;
- final-text and usage/cost metadata extraction; and
- sanitized provider-error classification.

The adapter never fetches URLs, decides evidence policy, writes CloudJobs, or
falls back to another provider or model.

### 5.3 Shared tool runtime

The runtime owns URL canonicalization, network validation, redirects, bounded
download, content-type verification, HTML/PDF extraction, source metadata,
content hashes, duplicate handling, and prompt-injection boundaries. Tool
implementations do not know which provider requested them.

Research code uses the existing application composition and configuration. It
must not open the Google Flow or Canva profiles or acquire their browser locks.

## 6. WebUI Design

The script-creation area gains two explicit modes:

- `Standard Script` renders and behaves as it does now.
- `Research Script` renders Research provider, model, Custom Model ID, API-key
  state, Research Custom System Prompt, the per-draft citation toggle, and
  one-to-three URL inputs.

The mode-specific controls appear before the shared Script Editor. Research
mode provides:

- provider selector;
- provider-specific model selector;
- `Custom Model ID` choice and field;
- configured/not-configured API-key state, write-only key input, separate
  confirmed key removal, and settings save;
- editable Research Custom System Prompt and explicit save;
- `อนุญาตให้ใส่อ้างอิงในสคริปต์` per-draft checkbox, default `false`;
- one URL per input row, with at most three rows;
- a Sources panel populated only after successful reads; and
- a Generate Research Script action with a visible busy state.

Before generation the UI states that the operation may make up to three
provider rounds. After completion or failure it displays actual provider rounds
and provider-supplied token/usage/cost metadata when present. Missing usage or
cost metadata is displayed as unavailable, never estimated.

Opening or refreshing the page and saving settings or prompts makes no provider
request. Save success appears only after a FastAPI readback matches all
persisted non-secret values. API-key readback is only `configured: true|false`.
A blank key on save retains the existing key.

On success the current `_store_draft` behavior receives the common response and
updates the existing Script Editor, master prompt, clip plan, and draft-script
snapshot. On failure none of those fields changes. The WebUI never opens SQLite,
runs Research tools, or calls providers directly.

## 7. API and Data Contracts

Research-specific routes remain under the existing `/api/v1/cloud-agent/`
router and use these contracts:

- `GET research/providers` returns the provider/model catalog and configured-key
  booleans;
- `GET research/settings` returns persisted non-secret Research defaults;
- `PUT research/settings` updates non-secret defaults and returns their
  persisted readback;
- `PUT research/providers/{provider_id}/api-key` sets a non-empty write-only
  key;
- `DELETE research/providers/{provider_id}/api-key` requires an explicit
  confirmation field and removes that key;
- `POST research/drafts` creates a bounded Research draft; and
- `GET research/drafts/{research_draft_id}` returns durable non-secret draft and
  source metadata.

### 7.1 Create Research draft request

```json
{
  "subject": "string",
  "language": "string",
  "target_words": 130,
  "provider": "openrouter | aihubmix",
  "model_choice": "provider model id | custom",
  "custom_model_id": "string",
  "source_urls": ["https://example.com/source"],
  "custom_system_prompt": "string",
  "allow_citations": false
}
```

The effective model is the selected catalog model or the non-empty Custom Model
ID. There must be one to three unique canonical source URLs. Validation occurs
before any provider generation call.

### 7.2 Successful common draft response

```json
{
  "script": "accepted narration",
  "master_prompt": "existing master prompt contract",
  "clip_plan": {},
  "research_draft_id": "opaque id",
  "sources": [
    {
      "url": "canonical public URL",
      "title": "extracted title",
      "content_hash": "non-secret digest"
    }
  ],
  "accounting": {
    "tool_calls": 1,
    "provider_rounds": 2,
    "usage": {},
    "cost": null
  }
}
```

Usage and cost contain only sanitized metadata supplied by the provider. Raw
provider requests, responses, headers, and tool transcripts are not returned to
the WebUI.

### 7.3 Start association

Job creation may carry an optional `research_draft_id`. The API verifies that
the durable draft exists and that its script hash matches the script currently
being started. It then writes a separate Research-draft-to-job association. The
CloudJob workflow record, script/master-prompt/clip-plan contract, and worker
remain unchanged and do not parse Research provenance.

## 8. Tool-Calling State Machine

One click on Generate Research Script creates one bounded attempt:

1. Validate subject, language, target length, one-to-three canonical URLs,
   provider settings, API-key presence, and effective model.
2. Prove Tool Calling support and an applicable context limit for the effective
   model. A Custom Model ID whose support cannot be proved is rejected before a
   generation call.
3. Build the non-editable security/evidence instructions, append the editable
   writing instructions, and supply the canonical URL list and two tool schemas.
4. Make provider round one.
5. If the assistant requests tools, validate every call and execute permitted
   calls through the shared runtime. Count every requested execution against the
   total of three. A repeated canonical URL reuses the already verified result
   but still counts as a requested tool execution, preventing free loops.
6. Return sanitized tool results, with stable source identifiers, to the same
   provider conversation and continue while a final provider round remains.
7. Accept final narration only after at least one supplied URL was read
   successfully and all factual source attributions refer to successful source
   identifiers.
8. Validate narration, create the normal master prompt and six-clip plan,
   persist provenance, and return the common draft response.

The service permits no more than three provider API requests and three requested
tool executions. A round that requests a tool is allowed only when at least one
provider round remains for final synthesis. Multiple tool calls returned in one
assistant message are processed in stable order until the tool budget is
exhausted; the service never partially executes a batch whose declared calls
would exceed the remaining budget.

Provider HTTP retries are disabled for generation calls. A network retry,
authentication retry, model substitution, or provider substitution requires a
new explicit operator action. Normal multi-round tool calling within the single
attempt is not classified as a retry.

If one supplied source fails but at least one other source succeeds, the failed
source is reported and excluded from evidence and the Sources panel. If every
source read fails, the attempt stops. A final response produced before any
successful source read is rejected rather than accepted from model memory.

## 9. URL and Network Security

The runtime accepts only `http` and `https` URLs and version-one ports 80 and
443. It rejects:

- URL credentials and recognized bearer/signature query parameters;
- localhost names and literal loopback addresses;
- private, reserved, unspecified, multicast, link-local, and documentation IPs;
- cloud metadata hosts and addresses;
- non-public DNS answers or mixed public/private answer sets; and
- unsupported schemes, content types, redirects, and ports.

DNS is resolved before connection, the connection is constrained to a validated
public address while preserving the original Host/SNI identity, and every
redirect target is canonicalized and fully revalidated. Redirects are bounded
to five hops. DNS rebinding or a redirect toward a prohibited target fails
closed.

Each tool has bounded connect/read/total timeouts. The decoded response body is
limited to 10 MiB per URL. PDFs are limited to 30 pages. Crossing either limit
rejects the source with a typed error; the runtime does not truncate and pretend
the partial source is complete.

Only readable HTML/XHTML and valid PDFs are supported. JavaScript-only shells,
login-required pages, paywalls, CAPTCHA, bot challenges, downloads requiring
cookies, and authenticated sessions are rejected. Research never receives
browser cookies or authorization headers.

URL query values that could be sensitive are excluded from logs and durable
records. A signed URL is rejected rather than persisted as provenance.

## 10. Extraction, Evidence, and Duplicate Handling

### 10.1 HTML

HTML extraction records the canonical URL and title, removes scripts, styles,
navigation, footer, cookie banners, repeated chrome, and hidden/non-readable
content, then emits normalized readable blocks in document order. A page whose
remaining readable content is empty fails as unsupported evidence.

### 10.2 PDF

PDF extraction verifies the MIME type and file signature, refuses encrypted or
malformed files, and extracts text in page order. A scanned/image-only PDF with
no usable text fails; OCR is outside version-one scope. The runtime records page
count and a hash of normalized extracted text.

### 10.3 Duplicates

Canonical duplicate URLs are collapsed before provider execution and count as
one supplied source. Within and across successful sources, exact normalized
duplicate blocks are represented once with all contributing source identifiers.
Near-duplicate passages retain their differing content and attribution; the
system must not erase distinctions merely to reduce context.

### 10.4 Complete evidence and model context

The evidence packet contains the complete accepted normalized content after
boilerplate and exact-duplicate removal. It remains divided by stable source
identifier, title, and canonical URL. The service does not summarize, select
chunks, or apply a product character limit before sending it.

Before each provider request the adapter calculates the complete input size plus
a reserved output allowance against a proved context limit for the effective
model. If the complete packet does not fit, the request fails with
`SOURCE_CONTEXT_TOO_LARGE`. It never silently drops the middle/end of a source,
uses only the first N characters, or hides provider truncation.

Content from pages and PDFs is untrusted data surrounded by explicit boundaries.
Instructions inside evidence cannot change system policy, request secrets,
authorize additional URLs, or cause tool calls outside the supplied URL set.

## 11. Evidence and Model-Knowledge Policy

Supplied URLs are primary sources. The final script may combine:

1. evidence successfully read by VideosTurbo; and
2. knowledge already present in the selected model from training.

The code-enforced and prompt-enforced rules are:

- Model knowledge must not be attributed to a supplied source.
- When model knowledge conflicts with source evidence, prefer the source or
  disclose/omit the conflict.
- News, prices, current office holders, and other unstable facts cannot be
  asserted from model memory alone.
- Sources lists only successful VideosTurbo reads.
- Narration omits citations and URLs unless the per-draft citation toggle is enabled.
- At least one source must succeed; model-only narration is invalid.
- Provenance records evidence mode as `source_evidence + model_knowledge`.

## 12. Prompt Model

Research uses two conceptual layers:

1. Non-editable system invariants enforce URL requirements, the supplied-URL
   allowlist, SSRF and secret protection, tool and round budgets, successful
   source reads, attribution policy, no fallback, no paid retry, content and
   context guardrails, and provider capability validation.
2. The editable Research Custom System Prompt controls writing style, tone,
   audience, language, structure, target length, model-knowledge usage, and how
   evidence should be narrated.

The editable prompt is stored independently from the existing Standard Script
Custom System Prompt. User text is never interpolated into or allowed to replace
the non-editable instruction layer. It cannot grant citation permission;
`allow_citations` is the sole authority for citations in narration.

## 13. Settings and Secrets

Research settings are persisted server-side through a dedicated service using
the existing application configuration and factory patterns. Persisted defaults
include provider, provider-specific model choice, provider-specific Custom Model
ID, and the Research Custom System Prompt.

OpenRouter and AIHubMix API keys are separate write-only secrets. GET responses
expose only configured state. Blank input retains a stored key. Removal uses a
separate confirmed operation. Keys, authorization headers, raw request/response
bodies, signed URLs, cookies, and private session data never enter logs,
provenance, API errors, or WebUI state.

No provider request is made by settings load, page load, refresh, prompt save,
or settings save.

## 14. Provider Capability Validation

Catalog models have adapter-owned, tested capability metadata. Custom Model IDs
must be resolved through a provider-supported model metadata capability when the
operator explicitly generates a Research draft. If Tool Calling support and a
usable context limit cannot be proved, the service stops with
`PROVIDER_TOOL_CALLING_UNSUPPORTED` or `PROVIDER_MODEL_UNSUPPORTED` before a
paid generation call.

A failure never changes the requested model, adds an `:online` suffix, enables a
server-side Web Search tool, or chooses the provider default implicitly.

## 15. Durable Provenance

Successful drafts are stored in Research-specific tables/repositories separate
from CloudJob workflow records. The storage may share the configured Cloud Agent
SQLite database and connection policy, but it has separate schema ownership and
does not create a second application/database loader.

At minimum, each successful draft stores:

- `research_draft_id`;
- script hash;
- selected provider and effective model ID;
- canonical successful-source URLs and titles;
- normalized source-content hashes;
- tool-call count and provider-round count;
- sanitized provider usage/cost metadata when supplied;
- creation timestamp;
- fingerprints of editable and non-editable prompts; and
- evidence mode `source_evidence + model_knowledge`.

It never stores API keys, authorization headers, cookies, signed URLs, raw
private browser state, or raw provider payloads. Full extracted source content
is request-local and is not persisted after the attempt; hashes and non-secret
metadata provide the durable provenance contract.

## 16. Typed Failures and WebUI Messages

The domain exposes stable typed codes. FastAPI maps them to appropriate HTTP
responses, while the WebUI renders plain-language Thai explanations and a next
action without showing tracebacks.

Required codes include:

- `URL_REQUIRED`
- `URL_INVALID`
- `URL_TARGET_NOT_PUBLIC`
- `URL_REDIRECT_REJECTED`
- `URL_FETCH_FAILED`
- `URL_CONTENT_UNSUPPORTED`
- `URL_CONTENT_TOO_LARGE`
- `PDF_INVALID`
- `PDF_TOO_LARGE`
- `PDF_TEXT_UNAVAILABLE`
- `SOURCE_EVIDENCE_EMPTY`
- `SOURCE_CONTEXT_TOO_LARGE`
- `PROVIDER_API_KEY_MISSING`
- `PROVIDER_AUTHENTICATION_FAILED`
- `PROVIDER_MODEL_UNSUPPORTED`
- `PROVIDER_TOOL_CALLING_UNSUPPORTED`
- `PROVIDER_TIMEOUT`
- `TOOL_CALL_LIMIT_EXCEEDED`
- `PROVIDER_ROUND_LIMIT_EXCEEDED`
- `RESEARCH_RESPONSE_INVALID`

For example, `URL_REQUIRED` displays
`กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง`, and
`PROVIDER_AUTHENTICATION_FAILED` explains that the selected provider key is
invalid or unavailable. Diagnostics retain only sanitized error class, phase,
counts, and non-secret identifiers.

## 17. Failure Atomicity

Every Research failure:

- stops the current attempt;
- performs no automatic paid retry;
- performs no Standard/provider/model fallback;
- does not overwrite Script Editor, master prompt, clip plan, or draft snapshot;
- does not create a successful Research draft;
- does not create or queue a CloudJob; and
- returns the typed code, Thai-facing explanation, and sanitized accounting.

Partial source reads and provider messages exist only for the bounded request and
sanitized diagnostics. They are not represented as a successful draft.

## 18. Verification Strategy

All production behavior follows RED -> observed intended failure -> smallest
GREEN -> focused regression -> full relevant regression -> Ruff.

### 18.1 Unit and contract tests

- URL normalization, one-to-three uniqueness, schemes, ports, credentials,
  prohibited IP classes, DNS rebinding, redirects, and signed-query rejection.
- HTML readability extraction, PDF signature/page/text validation, exact block
  deduplication, provenance hashes, and empty evidence.
- No silent truncation and explicit `SOURCE_CONTEXT_TOO_LARGE` behavior.
- Exactly three total tool executions and three total provider rounds, including
  multi-call batches, repeated URLs, premature final responses, and a tool call
  with no synthesis round remaining.
- Provider request/tool/result parsing for OpenRouter and AIHubMix fixtures.
- Strict Custom Model Tool Calling/context capability failure and no fallback.
- Source-versus-model-knowledge policy and no fabricated source attribution.
- Every typed domain error maps to a stable API code and safe Thai UI message.
- API keys remain write-only; blank saves retain and confirmed removal deletes.
- Settings/prompt save succeeds only after matching server readback.
- Provenance contains required non-secret fields and excludes secrets/payloads.

### 18.2 Integration and WebUI tests

- Standard Script endpoint and controls remain behaviorally unchanged.
- Standard modules do not import Research modules.
- Page load, refresh, and settings/prompt saves make zero provider calls.
- Research success feeds the existing Script Editor/master-prompt/clip-plan
  state through the common draft response.
- Research failure preserves every prior editor/draft value and creates no job.
- Sources contains only successful reads.
- Busy state, maximum-round disclosure, actual accounting, and unavailable-cost
  behavior are observable without fabricated progress percentages.
- Optional `research_draft_id` association verifies the script hash while the
  worker receives the unchanged workflow contract.

### 18.3 Regression and live gates

Run focused Research tests, the full relevant Cloud Agent regression suite, and
Ruff. Verify API, WebUI, and Worker remain separate. Non-paid local fixtures and
smokes must prove all behavior possible without provider billing.

A live OpenRouter or AIHubMix generation requires separate explicit operator
approval. It is one bounded attempt with no automatic retry. No test or smoke
may invoke TTS, Google Flow Generate, Canva mutation, or other paid production
work merely to verify Research Script.

## 19. Rollout and Compatibility

- `Standard Script` remains the default mode.
- Existing config, scripts, CloudJobs, and historic artifacts require no
  behavioral migration.
- Research can be disabled without disabling the Cloud Agent.
- A failed or disabled Research subsystem does not degrade Standard Script.
- Deployment does not change VNC/noVNC exposure, services, browser profiles, or
  the production worker.
- The superseded Web Search documents remain historical and must not be used as
  implementation authority.

## 20. Implementation Gate

This document authorizes no production code by itself. After the operator
reviews and explicitly approves this written specification, create a new
RED-to-GREEN implementation plan using `superpowers:writing-plans`. Production
code begins only after that plan is separately approved.
