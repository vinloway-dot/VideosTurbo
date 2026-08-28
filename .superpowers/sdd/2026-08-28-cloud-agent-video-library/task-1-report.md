# Task 1 report: job store and storage primitives

## Changes

- Added `CloudJobStore.list_completed_final_candidates()`, filtering to `COMPLETED` jobs with `FINAL_VALIDATED` or `COMPLETED` checkpoints and ordering by `completed_at DESC, id DESC`.
- Added `CloudJobStore.delete_job()`, deleting exactly one record and raising `KeyError` when the ID is absent.
- Added safe final-video validation to `CloudJobStorage`, requiring the recorded path to resolve to the canonical job final file and to be an existing file under the storage root.
- Added artifact staging under `<root>/.deleting/<job>-<uuid>`, with job-ID/path boundary checks and protection for the reserved `.deleting` directory.
- Added validated restore and purge operations for staged directories; purge uses `shutil.rmtree` only for a direct child of the storage root's `.deleting` directory.
- Added focused tests covering ordering/filtering, deletion, canonical final-file validation, staging isolation, restoration, purge, and traversal rejection.

## RED/GREEN evidence

RED command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -v
```

Result: 2 failed. Failures were the expected `AttributeError`s for the missing `list_completed_final_candidates` and `stage_job_artifacts` methods.

GREEN command:

```text
uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -v
```

Result: 5 passed.

## Final verification

- `uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -v` — 5 passed.
- `uv run python -X utf8 -m pytest test/services/test_cloud_agent_controller.py -q` — 26 passed, 11 pre-existing dependency/deprecation warnings.
- `uv run ruff check app/services/cloud_agent/job_store.py app/services/cloud_agent/storage.py test/services/test_cloud_agent_video_library.py` — clean.
- `git diff --check` — clean.

## Files changed

- `app/services/cloud_agent/job_store.py`
- `app/services/cloud_agent/storage.py`
- `test/services/test_cloud_agent_video_library.py`

## Self-review and concerns

The storage operations derive all job paths through `_paths`, resolve paths before boundary comparisons, avoid `prepare()` during staging, and leave the original directory untouched if validation fails before rename. Restore refuses to overwrite an existing job directory, and purge rejects paths outside the dedicated staging directory. No provider, worker, session, Flow, Canva, or unrelated behavior was changed.

No task-specific concerns remain. The controller test suite emits only existing upstream deprecation warnings.

## Review fix follow-up

Addressed review findings C1 and M1.

- `CloudJobStorage` now validates the unresolved `<root>/.deleting` entry before every stage, restore, or purge operation. Symlinks and non-directory entries are rejected before resolving children, preventing staging or `rmtree` from escaping the storage root.
- Added a symlink-escape regression test that confirms both staging and purging reject a symlinked `.deleting` directory and that the external sentinel remains intact.
- Strengthened candidate ordering/filtering coverage with equal completion timestamps (asserting ID descending) and a completed job at a non-final checkpoint (asserting exclusion).

Fix verification:

- RED: the new symlink regression initially failed because staging did not reject the symlinked `.deleting` root.
- `uv run python -X utf8 -m pytest test/services/test_cloud_agent_video_library.py -v` — 6 passed.
- `uv run ruff check app/services/cloud_agent/job_store.py app/services/cloud_agent/storage.py test/services/test_cloud_agent_video_library.py` — clean.

Files changed in the fix commit:

- `app/services/cloud_agent/storage.py`
- `test/services/test_cloud_agent_video_library.py`
- `.superpowers/sdd/2026-08-28-cloud-agent-video-library/task-1-report.md`
