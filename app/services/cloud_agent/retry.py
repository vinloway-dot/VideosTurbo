"""Fail-closed operator retry for pure pre-paid Google Flow failures."""

from pathlib import Path

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobStatus
from app.services.cloud_agent.errors import (
    MediaValidationError,
    NarrationTooLongError,
    PreFlowRetryEligibilityError,
)
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.media_probe import validate_audio
from app.services.cloud_agent.storage import CloudJobStorage, JobPaths
from app.services.cloud_agent.timing import calculate_adaptive_timing


_PRE_FLOW_ERROR_CODE = "FLOW_WORKSPACE_VERIFICATION_FAILED"


class PreFlowRetryService:
    """Requeue a failed TTS_READY job only when no Flow side effect is possible."""

    def __init__(
        self,
        store: CloudJobStore,
        storage: CloudJobStorage,
        *,
        tts_min_duration: float,
        canva_min_playback_speed: float,
    ) -> None:
        self.store = store
        self.storage = storage
        self.tts_min_duration = float(tts_min_duration)
        self.canva_min_playback_speed = float(canva_min_playback_speed)

    @staticmethod
    def _directory_has_entries(directory: Path) -> bool:
        try:
            return next(directory.iterdir(), None) is not None
        except OSError as exc:
            raise PreFlowRetryEligibilityError(
                "Flow artifact state cannot be verified safely"
            ) from exc

    def _has_flow_artifact_evidence(self, paths: JobPaths) -> bool:
        if any(path.exists() or path.is_symlink() for path in paths.flow_files):
            return True
        if paths.flow_archive_file.exists() or paths.flow_archive_file.is_symlink():
            return True
        return any(
            self._directory_has_entries(directory)
            for directory in (
                paths.flow_downloads_dir,
                paths.flow_staging_dir,
                paths.flow_quarantine_dir,
            )
        )

    def _validate_retry_record(self, job, paths: JobPaths) -> None:
        if job.status is not CloudJobStatus.FAILED or job.checkpoint is not CloudJobCheckpoint.TTS_READY:
            raise PreFlowRetryEligibilityError(
                "job is not in a retryable pre-Flow state"
            )
        if job.flow_generation_unresolved:
            raise PreFlowRetryEligibilityError(
                "paid Flow generation may already have been submitted; reconciliation required"
            )
        if job.flow_cleanup_unresolved:
            raise PreFlowRetryEligibilityError(
                "Flow cleanup state requires recovery before retry"
            )
        if job.error_code != _PRE_FLOW_ERROR_CODE:
            raise PreFlowRetryEligibilityError(
                "job is not in a retryable pre-Flow state"
            )
        if job.worker_id or job.lease_until:
            raise PreFlowRetryEligibilityError("job is currently owned by a worker")
        if Path(job.voice_file).resolve() != paths.voice_file.resolve():
            raise PreFlowRetryEligibilityError("canonical narration is unavailable")
        if not paths.voice_file.is_file() or paths.voice_file.is_symlink():
            raise PreFlowRetryEligibilityError("canonical narration is unavailable")
        if self._has_flow_artifact_evidence(paths):
            raise PreFlowRetryEligibilityError(
                "Flow artifacts require recovery or reconciliation"
            )

    def retry(self, job_id: str):
        job = self.store.get_job(job_id)
        if job is None:
            raise PreFlowRetryEligibilityError("cloud agent job not found")
        paths = self.storage.prepare(job.id)
        self._validate_retry_record(job, paths)
        try:
            probe = validate_audio(
                paths.voice_file,
                min_duration=self.tts_min_duration,
            )
            timing = calculate_adaptive_timing(
                probe.duration,
                min_playback_speed=self.canva_min_playback_speed,
            )
        except (MediaValidationError, NarrationTooLongError) as exc:
            raise PreFlowRetryEligibilityError(
                "canonical narration is unavailable or violates the timing policy"
            ) from exc
        try:
            return self.store.requeue_pre_flow_retry(
                job.id,
                voice_file=str(paths.voice_file),
                audio_duration_seconds=timing.audio_duration_seconds,
                canva_playback_speed=timing.canva_playback_speed,
                target_final_duration_seconds=timing.target_final_duration_seconds,
            )
        except ValueError as exc:
            raise PreFlowRetryEligibilityError(
                "job is not in a retryable pre-Flow state"
            ) from exc
