from types import SimpleNamespace

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.asgi import exception_handler
from app.config import config
from app.config.config import THUMBNAIL_PROMPT_DEFAULTS
from app.models.exception import HttpException
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)


def _controller():
    return importlib.import_module("app.controllers.v1.cloud_agent")


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))
    monkeypatch.setattr(config, "save_config", lambda: None)

    class Library:
        def get_visible_job(self, job_id):
            return SimpleNamespace(id="visible") if job_id == "visible" else None

    class Thumbnail:
        def __init__(self):
            self.calls = []
            self.error = None

        def generate_for_job(self, job_id):
            self.calls.append(job_id)
            if self.error:
                raise self.error
            return "ready image prompt"

    return SimpleNamespace(
        library=Library(),
        thumbnail=Thumbnail(),
        settings=ThumbnailPromptSettingsService(),
    )


@pytest.fixture
def client(services):
    cloud_agent = _controller()
    app = FastAPI()
    app.include_router(cloud_agent.router)
    app.add_exception_handler(HttpException, exception_handler)
    app.dependency_overrides[cloud_agent.get_cloud_video_library_service] = (
        lambda: services.library
    )
    app.dependency_overrides[cloud_agent.get_thumbnail_prompt_service] = (
        lambda: services.thumbnail
    )
    app.dependency_overrides[cloud_agent.get_thumbnail_prompt_settings_service] = (
        lambda: services.settings
    )
    return TestClient(app, raise_server_exceptions=False)


def _settings_payload(**overrides):
    payload = {
        "master_prompt": "Create a striking thumbnail.",
        "default_provider": "aihubmix",
        "aihubmix_model": "gpt-5.6-sol",
        "aihubmix_custom_model_id": "",
        "openrouter_model": "openai/gpt-5.6-sol",
        "openrouter_custom_model_id": "",
    }
    payload.update(overrides)
    return payload


def test_thumbnail_prompt_endpoint_only_generates_for_visible_completed_job(client, services):
    response = client.post("/api/v1/cloud-agent/videos/visible/thumbnail-prompt")

    assert response.status_code == 200
    assert response.json()["data"] == {"prompt": "ready image prompt"}
    assert services.thumbnail.calls == ["visible"]


def test_thumbnail_prompt_endpoint_rejects_queued_job_without_calling_provider(client, services):
    response = client.post("/api/v1/cloud-agent/videos/queued/thumbnail-prompt")

    assert response.status_code == 404
    assert services.thumbnail.calls == []


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("PROVIDER_API_KEY_MISSING", 422),
        ("PROVIDER_AUTHENTICATION_FAILED", 401),
        ("PROVIDER_TIMEOUT", 504),
        ("PROVIDER_REQUEST_FAILED", 502),
    ],
)
def test_thumbnail_prompt_endpoint_maps_typed_provider_errors(
    client, services, code, status_code
):
    services.thumbnail.error = ThumbnailPromptError(code, "provider secret detail")

    response = client.post("/api/v1/cloud-agent/videos/visible/thumbnail-prompt")

    assert response.status_code == status_code
    assert "provider secret detail" not in response.text
    assert services.thumbnail.calls == ["visible"]


def test_thumbnail_prompt_settings_and_providers_redact_api_keys(client, services):
    services.settings.set_api_key("aihubmix", "credential-must-not-be-returned")

    settings = client.get("/api/v1/cloud-agent/thumbnail-prompt/settings")
    providers = client.get("/api/v1/cloud-agent/thumbnail-prompt/providers")

    assert settings.status_code == 200
    assert providers.status_code == 200
    assert "credential-must-not-be-returned" not in settings.text
    assert "credential-must-not-be-returned" not in providers.text
    provider = next(
        item for item in providers.json()["data"] if item["id"] == "aihubmix"
    )
    assert provider["api_key_configured"] is True


def test_thumbnail_prompt_settings_validate_and_persist_dedicated_values(client):
    invalid = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(master_prompt=" "),
    )
    updated = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(),
    )

    assert invalid.status_code == 422
    assert updated.status_code == 200
    assert updated.json()["data"]["master_prompt"] == "Create a striking thumbnail."


def test_thumbnail_prompt_provider_key_can_be_updated_and_removed(client):
    updated = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/providers/aihubmix/api-key",
        json={"api_key": "dedicated-thumbnail-key"},
    )
    removed = client.request(
        "DELETE",
        "/api/v1/cloud-agent/thumbnail-prompt/providers/aihubmix/api-key",
        json={"confirmed": True},
    )

    assert updated.status_code == 200
    assert updated.json()["data"]["api_key_configured"] is True
    assert "dedicated-thumbnail-key" not in updated.text
    assert removed.status_code == 200
    assert removed.json()["data"]["api_key_configured"] is False


def test_thumbnail_prompt_provider_key_rejects_oversized_input(client):
    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/providers/aihubmix/api-key",
        json={"api_key": "x" * 4097},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"


def test_thumbnail_prompt_settings_reject_oversized_model_input(client):
    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(aihubmix_model="x" * 257),
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"
