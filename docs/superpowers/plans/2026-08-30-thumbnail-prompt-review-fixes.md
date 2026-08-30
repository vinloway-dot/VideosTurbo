# Thumbnail Prompt Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every Critical/Important/Minor finding from the 2026-08-30 independent Thumbnail Prompt review without merging, deploying, touching production configuration/jobs, or calling paid/external providers.

**Architecture:** Keep Thumbnail Prompt isolated from the main LLM/config/video workflow. Harden parser validation at the structural-marker layer, enforce a true provider wall-clock deadline outside HTTPX phase timeouts, strengthen server-only master-prompt filesystem permissions, validate non-leaf settings ancestors against writable shared directories, and add repository verification checks to CI. All behavior changes follow RED → GREEN TDD on the existing draft review PR.

**Tech Stack:** Python 3.11/3.13, pytest, httpx/openai SDK, POSIX file descriptors, GitHub Actions, Ruff.

**Spec:** `docs/handoffs/2026-08-29-thumbnail-prompt-review-handoff.md`

## Global Constraints

- Work only on `codex/thumbnail-prompt`.
- Do not merge or deploy.
- Do not restart services or touch production.
- Do not read/write the main `config.toml` / `config.app` namespace.
- Do not mutate production jobs, database, queue, media, Google Flow, Canva, worker, TTS, Research, or the main video workflow.
- Do not call a paid/external provider; tests must use fake/local behavior only.
- `storage/thumbnail_prompt/settings.toml` remains the only Thumbnail Prompt settings store.
- UI timeout remains 60 seconds; server generation must have a hard wall-clock deadline below that value.

---

### Task 1: Parser bypasses and numeric/Unicode false positives

**Files:**
- Modify: `test/services/cloud_agent/test_thumbnail_prompt_parser_regressions.py`
- Modify: `test/services/cloud_agent/test_thumbnail_prompt_service.py`
- Modify: `app/services/cloud_agent/thumbnail_prompt/service.py`

**Interfaces:**
- Consumes: `_has_alternative_marker(text: str) -> bool`, `ThumbnailPromptService._normalize_completion(response) -> str`.
- Produces: structural-marker detection that rejects Unicode-confusable A/B labels and standalone Alternative/Option separators while accepting safe years, ratios, decimals, fractions, grouped numbers and safe Unicode numeric punctuation.

- [ ] **Step 1: Write failing parser regression tests**

Add rejection cases for Greek/Cyrillic confusable single-letter labels and standalone `Alternative`/`Option` labels followed by comma, semicolon, slash or ellipsis. Add acceptance cases for years outside 1900–2199, U+2236 ratio punctuation, Unicode fractions and safe grouped-number separators.

- [ ] **Step 2: Run focused parser tests and verify RED**

Run:

```bash
pytest -q test/services/cloud_agent/test_thumbnail_prompt_parser_regressions.py test/services/cloud_agent/test_thumbnail_prompt_service.py
```

Expected: new cases fail for the reviewed reasons, while existing cases remain unchanged.

- [ ] **Step 3: Implement the minimal parser fix**

Use structural Unicode-confusable folding only for single-letter option-label positions rather than normal prose. Replace the fixed year range heuristic with contextual sequential-marker evidence. Normalize only approved numeric punctuation for validation (ratio/fraction/grouping) without rewriting returned user-visible prompt text. Treat standalone known option labels as alternatives only when followed by an explicit structural separator.

- [ ] **Step 4: Re-run focused parser tests and verify GREEN**

Expected: all parser/service tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_parser_regressions.py test/services/cloud_agent/test_thumbnail_prompt_service.py
git commit -m "fix: harden thumbnail prompt parser boundaries"
```

### Task 2: True server wall-clock provider deadline

**Files:**
- Modify: `test/services/cloud_agent/test_thumbnail_prompt_service.py`
- Modify: `app/services/cloud_agent/thumbnail_prompt/service.py`

**Interfaces:**
- Consumes: `ThumbnailPromptService.generate_for_job(job_id: str) -> str`.
- Produces: generation that cannot remain active beyond a server hard deadline lower than the 60-second UI timeout; existing HTTPX connect/read/write/pool phase timeouts and `max_retries=0` remain defense-in-depth.

- [ ] **Step 1: Write failing deadline tests**

Use a fake blocking completion callable plus an injectable monotonic/deadline execution seam; no network provider. Assert a server deadline constant below 60 seconds and assert timeout maps to sanitized `PROVIDER_TIMEOUT`.

- [ ] **Step 2: Run focused service test and verify RED**

```bash
pytest -q test/services/cloud_agent/test_thumbnail_prompt_service.py
```

- [ ] **Step 3: Implement the minimal deadline mechanism**

Run the synchronous provider call behind a bounded execution primitive that raises a private timeout when the hard deadline expires. Do not wait for executor shutdown after timeout; cancel queued work where possible. Keep HTTPX phase timeouts and `max_retries=0`. Do not expose provider details.

- [ ] **Step 4: Re-run focused service tests and verify GREEN**

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_service.py
git commit -m "fix: enforce thumbnail provider deadline"
```

