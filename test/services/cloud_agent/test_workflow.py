import importlib
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.errors import (
    FlowWorkspaceVerificationError,
    HumanRequiredError,
    MediaValidationError,
)
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

    def ensure_ready(self, job_id: str, *, worker_id: str) -> None:
        del worker_id
        self.calls.append(job_id)
        if self.error is not None:
            raise self.error


class PausingPreflight(RecordingPreflight):
    def __init__(self, store: CloudJobStore):
        super().__init__()
        self.store = store

    def ensure_ready(self, job_id: str, *, worker_id: str) -> None:
        super().ensure_ready(job_id, worker_id=worker_id)
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
        self.cleanup_calls = 0

    @contextmanager
    def acquire_workspace(self, job):
        owner = self

        class Workspace:
            def generate_and_download(self, current_job, paths, expected_count=6):
                assert current_job.id == job.id
                assert expected_count == 6
                owner.calls.append((current_job.id, paths.flow_dir))
                for path in paths.flow_files:
                    path.write_bytes(b"clip")
                return paths.flow_files

            def cleanup_and_verify_empty(self):
                owner.cleanup_calls += 1

        yield Workspace()


class RecordingWorkspace:
    def __init__(
        self,
        store,
        events,
        *,
        cleanup_error=None,
        control_request_on_cleanup=None,
    ):
        self.store = store
        self.events = events
        self.cleanup_error = cleanup_error
        self.generate_calls = []
        self.cleanup_calls = 0
        self.job_id = None
        self.control_request_on_cleanup = control_request_on_cleanup

    def generate_and_download(self, job, paths, expected_count=6):
        self.events.append("generate")
        self.generate_calls.append((job.id, paths, expected_count))
        for path in paths.flow_files:
            path.write_bytes(b"clip")
        return paths.flow_files

    def cleanup_and_verify_empty(self):
        self.cleanup_calls += 1
        current = self.store.get_job(self.job_id)
        assert current is not None
        self.events.append(
            (
                "cleanup",
                current.checkpoint,
                current.flow_cleanup_unresolved,
            )
        )
        if self.control_request_on_cleanup is not None:
            self.store.patch_job(
                self.job_id,
                control_request=self.control_request_on_cleanup,
            )
        if self.cleanup_error is not None:
            raise self.cleanup_error


class RecordingWorkspaceFlow:
    def __init__(
        self,
        store,
        events=None,
        *,
        cleanup_error=None,
        control_request_on_cleanup=None,
    ):
        self.store = store
        self.events = events if events is not None else []
        self.workspace = RecordingWorkspace(
            store,
            self.events,
            cleanup_error=cleanup_error,
            control_request_on_cleanup=control_request_on_cleanup,
        )
        self.acquire_calls = []

    @contextmanager
    def acquire_workspace(self, job):
        self.acquire_calls.append(job.id)
        self.workspace.job_id = job.id
        self.events.append("workspace_enter")
        try:
            yield self.workspace
        finally:
            self.events.append("workspace_exit")


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


