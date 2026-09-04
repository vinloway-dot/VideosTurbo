# VideosTurbo Research Script Tool-Calling Handoff

**Date:** 2026-08-27

**Repository:** `/opt/VideosTurbo`

**Branch:** `feature/cloud-video-agent`

**Status:** Requirements refinement in progress. No replacement Design Spec or production implementation is approved yet.

## Purpose

Continue designing a new Research Script mode for the Cloud Agent. The operator explicitly cancelled the earlier OpenRouter server-side Web Search design. The replacement must use VideosTurbo-owned tools invoked through provider Tool calling.

Do not implement production code until the replacement Design Spec is complete, internally consistent, written to the repository, and explicitly approved by the operator.

## Superseded Documents

Do not implement these documents:

- `docs/superpowers/specs/2026-08-27-cloud-agent-research-script-design.md`
- `docs/superpowers/plans/2026-08-27-cloud-agent-research-script.md`

They describe the cancelled `openrouter:web_search` server-tool architecture and are retained only as history.

## Confirmed Product Boundaries

### Existing mode

- Preserve Standard Script exactly as it works today.
- Do not couple or refactor the legacy script generator merely to add Research Script.
- A Standard draft continues to populate the existing Script Editor and production pipeline.

### New mode

- Add a separate Research Script mode.
- Both Standard and Research results converge on the existing Script Editor.
- After the Script Editor, the current master-prompt, six-clip plan, TTS, Google Flow, Canva, checkpoint, worker, and final-validation behavior remains unchanged.
- Research failure must stop with an actionable reason and must never silently fall back to Standard Script.

## Confirmed Providers and Models

Research Script must let the operator select either provider:

### OpenRouter

- Provider-specific model selector.
- Default model: `openai/gpt-5.6-sol-pro`.
- A `Custom model ID` choice and editable field.
- The custom field initializes to `openai/gpt-5.6-sol-pro`.

### AIHubMix

- Provider-specific model selector.
- Default model: `gpt-5.6-sol`.
- A `Custom model ID` choice and editable field.
- The custom field initializes to `gpt-5.6-sol`.

### Settings and secrets

- Each provider has a separate server-side API key setting.
- Keys are write-only from the WebUI; GET responses expose only configured/not-configured state.
- Blank key input retains the stored key.
- Key removal is a separate confirmed action.
- Provider, model choice, and custom model ID can be saved as defaults.
- Save success is shown only after server readback verifies the persisted non-secret values.
- No provider request occurs on page load, page refresh, settings save, or prompt save.
- There is no automatic provider failover.

## Confirmed Tool Scope

The replacement architecture exposes exactly two VideosTurbo-owned tools to the selected model:

```text
fetch_url(url) -> open a public web page and return extracted readable content
read_pdf(url)  -> download a public PDF and return extracted text
```

The following ideas were explicitly removed from this version:

- `search_web`
- `get_product`
- `get_news`
- `get_database`
- SearXNG
- Brave Search or another Search API
- OpenRouter `openrouter:web_search`
- OpenRouter `:online` or legacy web-search plugins
- local knowledge-base upload/indexing

The only external AI APIs are the provider selected by the operator: OpenRouter or AIHubMix. VideosTurbo itself executes `fetch_url` and `read_pdf` on the VPS.

## Confirmed URL Requirement

- Research Script requires at least one URL in the operator's request.
- If no URL is supplied, stop before any provider call and show: `กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง`.
- Research mode does not search the internet for additional URLs.
- Every URL that contributes evidence must have been successfully read by `fetch_url` or `read_pdf`.
- If all supplied URLs fail, stop and do not write from model memory alone.

## Confirmed Evidence and Model-Knowledge Policy

The Research script may combine:

1. source evidence extracted from supplied web pages or PDFs; and
2. knowledge already present in the selected model from training.

Enforce these rules in code-level policy as well as prompts:

