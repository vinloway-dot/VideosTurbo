# Thumbnail prompt final fix 4

## Scope

- No deployment, production operation, or production configuration change was
  performed.
- Fixed only the three final-review gaps in the thumbnail-prompt subsystem.

## Root causes

1. The write and generation paths validated base URLs, but the settings and
   provider read paths returned persisted configuration text directly. A legacy
   malformed URL, including credentials in userinfo or secrets in a query,
   could therefore be reflected by both GET endpoints.
2. The inline Markdown expression required at least one character inside a
   link destination and did not recognize raw HTML markup.
3. The controller had no public message entries for
   `PROVIDER_MODEL_UNSUPPORTED` or `PROVIDER_CUSTOM_MODEL_REQUIRED`, so both
   fell through to the generic request error despite using status 422.

## Changes

- Settings and provider GET models now pass persisted base URLs through the
  same strict validator used for writes and generation. Invalid values are
  replaced with an empty editable field, and settings include a public recovery
  message directing the user to enter valid HTTP(S) URLs and save.
- Generation remains strict and still raises `PROVIDER_BASE_URL_INVALID` before
  client creation for malformed, userinfo-bearing, or query-bearing URLs.
- The output validator rejects empty-destination Markdown such as `[Earth]()`,
  HTML tags, comments, declarations, and processing-instruction markup while
  preserving normal single-line prompt prose.
- Both model configuration errors now have explicit 422 mappings and actionable
  public settings guidance; provider-private detail remains hidden.

## Regression coverage and TDD evidence

- The first focused red run failed six cases for reflected URL secrets, both
  generic model messages, `[Earth]()`, and raw HTML tags.
- A second red run proved processing-instruction markup was still accepted
  before adding its validator branch.
- A final boundary red run reproduced an oversized persisted URL as a settings
  500 and provider reflection before the shared validator gained its length
  limit.
- GET regression coverage asserts both base URL fields are empty, a recovery
  message is present, and the literal userinfo/query secret markers are absent
  from both settings and provider response bodies.
- Generation coverage asserts malformed, userinfo-bearing, and query-bearing
  persisted URLs all stop before provider client construction.
- The existing normal-prose acceptance regression remains green.

## Verification

- `uv run pytest -q test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py test/services/test_cloud_agent_ui.py`
  — 101 passed.
- `uv run ruff check app/controllers/v1/cloud_agent.py app/services/cloud_agent/thumbnail_prompt/settings.py app/services/cloud_agent/thumbnail_prompt/service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py test/services/cloud_agent/test_thumbnail_prompt_service.py`
  — passed.
- `uv run ruff format --check app/controllers/v1/cloud_agent.py app/services/cloud_agent/thumbnail_prompt/settings.py app/services/cloud_agent/thumbnail_prompt/service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py test/services/cloud_agent/test_thumbnail_prompt_service.py`
  — passed.
- `uv run --no-sync python -X utf8 -m coverage run -m pytest -q test`
  — 1,437 passed, 23 skipped, 4,359 subtests passed.
- `uv run --no-sync python -m coverage report`
  — 78% total coverage (70% minimum).
- `git diff --check` — passed.
