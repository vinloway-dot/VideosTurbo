import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import config
from app.models.cloud_agent import CloudJobCreate, CloudJobStatus
from app.models.exception import HttpException
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.models import (
    ResearchDraftRequest,
    ResearchUsageAccounting,
)
from app.services.cloud_agent.research.service import (
    ResearchAccounting,
    ResearchDraftResponse,
    ResearchDraftSource,
)
from app.services.cloud_agent.research.settings import ResearchSettingsService
from app.services.cloud_agent.research.store import (
    ResearchDraftStore,
    ResearchSourceDraft,
    SuccessfulResearchDraft,
    sha256_text,
)
from app.services.cloud_agent.storage import CloudJobStorage


def _cloud_agent_controller():
    return importlib.import_module("app.controllers.v1.cloud_agent")


def registered_cloud_agent_routes() -> set[str]:
    app_router = importlib.import_module("app.router")
    registered = set()
    for route in app_router.root_api_router.routes:
        path = str(route.path)
        if not path.startswith("/api/v1/cloud-agent"):
            continue
        for method in getattr(route, "methods", set()):
            registered.add(f"{method} {path}")
    return registered


def research_payload() -> dict:
    return {
        "subject": "Research-backed draft",
        "language": "English",
        "target_words": 130,
        "provider": "openrouter",
        "model_choice": "openai/gpt-5.6-sol-pro",
        "custom_model_id": "",
        "source_urls": ["https://example.com/article"],
        "custom_system_prompt": "Use a careful documentary tone.",
    }


def standard_draft_payload() -> dict:
    return {
        "subject": "Standard draft",
        "language": "English",
        "target_words": 130,
        "script": "A complete narration draft that already exists.",
        "custom_system_prompt": "",
    }


def valid_job_payload() -> dict:
    request = CloudJobCreate(
        subject="Research start",
        script="A valid narration script for research association testing.",
        master_prompt="Create six chronological videos from this narration.",
        clip_plan=empty_six_clip_plan(target_words=130),
        language="English",
        target_words=130,
        tts_provider="azure-tts-v1",
        voice_id="en-US-JennyNeural-Female",
        voice_speed=1.0,
    )
    return request.model_dump(mode="json")


def matching_research_job_payload() -> dict:
    payload = valid_job_payload()
    payload["research_draft_id"] = "draft-1"
    return payload


def valid_settings_payload() -> dict:
    return {
        "enabled": True,
        "provider": "aihubmix",
        "openrouter_model": "openai/gpt-5.6-sol-pro",
        "openrouter_custom_model_id": "openai/gpt-5.6-sol-pro",
        "aihubmix_model": "gpt-5.6-sol",
        "aihubmix_custom_model_id": "gpt-5.6-sol-custom",
        "custom_system_prompt": "Stay faithful to the cited source material.",
    }


class _StubDraftVoices:
    def get(self, fingerprint):
        if fingerprint != "f" * 64:
            raise ValueError(fingerprint)
        return Path("/tmp/prepared.mp3")

    def materialize(self, fingerprint, destination):
        if fingerprint != "f" * 64:
            raise ValueError(fingerprint)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"prepared voice")
        return destination


class _ResearchServiceStub:
    def __init__(self, *, result=None, error=None):
        self.result = result or ResearchDraftResponse(
            script="A research-backed narration draft.",
            master_prompt="Create six chronological videos from this narration.",
            clip_plan=empty_six_clip_plan(target_words=130),
            research_draft_id="draft-1",
            sources=[
                ResearchDraftSource(
                    source_id="source-1",
                    url="https://example.com/article",
                    title="Example Article",
                    content_hash="a" * 64,
                )
            ],
            accounting=ResearchAccounting(
                provider_rounds=2,
                tool_calls=1,
                usage={"prompt_tokens": 120, "completion_tokens": 40},
                cost=0.03,
            ),
        )
        self.error = error
        self.calls = []

    def create_draft(self, request: ResearchDraftRequest) -> ResearchDraftResponse:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class _LinkFailingResearchStore(ResearchDraftStore):
    def link_job(self, research_draft_id: str, job_id: str) -> None:
        raise sqlite3.OperationalError("link insert failed")


_BROKEN_RESEARCH_STORE = object()


