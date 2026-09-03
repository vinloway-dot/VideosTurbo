"""Fail-closed operator retry for pure pre-paid Google Flow failures."""

from pathlib import Path

from app.models.cloud_agent import (
    CloudJobCheckpoint,
    CloudJobStatus,
    FlowRecoveryState,
)
from app.services.cloud_agent.errors import (
    MediaValidationError,
    PreFlowRetryEligibilityError,
)
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.media_probe import validate_audio
from app.services.cloud_agent.storage import CloudJobStorage, JobPaths
from app.services.cloud_agent.timing import calculate_adaptive_timing


_PRE_FLOW_ERROR_CODE = "FLOW_WORKSPACE_VERIFICATION_FAILED"
_INVENTORY_RECOVERY_ERROR_CODE = "FLOW_ARCHIVE_VALIDATION_FAILED"


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
        try:
            download_entries = tuple(paths.flow_downloads_dir.iterdir())
        except OSError as exc:
            raise PreFlowRetryEligibilityError(
                "Flow artifact state cannot be verified safely"
            ) from exc
        if any(entry != paths.flow_snapshots_dir for entry in download_entries):
            return True
        return any(
            self._directory_has_entries(directory)
            for directory in (
                paths.flow_snapshots_dir,
                paths.flow_staging_dir,
                paths.flow_quarantine_dir,
            )
        )

    def _validate_inventory_recovery_artifacts(self, paths: JobPaths) -> None:
        expected_snapshot = paths.flow_snapshots_dir / "partial-0.zip"
        try:
            download_entries = set(paths.flow_downloads_dir.iterdir())
            snapshot_entries = set(paths.flow_snapshots_dir.iterdir())
        except OSError as exc:
            raise PreFlowRetryEligibilityError(
                "Flow recovery artifact state cannot be verified safely"
            ) from exc
        unsafe = (
            expected_snapshot.is_symlink()
            or not expected_snapshot.is_file()
            or download_entries != {paths.flow_snapshots_dir}
            or snapshot_entries != {expected_snapshot}
            or paths.flow_archive_file.exists()
            or paths.flow_archive_file.is_symlink()
            or any(path.exists() or path.is_symlink() for path in paths.flow_files)
            or self._directory_has_entries(paths.flow_staging_dir)
            or self._directory_has_entries(paths.flow_quarantine_dir)
        )
        if unsafe:
            raise PreFlowRetryEligibilityError(
                "Flow recovery artifacts are incomplete or ambiguous"
            )

    def _validate_retry_record(self, job, paths: JobPaths) -> bool:
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
        inventory_recovery = (
            job.error_code == _INVENTORY_RECOVERY_ERROR_CODE
            and job.flow_recovery_state is FlowRecoveryState.INVENTORY_PENDING
            and job.flow_recovery_attempts == 0
            and job.flow_missing_clip_index == 0
            and not job.flow_recovery_baseline
        )
        if job.error_code not in {
            _PRE_FLOW_ERROR_CODE,
            _INVENTORY_RECOVERY_ERROR_CODE,
        } or (
            job.error_code == _INVENTORY_RECOVERY_ERROR_CODE
            and not inventory_recovery
        ):
            raise PreFlowRetryEligibilityError(
                "job is not in a retryable pre-Flow state"
            )
        if job.worker_id or job.lease_until:
            raise PreFlowRetryEligibilityError("job is currently owned by a worker")
        if Path(job.voice_file).resolve() != paths.voice_file.resolve():
            raise PreFlowRetryEligibilityError("canonical narration is unavailable")
        if not paths.voice_file.is_file() or paths.voice_file.is_symlink():
            raise PreFlowRetryEligibilityError("canonical narration is unavailable")
        if inventory_recovery:
            self._validate_inventory_recovery_artifacts(paths)
            return True
        if self._has_flow_artifact_evidence(paths):
            raise PreFlowRetryEligibilityError(
                "Flow artifacts require recovery or reconciliation"
            )
        return False

    def retry(self, job_id: str):
        job = self.store.get_job(job_id)
        if job is None:
            raise PreFlowRetryEligibilityError("cloud agent job not found")
        paths = self.storage.prepare(job.id)
        inventory_recovery = self._validate_retry_record(job, paths)
        try:
            probe = validate_audio(
                paths.voice_file,
                min_duration=self.tts_min_duration,
            )
            timing = calculate_adaptive_timing(
                probe.duration,
                min_playback_speed=self.canva_min_playback_speed,
            )
        except MediaValidationError as exc:
            raise PreFlowRetryEligibilityError(
                "canonical narration is unavailable or invalid"
            ) from exc
        try:
            requeue = (
                self.store.requeue_flow_inventory_retry
                if inventory_recovery
                else self.store.requeue_pre_flow_retry
            )
            return requeue(
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
