# Thumbnail Prompt — Review Handoff

Date: 2026-08-29 UTC

## Review objective

Perform an independent, read-only security and correctness review of the
Thumbnail Prompt feature before any merge or deployment. The feature must stay
fully isolated from the main video-production system.

Do **not** merge, deploy, restart services, edit production configuration,
modify jobs, or call paid/external providers during review.

## Workspace and Git state

- Production checkout: `/opt/VideosTurbo`
- Isolated feature worktree: `/opt/VideosTurbo/.worktrees/codex-thumbnail-prompt`
- Branch: `codex/thumbnail-prompt`
- Review base: `aaa8ab6e6db7cb1ca1eff1b3fc8a038d88c759df`
- Current committed HEAD: `139e8e14b9ad478e96344e537268f99efb7b848b`
- Full committed range: `aaa8ab6e6db7cb1ca1eff1b3fc8a038d88c759df..139e8e14b9ad478e96344e537268f99efb7b848b`

The worktree is intentionally **dirty**. An interrupted parser-fix iteration
left uncommitted changes in exactly these files:

- `app/services/cloud_agent/thumbnail_prompt/service.py`
- `test/services/cloud_agent/test_thumbnail_prompt_service.py`

Review both the committed range and the uncommitted diff. Do not discard,
overwrite, or commit the dirty changes until they have been independently
reviewed.

Useful commands:

```bash
cd /opt/VideosTurbo/.worktrees/codex-thumbnail-prompt
git status --short
git diff --stat aaa8ab6e6db7cb1ca1eff1b3fc8a038d88c759df..HEAD
git diff aaa8ab6e6db7cb1ca1eff1b3fc8a038d88c759df..HEAD
git diff
```

## Authoritative requirements

- Design specification:
  `docs/superpowers/specs/2026-08-29-thumbnail-prompt-design.md`
- Implementation plan:
  `docs/superpowers/plans/2026-08-29-thumbnail-prompt.md`

Current architecture intentionally supersedes the original plan where it
mentioned storing Thumbnail Prompt keys inside `config.app`:

1. Thumbnail Prompt owns a separate settings file at
   `storage/thumbnail_prompt/settings.toml`.
2. It must never read or write `config.toml`, `config.app`, `save_config`, the
   main runtime lock, existing LLM/Research/TTS settings, or their credentials.
3. On POSIX/Linux it uses the secure POSIX settings backend.
4. On unsupported platforms such as Windows, the main ASGI application must
   still import and start; only Thumbnail Prompt fails closed with the
   sanitized `THUMBNAIL_PROMPT_PLATFORM_UNSUPPORTED` error. No insecure
   fallback is allowed.
5. The feature must not mutate jobs, queues, databases, media, Google Flow,
   Canva, workers, or the main production workflow.

Functional behavior:

- The completed-video card has a `Prompt หน้าปก` action beside Delete.
- The server verifies the video is visible and completed.
- The server reads the complete saved `input/master_prompt.txt`; the browser
  cannot submit a master prompt, provider, or model.
- The complete video master prompt is analyzed together with the global
  Thumbnail Master Prompt.
- The globally selected dedicated provider is used: AIHubMix defaults to
  `gpt-5.6-sol`; OpenRouter defaults to `openai/gpt-5.6-sol`; both support a
  custom model ID.
- Exactly one plain, copyable, ready-to-use image prompt is returned. No image
  is generated and the generated prompt is not persisted.
- API keys are write-only/redacted. Provider errors and storage errors must not
  expose secrets or filesystem internals.
- Provider timeout phase budgets total 45 seconds, `max_retries=0`, and the UI
  caller timeout is 60 seconds.

## Security work already implemented

The committed feature includes tests and implementation for:

- Dedicated settings store and lock, independent of the main configuration.
- Atomic settings replacement, file and directory `fsync`, mode `0600`, owner,
  link-count, inode and path-identity checks.
- Cross-process locking and protection against lock, temp, leaf and ancestor
  replacement races.