def _seed_research_draft(store: ResearchDraftStore, script: str) -> None:
    store.save_success(
        SuccessfulResearchDraft(
            research_draft_id="draft-1",
            script_hash=sha256_text(script),
            provider="openrouter",
            model="openai/gpt-5.6-sol-pro",
            evidence_mode="source_evidence + model_knowledge",
            usage=ResearchUsageAccounting(
                provider="openrouter",
                model="openai/gpt-5.6-sol-pro",
                input_tokens=120,
                output_tokens=40,
                total_tokens=160,
            ),
            estimated_cost_usd=0.03,
            system_prompt_fingerprint="b" * 64,
            source_prompt_fingerprint="c" * 64,
            sources=[
                ResearchSourceDraft(
                    url="https://example.com/article",
                    title="Example Article",
                    body="evidence body",
                )
            ],
        )
    )


def _research_client(
    tmp_path,
    monkeypatch,
    *,
    research_service=None,
    research_store=None,
):
    cloud_agent = _cloud_agent_controller()
    asgi = importlib.import_module("app.asgi")
    store = CloudJobStore(str(tmp_path / "cloud-agent.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    app = FastAPI()
    app.include_router(cloud_agent.router)
    app.add_exception_handler(HttpException, asgi.exception_handler)
    app.dependency_overrides[cloud_agent.get_cloud_job_store] = lambda: store
    app.dependency_overrides[cloud_agent.get_cloud_job_storage] = lambda: storage
    app.dependency_overrides[cloud_agent.get_draft_voice_service] = (
        lambda: _StubDraftVoices()
    )
    app.dependency_overrides[cloud_agent.get_research_service] = (
        lambda: research_service or _ResearchServiceStub()
    )
    if research_store is _BROKEN_RESEARCH_STORE:
        app.dependency_overrides[cloud_agent.get_research_draft_store] = lambda: pytest.fail(
            "Standard Start must not construct Research storage"
        )
    else:
        resolved_research_store = research_store or ResearchDraftStore(
            str(tmp_path / "cloud-agent.sqlite3")
        )
        app.dependency_overrides[cloud_agent.get_research_draft_store] = (
            lambda: resolved_research_store
        )
        if hasattr(cloud_agent, "get_research_draft_store_factory"):
            app.dependency_overrides[cloud_agent.get_research_draft_store_factory] = (
                lambda: lambda: resolved_research_store
            )
    app.dependency_overrides[cloud_agent.get_research_settings_service] = (
        lambda: ResearchSettingsService()
    )
    monkeypatch.setattr(config, "save_config", lambda: None)
    monkeypatch.setitem(config.app, "cloud_agent_research_enabled", True)
    monkeypatch.setitem(
        config.app, "cloud_agent_research_default_provider", "openrouter"
    )
    monkeypatch.setitem(
        config.app,
        "cloud_agent_research_openrouter_model",
        "openai/gpt-5.6-sol-pro",
    )
    monkeypatch.setitem(
        config.app,
        "cloud_agent_research_openrouter_custom_model",
        "openai/gpt-5.6-sol-pro",
    )
    monkeypatch.setitem(config.app, "cloud_agent_research_aihubmix_model", "gpt-5.6-sol")
    monkeypatch.setitem(
        config.app,
        "cloud_agent_research_aihubmix_custom_model",
        "gpt-5.6-sol",
    )
    monkeypatch.setitem(config.app, "cloud_agent_research_custom_system_prompt", "")
    client = TestClient(app, raise_server_exceptions=False)
    client.app.state.job_store = store
    return client, store


def research_client(tmp_path, monkeypatch, **kwargs):
    client, _store = _research_client(tmp_path, monkeypatch, **kwargs)
    return client


def research_client_with_failing_link(tmp_path, monkeypatch):
    failing_store = _LinkFailingResearchStore(str(tmp_path / "cloud-agent.sqlite3"))
    _seed_research_draft(failing_store, matching_research_job_payload()["script"])
    return _research_client(
        tmp_path,
        monkeypatch,
        research_store=failing_store,
    )


def forbidden_adapter():
    return _ResearchServiceStub(
        error=AssertionError("settings endpoint must not call the research provider")
    )


def test_research_draft_route_is_on_existing_cloud_agent_router(tmp_path, monkeypatch):
    response = research_client(tmp_path, monkeypatch).post(
        "/api/v1/cloud-agent/research/drafts",
        json=research_payload(),
    )

    assert response.status_code == 200
    assert response.json()["data"]["research_draft_id"] == "draft-1"
    assert response.json()["data"]["sources"][0]["content_hash"] == "a" * 64
    assert "source_hash" not in response.json()["data"]["sources"][0]


def test_research_route_inventory_uses_only_cloud_agent_prefix():
    routes = registered_cloud_agent_routes()

    assert "POST /api/v1/cloud-agent/research/drafts" in routes
    assert not any("web_search" in route or ":online" in route for route in routes)


def test_research_failure_is_typed_safe_and_creates_no_job(tmp_path, monkeypatch):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=_ResearchServiceStub(
            error=ResearchError(
                "URL_REQUIRED",
                "secret upstream detail",
                accounting=ResearchAccounting(
                    provider_rounds=0,
                    tool_calls=0,
                    usage={},
                    cost=None,
                ),
            )
        ),
    )

    response = client.post(
        "/api/v1/cloud-agent/research/drafts",
        json={**research_payload(), "source_urls": []},
    )

    assert response.status_code == 422
    assert response.json()["data"]["code"] == "URL_REQUIRED"
    assert response.json()["message"] == "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง"
    assert "secret" not in response.text
    assert client.app.state.job_store.list_jobs() == []


def test_safe_accounting_drops_non_finite_provider_metadata():
    cloud_agent = _cloud_agent_controller()

    assert cloud_agent._safe_accounting(
        SimpleNamespace(
            tool_calls=float("inf"),
            provider_rounds=float("nan"),
            usage={"tokens": float("inf"), "valid_tokens": 12},
            cost=float("inf"),
        )
    ) == {
        "tool_calls": 0,
        "provider_rounds": 0,
        "usage": {"valid_tokens": 12},
        "cost": None,
    }


def test_research_detail_route_returns_safe_persisted_metadata(
    tmp_path, monkeypatch
):
    research_store = ResearchDraftStore(str(tmp_path / "cloud-agent.sqlite3"))
    _seed_research_draft(research_store, matching_research_job_payload()["script"])
    client = research_client(
        tmp_path,
        monkeypatch,
        research_store=research_store,
    )

    response = client.get("/api/v1/cloud-agent/research/drafts/draft-1")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["research_draft_id"] == "draft-1"
    assert data["provider"] == "openrouter"
    assert "evidence body" not in response.text


def test_start_requires_research_script_hash_match(tmp_path, monkeypatch):
    research_store = ResearchDraftStore(str(tmp_path / "cloud-agent.sqlite3"))
    _seed_research_draft(research_store, matching_research_job_payload()["script"])
    client = research_client(
        tmp_path,
        monkeypatch,
        research_store=research_store,
    )

    payload = {**valid_job_payload(), "research_draft_id": "draft-1", "script": "different"}

    response = client.post("/api/v1/cloud-agent/jobs", json=payload)

    assert response.status_code == 422
    assert response.json()["data"]["code"] == "RESEARCH_RESPONSE_INVALID"
    assert client.app.state.job_store.list_jobs() == []


def test_link_failure_never_queues_job(tmp_path, monkeypatch):
    client, store = research_client_with_failing_link(tmp_path, monkeypatch)

    response = client.post("/api/v1/cloud-agent/jobs", json=matching_research_job_payload())

    assert response.status_code == 422
    assert response.json()["data"]["code"] == "RESEARCH_DRAFT_ASSOCIATION_FAILED"
    assert store.list_jobs()[0].status is CloudJobStatus.FAILED
    assert store.list_jobs()[0].error_code == "RESEARCH_DRAFT_ASSOCIATION_FAILED"


def test_standard_draft_does_not_resolve_or_call_research_service(
    tmp_path, monkeypatch
):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=forbidden_adapter(),
    )

    response = client.post("/api/v1/cloud-agent/draft", json=standard_draft_payload())

    assert response.status_code == 200


