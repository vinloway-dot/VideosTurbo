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