- Supplied URLs are primary sources, but not necessarily the only information used.
- The model may add context, explanations, and prior knowledge.
- The model must not claim that prior knowledge came from a supplied source.
- When model knowledge conflicts with source evidence, prefer the source or disclose/omit the conflict.
- Current or unstable facts such as news, prices, office holders, and recent events must not be asserted from model memory alone.
- The Sources panel lists only URLs/PDFs that VideosTurbo actually read successfully.
- Spoken narration does not include citations or URLs unless the operator's editable Research prompt explicitly requests them.
- Research metadata records that the draft used `source_evidence` and `model_knowledge` without pretending model knowledge has a citation.
- Model knowledge may appear without its own citation only when it does not conflict with the primary sources.

## Proposed Architecture Requiring Final Review

The recommended architecture is a shared Tool Runtime behind two provider adapters:

```text
Cloud Agent Research Script Service
├── OpenRouter Tool-Calling Adapter
├── AIHubMix Tool-Calling Adapter
└── VideosTurbo Research Tool Runtime
    ├── fetch_url
    └── read_pdf
```

Provider adapters own provider-specific request/response and Tool-call message formats. The shared Research service owns the bounded loop, evidence aggregation, policy validation, prompt construction, and final shared draft contract. Tool implementations do not know which provider requested them.

Alternative designs considered and not recommended:

- duplicate complete Research pipelines per provider, because validation and tool behavior would drift;
- LangChain/LlamaIndex, because they add unnecessary dependency and debugging complexity to the existing production system.

## Proposed Safety Boundaries Requiring Final Review

- Maximum 10 supplied URLs.
- Maximum 10 tool executions per Research draft.
- Maximum 6 provider API rounds per Research draft.
- No automatic paid retry.
- Public HTTP/HTTPS targets only.
- Reject URL credentials, localhost, loopback, link-local, private/reserved IPs, and cloud metadata targets.
- Resolve DNS and revalidate every redirect before connecting.
- Apply response-size, decompression, content-type, PDF-page, extraction-text, and total-context limits.
- Treat all page/PDF content as untrusted data, never as system instructions.
- Never reuse Google Flow or Canva authenticated browser profiles for Research tools.
- Use ordinary HTTP extraction first. A possible isolated, non-persistent, unauthenticated Playwright fallback for JavaScript-only public pages still needs explicit design approval.
- Reject login-required, authenticated, paywalled, CAPTCHA, and bot-challenge content rather than attempting to bypass it.
- Store sanitized source metadata and content hashes; never store authorization headers, cookies, signed URLs, or private session data.

## Remaining Design Work for the New Task

Use `superpowers:brainstorming` and continue at the design-review stage. Do not restart requirements discovery already settled above.

Resolve these points one at a time with the operator:

1. Approve or revise the recommended Provider Adapters + shared Tool Runtime boundary and proposed budgets.
2. Decide whether to include the isolated Playwright fallback or support HTTP-readable pages only in version one.
3. Define exact HTML extraction, PDF limits, context truncation/chunk selection, and duplicate-URL behavior.
4. Define the bounded multi-round Tool-calling state machine and paid-call accounting shown in the WebUI.
5. Define strict provider capability checks when a custom model does not support Tool calling.
6. Define Research Custom System Prompt and non-editable security/evidence invariants.
7. Define typed failure codes and plain-language UI messages.
8. Define durable Research provenance and optional CloudJob association without changing the workflow record.
9. Present the complete replacement Design in sections and obtain operator approval.
10. Write and commit a new Design Spec with a new filename; do not overwrite historical contents beyond their Superseded status.
11. Ask the operator to review the written replacement Spec before creating a new implementation plan.

## Git and Workspace Safety

- Preserve pre-existing untracked `config.toml.backup-*` and `config.toml.save*` files.
- Do not expose the existing AIHubMix key or any other secret.
- Do not modify TTS, Google Flow, Canva, live services, CloudJob state, or paid-provider usage while designing.
- Do not execute the superseded implementation plan.