def test_standard_start_does_not_construct_research_storage(tmp_path, monkeypatch):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_store=_BROKEN_RESEARCH_STORE,
    )

    response = client.post("/api/v1/cloud-agent/jobs", json=valid_job_payload())

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/cloud-agent/research/providers"),
        ("GET", "/api/v1/cloud-agent/research/settings"),
        ("PUT", "/api/v1/cloud-agent/research/settings"),
    ],
)
def test_load_refresh_and_save_never_call_provider(
    method, path, tmp_path, monkeypatch
):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=forbidden_adapter(),
    )

    response = client.request(
        method,
        path,
        json=valid_settings_payload() if method == "PUT" else None,
    )

    assert response.status_code == 200


def test_provider_catalog_exposes_selectable_catalog_and_custom_model_handoff(
    tmp_path, monkeypatch
):
    response = research_client(tmp_path, monkeypatch).get(
        "/api/v1/cloud-agent/research/providers"
    )

    assert response.status_code == 200
    providers = {item["id"]: item for item in response.json()["data"]}
    assert providers["openrouter"] == {
        "id": "openrouter",
        "label": "OpenRouter",
        "models": ["openai/gpt-5.6-sol-pro", "custom"],
        "default_model": "openai/gpt-5.6-sol-pro",
        "custom_model_id": "openai/gpt-5.6-sol-pro",
        "api_key_configured": False,
    }
    assert providers["aihubmix"]["models"] == ["gpt-5.6-sol", "custom"]