class EventRecordingCanva(RecordingCanva):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def assemble_and_export(self, job, clips, audio, output):
        self.events.append("canva")
        return super().assemble_and_export(job, clips, audio, output)


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
    monkeypatch.setattr(
        "app.services.cloud_agent.flow_archive.validate_video",
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
    monkeypatch.setattr(
        "app.services.cloud_agent.flow_archive.validate_video",
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


def test_workflow_persists_flow_ready_before_cleanup_while_workspace_is_held(
    monkeypatch, tmp_path
):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    events = []
    flow = RecordingWorkspaceFlow(store, events)
    canva = EventRecordingCanva(events)
    _accept_media(monkeypatch)
    workflow = _workflow(tmp_path, store, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert events == [
        "workspace_enter",
        "generate",
        ("cleanup", CloudJobCheckpoint.FLOW_READY, True),
        "workspace_exit",
        "canva",
    ]
    assert result.status is CloudJobStatus.COMPLETED
    assert result.flow_cleanup_unresolved is False


def test_tts_ready_valid_canonical_recovery_skips_flow_generation(monkeypatch, tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare(job.id)
    paths.voice_file.write_bytes(b"voice")
    for number, path in enumerate(paths.flow_files, start=1):
        path.write_bytes(f"canonical-{number}".encode())
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
        voice_file=str(paths.voice_file),
    )
    flow = RecordingWorkspaceFlow(store)
    _accept_media(monkeypatch)
    workflow = _workflow(tmp_path, store, tts=RecordingTTS(), flow=flow)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert flow.acquire_calls == [job.id]
    assert flow.workspace.generate_calls == []
    assert flow.workspace.cleanup_calls == 1
    assert result.status is CloudJobStatus.COMPLETED


@pytest.mark.parametrize("salvage_source", ["archive", "staging"])
def test_tts_ready_partial_canonical_salvage_reconstructs_without_generation(
    monkeypatch, tmp_path, salvage_source
):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare(job.id)
    paths.voice_file.write_bytes(b"voice")
    paths.flow_files[0].write_bytes(b"partial-canonical")
    if salvage_source == "archive":
        with ZipFile(paths.flow_archive_file, "w") as archive:
            for number in (4, 1, 6, 2, 5, 3):
                archive.writestr(f"clip {number}.mp4", f"archive-{number}".encode())
    else:
        staged = paths.flow_staging_dir / "validated-before-restart"
        staged.mkdir()
        for number in range(1, 7):
            (staged / f"clip {number}.mp4").write_bytes(
                f"staged-{number}".encode()
            )
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
        voice_file=str(paths.voice_file),
    )
    flow = RecordingWorkspaceFlow(store)
    _accept_media(monkeypatch)
    workflow = _workflow(tmp_path, store, tts=RecordingTTS(), flow=flow)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert flow.acquire_calls == [job.id]
    assert flow.workspace.generate_calls == []
    assert all(path.is_file() for path in paths.flow_files)
    assert list(paths.flow_quarantine_dir.rglob("clip_01.mp4"))
    assert result.status is CloudJobStatus.COMPLETED


def test_post_flow_ready_cleanup_failure_keeps_checkpoint_and_continues_canva(
    monkeypatch, tmp_path
):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    flow = RecordingWorkspaceFlow(
        store,
        cleanup_error=FlowWorkspaceVerificationError("remote state unavailable"),
    )
    canva = RecordingCanva()
    _accept_media(monkeypatch)
    workflow = _workflow(tmp_path, store, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert flow.workspace.cleanup_calls == 1
    assert len(flow.workspace.generate_calls) == 1
    assert len(canva.calls) == 1
    assert result.status is CloudJobStatus.COMPLETED
    assert result.flow_cleanup_unresolved is True
    assert result.error_code == ""
    assert result.error_message == ""


def test_crash_after_flow_ready_never_reopens_or_regenerates_flow(monkeypatch, tmp_path):
    class SimulatedProcessCrash(BaseException):
        pass

    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    crashing_flow = RecordingWorkspaceFlow(
        store,
        cleanup_error=SimulatedProcessCrash(),
    )
    _accept_media(monkeypatch)
    first_workflow = _workflow(tmp_path, store, flow=crashing_flow)

    with pytest.raises(SimulatedProcessCrash):
        first_workflow.run(job.id, worker_id=WORKER_ID)

    durable = store.get_job(job.id)
    assert durable is not None
    assert durable.checkpoint is CloudJobCheckpoint.FLOW_READY
    assert durable.flow_cleanup_unresolved is True

    never_flow = RecordingWorkspaceFlow(store)
    canva = RecordingCanva()
    resumed = _workflow(tmp_path, store, flow=never_flow, canva=canva).run(
        job.id,
        worker_id=WORKER_ID,
    )

    assert never_flow.acquire_calls == []
    assert never_flow.workspace.generate_calls == []
    assert len(canva.calls) == 1
    assert resumed.status is CloudJobStatus.COMPLETED
    assert resumed.flow_cleanup_unresolved is True


@pytest.mark.parametrize(
    ("control_request", "expected_status"),
    [
        (CloudControlRequest.PAUSE, CloudJobStatus.PAUSED),
        (CloudControlRequest.CANCEL, CloudJobStatus.CANCELLED),
    ],
)
def test_flow_boundary_honors_control_only_after_cleanup_attempt(
    monkeypatch,
    tmp_path,
    control_request,
    expected_status,
):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _claimed_job(store)
    flow = RecordingWorkspaceFlow(
        store,
        control_request_on_cleanup=control_request,
    )
    canva = RecordingCanva()
    _accept_media(monkeypatch)
    workflow = _workflow(tmp_path, store, flow=flow, canva=canva)

    result = workflow.run(job.id, worker_id=WORKER_ID)

    assert flow.workspace.cleanup_calls == 1
    assert result.status is expected_status
    assert result.checkpoint is CloudJobCheckpoint.FLOW_READY
    assert canva.calls == []


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


def test_factory_builds_worker_and_workflow_from_existing_app_config(monkeypatch, tmp_path):
    """The production factory must use the existing app config, not a second loader."""
    factory = importlib.import_module("app.services.cloud_agent.factory")
    app_config = {
        "cloud_agent_db_path": str(tmp_path / "agent.sqlite3"),
        "cloud_agent_worker_poll_seconds": 3,
        "cloud_agent_worker_lease_seconds": 90,
        "cloud_agent_min_free_disk_gb": 2,
        "cloud_agent_tts_min_duration_seconds": 1,
        "cloud_agent_canva_min_playback_speed": 0.85,
        "cloud_agent_final_duration_tolerance_seconds": 1.0,
        "cloud_agent_final_min_size_bytes": 1,
        "cloud_agent_expected_width": 1080,
        "cloud_agent_expected_height": 1920,
        "cloud_agent_browser_headless": False,
        "cloud_agent_google_profile_dir": str(tmp_path / "google-profile"),
        "cloud_agent_canva_profile_dir": str(tmp_path / "canva-profile"),
        "cloud_agent_browser_lock_dir": str(tmp_path / "browser-locks"),
        "cloud_agent_flow_url": "https://flow.example.test",
        "cloud_agent_canva_template_url": "https://www.canva.com/design/demo/edit",
    }
    monkeypatch.setattr(factory.config, "app", app_config)

    workflow = factory.build_workflow()
    worker = factory.build_worker()

    assert isinstance(workflow, CloudAgentWorkflow)
    assert workflow.store.db_path == app_config["cloud_agent_db_path"]
    assert workflow.canva_min_playback_speed == 0.85
    assert workflow.final_duration_tolerance_seconds == 1.0
    assert worker.workflow.store.db_path == app_config["cloud_agent_db_path"]
    assert worker.lease_seconds == 90
    assert worker.poll_seconds == 3
