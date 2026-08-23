from pathlib import Path
from typing import ContextManager, Protocol

from loguru import logger

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobRecord,
    CloudJobStatus,
)
from app.services.cloud_agent.errors import (
    HumanRequiredError,
    MediaValidationError,
    NarrationTooLongError,
)
from app.services.cloud_agent.flow_archive import recover_flow_artifacts
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.media_probe import MediaProbe, validate_audio, validate_video
from app.services.cloud_agent.storage import CloudJobStorage, JobPaths
from app.services.cloud_agent.timing import calculate_adaptive_timing


class PreflightClient(Protocol):
    def ensure_ready(self, job_id: str, *, worker_id: str) -> None: ...


class TTSClient(Protocol):
    def generate(self, job: CloudJobRecord, output_path: Path) -> Path: ...


class FlowWorkspace(Protocol):
    def generate_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...]: ...

    def cleanup_and_verify_empty(self) -> None: ...


class FlowClient(Protocol):
    def acquire_workspace(
        self,
        job: CloudJobRecord,
    ) -> ContextManager[FlowWorkspace]: ...


class CanvaClient(Protocol):
    def assemble_and_export(
        self,
        job: CloudJobRecord,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path: ...


_CHECKPOINT_RANK = {
    CloudJobCheckpoint.NONE: 0,
    CloudJobCheckpoint.PREFLIGHT_PASSED: 1,
    CloudJobCheckpoint.TTS_READY: 2,
    CloudJobCheckpoint.FLOW_READY: 3,
    CloudJobCheckpoint.FINAL_VALIDATED: 4,
    CloudJobCheckpoint.COMPLETED: 5,
}


class CloudAgentWorkflow:
    def __init__(
        self,
        store: CloudJobStore,
        storage: CloudJobStorage,
        preflight: PreflightClient,
        tts: TTSClient,
        flow: FlowClient,
        canva: CanvaClient,
        *,
        tts_min_duration: float,
        canva_min_playback_speed: float,
        final_duration_tolerance_seconds: float,
        final_min_size_bytes: int,
        expected_width: int,
        expected_height: int,
    ):
        self.store = store
        self.storage = storage
        self.preflight = preflight
        self.tts = tts
        self.flow = flow
        self.canva = canva
        self.tts_min_duration = tts_min_duration
        self.canva_min_playback_speed = canva_min_playback_speed
        self.final_duration_tolerance_seconds = final_duration_tolerance_seconds
        self.final_min_size_bytes = final_min_size_bytes
        self.expected_width = expected_width
        self.expected_height = expected_height

    def _get_job(self, job_id: str) -> CloudJobRecord:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"cloud job not found: {job_id}")
        return job

    @staticmethod
    def _at_least(checkpoint: CloudJobCheckpoint, target: CloudJobCheckpoint) -> bool:
        return _CHECKPOINT_RANK[checkpoint] >= _CHECKPOINT_RANK[target]

    def _control_boundary(self, job_id: str) -> CloudJobRecord | None:
        job = self._get_job(job_id)
        if job.control_request is CloudControlRequest.PAUSE:
            return self.store.patch_job(
                job.id,
                status=CloudJobStatus.PAUSED,
                current_step="paused",
                control_request=CloudControlRequest.NONE,
            )
        if job.control_request is CloudControlRequest.CANCEL:
            return self.store.patch_job(
                job.id,
                status=CloudJobStatus.CANCELLED,
                current_step="cancelled",
                control_request=CloudControlRequest.NONE,
            )
        return None

    def _timing_from_probe(self, job_id: str, probe: MediaProbe) -> CloudJobRecord:
        timing = calculate_adaptive_timing(
            probe.duration,
            min_playback_speed=self.canva_min_playback_speed,
        )
        return self.store.patch_job(
            job_id,
            audio_duration_seconds=timing.audio_duration_seconds,
            canva_playback_speed=timing.canva_playback_speed,
            target_final_duration_seconds=timing.target_final_duration_seconds,
        )

    def _validate_audio_checkpoint(self, job: CloudJobRecord, paths: JobPaths) -> CloudJobRecord:
        checkpoint = job.checkpoint
        if not paths.voice_file.is_file():
            raise MediaValidationError(
                f"checkpoint {checkpoint.value} requires audio artifact: {paths.voice_file}"
            )
        try:
            probe = validate_audio(
                paths.voice_file,
                min_duration=self.tts_min_duration,
            )
        except MediaValidationError as exc:
            raise MediaValidationError(
                f"checkpoint {checkpoint.value} has invalid audio artifact: {exc}"
            ) from exc
        return self._timing_from_probe(job.id, probe)

    def _validate_flow_checkpoint(self, checkpoint: CloudJobCheckpoint, paths: JobPaths) -> None:
        missing = [path for path in paths.flow_files if not path.is_file()]
        if missing:
            raise MediaValidationError(
                f"checkpoint {checkpoint.value} requires six Flow artifacts; missing: {missing[0]}"
            )
        for path in paths.flow_files:
            try:
                validate_video(
                    path,
                    min_size_bytes=1,
                    expected_width=self.expected_width,
                    expected_height=self.expected_height,
                )
            except MediaValidationError as exc:
                raise MediaValidationError(
                    f"checkpoint {checkpoint.value} has invalid Flow artifact {path.name}: {exc}"
                ) from exc

    def _validate_final_duration(self, job: CloudJobRecord, probe: MediaProbe) -> None:
        difference = abs(probe.duration - job.target_final_duration_seconds)
        if difference > self.final_duration_tolerance_seconds:
            raise MediaValidationError(
                f"final media duration {probe.duration:.3f}s differs from target "
                f"{job.target_final_duration_seconds:.3f}s by {difference:.3f}s; "
                f"tolerance is {self.final_duration_tolerance_seconds:.3f}s"
            )

    def _validate_final_checkpoint(self, job: CloudJobRecord, paths: JobPaths) -> None:
        checkpoint = job.checkpoint
        if not paths.final_file.is_file():
            raise MediaValidationError(
                f"checkpoint {checkpoint.value} requires final artifact: {paths.final_file}"
            )
        try:
            probe = validate_video(
                paths.final_file,
                require_audio=True,
                min_size_bytes=self.final_min_size_bytes,
                expected_width=self.expected_width,
                expected_height=self.expected_height,
            )
            self._validate_final_duration(job, probe)
        except MediaValidationError as exc:
            raise MediaValidationError(
                f"checkpoint {checkpoint.value} has invalid final artifact: {exc}"
            ) from exc

    def _validate_checkpoint_artifacts(self, job: CloudJobRecord, paths: JobPaths) -> None:
        checkpoint = job.checkpoint
        if self._at_least(checkpoint, CloudJobCheckpoint.TTS_READY):
            job = self._validate_audio_checkpoint(job, paths)
        if self._at_least(checkpoint, CloudJobCheckpoint.FLOW_READY):
            self._validate_flow_checkpoint(checkpoint, paths)
        if self._at_least(checkpoint, CloudJobCheckpoint.FINAL_VALIDATED):
            self._validate_final_checkpoint(job, paths)

    def run(self, job_id: str, *, worker_id: str) -> CloudJobRecord:
        job = self._get_job(job_id)
        if job.status is CloudJobStatus.COMPLETED or job.checkpoint is CloudJobCheckpoint.COMPLETED:
            return job

        stopped = self._control_boundary(job.id)
        if stopped is not None:
            return stopped

        paths = self.storage.write_inputs(job.id, job.script, job.master_prompt)

        try:
            job = self._get_job(job.id)
            self._validate_checkpoint_artifacts(job, paths)

            if job.checkpoint is CloudJobCheckpoint.NONE:
                self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.PREFLIGHT,
                    current_step="preflight",
                    progress=5,
                    error_code="",
                    error_message="",
                )
                self.preflight.ensure_ready(job.id, worker_id=worker_id)
                job = self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.PREFLIGHT_PASSED,
                    checkpoint=CloudJobCheckpoint.PREFLIGHT_PASSED,
                    current_step="preflight_passed",
                    progress=10,
                )
                stopped = self._control_boundary(job.id)
                if stopped is not None:
                    return stopped

            job = self._get_job(job.id)
            if job.checkpoint is CloudJobCheckpoint.PREFLIGHT_PASSED:
                self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.TTS_GENERATING,
                    current_step="tts_generating",
                    progress=15,
                )
                self.tts.generate(job, paths.voice_file)
                if not paths.voice_file.is_file():
                    raise MediaValidationError("TTS step did not produce the canonical audio artifact")
                probe = validate_audio(
                    paths.voice_file,
                    min_duration=self.tts_min_duration,
                )
                timing = calculate_adaptive_timing(
                    probe.duration,
                    min_playback_speed=self.canva_min_playback_speed,
                )
                job = self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.TTS_READY,
                    checkpoint=CloudJobCheckpoint.TTS_READY,
                    current_step="tts_ready",
                    progress=30,
                    voice_file=str(paths.voice_file),
                    audio_duration_seconds=timing.audio_duration_seconds,
                    canva_playback_speed=timing.canva_playback_speed,
                    target_final_duration_seconds=timing.target_final_duration_seconds,
                )
                stopped = self._control_boundary(job.id)
                if stopped is not None:
                    return stopped

            job = self._get_job(job.id)
            if job.checkpoint is CloudJobCheckpoint.TTS_READY:
                recovered = recover_flow_artifacts(
                    self.storage,
                    job.id,
                    min_size_bytes=1,
                    expected_width=self.expected_width,
                    expected_height=self.expected_height,
                )
                if recovered is None:
                    self.store.patch_job(
                        job.id,
                        status=CloudJobStatus.FLOW_GENERATING,
                        current_step="flow_generating",
                        progress=35,
                    )
                with self.flow.acquire_workspace(job) as workspace:
                    generated = (
                        recovered.paths
                        if recovered is not None
                        else workspace.generate_and_download(job, paths)
                    )
                    if len(generated) != 6:
                        raise MediaValidationError(
                            f"Flow step must produce exactly six clips; got {len(generated)}"
                        )
                    missing = [path for path in paths.flow_files if not path.is_file()]
                    if missing:
                        raise MediaValidationError(
                            f"Flow step did not produce canonical clip: {missing[0]}"
                        )
                    for path in paths.flow_files:
                        validate_video(
                            path,
                            min_size_bytes=1,
                            expected_width=self.expected_width,
                            expected_height=self.expected_height,
                        )
                    job = self.store.patch_job(
                        job.id,
                        status=CloudJobStatus.FLOW_READY,
                        checkpoint=CloudJobCheckpoint.FLOW_READY,
                        current_step="flow_ready",
                        progress=60,
                        flow_cleanup_unresolved=True,
                    )
                    try:
                        workspace.cleanup_and_verify_empty()
                    except Exception:
                        logger.warning(
                            "Flow workspace cleanup remains unresolved for cloud job {}",
                            job.id,
                        )
                    else:
                        job = self.store.patch_job(
                            job.id,
                            flow_cleanup_unresolved=False,
                        )
                stopped = self._control_boundary(job.id)
                if stopped is not None:
                    return stopped

            job = self._get_job(job.id)
            if job.checkpoint is CloudJobCheckpoint.FLOW_READY:
                stopped = self._control_boundary(job.id)
                if stopped is not None:
                    return stopped
                self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.CANVA_UPLOADING,
                    current_step="canva_assembling",
                    progress=65,
                )
                self.canva.assemble_and_export(
                    job,
                    list(paths.flow_files),
                    paths.voice_file,
                    paths.final_file,
                )
                if not paths.final_file.is_file():
                    raise MediaValidationError("Canva step did not produce the canonical final artifact")
                self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.VALIDATING,
                    current_step="validating",
                    progress=90,
                )
                probe = validate_video(
                    paths.final_file,
                    require_audio=True,
                    min_size_bytes=self.final_min_size_bytes,
                    expected_width=self.expected_width,
                    expected_height=self.expected_height,
                )
                self._validate_final_duration(job, probe)
                job = self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.FINAL_VALIDATED,
                    checkpoint=CloudJobCheckpoint.FINAL_VALIDATED,
                    current_step="final_validated",
                    progress=95,
                    final_video=str(paths.final_file),
                )
                stopped = self._control_boundary(job.id)
                if stopped is not None:
                    return stopped

            job = self._get_job(job.id)
            if job.checkpoint is CloudJobCheckpoint.FINAL_VALIDATED:
                self._validate_final_checkpoint(job, paths)
                return self.store.patch_job(
                    job.id,
                    status=CloudJobStatus.COMPLETED,
                    checkpoint=CloudJobCheckpoint.COMPLETED,
                    current_step="completed",
                    progress=100,
                    final_video=str(paths.final_file),
                )

            return self._get_job(job.id)
        except NarrationTooLongError as exc:
            return self.store.patch_job(
                job.id,
                status=CloudJobStatus.FAILED,
                current_step="failed",
                error_code=exc.error_code,
                error_message=str(exc),
            )
        except HumanRequiredError as exc:
            return self.store.patch_job(
                job.id,
                status=CloudJobStatus.HUMAN_REQUIRED,
                current_step="human_required",
                error_code="HUMAN_REQUIRED",
                error_message=str(exc),
            )
