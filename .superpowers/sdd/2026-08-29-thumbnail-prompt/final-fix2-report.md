# Thumbnail Prompt final re-review fix report

## Scope

Fixed both Important re-review gaps in the isolated
`codex/thumbnail-prompt` worktree. No production configuration, credential, or
provider request was used.

## Corrections

- A malformed persisted default-provider ID no longer makes
  `GET /cloud-agent/thumbnail-prompt/settings` return HTTP 500. The readable
  settings state now returns `default_provider: null` and a fixed, sanitized,
  actionable `configuration_error`; it never returns the malformed saved value.
- Provider-catalog reads remain available in this recovery state. The Settings
  UI displays the recovery message, initializes its unsaved selector from the
  real catalog, and can submit a valid provider through the normal settings-save
  path.
- Generation remains strict: `get_configured_provider_id()` still rejects the
  malformed persisted value before API-key/model/client resolution, so the UI
  recovery selection cannot silently become a generation fallback.
- Provider completions must now contain exactly one choice and exactly one
  non-empty line of plain prompt prose. Block quotes, inline code, Markdown
  links/autolinks, Markdown emphasis, newline-separated or multi-paragraph
  alternatives, and all previously rejected headings/lists/fences/labels remain
  invalid.

## Regression coverage and TDD evidence

- Added a real FastAPI endpoint regression that begins with a malformed saved
  provider, asserts a 200 sanitized settings payload, verifies the invalid value
  is absent, and confirms both provider catalog entries are still returned.
- Added a Settings renderer regression that starts from the sanitized invalid
  state, verifies the actionable error and catalog selector, submits a valid
  provider payload, and verifies the rerun path.
- Added focused service cases for block quotes, inline code, Markdown links,
  wrapped and inline emphasis, unlabeled newline alternatives, multi-paragraph
  alternatives, and zero/two provider choices. Normal single-line prose remains
  accepted.
- The initial focused red run produced `10 failed, 1 passed`: the endpoint was
  HTTP 500, the UI state was unusable, all seven newly specified text shapes
  were accepted, and the second provider choice was ignored. The same focused
  boundary was observed green after the minimal implementation.

## Verification

Fresh subsystem suite:

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

Result: `214 passed`, with 11 pre-existing dependency deprecation warnings.

Static verification:

- `ruff check` on every changed Python file: passed.
- `ruff format --check` on every changed Python file: passed.
- `git diff --check`: passed.
