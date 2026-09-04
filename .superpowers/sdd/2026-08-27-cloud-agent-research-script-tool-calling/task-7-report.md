Task 7 report
=============

Summary
-------
- Added explicit `Standard Script` and `Research Script` modes to the Streamlit Cloud Agent thin client.
- Preserved the existing Standard Script generation request shape and shared Script Editor handoff.
- Added FastAPI-only Research draft creation, settings, API-key, source, and accounting handling in the WebUI.

Files changed
-------------
- `webui/cloud_agent.py`
- `test/services/test_cloud_agent_webui.py`

Implementation notes
--------------------
- Added FastAPI-backed Research helpers for provider metadata, settings load/save verification, API-key updates/removal, research draft generation, safe error extraction, and optional `research_draft_id` Start payload merging.
- Added stale Research provenance clearing in `_store_draft()`, plus `_store_refreshed_draft()` so unchanged refreshed Research scripts retain provenance while edited scripts clear it.
- Kept `Refresh Draft` below the shared Script Editor so both Standard and Research flows hand off through the same editor.
- Research accounting and source rendering use only the sanitized API response shape and never read SQLite, browser state, or provider adapters directly.

TDD evidence
------------
RED:
- Command: `uv run pytest /opt/VideosTurbo/test/services/test_cloud_agent_webui.py -q`
- Output:

```text
.................FFFFFFFFF                                          [100%]
...
E       AttributeError: module 'webui.cloud_agent' has no attribute '_research_error_data'
...
E       TypeError: _start_job() got an unexpected keyword argument 'research_draft_id'
...
9 failed, 22 passed in 1.12s
```

GREEN / focused verification:
- Command: `uv run pytest /opt/VideosTurbo/test/services/test_cloud_agent_webui.py /opt/VideosTurbo/test/services/cloud_agent/test_research_controller.py -q && uv run ruff check /opt/VideosTurbo/webui/cloud_agent.py /opt/VideosTurbo/test/services/test_cloud_agent_webui.py`
- Output:

```text
............................................                         [100%]
46 passed, 11 warnings in 33.24s
All checks passed!
```

Notes
-----
- The passing verification still emits pre-existing FastAPI/Starlette and Pydantic deprecation warnings outside Task 7.
- Existing untracked `config.toml.backup-*` and `config.toml.save*` files were preserved untouched.

Fix round 1
-----------

Reviewer findings addressed
---------------------------
- IMPORTANT: The `Start` button path now forwards the stored `cloud_agent_research_draft_id` from session state into `_start_job()`, so Research-backed scripts preserve backend validation and job-link provenance while Standard mode still omits the field when blank.
- IMPORTANT: Research Provider, Research Settings, Research API Key, Source URLs, and Research accounting/source rendering are now gated under `Research Script` mode only, so Standard mode keeps its original visible surface and cannot trigger Research settings writes from rendered controls.

Fix round 1 TDD evidence
------------------------
RED:
- Command: `uv run pytest /opt/VideosTurbo/test/services/test_cloud_agent_webui.py -q -k "start_button_forwards_stored_research_draft_id or standard_mode_hides_research_only_controls"`
- Output:

```text
FF                                                                       [100%]
...
E       AssertionError: Voice is required before starting the job.
...
E       AssertionError: assert 'Research Provider' not in ['Language', 'Research Provider', 'TTS Provider', 'Voice']
...
2 failed, 31 deselected in 0.85s
```

GREEN / verification:
- Command: `uv run pytest /opt/VideosTurbo/test/services/test_cloud_agent_webui.py /opt/VideosTurbo/test/services/cloud_agent/test_research_controller.py -q && uv run ruff check /opt/VideosTurbo/webui/cloud_agent.py /opt/VideosTurbo/test/services/test_cloud_agent_webui.py`
- Output:

```text
..............................................                           [100%]
46 passed, 11 warnings in 26.18s
All checks passed!
```
