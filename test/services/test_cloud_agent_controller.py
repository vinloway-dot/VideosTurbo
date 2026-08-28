import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import config
from app.models.cloud_agent import (
    CloudAgentDefaults,
    CloudDraftVoiceArtifact,
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
    ServiceSessionStatus,
    SessionCheckResult,
)
from app.models.exception import HttpException
from app.models.six_clip import empty_six_clip_plan
from app.services import voice
from app.services.cloud_agent.browser import PersistentBrowserManager
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage


EXPECTED_CLOUD_AGENT_PATHS = {
    ("GET", "/api/v1/cloud-agent/health"),
    ("GET", "/api/v1/cloud-agent/defaults"),
    ("PUT", "/api/v1/cloud-agent/defaults"),
    ("POST", "/api/v1/cloud-agent/defaults/reset"),
    ("POST", "/api/v1/cloud-agent/draft"),
    ("POST", "/api/v1/cloud-agent/draft/voice"),
    ("GET", "/api/v1/cloud-agent/draft/voices/{fingerprint}/audio"),
    ("POST", "/api/v1/cloud-agent/jobs"),
    ("GET", "/api/v1/cloud-agent/jobs"),
    ("GET", "/api/v1/cloud-agent/jobs/{job_id}"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/pause"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/resume"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/retry"),
    ("POST", "/api/v1/cloud-agent/jobs/{job_id}/cancel"),
    ("GET", "/api/v1/cloud-agent/jobs/{job_id}/final"),
    ("GET", "/api/v1/cloud-agent/videos"),
    ("DELETE", "/api/v1/cloud-agent/videos/{job_id}"),
    ("GET", "/api/v1/cloud-agent/tts/providers"),
    ("GET", "/api/v1/cloud-agent/tts/providers/{provider_id}"),
    ("PUT", "/api/v1/cloud-agent/tts/providers/{provider_id}/settings"),
    ("POST", "/api/v1/cloud-agent/tts/providers/{provider_id}/voices/refresh"),
    ("GET", "/api/v1/cloud-agent/research/providers"),
    ("GET", "/api/v1/cloud-agent/research/settings"),
    ("PUT", "/api/v1/cloud-agent/research/settings"),
    ("PUT", "/api/v1/cloud-agent/research/providers/{provider_id}/api-key"),
    ("DELETE", "/api/v1/cloud-agent/research/providers/{provider_id}/api-key"),
    ("POST", "/api/v1/cloud-agent/research/drafts"),
    ("GET", "/api/v1/cloud-agent/research/drafts/{research_draft_id}"),
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
    asgi = importlib.import_module("app.asgi")
    store = CloudJobStore(str(tmp_path / "cloud-agent.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    app = FastAPI()
    app.include_router(cloud_agent.router)
    app.add_exception_handler(HttpException, asgi.exception_handler)
    app.dependency_overrides[cloud_agent.get_cloud_job_store] = lambda: store
    app.dependency_overrides[cloud_agent.get_cloud_job_storage] = lambda: storage

    class FakeDraftVoices:
        def __init__(self):
            self.requests = []
            self.source = tmp_path / "prepared.mp3"
            self.source.write_bytes(b"prepared voice")

        def prepare(self, request):
            self.requests.append(request)
            return CloudDraftVoiceArtifact(fingerprint="f" * 64, reused=False)

        def get(self, fingerprint):
            assert fingerprint == "f" * 64
            return self.source

        def materialize(self, fingerprint, destination):
            assert fingerprint == "f" * 64
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(self.source.read_bytes())
            return Path(destination)

    voices = FakeDraftVoices()
    app.dependency_overrides[cloud_agent.get_draft_voice_service] = lambda: voices

    class FakeDefaults:
        def __init__(self):
            self.value = CloudAgentDefaults(
                tts_provider="azure-tts-v1",
                voice_id="",
                voice_speed=1.0,
                custom_system_prompt="",
            )

        def get(self):
            return self.value

        def update(self, patch):
            self.value = CloudAgentDefaults.model_validate(patch)
            return self.value

        def reset(self):
            self.value = CloudAgentDefaults(
                tts_provider="azure-tts-v1",
                voice_id="",
                voice_speed=1.0,
                custom_system_prompt="",
            )
            return self.value

    defaults = FakeDefaults()
    app.dependency_overrides[cloud_agent.get_cloud_agent_defaults_service] = lambda: defaults
    client = TestClient(app, raise_server_exceptions=False)
    client.app.state.draft_voices = voices
    client.app.state.defaults = defaults
    client.app.state.cloud_job_storage = storage
    return client, store


def _created_job(store: CloudJobStore):
    return store.create_job(CloudJobCreate.model_validate(_request_payload()))


def _created_completed_final_job(client, store: CloudJobStore):
    job = _created_job(store)
    storage = client.app.state.cloud_job_storage
    paths = storage.prepare(job.id)
    paths.final_file.write_bytes(b"mp4")
    return store.patch_job(
        job.id,
        status=CloudJobStatus.COMPLETED,
        checkpoint=CloudJobCheckpoint.COMPLETED,
        completed_at="2026-08-28T12:00:00+00:00",
        final_video=str(paths.final_file),
    )


def test_video_library_api_exposes_no_local_path(tmp_path):
    client, store = _client(tmp_path)
    job = _created_completed_final_job(client, store)

    response = client.get("/api/v1/cloud-agent/videos?page=1&page_size=10")

    assert response.status_code == 200
    assert response.json()["data"]["items"][0] == {
        "job_id": job.id,
        "subject": job.subject,
        "completed_at": job.completed_at,
        "final_url": f"/api/v1/cloud-agent/jobs/{job.id}/final",
    }
    assert str(tmp_path) not in response.text


def test_video_delete_api_removes_visible_job(tmp_path):
    client, store = _client(tmp_path)
    job = _created_completed_final_job(client, store)

    response = client.delete(f"/api/v1/cloud-agent/videos/{job.id}")

    assert response.status_code == 200
    assert store.get_job(job.id) is None


def test_video_delete_api_hides_nonvisible_job_details(tmp_path):
    client, store = _client(tmp_path)
    job = _created_job(store)

    response = client.delete(f"/api/v1/cloud-agent/videos/{job.id}")

    assert response.status_code == 404
    assert job.id not in response.text


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


def test_tts_provider_metadata_api_redacts_stored_credential(monkeypatch, tmp_path):
    client, _store = _client(tmp_path)
    sentinel_secret = "credential-must-not-be-returned"
    monkeypatch.setitem(config.elevenlabs, "api_key", sentinel_secret)

    response = client.get("/api/v1/cloud-agent/tts/providers/elevenlabs")

    assert response.status_code == 200
    assert sentinel_secret not in response.text
    field = next(
        item
        for item in response.json()["data"]["settings"]
        if item["name"] == "api_key"
    )
    assert field["configured"] is True
    assert field["value"] is None


def test_tts_settings_endpoints_do_not_start_tts_or_browser(monkeypatch, tmp_path):
    client, _store = _client(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("settings endpoint must not start production work")

    monkeypatch.setattr(voice, "tts", forbidden)
    monkeypatch.setattr(PersistentBrowserManager, "open", forbidden)

    response = client.get("/api/v1/cloud-agent/tts/providers")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [
        "azure-tts-v1",
        "azure-tts-v2",
        "siliconflow",
        "gemini-tts",
        "mimo-tts",
        "minimax-tts",
        "elevenlabs",
        "chatterbox",
    ]


def test_health_reports_enabled_worker_storage_and_free_space(monkeypatch, tmp_path):
    client, store = _client(tmp_path)
    last_seen = "2026-08-22T15:00:00+00:00"
    store.update_worker_heartbeat("worker-health", now=last_seen)
    monkeypatch.setitem(config.app, "cloud_agent_enabled", True)
    monkeypatch.setitem(config.app, "cloud_agent_min_free_disk_gb", 0)

    response = client.get("/api/v1/cloud-agent/health")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["worker_last_seen"] == last_seen
    assert data["worker_online"] is True
    assert data["storage_writable"] is True
    assert data["free_space_bytes"] > 0
    assert data["free_space_ok"] is True


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


def test_create_draft_voice_synthesizes_the_entire_editor_script_without_creating_a_job(
    monkeypatch, tmp_path
):
    client, store = _client(tmp_path)
    complete_script = "One complete narration that must be synthesized as a whole."

    response = client.post(
        "/api/v1/cloud-agent/draft/voice",
        json={
            "script": complete_script,
            "tts_provider": "elevenlabs",
            "voice_id": "elevenlabs:P9NVJuTccNIK9usP8iEI:001",
            "voice_speed": 1.0,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["fingerprint"]
    assert data["reused"] is False
    assert client.app.state.draft_voices.requests[0].script == complete_script
    assert store.list_jobs() == []


def test_create_job_materializes_a_matching_prepared_voice_before_queueing(tmp_path):
    client, store = _client(tmp_path)
    payload = _request_payload()
    payload["prepared_voice_fingerprint"] = "f" * 64

    response = client.post("/api/v1/cloud-agent/jobs", json=payload)

    assert response.status_code == 200
    job = store.get_job(response.json()["data"]["id"])
    assert job is not None
    assert job.status is CloudJobStatus.QUEUED
    assert Path(job.voice_file).read_bytes() == b"prepared voice"


def test_cloud_agent_defaults_api_persists_and_resets_operator_preferences(tmp_path):
    client, _store = _client(tmp_path)

    saved = client.put(
        "/api/v1/cloud-agent/defaults",
        json={
            "tts_provider": "elevenlabs",
            "voice_id": "elevenlabs:P9NVJuTccNIK9usP8iEI:001",
            "voice_speed": 1.1,
            "custom_system_prompt": "Write in a calm documentary tone.",
        },
    )
    reset = client.post("/api/v1/cloud-agent/defaults/reset")

    assert saved.status_code == 200
    assert saved.json()["data"]["voice_id"] == "elevenlabs:P9NVJuTccNIK9usP8iEI:001"
    assert reset.status_code == 200
    assert reset.json()["data"]["tts_provider"] == "azure-tts-v1"
    assert reset.json()["data"]["custom_system_prompt"] == ""


def test_draft_generates_a_complete_start_payload_without_starting_production_work(
    monkeypatch, tmp_path
):
    client, _store = _client(tmp_path)
    cloud_agent = _cloud_agent_controller()
    calls = {}

    def generate_script(**kwargs):
        calls["script"] = kwargs
        return "Saturn has a stable hexagonal polar jet."

    def generate_plan(video_script, language, target_words, app_config=None):
        calls["plan"] = {
            "video_script": video_script,
            "language": language,
            "target_words": target_words,
            "app_config": app_config,
        }
        return empty_six_clip_plan(target_words=target_words)

    monkeypatch.setattr(cloud_agent, "generate_script", generate_script, raising=False)
    monkeypatch.setattr(
        cloud_agent, "generate_six_clip_plan", generate_plan, raising=False
    )
    monkeypatch.setattr(voice, "tts", lambda *_args, **_kwargs: pytest.fail("no TTS"))
    monkeypatch.setattr(
        PersistentBrowserManager,
        "open",
        lambda *_args, **_kwargs: pytest.fail("no browser work"),
    )

    response = client.post(
        "/api/v1/cloud-agent/draft",
        json={
            "subject": "Why Saturn Has a Hexagon",
            "language": "English",
            "target_words": 130,
            "script": "",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["script"] == "Saturn has a stable hexagonal polar jet."
    assert data["clip_plan"]["target_words"] == 130
    assert len(data["clip_plan"]["segments"]) == 6
    assert data["master_prompt"].startswith("Create six videos")
    assert calls["script"]["video_subject"] == "Why Saturn Has a Hexagon"
    assert calls["plan"]["video_script"] == data["script"]
    assert calls["plan"]["target_words"] == 130


def test_draft_forwards_custom_system_prompt_only_to_script_generation(
    monkeypatch, tmp_path
):
    client, _store = _client(tmp_path)
    cloud_agent = _cloud_agent_controller()
    calls = {}

    def generate_script(**kwargs):
        calls["script"] = kwargs
        return "A narration generated with the requested writing rules."

    monkeypatch.setattr(cloud_agent, "generate_script", generate_script)
    monkeypatch.setattr(
        cloud_agent,
        "generate_six_clip_plan",
        lambda *_args, **_kwargs: empty_six_clip_plan(target_words=130),
    )

    response = client.post(
        "/api/v1/cloud-agent/draft",
        json={
            "subject": "Why Saturn Has a Hexagon",
            "language": "",
            "target_words": 130,
            "script": "",
            "custom_system_prompt": "Write in a calm documentary tone.",
        },
    )

    assert response.status_code == 200
    assert calls["script"]["custom_system_prompt"] == (
        "Write in a calm documentary tone."
    )


def test_draft_rejects_a_sanitized_llm_error_without_generating_a_clip_plan(
    monkeypatch, tmp_path
):
    client, _store = _client(tmp_path)
    cloud_agent = _cloud_agent_controller()

    monkeypatch.setattr(
        cloud_agent,
        "generate_script",
        lambda **_kwargs: "Error: aihubmix: api_key is not set",
        raising=False,
    )
    monkeypatch.setattr(
        cloud_agent,
        "generate_six_clip_plan",
        lambda *_args, **_kwargs: pytest.fail(
            "a failed script generation must not create a clip plan"
        ),
        raising=False,
    )

    response = client.post(
        "/api/v1/cloud-agent/draft",
        json={
            "subject": "Why Saturn Has a Hexagon",
            "language": "English",
            "target_words": 130,
            "script": "",
        },
    )

    assert response.status_code == 422
    assert response.json()["message"] == "aihubmix: api_key is not set"


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


def test_retry_route_is_registered_as_an_explicit_job_control(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)

    response = client.post(f"/api/v1/cloud-agent/jobs/{created.id}/retry")

    assert response.status_code != 404


def test_retry_controller_requeues_only_safe_pre_flow_job_and_sanitizes_refusal(
    monkeypatch, tmp_path
):
    cloud_agent = _cloud_agent_controller()
    client, store = _client(tmp_path)
    storage = CloudJobStorage(tmp_path / "jobs")
    eligible = _created_job(store)
    blocked = _created_job(store)
    for job, unresolved in ((eligible, False), (blocked, True)):
        paths = storage.prepare(job.id)
        paths.voice_file.write_bytes(b"canonical voice")
        store.patch_job(
            job.id,
            status=CloudJobStatus.FAILED,
            checkpoint=CloudJobCheckpoint.TTS_READY,
            current_step="failed",
            voice_file=str(paths.voice_file),
            error_code="FLOW_WORKSPACE_VERIFICATION_FAILED",
            error_message="vendor error",
            flow_generation_unresolved=unresolved,
        )

    retry_module = importlib.import_module("app.services.cloud_agent.retry")
    monkeypatch.setattr(
        retry_module,
        "validate_audio",
        lambda *_args, **_kwargs: SimpleNamespace(duration=63.936),
    )
    retry_service = retry_module.PreFlowRetryService(
        store,
        storage,
        tts_min_duration=1.0,
        canva_min_playback_speed=0.85,
    )
    client.app.dependency_overrides[cloud_agent.get_pre_flow_retry_service] = (
        lambda: retry_service
    )

    accepted = client.post(f"/api/v1/cloud-agent/jobs/{eligible.id}/retry")
    refused = client.post(f"/api/v1/cloud-agent/jobs/{blocked.id}/retry")

    assert accepted.status_code == 200
    assert accepted.json()["data"]["id"] == eligible.id
    assert accepted.json()["data"]["status"] == CloudJobStatus.QUEUED.value
    assert accepted.json()["data"]["checkpoint"] == CloudJobCheckpoint.TTS_READY.value
    assert refused.status_code == 409
    assert "reconciliation required" in refused.json()["message"]
    assert "Traceback" not in refused.json()["message"]


def test_final_download_requires_final_validated_checkpoint(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)
    paths = CloudJobStorage(tmp_path / "jobs").prepare(created.id)
    paths.final_file.write_bytes(b"validated-video")
    store.patch_job(created.id, final_video=str(paths.final_file))

    response = client.get(f"/api/v1/cloud-agent/jobs/{created.id}/final")

    assert response.status_code == 409


def test_final_download_serves_only_canonical_job_owned_file(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)
    paths = CloudJobStorage(tmp_path / "jobs").prepare(created.id)
    expected = b"validated-video-bytes"
    paths.final_file.write_bytes(expected)
    store.patch_job(
        created.id,
        status=CloudJobStatus.FINAL_VALIDATED,
        checkpoint=CloudJobCheckpoint.FINAL_VALIDATED,
        current_step="final_validated",
        final_video=str(paths.final_file),
    )

    response = client.get(f"/api/v1/cloud-agent/jobs/{created.id}/final")

    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["content-type"].startswith("video/mp4")


def test_final_download_rejects_external_or_mismatched_artifact_path(tmp_path):
    client, store = _client(tmp_path)
    created = _created_job(store)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"must-not-be-served")
    store.patch_job(
        created.id,
        status=CloudJobStatus.FINAL_VALIDATED,
        checkpoint=CloudJobCheckpoint.FINAL_VALIDATED,
        current_step="final_validated",
        final_video=str(outside),
    )

    response = client.get(f"/api/v1/cloud-agent/jobs/{created.id}/final")

    assert response.status_code == 409
    assert response.content != outside.read_bytes()


class _SessionProvider:
    def __init__(self, result):
        self.result = result
        self.checks = 0

    def check_session(self, **_kwargs):
        self.checks += 1
        return self.result


class _Sessions:
    def __init__(self, *, repair_result=None):
        self.providers = {
            "google_flow": _SessionProvider(_session_result("google_flow", "READY")),
            "canva": _SessionProvider(_session_result("canva", "SESSION_EXPIRED")),
        }
        self.repair_result = repair_result
        self.repair_calls = []

    def check_all(self):
        return {service: provider.check_session() for service, provider in self.providers.items()}

    def ensure_service_ready(self, service, job_id):
        self.repair_calls.append((service, job_id))
        if isinstance(self.repair_result, Exception):
            raise self.repair_result
        return self.repair_result or self.providers[service].check_session()


def _session_result(service, status):
    return SessionCheckResult(
        service=service,
        status=ServiceSessionStatus(status),
        checked_at="2026-08-23T00:00:00+00:00",
    )


def _session_client(tmp_path, sessions):
    client, _ = _client(tmp_path)
    cloud_agent = _cloud_agent_controller()
    client.app.dependency_overrides[cloud_agent.get_cloud_agent_sessions] = lambda: sessions
    return client


def test_session_check_routes_use_existing_session_manager_without_repair(tmp_path):
    sessions = _Sessions()
    client = _session_client(tmp_path, sessions)

    all_response = client.post("/api/v1/cloud-agent/sessions/check")
    flow_response = client.post("/api/v1/cloud-agent/sessions/google-flow/check")
    canva_response = client.post("/api/v1/cloud-agent/sessions/canva/check")

    assert all_response.status_code == flow_response.status_code == canva_response.status_code == 200
    assert set(all_response.json()["data"]) == {"google_flow", "canva"}
    assert flow_response.json()["data"]["status"] == "READY"
    assert canva_response.json()["data"]["status"] == "SESSION_EXPIRED"
    assert sessions.repair_calls == []


def test_session_repair_preserves_human_required_result(tmp_path):
    from app.services.cloud_agent.errors import HumanRequiredError

    sessions = _Sessions(repair_result=HumanRequiredError("canva: CAPTCHA_REQUIRED"))
    client = _session_client(tmp_path, sessions)

    response = client.post("/api/v1/cloud-agent/sessions/canva/repair")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "HUMAN_REQUIRED"
    assert "CAPTCHA_REQUIRED" in response.json()["data"]["message"]
    assert sessions.repair_calls == [("canva", "")]


def test_open_browser_is_allowlisted_and_returns_only_configured_novnc_url(monkeypatch, tmp_path):
    client, _ = _client(tmp_path)
    monkeypatch.setitem(config.app, "cloud_agent_remote_browser_url", "http://127.0.0.1:6080/vnc.html")

    response = client.get("/api/v1/cloud-agent/sessions/canva/open-browser")
    unsupported = client.get("/api/v1/cloud-agent/sessions/other/open-browser")

    assert response.status_code == 200
    assert response.json()["data"] == {"url": "http://127.0.0.1:6080/vnc.html"}
    assert unsupported.status_code == 400
