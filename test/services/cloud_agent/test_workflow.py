from pathlib import Path

import pytest

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.errors import HumanRequiredError, MediaValidationError
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.workflow import CloudAgentWorkflow


WORKER_ID = "worker-a"


def _request(subject: str = "Workflow test") -> CloudJobCreate:
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


def _claimed_job(store: CloudJobStore):
    created = store.create_job(_request())
    claimed = store.claim_next_job(WORKER_ID, lease_seconds=60)
    assert claimed is not None
    assert claimed.id == created.id
    return claimed


class RecordingPreflight:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[str] = []

    def ensure_ready(self, job_id: str) -> None:
        self.calls.append(job_id)
        if self.error is not None:
            raise self.error


class RecordingTTS:
    def __init__(self):
        self.calls: list[tuple[str, Path]] = []

    def generate(self, job, output_path: Path) -> Path:
        self.calls.append((job.id, output_path))
        output_path.write_bytes(b"voice")
        return output_path


class RecordingFlow:
    def __init__(self):
        self.calls: list[tuple[str, Path]] = []

    def generate_and_download(self, job, flow_dir: Path) -> list[Path]:
        self.calls.append((job.id, flow_dir))
        files = [flow_dir / f"clip_{index:02d}.mp4" for index in range(1, 7)]
        for path in files:
            path.write_bytes(b"clip")
        return files


class RecordingCanva:
    def __init__(self):
        self.calls: list[tuple[str, list[Path], Path, Path]] = []

    def assemble_and_export(
        self,
        job,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path:
        self.calls.append((job.id, clips, audio, output))
        output.write_bytes(b"final")
        return output


def _workflow(tmp_path, store, *, preflight=None, tts=None, flow=None, canva=None):
    return CloudAgentWorkflow(
        store,
        CloudJobStorage(tmp_path / "jobs"),
        preflight or RecordingPreflight(),
        tts or RecordingTTS(),
        flow or RecordingFlow(),
        canva or RecordingCanva(),
        tts_min_duration=58.0,
        tts_max_duration=62.0,
        final_min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )


def _accept_media(monkeypatch):
    monkeypatch.setattr(
        "app.services.cloud_agent.workflow.validate_audio",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.cloud_agent.workflow.validate_video",
        lambda *args, **kwargs: None,
    )


def test_flow_ready_checkpoint_skips_tts_and_flow_then_calls_canva(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare(job.id)
    paths.voice_file.write_bytes(b"voice")
    for path in paths.flow_files:
        path.write_bytes(b"clip")
    store.patch_job(
        job.id,
        status=CloudJobStatus.FLOW_READY,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="flow_ready",
        voice_file=str(paths.voice_file),
    )
    tts = RecordingTTS()
    flow = RecordingFlow()
    canva = RecordingCanva()
    _accept_media(monkeypatch)
    workflow = _workflow(tmp_path, store, tts=tts, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert tts.calls == []
    assert flow.calls == []
    assert len(canva.calls) == 1
    assert result.status is CloudJobStatus.COMPLETED
    assert result.checkpoint is CloudJobCheckpoint.COMPLETED
    assert result.final_video == str(paths.final_file)


def test_pause_request_stops_at_safe_boundary_and_preserves_checkpoint(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    store.patch_job(job.id, control_request=CloudControlRequest.PAUSE)
    preflight = RecordingPreflight()
    tts = RecordingTTS()
    flow = RecordingFlow()
    canva = RecordingCanva()
    workflow = _workflow(
        tmp_path,
        store,
        preflight=preflight,
        tts=tts,
        flow=flow,
        canva=canva,
    )

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert result.status is CloudJobStatus.PAUSED
    assert result.checkpoint is CloudJobCheckpoint.NONE
    assert preflight.calls == []
    assert tts.calls == []
    assert flow.calls == []
    assert canva.calls == []


def test_cancel_request_stops_before_next_external_step(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    store.patch_job(job.id, control_request=CloudControlRequest.CANCEL)
    preflight = RecordingPreflight()
    workflow = _workflow(tmp_path, store, preflight=preflight)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert result.status is CloudJobStatus.CANCELLED
    assert result.checkpoint is CloudJobCheckpoint.NONE
    assert preflight.calls == []


def test_human_required_preserves_last_durable_checkpoint(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    preflight = RecordingPreflight(HumanRequiredError("manual login required"))
    workflow = _workflow(tmp_path, store, preflight=preflight)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert result.status is CloudJobStatus.HUMAN_REQUIRED
    assert result.checkpoint is CloudJobCheckpoint.NONE
    assert result.error_message == "manual login required"


def test_checkpoint_with_missing_artifacts_never_repeats_paid_steps(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    store.patch_job(
        job.id,
        status=CloudJobStatus.FLOW_READY,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="flow_ready",
    )
    tts = RecordingTTS()
    flow = RecordingFlow()
    canva = RecordingCanva()
    workflow = _workflow(tmp_path, store, tts=tts, flow=flow, canva=canva)

    with pytest.raises(MediaValidationError, match="checkpoint"):
        workflow.run(job.id, worker_id=WORKER_ID)

    assert tts.calls == []
    assert flow.calls == []
    assert canva.calls == []