def test_disabled_research_rejects_generation_without_constructing_service(
    tmp_path, monkeypatch
):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=lambda: pytest.fail("disabled Research must stay inactive"),
    )
    monkeypatch.setitem(config.app, "cloud_agent_research_enabled", False)

    response = client.post(
        "/api/v1/cloud-agent/research/drafts",
        json=research_payload(),
    )

    assert response.status_code == 404


def test_provider_key_routes_are_write_only_and_confirmed(tmp_path, monkeypatch):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=forbidden_adapter(),
    )

    saved = client.put(
        "/api/v1/cloud-agent/research/providers/openrouter/api-key",
        json={"api_key": "secret-value"},
    )
    refused = client.request(
        "DELETE",
        "/api/v1/cloud-agent/research/providers/openrouter/api-key",
        json={"confirmed": False},
    )
    removed = client.request(
        "DELETE",
        "/api/v1/cloud-agent/research/providers/openrouter/api-key",
        json={"confirmed": True},
    )

    assert saved.status_code == 200
    assert saved.json()["data"]["api_key_configured"] is True
    assert "secret-value" not in saved.text
    assert refused.status_code == 422
    assert removed.status_code == 200
    assert removed.json()["data"]["api_key_configured"] is False


def test_oversized_provider_key_request_returns_safe_typed_error(
    tmp_path, monkeypatch
):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=forbidden_adapter(),
    )
    oversized_secret = "s" * 5000

    response = client.put(
        "/api/v1/cloud-agent/research/providers/openrouter/api-key",
        json={"api_key": oversized_secret},
    )

    assert response.status_code == 422
    assert response.json()["message"] == "ผลลัพธ์ Research ไม่สมบูรณ์ จึงยังไม่เปลี่ยน Script Editor"
    assert response.json()["data"]["code"] == "RESEARCH_RESPONSE_INVALID"
    assert response.json()["data"]["accounting"] == {
        "tool_calls": 0,
        "provider_rounds": 0,
        "usage": {},
        "cost": None,
    }
    assert oversized_secret not in response.text
    assert "input" not in response.text


def test_malformed_provider_key_json_returns_safe_typed_error(
    tmp_path, monkeypatch
):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=forbidden_adapter(),
    )

    response = client.request(
        "PUT",
        "/api/v1/cloud-agent/research/providers/openrouter/api-key",
        content='{"api_key": "secret"',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["message"] == "ผลลัพธ์ Research ไม่สมบูรณ์ จึงยังไม่เปลี่ยน Script Editor"
    assert response.json()["data"]["code"] == "RESEARCH_RESPONSE_INVALID"
    assert response.json()["data"]["accounting"] == {
        "tool_calls": 0,
        "provider_rounds": 0,
        "usage": {},
        "cost": None,
    }
    assert "secret" not in response.text
    assert "detail" not in response.text


def test_research_settings_rejects_unsupported_provider(tmp_path, monkeypatch):
    client = research_client(
        tmp_path,
        monkeypatch,
        research_service=forbidden_adapter(),
    )

    response = client.put(
        "/api/v1/cloud-agent/research/settings",
        json={**valid_settings_payload(), "provider": "unsupported-provider"},
    )

    assert response.status_code == 422
    assert response.json()["data"]["code"] == "RESEARCH_RESPONSE_INVALID"
    assert response.json()["message"] == "ผลลัพธ์ Research ไม่สมบูรณ์ จึงยังไม่เปลี่ยน Script Editor"
    assert config.app["cloud_agent_research_default_provider"] == "openrouter"
