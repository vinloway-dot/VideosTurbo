Task 9 report
=============

Summary
-------
- Preserved every existing untracked `config.toml.backup-*` and `config.toml.save*` file untouched.
- Pushed `feature/cloud-video-agent`, diagnosed the failing Draft PR CI run, and fixed the issue with a minimal controller-test change.
- Synced frozen dependencies, restarted only `videosturbo-api` and `videosturbo-webui`, confirmed the required services stayed active, and completed the safe loopback smoke checks.
- Appended sanitized CI and deployment-smoke evidence to `docs/cloud-agent-research-script-verification.md`.

Protected files
---------------
- Left these untracked files untouched throughout the task:
  - `config.toml.backup-20260825T181857Z`
  - `config.toml.backup-20260826T055642Z`
  - `config.toml.backup-20260826T160959Z-before-aihubmix`
  - `config.toml.backup-20260826T165339Z-before-flow-locale`
  - `config.toml.backup-20260827T015730Z-before-canva-design-replace`
  - `config.toml.save`
  - `config.toml.save.1`
  - `config.toml.save.2`

Execution notes
---------------
- Initial push advanced `origin/feature/cloud-video-agent` to commit `da6100e`.
- Draft PR `#4` CI run `33096470647` failed on both Linux test jobs with the same controller test:
  - `test/services/cloud_agent/test_research_controller.py::test_standard_draft_does_not_resolve_or_call_research_service`
- Root cause investigation:
  - The failing test only forbade the research service.
  - The `/api/v1/cloud-agent/draft` route still always builds a six-clip plan, and that helper uses the normal LLM response path.
  - In CI, that leaked live LLM configuration into a controller test that was supposed to be hermetic, producing the `500` failure.
  - Local reproduction supported this diagnosis: the isolated test and the full local suite both passed, which showed the production code path was sound and the CI breakage came from the test's unintended dependency on real LLM setup.
- Minimal fix:
  - Updated `test_standard_draft_does_not_resolve_or_call_research_service` to:
    - fail immediately if `generate_script` is called for a provided script
    - stub `generate_six_clip_plan` with a deterministic `empty_six_clip_plan(...)`
  - No production code changed.
- Fix commit:
  - `709cfc1` — `test: hermeticize standard draft research controller`
- After pushing `709cfc1`, Draft PR `#4` CI run `33097217417` passed on all three jobs.

Verification evidence
---------------------
- `uv run --no-sync pytest -q test/services/cloud_agent/test_research_controller.py -k standard_draft_does_not_resolve_or_call_research_service`
  - Result: PASS (`1 passed, 18 deselected, 11 warnings in 3.49s`)
- `uv run --no-sync pytest -q test`
  - Result: PASS (`1237 passed, 23 skipped, 12 warnings, 4359 subtests passed in 39.15s`)
- `uv sync --frozen`
  - Result: PASS (`Checked 124 packages in 3ms`)
- `systemctl is-active videosturbo-api videosturbo-webui videosturbo-worker`
  - Result: PASS (`active`, `active`, `active`)
- `curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/health | jq '{status, enabled: .data.enabled, worker_online: .data.worker_online, storage_writable: .data.storage_writable}'`
  - Result: PASS (`status=200`, `enabled=true`, `worker_online=true`, `storage_writable=true`)
- `curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/research/providers | jq '{status, providers: [.data[] | {id, api_key_configured}]}'`
  - Result: PASS (`status=200`, `openrouter=false`, `aihubmix=false`)
- `curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/research/settings | jq '{status, provider: .data.provider}'`
  - Result: PASS (`status=200`, `provider="unsupported"`)
- `curl -fsSI http://127.0.0.1:8501/ | head -n 1`
  - Result: PASS (`HTTP/1.1 200 OK`)
- `ss -ltn | awk '$4 ~ /:(5900|6080)$/ {print $4}'`
  - Result: PASS (`127.0.0.1:6080`, `127.0.0.1:5900`, `[::1]:5900`)

