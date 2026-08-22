import importlib


EXPECTED_CLOUD_AGENT_PATHS = {
    ("GET", "/api/v1/cloud-agent/health"),
    ("POST", "/api/v1/cloud-agent/jobs"),
    ("GET", "/api/v1/cloud-agent/jobs"),
    ("GET", "/api/v1/cloud-agent/jobs/{job_id}"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/pause"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/resume"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/cancel"),
    ("GET", "/api/v1/cloud-agent/jobs/{job_id}/final"),
    ("POST", "/api/v1/cloud-agent/sessions/check"),
    ("POST", "/api/v1/cloud-agent/sessions/google-flow/check"),
    ("POST", "/api/v1/cloud-agent/sessions/canva/check"),
    ("POST", "/api/v1/cloud-agent/sessions/google-flow/repair"),
    ("POST", "/api/v1/cloud-agent/sessions/canva/repair"),
    ("GET", "/api/v1/cloud-agent/sessions/{service}/open-browser"),
}


def test_cloud_agent_router_contract_is_registered_on_existing_root_router():
    cloud_agent = importlib.import_module("app.controllers.v1.cloud_agent")
    app_router = importlib.import_module("app.router")

    registered = set()
    for route in app_router.root_api_router.routes:
        for method in getattr(route, "methods", set()):
            if str(route.path).startswith("/api/v1/cloud-agent"):
                registered.add((method, route.path))

    assert cloud_agent.router.prefix == "/api/v1"
    assert registered == EXPECTED_CLOUD_AGENT_PATHS
