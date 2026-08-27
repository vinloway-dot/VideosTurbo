# Cloud Agent Research Script Verification

## Automated non-paid verification

- Route inventory regression: `uv run pytest test/services/cloud_agent/test_research_controller.py::test_research_route_inventory_uses_only_cloud_agent_prefix -q` -> PASS (`1 passed`)
- Research stale-provider regression and settings/controller readback checks: PASS (`3 passed, 23 deselected`)
- Research contract, settings, store, network, runtime, adapter, service, controller, and WebUI tests: PASS (`201 passed`)
- Existing Cloud Agent controller/WebUI regressions and full Cloud Agent non-paid regression matrix: PASS (`672 passed, 1 skipped`)
- Ruff: PASS (`uv run ruff check app webui test`)
- Lockfile check: PASS (`uv lock --check`)

## Live-operation scope

No OpenRouter or AIHubMix generation, TTS synthesis, Google Flow generation, Canva mutation, browser session, or paid retry was executed. All verification used local tests, fixtures, mocks, and static checks only.

## Remote CI and deployed smoke

- Remote CI run `33096470647`: FAIL on `Python 3.11 tests` and `Python 3.13 tests`; `Windows smoke tests` passed.
- Remote CI run `33097217417`: PASS on `Python 3.11 tests`, `Python 3.13 tests`, and `Windows smoke tests`.
- Remote CI run `33097740595`: PASS on `Python 3.11 tests`, `Python 3.13 tests`, and `Windows smoke tests`.
- Remote CI run `33098579563`: PASS on `Python 3.11 tests`, `Python 3.13 tests`, and `Windows smoke tests`.
- Deployed dependency sync: `uv sync --frozen` -> PASS (`Checked 124 packages in 3ms`)
- Service status after restarting `videosturbo-api` and `videosturbo-webui`: PASS (`videosturbo-api`, `videosturbo-webui`, and `videosturbo-worker` all reported `active`)
- Loopback health smoke: `GET /api/v1/cloud-agent/health` -> PASS (`status=200`, `enabled=true`, `worker_online=true`, `storage_writable=true`)
- Loopback provider catalog smoke: `GET /api/v1/cloud-agent/research/providers` -> PASS (`status=200`, providers `openrouter` and `aihubmix`, configured booleans only, both `false`)
- Loopback settings smoke before stale-provider fix: `GET /api/v1/cloud-agent/research/settings` -> FAIL CONTRACT (`status=200`, `provider="unsupported"`)
- Loopback settings smoke after stale-provider fix: `GET /api/v1/cloud-agent/research/settings` -> PASS (`status=200`, `provider="openrouter"`)
- Loopback WebUI smoke: `HEAD http://127.0.0.1:8501/` -> PASS (`HTTP/1.1 200 OK`)
- Loopback browser-port bind smoke: PASS (`127.0.0.1:6080`, `127.0.0.1:5900`, and IPv6 loopback `[::1]:5900` only; IPv6 loopback remained within the security intent)
