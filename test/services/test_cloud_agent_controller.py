import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.models.exception import HttpException
from app.models.six_clip import empty_six_clip_plan
from app.services import voice
from app.services.cloud_agent.browser import PersistentBrowserManager
from app.services.cloud_agent.job_store import CloudJobStore


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


def _cloud_agent_controller():
    return importlib.import_module("app.controllers.v1.cloud_agent")


def _request_payload() -> dict:
    request = CloudJobCreate(
        subject="Controller test",
        script="A valid narration script for controller testing.",
        master_prompt="Create six chronological videos from this narration.",
        clip_plan=empty_six_clip_plan(target_words=130),
        language="English",
        target_words=130,
        tts_provider="azure-tts-v1",
        voice_id="en-US-JennyNeural-Female",
        voice_speed=1.0,
    )
    return request.model_dump(mode="json")


def _client(tmp_path):
    cloud_agent = _cloud_agent_controller()
    store = CloudJobStore(str(tmp_path / "cloud-agent.sqlite3"))
    app = FastAPI()
    app.include_router(cloud_agent.router)
    app.dependency_overrides[cloud_agent.get_cloud_job_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False), store


def _created_job(store: CloudJobStore):
    return store.create_job(CloudJobCreate.model_validate(_request_payload()))


def test_cloud_agent_router_contract_is_registered_on_existing_root_router():
    cloud_agent = _cloud_agent_controller()
    app_router = importlib.import_module("app.router")

    registered = set()
    for route in app_router.root_api_router.routes:
        for method in getattr(route, "methods", set()):
            if str(route.path).startswith("/api/v1/cloud-agent"):
                registered.add((method, route.path))

    assert cloud_agent.router.prefix == "/api/v1"
    assert registered == EXPECTED_CLOUD_AGENT_PATHS


def test_create_job_persists_queue_without_running_tts_or_browser(monkeypatch, tmp_path):
    client, store = _client(tmp_path)

    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("long production work must not run inside POST /jobs")

    monkeypatch.setattr(voice, "tts", forbidden)
    monkeypatch.setattr(PersistentBrowserManager, "open", forbidden)

    response = client.post("/api/v1/cloud-agent/jobs", json=_request_payload())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == CloudJobStatus.QUEUED.value
    assert data["progress"] == 0
    persisted = store.get_job(data["id"])
    assert persisted is not None
    assert persisted.status is CloudJobStatus.QUEUED


def test_job_list_and_detail_expose_server_derived_timing_fields(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)
    store.patch_job(
        created.id,
        audio_duration_seconds=63.25,
        canva_playback_speed=60.0 / 63.25,
        target_final_duration_seconds=63.25,
    )

    detail = client.get(f"/api/v1/cloud-agent/jobs/{created.id}")
    listing = client.get("/api/v1/cloud-agent/jobs")

    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["audio_duration_seconds"] == pytest.approx(63.25)
    assert detail_data["canva_playback_speed"] == pytest.approx(60.0 / 63.25)
    assert detail_data["target_final_duration_seconds"] == pytest.approx(63.25)

    assert listing.status_code == 200
    list_data = listing.json()["data"]
    assert [item["id"] for item in list_data] == [created.id]


def test_client_cannot_override_server_derived_timing_fields_on_create(tmp_path):
    client, store = _client(tmp_path)
    payload = _request_payload()
    payload.update(
        audio_duration_seconds=999.0,
        canva_playback_speed=0.1,
        target_final_duration_seconds=999.0,
    )

    response = client.post("/api/v1/cloud-agent/jobs", json=payload)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["audio_duration_seconds"] == pytest.approx(0.0)
    assert data["canva_playback_speed"] == pytest.approx(1.0)
    assert data["target_final_duration_seconds"] == pytest.approx(60.0)
    persisted = store.get_job(data["id"])
    assert persisted is not None
    assert persisted.audio_duration_seconds == pytest.approx(0.0)
    assert persisted.canva_playback_speed == pytest.approx(1.0)
    assert persisted.target_final_duration_seconds == pytest.approx(60.0)


