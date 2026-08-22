import pytest
from pydantic import ValidationError

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
    ServiceSessionStatus,
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
