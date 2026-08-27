Task 2 report
=============

Summary
-------
- Added a write-only `ResearchSettingsService` for the `openrouter` and `aihubmix` research providers.
- Added a durable `ResearchDraftStore` with separate SQLite tables for draft provenance, source provenance, and draft/job associations in the configured Cloud Agent database.
- Added factory builders for research settings and research draft storage, plus focused tests for the redaction, rollback, script-hash validation, and idempotent job-link behavior.

Files changed
-------------
- `app/services/cloud_agent/factory.py`
- `app/services/cloud_agent/research/settings.py`
- `app/services/cloud_agent/research/store.py`
- `test/services/cloud_agent/test_research_settings.py`
- `test/services/cloud_agent/test_research_store.py`

Implementation notes
--------------------
- `ResearchSettingsService` uses `config.runtime_config_lock()` plus `config.save_config()` for every real secret mutation, ignores blank secret writes so the previous key survives, and requires an explicit `confirmed=True` to remove a key.
- `ResearchProviderMetadata` is intentionally write-only: it only reports `id`, `label`, and `api_key_configured`, so the stored secret never appears in serialized API metadata.
- `ResearchDraftStore` stores only script hashes, source hashes, URLs, titles, usage totals, cost, timestamps, evidence mode, and prompt fingerprints. It never persists source bodies or any secret/provider payload fields.
- `save_success()` wraps the draft row plus all source rows in one `BEGIN IMMEDIATE` transaction and rolls the whole write back if any insert fails.
- `link_job()` uses `INSERT OR IGNORE` on a `(research_draft_id, job_id)` primary key so duplicate Start associations are idempotent while still allowing one draft to fan out to multiple jobs.
- `sha256_text()` trims the incoming text before hashing so draft validation matches the existing trimmed-script behavior expected by the Cloud Agent flow.

TDD evidence
------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py -q`
- Output:

```text
==================================== ERRORS ====================================
_____ ERROR collecting test/services/cloud_agent/test_research_settings.py _____
ImportError while importing test module '/opt/VideosTurbo/test/services/cloud_agent/test_research_settings.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/home/linuxuser/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
test/services/cloud_agent/test_research_settings.py:6: in <module>
    from app.services.cloud_agent.research.settings import ResearchSettingsService
E   ModuleNotFoundError: No module named 'app.services.cloud_agent.research.settings'
------------------------------- Captured stderr --------------------------------
2026-08-27 12:08:48.804 | INFO     | app.config.config:load_config:518 - load config from file: /opt/VideosTurbo/config.toml
2026-08-27 12:08:48.812 | INFO     | app.config.config:<module>:634 - MoneyPrinterTurbo v1.3.4
______ ERROR collecting test/services/cloud_agent/test_research_store.py _______
ImportError while importing test module '/opt/VideosTurbo/test/services/cloud_agent/test_research_store.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/home/linuxuser/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
test/services/cloud_agent/test_research_store.py:9: in <module>
    from app.services.cloud_agent.research.store import (
E   ModuleNotFoundError: No module named 'app.services.cloud_agent.research.store'
=========================== short test summary info ============================
ERROR test/services/cloud_agent/test_research_settings.py
ERROR test/services/cloud_agent/test_research_store.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
2 errors in 2.27s
```

GREEN:
- Command: `uv run pytest test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py -q`
- Output:

```text
.......                                                                  [100%]
7 passed in 2.33s
```

Focused verification
--------------------
- Command: `uv run pytest test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py -q`
- Output:

```text
.......                                                                  [100%]
7 passed in 2.58s
```

- Command: `uv run pytest test/services/cloud_agent/test_research_contracts.py test/services/cloud_agent/test_defaults.py test/services/cloud_agent/test_tts_settings.py -q`
- Output:

```text
.............                                                            [100%]
13 passed in 2.49s
```

- Command: `uv run ruff check app/services/cloud_agent/factory.py app/services/cloud_agent/research test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py`
- Output:

```text
All checks passed!
```

Notes
-----
- Preserved the existing untracked `config.toml.backup-*` and `config.toml.save*` artifacts.
- No additional concerns at handoff.
