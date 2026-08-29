# Thumbnail prompt final fix 5

## Scope

- No production operation or production configuration change was performed.
- Fixed two final-review gaps in the thumbnail-prompt subsystem only.

## Root causes

1. The base-URL validator relied on `urlsplit` plus whitespace checks. Python
   accepts backslashes and embedded control characters in URL text, so those
   persisted values were returned through settings/provider GET responses and
   reached provider client construction during generation.
2. The output validator rejected HTML tags, comments, declarations, and
   processing instructions, but did not recognize CommonMark CDATA raw HTML.

## Changes

- The shared base-URL validator now rejects every backslash and every Unicode
  control character. Because settings reads and generation both use it,
  malformed persisted values are redacted from read responses and rejected
  before a provider client is created.
- The raw-HTML expression now rejects `<![CDATA[...]]>` output.

## Regression coverage and TDD evidence

- The focused red run failed exactly the CDATA case plus backslash/NUL base
  URLs in both generation and settings/provider responses.
- Read-response coverage uses the exact persisted value
  `https://example.invalid\\persisted-secret-marker/v1`, checks both responses
  return an empty base URL, and asserts the secret marker never appears.
- Generation coverage checks backslash and NUL persisted URLs fail before the
  client factory is called.
- Existing normal prompt-prose acceptance remains covered and passing.

## Verification

- `uv run pytest -q test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py`
  — 68 passed.
- `uv run ruff check app/services/cloud_agent/thumbnail_prompt/settings.py app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py`
  — passed.
- `uv run ruff format --check app/services/cloud_agent/thumbnail_prompt/settings.py app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py`
  — passed.