Outstanding concerns
--------------------
- `GET /api/v1/cloud-agent/research/settings` returned `provider="unsupported"`. This did not block the safe smoke because the endpoint still returned `200`, but it is worth reviewing before any future live research attempt.
- VNC port `5900` is also listening on IPv6 loopback (`[::1]`) in addition to `127.0.0.1`. This remains loopback-only, so it stayed within the task's safe-boundary intent.

Final CI confirmation
---------------------
- After pushing docs commit `b853756`, Draft PR `#4` CI run `33097492762` passed on all three jobs while the PR remained Draft.

Fix round 1
-----------

Reviewer finding addressed
--------------------------
- The deployed `GET /api/v1/cloud-agent/research/settings` response surfaced `provider="unsupported"` from stale config, which violated the allowed `openrouter`/`aihubmix` contract and made the prior verification note incorrect.

Root cause
----------
- `_research_settings_data()` in `app/controllers/v1/cloud_agent.py` read `cloud_agent_research_default_provider` directly from `config.app` instead of validating or normalizing it through `ResearchSettingsService`.
- The write path already validated providers, so only older invalid persisted values could escape into the read response.

Fix
---
- Added `ResearchSettingsService.get_configured_provider_id()` to normalize stale or blank configured providers to `openrouter` and persist the healed value once via `config.save_config()`.
- Updated the controller settings read/update response helper to use the service-normalized provider rather than echoing the raw config value.
- Added regressions covering:
  - invalid configured provider normalizes to `openrouter`
  - valid configured provider `aihubmix` is preserved without resaving
  - the FastAPI `GET /api/v1/cloud-agent/research/settings` route heals stale config and returns `openrouter`

Fix round 1 commit and CI
-------------------------
- Commit `7c0c681` — `fix: normalize stale research provider defaults`
- Draft PR `#4` CI run `33098579563` passed on all three jobs for commit `7c0c681`.

Fix round 1 verification evidence
---------------------------------
- `uv run --no-sync pytest -q test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_controller.py -k 'configured_default_provider or stale_invalid_default_provider'`
  - Result: PASS (`3 passed, 23 deselected, 11 warnings in 3.97s`)
- `uv run --no-sync pytest -q test/services/cloud_agent/test_research_contracts.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py`
  - Result: PASS (`201 passed, 11 warnings in 9.34s`)
- `uv run --no-sync pytest -q test/services/cloud_agent test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py test/services/test_llm.py test/services/test_six_clip_plan.py`
  - Result: PASS (`672 passed, 1 skipped, 11 warnings in 18.87s`)
- `uv run ruff check app webui test`
  - Result: PASS (`All checks passed!`)
- `uv lock --check`
  - Result: PASS (`Resolved 130 packages in 2ms`)

Fix round 1 deploy smoke
------------------------
- Restarted only `videosturbo-api` and `videosturbo-webui`.
- `systemctl is-active videosturbo-api videosturbo-webui videosturbo-worker`
  - Result: PASS (`active`, `active`, `active`)
- `curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/research/settings | jq '{status, provider: .data.provider}'`
  - Result: PASS (`status=200`, `provider="openrouter"`)
- `curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/research/providers | jq '{status, providers: [.data[] | {id, api_key_configured}]}'`
  - Result: PASS (`status=200`, `openrouter=false`, `aihubmix=false`)
- `curl -fsS http://127.0.0.1:8080/api/v1/cloud-agent/health | jq '{status, enabled: .data.enabled, worker_online: .data.worker_online, storage_writable: .data.storage_writable}'`
  - Result: PASS (`status=200`, `enabled=true`, `worker_online=true`, `storage_writable=true`)
- `curl -fsSI http://127.0.0.1:8501/ | head -n 1`
  - Result: PASS (`HTTP/1.1 200 OK`)
- `ss -ltn | awk '$4 ~ /:(5900|6080)$/ {print $4}'`
  - Result: PASS (`127.0.0.1:6080`, `127.0.0.1:5900`, `[::1]:5900`)
  - Note: `[::1]:5900` is IPv6 loopback only and remained within the task's security intent; no VNC configuration changes were made.
