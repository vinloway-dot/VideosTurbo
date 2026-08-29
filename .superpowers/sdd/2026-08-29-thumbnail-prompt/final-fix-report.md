# Thumbnail Prompt final-review fix report

## Scope

Implemented every Important final-review correction in the isolated
`codex/thumbnail-prompt` worktree. No production configuration, credential, or
provider request was used.

## Corrections

- Invalid or blank configured default-provider IDs now raise a typed settings
  error instead of silently falling back to AIHubMix. Invalid provider and
  base-URL errors return sanitized, actionable Settings guidance.
- Both provider base URLs are part of the redacted settings payload, are
  editable in the selected-provider Settings UI, and are validated before
  OpenAI client construction. Validation requires an HTTP(S) URL with a host
  and rejects whitespace, embedded credentials, queries, fragments, and
  malformed ports.
- Empty, non-text, or structurally malformed provider completions retain the
  typed `THUMBNAIL_PROMPT_RESPONSE_INVALID` error and now map to retryable HTTP
  502 responses.
- Output validation now rejects Markdown bullets, fenced blocks, bold markup,
  horizontal-rule-separated alternatives, and the pre-existing labelled,
  numbered, or lettered alternatives.
- Provider calls use a 45-second timeout with SDK automatic retries disabled;
  the Streamlit caller waits 60 seconds. This keeps the caller alive longer
  than the single bounded paid request.
- A thumbnail-prompt click now clears the card's previous result and error,
  enters a rerun-visible busy state, disables the card action, and renders a
  progress caption before the request. Failure also removes any stale result.
- `Prompt หน้าปก` and `ลบ` now render in adjacent action columns.

## TDD evidence

Focused regression tests were added first and observed failing for the missing
contracts: provider/base-URL validation, editable base URLs, Markdown and
multi-alternative output rejection, 502 response mapping, bounded retry-free
timeouts, busy-state transition, stale-result clearing, and adjacent actions.
Each focused group was rerun green after its minimal implementation.

## Verification

Fresh final command:

```text
.venv/bin/pytest \
  test/services/cloud_agent/test_storage.py \
  test/services/cloud_agent/test_thumbnail_prompt_settings.py \
  test/services/cloud_agent/test_thumbnail_prompt_service.py \
  test/services/test_cloud_agent_thumbnail_prompt_controller.py \
  test/services/test_cloud_agent_controller.py \
  test/services/test_cloud_agent_ui.py \
  test/services/test_completed_videos_page.py \
  test/services/test_cloud_agent_webui.py -q
```

Result: `201 passed`, with 11 pre-existing dependency deprecation warnings.

Static verification:

- `ruff check` on every changed Python file: passed.
- `ruff format --check` on every changed Python file: 12 files formatted.
- `git diff --check`: passed.
