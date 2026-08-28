import pytest

from app.models.cloud_agent import (
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_events import (
    CloudJobIncidentEvent,
    CloudJobEvent,
    CloudJobEventType,
    EventPublishingCloudJobStore,
)


def _request(subject: str = "Event test") -> CloudJobCreate:
    return CloudJobCreate(
        subject=subject,
        script="A valid narration script.",
        master_prompt="Create six videos from this narration.",
        clip_plan=empty_six_clip_plan(target_words=130),
        language="English",
        target_words=130,
        tts_provider="azure-tts-v1",
        voice_id="en-US-JennyNeural-Female",
        voice_speed=1.0,
    )


class RecordingSink:
    def __init__(self):
        self.events = []

    def publish_nowait(self, event):
        self.events.append(event)
        assert event.job_id
        assert event.type.value in {"job.updated", "job.completed"}
        return True


def test_status_patch_emits_safe_projection_after_commit(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(
        str(tmp_path / "agent.sqlite3"), sink=sink
    )
    job = store.create_job(_request())

    changed = store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_GENERATING,
        current_step="tts_generating",
        progress=15,
        error_message="must-not-leak",
    )

    assert store.get_job(job.id) == changed
    assert sink.events[0].model_dump(mode="json") == {
        "event_id": sink.events[0].event_id,
        "type": "job.updated",
        "job_id": job.id,
        "status": "TTS_GENERATING",
        "checkpoint": "NONE",
        "current_step": "tts_generating",
        "progress": 15,
        "updated_at": changed.updated_at,
        "completed_at": "",
    }
    assert "must-not-leak" not in sink.events[0].model_dump_json()


def test_non_progress_and_duplicate_patches_do_not_emit(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(str(tmp_path / "agent.sqlite3"), sink=sink)
    job = store.create_job(_request())

    store.patch_job(job.id, voice_file="voice.mp3")
    store.patch_job(job.id, progress=0)

    assert sink.events == []


def test_timestamp_only_progress_does_not_emit_job_updated(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(str(tmp_path / "agent.sqlite3"), sink=sink)
    job = store.create_job(_request())

    store.mark_progress(
        job.id,
        "canva.audio.inserted",
        at="2026-08-28T00:00:00+00:00",
    )

    assert sink.events == []


def test_completed_transition_emits_one_completed_event(tmp_path):
    sink = RecordingSink()
    store = EventPublishingCloudJobStore(str(tmp_path / "agent.sqlite3"), sink=sink)
    job = store.create_job(_request())

    completed = store.patch_job(
        job.id,
        status=CloudJobStatus.COMPLETED,
        current_step="completed",
        progress=100,
    )
    store.patch_job(job.id, current_step=completed.current_step)

    assert [event.type.value for event in sink.events] == ["job.completed"]
    assert sink.events[0].completed_at == completed.completed_at
    assert completed.checkpoint is CloudJobCheckpoint.COMPLETED


def test_sink_failure_cannot_fail_committed_job_update(tmp_path):
    class FailingSink:
        def publish_nowait(self, _event):
            raise RuntimeError("event transport unavailable")

    store = EventPublishingCloudJobStore(
        str(tmp_path / "agent.sqlite3"), sink=FailingSink()
    )
    job = store.create_job(_request())

    completed = store.patch_job(
        job.id,
        status=CloudJobStatus.COMPLETED,
        current_step="completed",
        progress=100,
    )

    assert completed.status is CloudJobStatus.COMPLETED
    assert store.get_job(job.id).status is CloudJobStatus.COMPLETED


def test_event_model_rejects_sensitive_extra_fields():
    with pytest.raises(ValueError):
        CloudJobEvent.model_validate(
            {
                "event_id": "event-1",
                "type": CloudJobEventType.JOB_UPDATED,
                "job_id": "job-1",
                "status": CloudJobStatus.TTS_GENERATING,
                "checkpoint": CloudJobCheckpoint.NONE,
                "current_step": "tts_generating",
                "progress": 15,
                "updated_at": "2026-08-28T00:00:00+00:00",
                "completed_at": "",
                "script": "must-not-enter",
            }
        )


def test_incident_event_contains_no_subject_message_or_paths():
    event = CloudJobIncidentEvent(
        event_id="event-1",
        type="job.incident",
        incident_id="incident-1",
        former_job_id="job-1",
        reason_code="JOB_STALLED_TIMEOUT",
        stage="canva",
        created_at="2026-08-28T00:00:00+00:00",
    )

    assert set(event.model_dump(mode="json")) == {
        "event_id",
        "type",
        "incident_id",
        "former_job_id",
        "reason_code",
        "stage",
        "created_at",
    }
