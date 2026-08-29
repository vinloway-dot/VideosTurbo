# Thumbnail prompt validator final fix 3

## Scope

- No deployment or production operation was performed.
- Hardened the generated-thumbnail-prompt validator only.

## Root cause

The inline Markdown expression required nonempty link text, recognized only
HTTP(S) autolinks, and did not recognize reference-link definitions. As a
result, empty Markdown links/images, `mailto:` autolinks, and definitions such
as `[Earth]: https://example.invalid` reached callers as prompt prose.

## Change

- Reject inline links and images with empty or nonempty link text.
- Reject Markdown reference definitions.
- Reject URI-scheme and email-style autolinks, including `mailto:`.
- Retain the existing acceptance test for normal, single-line image prompt
  prose.

## Regression coverage

Added focused rejection coverage for exactly:

- `[](https://example.invalid)`
- `![](https://example.invalid/image.png)`
- `[Earth]: https://example.invalid`
- `<mailto:user@example.invalid>`

The test-first run failed for all four inputs before the validator change; the
focused suite then passed with 33 tests.

## Verification

- `uv run pytest test/services/cloud_agent/test_thumbnail_prompt_service.py -q`
  — 33 passed.
- `uv run ruff check app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_service.py`
  — passed.
- `uv run --no-sync python -X utf8 -m coverage run -m pytest -q test`
  followed by `uv run --no-sync python -m coverage report` — passed, 78% total
  coverage (70% minimum).
- `git diff --check` — passed.
