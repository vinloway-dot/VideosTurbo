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
from app.services.cloud_agent.media_probe import MediaProbe
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


class PausingPreflight(RecordingPreflight):
    def __init__(self, store: CloudJobStore):
        super().__init__()
        self.store = store

    def ensure_ready(self, job_id: str) -> None:
        super().ensure_ready(job_id)
        self.store.patch_job(job_id, control_request=CloudControlRequest.PAUSE)


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
        self.job_timings: list[tuple[float, float, float]] = []

    def assemble_and_export(
        self,
        job,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path:
        self.calls.append((job.id, clips, audio, output))
        self.job_timings.append(
            (
                job.audio_duration_seconds,
                job.canva_playback_speed,
                job.target_final_duration_seconds,
            )
        )
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
        tts_min_duration=1.0,
        canva_min_playback_speed=0.85,
        final_duration_tolerance_seconds=1.0,
        final_min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )


def _accept_media(monkeypatch):
    def fake_validate_audio(path, **kwargs):
        return _media_probe(
            Path(path),
            duration=60.0,
            has_audio=True,
            has_video=False,
        )

    def fake_validate_video(path, **kwargs):
        media_path = Path(path)
        is_final = media_path.name == "final.mp4"
        return _media_probe(
            media_path,
            duration=60.0 if is_final else 10.0,
            has_audio=is_final,
            has_video=True,
        )

    monkeypatch.setattr(
        "app.services.cloud_agent.workflow.validate_audio",
        fake_validate_audio,
    )
    monkeypatch.setattr(
        "app.services.cloud_agent.workflow.validate_video",
        fake_validate_video,
    )


def _media_probe(path: Path, *, duration: float, has_audio: bool, has_video: bool) -> MediaProbe:
    return MediaProbe(
        path=Path(path),
        size_bytes=max(1, Path(path).stat().st_size if Path(path).exists() else 1),
        duration=duration,
        has_audio=has_audio,
        has_video=has_video,
        audio_codec="aac" if has_audio else "",
        video_codec="h264" if has_video else "",
        width=1080 if has_video else None,
        height=1920 if has_video else None,
    )


def _patch_timed_media(
    monkeypatch,
    *,
    audio_duration: float,
    final_duration: float | None = None,
):
    def fake_validate_audio(path, **kwargs):
        return _media_probe(
            Path(path),
            duration=audio_duration,
            has_audio=True,
            has_video=False,
        )

    def fake_validate_video(path, **kwargs):
        media_path = Path(path)
        is_final = media_path.name == "final.mp4"
        return _media_probe(
            media_path,
            duration=(
                final_duration
                if is_final and final_duration is not None
                else audio_duration if is_final else 10.0
            ),
            has_audio=is_final,
            has_video=True,
        )

    monkeypatch.setattr(
        "app.services.cloud_agent.workflow.validate_audio",
        fake_validate_audio,
    )
    monkeypatch.setattr(
        "app.services.cloud_agent.workflow.validate_video",
        fake_validate_video,
    )


def test_workflow_progresses_from_queue_through_all_durable_checkpoints(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    preflight = RecordingPreflight()
    tts = RecordingTTS()
    flow = RecordingFlow()
    canva = RecordingCanva()
    _accept_media(monkeypatch)
    workflow = _workflow(
        tmp_path,
        store,
        preflight=preflight,
        tts=tts,
        flow=flow,
        canva=canva,
    )

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert preflight.calls == [job.id]
    assert len(tts.calls) == 1
    assert len(flow.calls) == 1
    assert len(canva.calls) == 1
    assert result.status is CloudJobStatus.COMPLETED
    assert result.checkpoint is CloudJobCheckpoint.COMPLETED
    assert result.progress == 100
    assert Path(result.voice_file).is_file()
    assert Path(result.final_video).is_file()


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


def test_pause_arriving_during_preflight_stops_before_tts(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    preflight = PausingPreflight(store)
    tts = RecordingTTS()
    workflow = _workflow(tmp_path, store, preflight=preflight, tts=tts)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert preflight.calls == [job.id]
    assert tts.calls == []
    assert result.status is CloudJobStatus.PAUSED
    assert result.checkpoint is CloudJobCheckpoint.PREFLIGHT_PASSED


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


def test_63_second_narration_persists_adaptive_timing_before_flow(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    tts = RecordingTTS()
    flow = RecordingFlow()
    canva = RecordingCanva()
    _patch_timed_media(monkeypatch, audio_duration=63.25, final_duration=63.25)
    workflow = _workflow(tmp_path, store, tts=tts, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert len(tts.calls) == 1
    assert len(flow.calls) == 1
    assert result.audio_duration_seconds == pytest.approx(63.25)
    assert result.canva_playback_speed == pytest.approx(60.0 / 63.25)
    assert result.target_final_duration_seconds == pytest.approx(63.25)
    assert canva.job_timings == [
        pytest.approx((63.25, 60.0 / 63.25, 63.25))
    ]


def test_narration_beyond_policy_fails_before_flow(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    flow = RecordingFlow()
    canva = RecordingCanva()
    _patch_timed_media(monkeypatch, audio_duration=71.0, final_duration=71.0)
    workflow = _workflow(tmp_path, store, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert result.status is CloudJobStatus.FAILED
    assert result.error_code == "NARRATION_TOO_LONG_FOR_SIX_CLIP"
    assert flow.calls == []
    assert canva.calls == []


def test_tts_ready_resume_reuses_audio_and_reconciles_exact_timing(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare(job.id)
    paths.voice_file.write_bytes(b"voice")
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
        voice_file=str(paths.voice_file),
    )
    tts = RecordingTTS()
    flow = RecordingFlow()
    canva = RecordingCanva()
    _patch_timed_media(monkeypatch, audio_duration=63.25, final_duration=63.25)
    workflow = _workflow(tmp_path, store, tts=tts, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert tts.calls == []
    assert len(flow.calls) == 1
    assert result.audio_duration_seconds == pytest.approx(63.25)
    assert result.canva_playback_speed == pytest.approx(60.0 / 63.25)
    assert result.target_final_duration_seconds == pytest.approx(63.25)


def test_flow_ready_resume_reuses_paid_work_and_reconciles_timing_before_canva(
    monkeypatch, tmp_path
):
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
    _patch_timed_media(monkeypatch, audio_duration=63.25, final_duration=63.25)
    workflow = _workflow(tmp_path, store, tts=tts, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert tts.calls == []
    assert flow.calls == []
    assert len(canva.calls) == 1
    assert canva.job_timings == [
        pytest.approx((63.25, 60.0 / 63.25, 63.25))
    ]
    assert result.status is CloudJobStatus.COMPLETED


def test_final_duration_near_adaptive_target_passes(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    _patch_timed_media(monkeypatch, audio_duration=63.25, final_duration=62.5)
    workflow = _workflow(tmp_path, store)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert result.status is CloudJobStatus.COMPLETED
    assert result.target_final_duration_seconds == pytest.approx(63.25)


def test_final_duration_truncation_beyond_tolerance_is_rejected_and_keeps_flow_sources(
    monkeypatch, tmp_path
):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    storage = CloudJobStorage(tmp_path / "jobs")
    _patch_timed_media(monkeypatch, audio_duration=63.25, final_duration=60.0)
    workflow = _workflow(tmp_path, store)

    with pytest.raises(MediaValidationError, match="duration"):
        workflow.run(job.id, worker_id=WORKER_ID)

    paths = storage.prepare(job.id)
    assert all(path.is_file() for path in paths.flow_files)
