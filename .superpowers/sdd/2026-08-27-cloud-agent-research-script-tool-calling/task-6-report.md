Task 6 report
=============

Summary
-------
- Added Research routes directly onto the existing `app/controllers/v1/cloud_agent.py` router for provider metadata, non-secret settings, write-only API-key operations, draft creation, and persisted draft lookup.
- Added safe Research error translation in the controller so typed error codes stay in `data.code`, Thai public messages stay in `message`, and only sanitized accounting is exposed.
- Extended `POST /api/v1/cloud-agent/jobs` so an optional `research_draft_id` is hash-validated before job creation, then linked before queueing; a link failure now flips the still-draft job to `FAILED` with `RESEARCH_DRAFT_ASSOCIATION_FAILED` and never queues it.

Files changed
-------------
- `app/controllers/v1/cloud_agent.py`
- `test/services/test_cloud_agent_controller.py`
- `test/services/cloud_agent/test_research_controller.py`

Implementation notes
--------------------
- The controller now builds Research dependencies through the existing factory layer only; Standard draft generation, CloudJob storage shape, and worker behavior were left unchanged.
- `GET`/`PUT` Research settings and provider-key routes operate only on `config.app` plus `ResearchSettingsService`; they never invoke Research provider adapters or any paid/browser work.
- The controller-local settings payload maps UI-safe field names (`provider`, `openrouter_model`, `openrouter_custom_model_id`, `aihubmix_model`, `aihubmix_custom_model_id`, `custom_system_prompt`) onto the existing persisted config keys.
- Research draft failures now use the existing global `HttpException`/`utils.get_response` envelope unchanged, with controller-local status mapping for authentication, timeout, fetch, and validation failures.
- Research draft lookup returns only persisted provenance metadata from `ResearchDraftStore`; source bodies and secrets remain excluded by the existing store model.

TDD evidence
------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py -q`
- Output:

```text
FFFFFFFFFFF.........................                                     [100%]
...
E       AttributeError: module 'app.controllers.v1.cloud_agent' has no attribute 'get_research_service'
...
E       AssertionError: assert registered == EXPECTED_CLOUD_AGENT_PATHS
...
11 failed, 25 passed, 11 warnings in 6.11s
```

GREEN / focused verification:
- Command: `uv run pytest test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py -q && uv run ruff check app/controllers/v1/cloud_agent.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py`
- Output:

```text
...............................                                     [100%]
36 passed, 11 warnings in 30.01s
All checks passed!
```

Notes
-----
- The passing test run still emits pre-existing Pydantic deprecation warnings from `app/models/schema.py` and a Starlette `TestClient` deprecation warning; Task 6 did not change those areas.
- Existing untracked `config.toml.backup-*` and `config.toml.save*` files were preserved untouched.

Fix round 1
-----------

Reviewer findings addressed
---------------------------
- IMPORTANT: `PUT /cloud-agent/research/providers/{provider_id}/api-key` now accepts an unvalidated JSON body and performs internal typed validation, so oversized or malformed API-key requests are converted into the safe Research envelope instead of falling through FastAPI's generic validation response with echoed `input`.
- MINOR: `PUT /cloud-agent/research/settings` now validates the selected provider through `ResearchSettingsService` before any config write, so unsupported providers are rejected and the stored default provider is left unchanged.

Fix round 1 TDD evidence
------------------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_controller.py -q`
- Output:

```text
.....FF                                                             [100%]
...
E       KeyError: 'message'
...
E       assert 200 == 422
...
2 failed, 10 passed, 11 warnings in 23.94s
```

GREEN / verification:
- Command: `uv run pytest test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py -q && uv run ruff check app/controllers/v1/cloud_agent.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py`
- Output:

```text
.................................                                   [100%]
38 passed, 11 warnings in 27.52s
All checks passed!
```

Fix round 2
-----------

Reviewer finding addressed
--------------------------
- IMPORTANT: `PUT /cloud-agent/research/providers/{provider_id}/api-key` now bypasses FastAPI body-model parsing entirely and parses the raw request body inside the endpoint, so syntactically malformed JSON, non-object JSON, and invalid API-key payloads all return the same safe Research envelope without `detail`, echoed `input`, or raw secret text.

Fix round 2 TDD evidence
------------------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_controller.py -q`
- Output:

```text
......F.                                                            [100%]
...
E       KeyError: 'message'
...
1 failed, 12 passed, 11 warnings in 28.69s
```

GREEN / verification:
- Command: `uv run pytest test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py -q && uv run ruff check app/controllers/v1/cloud_agent.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py`
- Output:

```text
...........................                                  [100%]
39 passed, 11 warnings in 26.46s
All checks passed!
```
