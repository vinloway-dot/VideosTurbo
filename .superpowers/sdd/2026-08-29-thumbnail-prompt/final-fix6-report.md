# Thumbnail prompt final fix 6

## Scope

- No production operation or production configuration change was performed.
- Fixed the three important final-review issues in the isolated
  `codex/thumbnail-prompt` worktree only.

## Root causes

1. `read_master_prompt()` resolved the complete job path before opening the
   `input` directory. A job symlink to another job under the same storage root
   therefore became an ordinary resolved path before `O_NOFOLLOW` was applied.
2. `ThumbnailPromptSettingsPayload` enforced the 2,048-character base-URL
   limit in Pydantic. FastAPI's global `RequestValidationError` handler returns
   Pydantic's `input` value, which exposed the submitted oversized URL text.
3. The inline-markdown expression allowed no `]` inside link labels. CommonMark
   labels containing an escaped closing bracket or nested label therefore
   bypassed the link/image rejection check.

## Changes

- Master-prompt reads now open the storage root, job directory, `input`
  directory, and `master_prompt.txt` in sequence. Every path component is
  opened descriptor-relative with `O_NOFOLLOW`, and the final descriptor must
  identify a regular file.
- Removed Pydantic base-URL length constraints from thumbnail settings models.
  Submitted URL text now reaches the thumbnail settings service, whose shared
  validator parses it, enforces the 2,048-character maximum, and raises the
  sanitized `PROVIDER_BASE_URL_INVALID` error.
- Made link/image label detection deliberately conservative by permitting
  closing brackets inside the candidate label before looking for an adjacent
  inline or reference destination. This rejects escaped and nested CommonMark
  labels while preserving ordinary bracketed prose without a destination.

## Regression coverage and TDD evidence

- Initial focused red run: 5 failed and 6 passed. Failures reproduced the
  same-root job symlink read, real-application oversized URL response, escaped
  link label, nested link label, and escaped image label.
- Focused green run: 11 passed.
- The storage regression creates `job-a` as a directory symlink to real
  `job-b`, verifies `job-a` cannot read `job-b`'s prompt, and confirms the
  target prompt remains unchanged.
- The URL regression uses `app.asgi.get_application()` so the real global
  validation handler is installed, then asserts the oversized sentinel is
  absent from the sanitized 422 response.
- Prompt regressions cover `[Earth\\]](...)`, `[Earth [orbit]](...)`, and
  `![Earth\\]](...)`. The acceptance test retains normal image-prompt prose
  containing `[viewed from orbit]` without a link destination.

## Verification

- `uv run ruff format <six changed source/test files>` — 2 files reformatted,
  4 files unchanged.
- `uv run ruff check <six changed source/test files>` — all checks passed.
- `uv run ruff format --check <six changed source/test files>` — all 6 files
  already formatted.
- `uv run pytest -q test/services/cloud_agent/test_storage.py test/services/cloud_agent/test_thumbnail_prompt_settings.py test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/test_cloud_agent_thumbnail_prompt_controller.py`
  — 103 passed.
- `uv run pytest -q` — 1,447 passed, 23 skipped, 12 warnings, and 4,359
  subtests passed.