- Safe corrupt-file recovery, bounded preservation, cleanup after partial
  writes/fsync/close failures, and protection against fd reuse/double-close.
- Symlink/hardlink and unsafe permission rejection.
- Immutable generation-settings snapshot resolved under the subsystem lock;
  network I/O begins after the lock is released.
- Bounded and identity-reverified master-prompt reading with owner, type,
  single-link and size checks.
- Separate corrupt-versus-unsafe storage status in API/UI.
- Cold simulated non-POSIX ASGI import/start smoke and fail-closed Thumbnail
  endpoints.
- Output limits, raw Unicode control rejection, Markdown/preamble rejection and
  explicit alternative-marker checks.

Review these claims from code and tests; do not assume they are correct merely
because they are listed here.

## Latest independent review status

At committed HEAD `139e8e1`, the latest reviewer reported no Critical issue but
two Important parser problems:

1. Numeric alternatives and numeric prose were ambiguous:
   - incorrectly accepted:
     `1:3D render of Earth;2:1960s collage of an eclipse`
   - incorrectly rejected:
     `Cinematic portrait; 1.8 aperture, 85-mm lens`
   - incorrectly rejected:
     `2026: futuristic city above the clouds`
2. Explicit markers still bypassed:
   - `Solar flare; Alternative—eclipse`
   - `Solar flare; Alternative — eclipse`
   - `Solar flare; Option #2:eclipse`
   - `A)first concept;B)second concept`

The uncommitted diff is the interrupted attempt to fix these findings. It:

- parses complete numeric tokens;
- treats pure digit-to-digit colon forms as ratios;
- treats `digit.period.digit` forms as decimals;
- treats four-digit values from 1900 through 2199 as year-like prose;
- adds en/em-dash textual delimiters, `#` option suffixes, and bare-letter
  option labels;
- adds a table-driven regression matrix.

This uncommitted attempt has **not** received independent review and must not be
considered ready merely because its targeted tests pass. In particular, review
the policy choices around year range, ratio component range, decimals,
letter-labelled prose, Unicode separators, and remaining false positives or
bypasses.

## Latest verification evidence

For committed HEAD `139e8e1`, the implementing agent reported:

- Full suite: `1565 passed, 23 skipped, 4359 subtests passed`
- Focused feature suite: `264 passed`
- Flow/Canva/workflow regressions: `244 passed`
- Ruff, formatting and diff checks passed

After interruption, on the current dirty tree, the coordinator freshly ran:

```text
pytest -q test/services/cloud_agent/test_thumbnail_prompt_service.py
126 passed in 1.92s

ruff check <two dirty files>
All checks passed

ruff format --check <two dirty files>
2 files already formatted

git diff --check
passed
```

No full suite has been run on the dirty parser patch.

Production `config.toml` fingerprint remained unchanged throughout the latest
full verification:

```text
sha256: 1d98345d0ac50437b5edc9a5c7d56c1c76769f1108bf11e369947017e6068932
size: 13832 bytes
inode: 4167775
mtime epoch: 1787987073
```

The ignored worktree `config.toml` is separate and must not be read, edited,
deleted, restored, or staged.

## Requested independent review

Please return findings grouped as Critical, Important and Minor, with exact
file/line references, impact, reproduction and recommended fix. Explicitly
review:

1. True isolation from main config and main workflow.
2. POSIX filesystem transaction security, cleanup and concurrency.
3. Unsupported-platform startup and fail-closed behavior.
4. Master-prompt path, inode, ownership, hardlink and size safety.
5. Credential redaction and sanitized error paths.
6. API authorization/visibility and absence of job/output persistence.
7. UI per-card busy/result/error/retry behavior.
8. Output contract, especially the current dirty parser patch.
9. Test isolation: no test may write production or ignored real config files,
   jobs, databases, or make paid network calls.
10. Regression risk to Google Flow, Canva, worker, TTS, Research and the main
    video pipeline.

Before running broad tests, statically verify that the test suite cannot touch
the main config. Keep the review read-only and do not merge or deploy.
