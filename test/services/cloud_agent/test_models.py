import pytest
from pydantic import ValidationError

from app.models.cloud_agent import (
    CloudAgentHealth,
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobRecord,
    CloudJobIncident,
    CloudJobStatus,
    FlowRecoveryState,
    ServiceSessionStatus,
    SessionCheckResult,
)
from app.models.six_clip import empty_six_clip_plan


def _valid_request(**changes):
    values = {
        "subject": "Why Saturn Has a Hexagon",
        "script": "A valid narration script.",
        "master_prompt": "Create six videos from this narration.",
        "clip_plan": empty_six_clip_plan(target_words=130),
        "language": "English",
        "target_words": 130,
        "tts_provider": "azure-tts-v1",
        "voice_id": "en-US-JennyNeural-Female",
        "voice_speed": 1.0,
    }
    values.update(changes)
    return values


def _valid_record(**changes):
    values = {
        **_valid_request(),
        "id": "job-123",
        "status": CloudJobStatus.QUEUED,
        "checkpoint": CloudJobCheckpoint.NONE,
        "control_request": CloudControlRequest.NONE,
        "current_step": "queued",
        "progress": 0,
        "flow_status": "",
        "canva_status": "",
        "voice_file": "",
        "final_video": "",
        "error_code": "",
        "error_message": "",
        "worker_id": "",
        "lease_until": "",
        "created_at": "2026-08-22T06:30:00+00:00",
        "started_at": "",
        "completed_at": "",
        "updated_at": "2026-08-22T06:30:00+00:00",
    }
    values.update(changes)
    return values


def test_status_and_checkpoint_are_separate_domains():
    assert CloudJobStatus.HUMAN_REQUIRED.value == "HUMAN_REQUIRED"
    assert CloudJobStatus.PAUSED.value == "PAUSED"
    assert CloudJobCheckpoint.FLOW_READY.value == "FLOW_READY"
    assert CloudJobCheckpoint.FINAL_VALIDATED.value == "FINAL_VALIDATED"


def test_control_request_is_not_encoded_as_status():
    assert CloudControlRequest.NONE.value == "NONE"
    assert CloudControlRequest.PAUSE.value == "PAUSE"
    assert CloudControlRequest.CANCEL.value == "CANCEL"


def test_session_states_include_safe_recovery_and_human_challenges():
    assert ServiceSessionStatus.AUTO_RELOGIN.value == "AUTO_RELOGIN"
    assert ServiceSessionStatus.CAPTCHA_REQUIRED.value == "CAPTCHA_REQUIRED"
    assert ServiceSessionStatus.READY.value == "READY"


def test_cloud_job_create_accepts_existing_six_clip_plan():
    request = CloudJobCreate(**_valid_request())

    assert request.clip_plan.target_words == 130
    assert request.target_words == 130


@pytest.mark.parametrize("field", ["script", "master_prompt"])
def test_cloud_job_create_rejects_blank_required_content(field):
    with pytest.raises(ValidationError):
        CloudJobCreate(**_valid_request(**{field: "   "}))


def test_cloud_job_create_rejects_target_words_that_disagree_with_clip_plan():
    with pytest.raises(ValidationError):
        CloudJobCreate(**_valid_request(target_words=140))


def test_cloud_job_create_rejects_non_positive_voice_speed():
    with pytest.raises(ValidationError):
        CloudJobCreate(**_valid_request(voice_speed=0))


def test_cloud_job_record_keeps_status_checkpoint_and_control_independent():
    record = CloudJobRecord(
        **_valid_record(
            status=CloudJobStatus.HUMAN_REQUIRED,
            checkpoint=CloudJobCheckpoint.FLOW_READY,
            control_request=CloudControlRequest.NONE,
            current_step="canva_session",
            progress=70,
        )
    )

    assert record.status is CloudJobStatus.HUMAN_REQUIRED
    assert record.checkpoint is CloudJobCheckpoint.FLOW_READY
    assert record.current_step == "canva_session"
    assert record.progress == 70


def test_cloud_job_record_has_restart_safe_adaptive_timing_defaults():
    record = CloudJobRecord(**_valid_record())

    assert record.audio_duration_seconds == 0.0
    assert record.canva_playback_speed == 1.0
    assert record.target_final_duration_seconds == 60.0


def test_cloud_job_record_accepts_persisted_adaptive_timing_values():
    record = CloudJobRecord(
        **_valid_record(
            audio_duration_seconds=63.25,
            canva_playback_speed=60.0 / 63.25,
            target_final_duration_seconds=63.25,
        )
    )

    assert record.audio_duration_seconds == 63.25
    assert record.canva_playback_speed == pytest.approx(60.0 / 63.25)
    assert record.target_final_duration_seconds == 63.25


def test_cloud_job_record_has_restart_safe_flow_cleanup_state():
    default_record = CloudJobRecord(**_valid_record())
    unresolved_record = CloudJobRecord(
        **_valid_record(flow_cleanup_unresolved=True)
    )

    assert default_record.flow_cleanup_unresolved is False
    assert unresolved_record.flow_cleanup_unresolved is True


def test_cloud_job_record_has_safe_recovery_defaults():
    record = CloudJobRecord(**_valid_record())

    assert record.flow_recovery_state is FlowRecoveryState.NONE
    assert record.flow_recovery_attempts == 0
    assert record.flow_missing_clip_index == 0
    assert record.flow_recovery_baseline == ""
    assert record.canva_restart_attempts == 0
    assert record.last_progress_at == ""
    assert record.last_progress_milestone == ""
    assert record.stage_started_at == ""
    assert record.canva_attempt_started_at == ""


def test_incident_rejects_sensitive_extra_fields():
    values = {
        "id": "incident-1",
        "former_job_id": "job-1",
        "subject": "Safe subject",
        "stage": "google_flow",
        "reason_code": "FLOW_RECOVERY_EXHAUSTED",
        "flow_attempts": 2,
        "canva_attempts": 0,
        "message_th": "สร้างคลิปทดแทนไม่สำเร็จ",
        "created_at": "2026-08-28T00:00:00+00:00",
    }

    with pytest.raises(ValidationError):
        CloudJobIncident.model_validate({**values, "script": "secret"})


def test_cloud_job_record_rejects_progress_outside_zero_to_one_hundred():
    with pytest.raises(ValidationError):
        CloudJobRecord(**_valid_record(progress=101))


def test_session_check_result_uses_typed_service_status():
    result = SessionCheckResult(
        service="google_flow",
        status=ServiceSessionStatus.READY,
        message="Session ready",
        checked_at="2026-08-22T06:30:00+00:00",
        evidence_path="",
    )

    assert result.status is ServiceSessionStatus.READY
    assert result.service == "google_flow"


def test_cloud_agent_health_tracks_worker_and_storage_readiness():
    health = CloudAgentHealth(
        enabled=True,
        worker_online=True,
        worker_last_seen="2026-08-22T06:30:00+00:00",
        storage_writable=True,
        free_space_ok=True,
        free_space_bytes=10_000,
    )

    assert health.worker_online is True
    assert health.storage_writable is True
    assert health.free_space_bytes == 10_000
