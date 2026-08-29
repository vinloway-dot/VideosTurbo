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
    app.dependency_overrides[cloud_agent.get_cloud_video_library_service] = lambda: (
        services.library
    )
    app.dependency_overrides[cloud_agent.get_thumbnail_prompt_service] = lambda: (
        services.thumbnail
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
        "aihubmix_base_url": "https://aihubmix.com/v1",
        "openrouter_model": "openai/gpt-5.6-sol",
        "openrouter_custom_model_id": "",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
    }
    payload.update(overrides)
    return payload


def test_thumbnail_prompt_endpoint_only_generates_for_visible_completed_job(
    client, services
):
    response = client.post("/api/v1/cloud-agent/videos/visible/thumbnail-prompt")

    assert response.status_code == 200
    assert response.json()["data"] == {"prompt": "ready image prompt"}
    assert services.thumbnail.calls == ["visible"]


def test_thumbnail_prompt_endpoint_rejects_queued_job_without_calling_provider(
    client, services
):
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
        ("THUMBNAIL_PROMPT_RESPONSE_INVALID", 502),
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


@pytest.mark.parametrize(
    ("code", "public_message"),
    [
        (
            "PROVIDER_UNSUPPORTED",
            "thumbnail prompt default provider is invalid; update settings",
        ),
        (
            "PROVIDER_MODEL_UNSUPPORTED",
            "thumbnail prompt provider model is unsupported; update settings",
        ),
        (
            "PROVIDER_CUSTOM_MODEL_REQUIRED",
            "thumbnail prompt custom model ID is required; update settings",
        ),
        (
            "PROVIDER_BASE_URL_INVALID",
            "thumbnail prompt provider base URL is invalid; update settings",
        ),
    ],
)
def test_invalid_provider_configuration_returns_actionable_settings_error(
    client, services, code, public_message
):
    services.thumbnail.error = ThumbnailPromptError(code, "provider secret detail")

    response = client.post("/api/v1/cloud-agent/videos/visible/thumbnail-prompt")

    assert response.status_code == 422
    assert response.json()["message"] == public_message
    assert "provider secret detail" not in response.text


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


def test_invalid_saved_default_provider_returns_recoverable_settings_and_catalog(
    client,
):
    config.app["cloud_agent_thumbnail_prompt_default_provider"] = (
        "private-invalid-provider-value"
    )

    settings = client.get("/api/v1/cloud-agent/thumbnail-prompt/settings")
    providers = client.get("/api/v1/cloud-agent/thumbnail-prompt/providers")

    assert settings.status_code == 200
    assert settings.json()["data"]["default_provider"] is None
    assert settings.json()["data"]["configuration_error"] == (
        "Saved default thumbnail provider is invalid. "
        "Select AIHubMix or OpenRouter and save Thumbnail Prompt Settings."
    )
    assert "private-invalid-provider-value" not in settings.text
    assert providers.status_code == 200
    assert [item["id"] for item in providers.json()["data"]] == [
        "aihubmix",
        "openrouter",
    ]


def test_invalid_saved_base_urls_return_redacted_recoverable_settings_and_catalog(
    client,
):
    config.app["cloud_agent_thumbnail_prompt_aihubmix_base_url"] = (
        "https://user:userinfo-secret-marker@example.invalid/v1"
    )
    config.app["cloud_agent_thumbnail_prompt_openrouter_base_url"] = (
        "https://example.invalid/v1?api_key=query-secret-marker"
    )

    settings = client.get("/api/v1/cloud-agent/thumbnail-prompt/settings")
    providers = client.get("/api/v1/cloud-agent/thumbnail-prompt/providers")

    assert settings.status_code == 200
    assert settings.json()["data"]["aihubmix_base_url"] == ""
    assert settings.json()["data"]["openrouter_base_url"] == ""
    assert settings.json()["data"]["configuration_error"] == (
        "Saved thumbnail provider base URL is invalid. "
        "Enter valid HTTP(S) Base URLs and save Thumbnail Prompt Settings."
    )
    assert providers.status_code == 200
    assert {item["id"]: item["base_url"] for item in providers.json()["data"]} == {
        "aihubmix": "",
        "openrouter": "",
    }
    for response in (settings, providers):
        assert "userinfo-secret-marker" not in response.text
        assert "query-secret-marker" not in response.text