def test_pause_queued_job_is_immediate_safe_pause(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)

    response = client.post(f"/api/v1/cloud-agent/jobs/{created.id}/pause")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == CloudJobStatus.PAUSED.value
    assert data["control_request"] == CloudControlRequest.NONE.value
    assert data["checkpoint"] == CloudJobCheckpoint.NONE.value
    persisted = store.get_job(created.id)
    assert persisted is not None
    assert persisted.status is CloudJobStatus.PAUSED


def test_pause_active_job_requests_stop_at_next_safe_boundary(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)
    store.patch_job(
        created.id,
        status=CloudJobStatus.TTS_GENERATING,
        current_step="tts_generating",
    )

    response = client.post(f"/api/v1/cloud-agent/jobs/{created.id}/pause")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == CloudJobStatus.TTS_GENERATING.value
    assert data["control_request"] == CloudControlRequest.PAUSE.value
    assert data["current_step"] == "tts_generating"


def test_cancel_inactive_job_is_immediate_but_active_job_uses_safe_boundary(tmp_path):
    client, store = _client(tmp_path)
    paused = _created_job(store)
    active = _created_job(store)
    store.patch_job(paused.id, status=CloudJobStatus.PAUSED, current_step="paused")
    store.patch_job(
        active.id,
        status=CloudJobStatus.FLOW_GENERATING,
        current_step="flow_generating",
    )

    paused_response = client.post(f"/api/v1/cloud-agent/jobs/{paused.id}/cancel")
    active_response = client.post(f"/api/v1/cloud-agent/jobs/{active.id}/cancel")

    assert paused_response.status_code == 200
    paused_data = paused_response.json()["data"]
    assert paused_data["status"] == CloudJobStatus.CANCELLED.value
    assert paused_data["control_request"] == CloudControlRequest.NONE.value

    assert active_response.status_code == 200
    active_data = active_response.json()["data"]
    assert active_data["status"] == CloudJobStatus.FLOW_GENERATING.value
    assert active_data["control_request"] == CloudControlRequest.CANCEL.value


def test_resume_paused_or_human_required_requeues_and_preserves_checkpoint(tmp_path):
    client, store = _client(tmp_path)
    paused = _created_job(store)
    human = _created_job(store)
    store.patch_job(
        paused.id,
        status=CloudJobStatus.PAUSED,
        checkpoint=CloudJobCheckpoint.PREFLIGHT_PASSED,
        current_step="paused",
    )
    store.patch_job(
        human.id,
        status=CloudJobStatus.HUMAN_REQUIRED,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="human_required",
        error_code="HUMAN_REQUIRED",
        error_message="manual login required",
    )

    paused_response = client.post(f"/api/v1/cloud-agent/jobs/{paused.id}/resume")
    human_response = client.post(f"/api/v1/cloud-agent/jobs/{human.id}/resume")

    assert paused_response.status_code == 200
    paused_data = paused_response.json()["data"]
    assert paused_data["status"] == CloudJobStatus.QUEUED.value
    assert paused_data["checkpoint"] == CloudJobCheckpoint.PREFLIGHT_PASSED.value
    assert paused_data["control_request"] == CloudControlRequest.NONE.value

    assert human_response.status_code == 200
    human_data = human_response.json()["data"]
    assert human_data["status"] == CloudJobStatus.QUEUED.value
    assert human_data["checkpoint"] == CloudJobCheckpoint.TTS_READY.value
    assert human_data["error_code"] == ""
    assert human_data["error_message"] == ""


def test_resume_rejects_job_that_is_not_paused_or_human_required(tmp_path):
    cloud_agent = _cloud_agent_controller()
    store = CloudJobStore(str(tmp_path / "cloud-agent.sqlite3"))
    created = _created_job(store)

    with pytest.raises(HttpException) as exc_info:
        cloud_agent.resume_cloud_agent_job(created.id, None, store=store)

    assert exc_info.value.status_code == 409