### Task 3: Master-prompt file and directory permissions

**Files:**
- Modify: `test/services/cloud_agent/test_storage.py`
- Modify: `app/services/cloud_agent/storage.py`

**Interfaces:**
- Consumes: `CloudJobStorage.write_inputs(...)`, `CloudJobStorage.read_master_prompt(job_id)`.
- Produces: `master_prompt.txt` created as private mode `0600` on POSIX; reads reject unsafe group/world-writable prompt files and unsafe writable job/input directories while preserving symlink/hardlink/owner/inode/size defenses.

- [ ] **Step 1: Write failing POSIX filesystem tests**

Add tests for `master_prompt.txt` creation mode, mode `0666`, group/world-writable job/input directories, and revalidation after permission mutation. Skip permission-specific assertions when POSIX semantics are unavailable.

- [ ] **Step 2: Run storage tests and verify RED**

```bash
pytest -q test/services/cloud_agent/test_storage.py
```

- [ ] **Step 3: Implement private write + safe directory/file checks**

Use `os.open(..., O_CREAT|O_TRUNC|O_WRONLY|O_NOFOLLOW, 0o600)` for the master prompt on POSIX and `fchmod(0600)` before writing. Reject group/world-writable master-prompt files and the job/input directory components used by the server-only trust boundary. Preserve the current descriptor-retention and identity revalidation strategy.

- [ ] **Step 4: Re-run storage tests and verify GREEN**

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/storage.py test/services/cloud_agent/test_storage.py
git commit -m "fix: secure saved thumbnail master prompts"
```

### Task 4: Dedicated settings ancestor safety

**Files:**
- Modify: `test/services/cloud_agent/test_thumbnail_prompt_settings.py`
- Modify: `app/services/cloud_agent/thumbnail_prompt/_settings_posix.py`

**Interfaces:**
- Consumes: `ThumbnailPromptSettingsService` POSIX directory traversal.
- Produces: non-leaf ancestors must be real directories with stable identity/link count and must not be group/world writable; ownership may legitimately differ for system ancestors such as `/` and `/opt`.

- [ ] **Step 1: Write failing unsafe-ancestor tests**

Construct a dedicated settings path beneath an intermediate directory changed to mode `0777`; assert reads/writes fail closed as unsafe. Preserve valid root-owned/non-writable ancestors.

- [ ] **Step 2: Run settings tests and verify RED**

```bash
pytest -q test/services/cloud_agent/test_thumbnail_prompt_settings.py
```

- [ ] **Step 3: Implement safe-ancestor validation**

Add a non-leaf ancestor validator requiring directory type, link count >= 2 and no group/world write bits. Use it both when opening and when revalidating retained ancestors.

- [ ] **Step 4: Re-run settings tests and verify GREEN**

- [ ] **Step 5: Commit**

```bash
git add app/services/cloud_agent/thumbnail_prompt/_settings_posix.py test/services/cloud_agent/test_thumbnail_prompt_settings.py
git commit -m "fix: reject writable thumbnail settings ancestors"
```

### Task 5: CI formatting and whitespace verification

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: Linux Python 3.11 CI checks `ruff format --check` and `git diff --check` before the full test suite.

- [ ] **Step 1: Add CI verification steps**

Add, on the Python 3.11 matrix leg:

```bash
uv run --no-sync ruff format --check app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_parser_regressions.py
git diff --check
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: verify thumbnail formatting and whitespace"
```

### Task 6: Final verification and handoff update

**Files:**
- Modify: `docs/handoffs/2026-08-29-thumbnail-prompt-review-handoff.md`

- [ ] **Step 1: Run required focused verification**

```bash
ruff check .
ruff format --check app/services/cloud_agent/thumbnail_prompt/service.py test/services/cloud_agent/test_thumbnail_prompt_parser_regressions.py
git diff --check
pytest -q test/services/cloud_agent/test_thumbnail_prompt_service.py test/services/cloud_agent/test_thumbnail_prompt_parser_regressions.py test/services/cloud_agent/test_thumbnail_prompt_settings.py test/services/test_cloud_agent_thumbnail_prompt_controller.py test/services/test_asgi.py
pytest -q
```

- [ ] **Step 2: Verify test isolation**

Confirm no paid provider calls and no production config/database/job writes. Verify the main `config.toml` is not referenced by the changed tests.

- [ ] **Step 3: Review final diff and graph**

Confirm only intended Thumbnail Prompt/security/test/CI/docs files changed from the starting SHA and that `main` was not merged or modified.

- [ ] **Step 4: Update handoff with exact final SHA and CI/test evidence**

- [ ] **Step 5: Stop before merge/deploy**

Report the branch SHA and verification results for independent review/approval. Do not merge or deploy.