def test_oversized_saved_base_url_returns_redacted_recoverable_state(client):
    oversized_marker = "oversized-secret-marker-" + "x" * 2048
    config.app["cloud_agent_thumbnail_prompt_aihubmix_base_url"] = (
        f"https://example.invalid/{oversized_marker}"
    )

    settings = client.get("/api/v1/cloud-agent/thumbnail-prompt/settings")
    providers = client.get("/api/v1/cloud-agent/thumbnail-prompt/providers")

    assert settings.status_code == 200
    assert settings.json()["data"]["aihubmix_base_url"] == ""
    assert settings.json()["data"]["configuration_error"]
    assert providers.status_code == 200
    provider = next(
        item for item in providers.json()["data"] if item["id"] == "aihubmix"
    )
    assert provider["base_url"] == ""
    assert oversized_marker not in settings.text
    assert oversized_marker not in providers.text


@pytest.mark.parametrize(
    "persisted_base_url",
    [
        "https://example.invalid\\persisted-secret-marker/v1",
        "https://example.invalid/\x00persisted-secret-marker/v1",
    ],
)
def test_saved_base_urls_with_backslashes_or_controls_are_redacted(
    client, persisted_base_url
):
    config.app["cloud_agent_thumbnail_prompt_aihubmix_base_url"] = persisted_base_url

    settings = client.get("/api/v1/cloud-agent/thumbnail-prompt/settings")
    providers = client.get("/api/v1/cloud-agent/thumbnail-prompt/providers")

    assert settings.status_code == 200
    assert settings.json()["data"]["aihubmix_base_url"] == ""
    assert providers.status_code == 200
    provider = next(
        item for item in providers.json()["data"] if item["id"] == "aihubmix"
    )
    assert provider["base_url"] == ""
    assert "persisted-secret-marker" not in settings.text
    assert "persisted-secret-marker" not in providers.text


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


def test_thumbnail_prompt_settings_persist_editable_provider_base_urls(client):
    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(
            aihubmix_base_url="https://aihubmix.example/v1",
            openrouter_base_url="https://openrouter.example/api/v1",
        ),
    )

    assert response.status_code == 200
    assert response.json()["data"]["aihubmix_base_url"] == "https://aihubmix.example/v1"
    assert response.json()["data"]["openrouter_base_url"] == (
        "https://openrouter.example/api/v1"
    )


def test_thumbnail_prompt_settings_reject_invalid_base_url(client):
    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(aihubmix_base_url="not-a-url"),
    )

    assert response.status_code == 422


def test_real_app_hides_oversized_submitted_base_url_secret(monkeypatch):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))
    secret_marker = "submitted-base-url-secret-must-not-leak-" + "x" * 2048
    app = importlib.import_module("app.asgi").get_application()
    real_client = TestClient(app, raise_server_exceptions=False)

    response = real_client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(
            aihubmix_base_url=f"https://example.invalid/{secret_marker}"
        ),
    )

    assert response.status_code == 422
    assert response.json()["message"] == (
        "thumbnail prompt provider base URL is invalid; update settings"
    )
    assert secret_marker not in response.text


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
    secret = "test-thumbnail-key-" + "x" * 4097
    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/providers/aihubmix/api-key",
        json={"api_key": secret},
    )

    assert response.status_code == 422
    assert secret not in response.text


def test_thumbnail_prompt_provider_key_validation_hides_oversized_secret(
    monkeypatch,
):
    monkeypatch.setattr(config, "app", dict(THUMBNAIL_PROMPT_DEFAULTS))
    secret = "thumbnail-api-key-secret-must-not-leak-" + "x" * 4097
    app = importlib.import_module("app.asgi").get_application()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/providers/aihubmix/api-key",
        json={"api_key": secret},
    )

    assert response.status_code == 422
    assert secret not in response.text


def test_thumbnail_prompt_settings_reject_oversized_model_input(client):
    response = client.put(
        "/api/v1/cloud-agent/thumbnail-prompt/settings",
        json=_settings_payload(aihubmix_model="x" * 257),
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"
