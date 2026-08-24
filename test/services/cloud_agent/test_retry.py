import importlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobCreate, CloudJobStatus
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.media_probe import MediaProbe
from app.services.cloud_agent.storage import CloudJobStorage


def _request() -> CloudJobCreate:
    return CloudJobCreate(
        subject="Retry test",
        script="A valid narration script.",
        master_prompt="Create six chronological videos from this narration.",
        clip_plan=empty_six_clip_plan(target_words=130),
        language="English",
        target_words=130,
        tts_provider="azure-tts-v1",
        voice_id="en-AU-NatashaNeural-Female",
        voice_speed=1.0,
    )


def _retry_module():
    assert importlib.util.find_spec("app.services.cloud_agent.retry") is not None, (
        "safe pre-Flow retry service is not implemented"
    )
    return importlib.import_module("app.services.cloud_agent.retry")


def _retry_service(module, store, storage):
    service_cls = getattr(module, "PreFlowRetryService", None)
    assert service_cls is not None, "safe pre-Flow retry service is not implemented"
    return service_cls(
        store,
        storage,
        tts_min_duration=1.0,
        canva_min_playback_speed=0.85,
    )


def _probe(path: Path, *, duration: float = 63.936) -> MediaProbe:
    return MediaProbe(
        path=path,
        size_bytes=path.stat().st_size,
        duration=duration,
        has_audio=True,
        has_video=False,
        audio_codec="aac",
        video_codec="",
        width=None,
        height=None,
    )


def _failed_tts_ready_job(store: CloudJobStore, storage: CloudJobStorage):
    job = store.create_job(_request())
    paths = storage.prepare(job.id)
    paths.voice_file.write_bytes(b"canonical voice")
    return store.patch_job(
        job.id,
        status=CloudJobStatus.FAILED,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="failed",
        voice_file=str(paths.voice_file),
        audio_duration_seconds=60.0,
        canva_playback_speed=1.0,
        target_final_duration_seconds=60.0,
        error_code="FLOW_WORKSPACE_VERIFICATION_FAILED",
        error_message="Google Flow project editor could not be verified",
    ), paths


def _patch_valid_audio(monkeypatch, module):
    monkeypatch.setattr(module, "validate_audio", lambda path, **_: _probe(Path(path)))


def test_failed_tts_ready_retry_reuses_same_audio_and_recomputes_timing(monkeypatch, tmp_path):
    module = _retry_module()
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    job, paths = _failed_tts_ready_job(store, storage)
    _patch_valid_audio(monkeypatch, module)

    retried = _retry_service(module, store, storage).retry(job.id)

    assert retried.id == job.id
    assert retried.status is CloudJobStatus.QUEUED
    assert retried.checkpoint is CloudJobCheckpoint.TTS_READY
    assert retried.voice_file == str(paths.voice_file)
    assert retried.audio_duration_seconds == pytest.approx(63.936)
    assert retried.canva_playback_speed == pytest.approx(60.0 / 63.936)
    assert retried.target_final_duration_seconds == pytest.approx(63.936)
    assert retried.flow_generation_unresolved is False
    assert retried.flow_cleanup_unresolved is False
    assert retried.error_code == ""
    assert retried.error_message == ""


@pytest.mark.parametrize("artifact", ["canonical", "archive", "staging", "quarantine"])
def test_retry_fails_closed_when_any_flow_artifact_evidence_exists(
    monkeypatch, tmp_path, artifact
):
    module = _retry_module()
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    job, paths = _failed_tts_ready_job(store, storage)
    _patch_valid_audio(monkeypatch, module)
    if artifact == "canonical":
        paths.flow_files[0].write_bytes(b"possible clip")
    elif artifact == "archive":
        paths.flow_archive_file.write_bytes(b"possible archive")
    elif artifact == "staging":
        (paths.flow_staging_dir / "possible-output").mkdir()
    else:
        (paths.flow_quarantine_dir / "possible-output").mkdir()

    with pytest.raises(module.PreFlowRetryEligibilityError, match="Flow artifact"):
        _retry_service(module, store, storage).retry(job.id)

    durable = store.get_job(job.id)
    assert durable is not None
    assert durable.status is CloudJobStatus.FAILED
    assert durable.checkpoint is CloudJobCheckpoint.TTS_READY
    assert paths.voice_file.read_bytes() == b"canonical voice"


def test_retry_rejects_unresolved_generation_and_missing_or_invalid_audio(
    monkeypatch, tmp_path
):
    module = _retry_module()
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    unresolved, _ = _failed_tts_ready_job(store, storage)
    missing, missing_paths = _failed_tts_ready_job(store, storage)
    invalid, _ = _failed_tts_ready_job(store, storage)
    store.patch_job(unresolved.id, flow_generation_unresolved=True)
    missing_paths.voice_file.unlink()

    def invalid_audio(path, **_):
        if Path(path) == storage.prepare(invalid.id).voice_file:
            raise module.MediaValidationError("invalid audio")
        return _probe(Path(path))

    monkeypatch.setattr(module, "validate_audio", invalid_audio)
    service = _retry_service(module, store, storage)

    with pytest.raises(module.PreFlowRetryEligibilityError, match="reconciliation"):
        service.retry(unresolved.id)
    with pytest.raises(module.PreFlowRetryEligibilityError, match="canonical narration"):
        service.retry(missing.id)
    with pytest.raises(module.PreFlowRetryEligibilityError, match="canonical narration"):
        service.retry(invalid.id)


def test_two_simultaneous_retries_accept_once_and_only_one_worker_claims(monkeypatch, tmp_path):
    module = _retry_module()
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    job, _ = _failed_tts_ready_job(store, storage)
    _patch_valid_audio(monkeypatch, module)

    def retry_once():
        try:
            return _retry_service(module, store, storage).retry(job.id)
        except module.PreFlowRetryEligibilityError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: retry_once(), range(2)))

    accepted = [result for result in results if not isinstance(result, Exception)]
    rejected = [result for result in results if isinstance(result, Exception)]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert accepted[0].checkpoint is CloudJobCheckpoint.TTS_READY
    assert store.claim_next_job("worker-one", lease_seconds=60) is not None
    assert store.claim_next_job("worker-two", lease_seconds=60) is None
