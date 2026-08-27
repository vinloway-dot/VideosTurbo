Task 1 report
=============

Summary
-------
- Added the shared research contract package at `app/services/cloud_agent/research/`.
- Added `research_draft_id` to `CloudJobCreate` with `exclude=True` so it never enters workflow payloads.
- Added research-specific config defaults without changing Standard/TTS defaults or touching protected backup files.

Files changed
-------------
- `app/config/config.py`
- `app/models/cloud_agent.py`
- `app/services/cloud_agent/research/__init__.py`
- `app/services/cloud_agent/research/errors.py`
- `app/services/cloud_agent/research/models.py`
- `test/services/cloud_agent/test_research_contracts.py`

Implementation notes
--------------------
- `ResearchDraftRequest` keeps URL handling intentionally permissive so later domain preflight can return typed `URL_REQUIRED` / `URL_INVALID` errors instead of generic Pydantic failures.
- `ResearchError` normalizes unknown codes to `RESEARCH_RESPONSE_INVALID`, exposes `code`, stores internal `detail`, and surfaces only the public Thai-safe message through `str(error)`.
- `models.py` stays independent from `errors.py`; `errors.py` uses a `TYPE_CHECKING` import for the optional `ResearchUsageAccounting` annotation to avoid runtime circular imports.
- Research defaults were added in a dedicated `RESEARCH_DEFAULTS` mapping and applied alongside existing `CLOUD_AGENT_DEFAULTS`.

TDD evidence
------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_contracts.py -q`
- Observed failure:

```text
==================================== ERRORS ====================================
____ ERROR collecting test/services/cloud_agent/test_research_contracts.py _____
E   ModuleNotFoundError: No module named 'app.services.cloud_agent.research'
=========================== short test summary info ============================
ERROR test/services/cloud_agent/test_research_contracts.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.49s
```

GREEN:
- Command: `uv run pytest test/services/cloud_agent/test_research_contracts.py -q`
- Output:

```text
...                                                                      [100%]
3 passed in 0.25s
```

Focused verification
--------------------
- Command: `uv run ruff check app/config/config.py app/models/cloud_agent.py app/services/cloud_agent/research test/services/cloud_agent/test_research_contracts.py`
- Output:

```text
All checks passed!
```

- Command: `uv run pytest test/services/test_config.py -k cloud_agent -q`
- Output:

```text
..                                                                       [100%]
2 passed, 19 deselected in 0.12s
```

Notes
-----
- Preserved existing untracked config backup/save artifacts.
