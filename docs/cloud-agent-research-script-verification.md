# Cloud Agent Research Script Verification

## Automated non-paid verification

- Route inventory regression: `uv run pytest test/services/cloud_agent/test_research_controller.py::test_research_route_inventory_uses_only_cloud_agent_prefix -q` -> PASS (`1 passed`)
- Research contract, settings, store, runtime, adapter, service, controller, and WebUI tests: PASS (`150 passed`)
- Existing Cloud Agent controller/WebUI plus full non-paid regression matrix: PASS (`621 passed, 1 skipped`)
- Ruff: PASS (`uv run ruff check app webui test`)
- Lockfile check: PASS (`uv lock --check`)

## Live-operation scope

No OpenRouter or AIHubMix generation, TTS synthesis, Google Flow generation, Canva mutation, browser session, or paid retry was executed. All verification used local tests, fixtures, mocks, and static checks only.
