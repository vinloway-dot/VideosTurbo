import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.models.cloud_agent import CloudJobRecord, FlowRecoveryState
from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    FlowWorkspaceVerificationError,
    RecoveryBudgetExhausted,
)
from app.services.cloud_agent.flow_archive import (
    FlowPartialInventory,
    FlowRecoveryCapture,
    FlowRecoveryMaterialization,
    inspect_partial_flow_archive,
    materialize_latest_or_merge_recovery,
)
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.progress import ProgressReporter
from app.services.cloud_agent.providers.google_flow import (
    FlowRecoveryObservation,
    FlowRecoveryRemoteState,
)
from app.services.cloud_agent.storage import JobPaths


class FlowRecoveryMappingError(RuntimeError):
    error_code = "FLOW_MISSING_CLIP_UNRESOLVED"


class FlowRecoveryExhausted(RuntimeError):
    error_code = "FLOW_RECOVERY_EXHAUSTED"


class FlowRecoveryWorkspace(Protocol):
    def capture_partial_inventory(
        self, paths: JobPaths, *, attempt: int
    ) -> FlowRecoveryCapture: ...

    def prepare_targeted_replacement(
        self, prompt: str, *, missing_index: int
    ): ...

    def submit_targeted_replacement(
        self, prompt: str, *, missing_index: int
    ) -> None: ...

    def reconcile_targeted_replacement(
        self,
        paths: JobPaths,
        *,
        missing_index: int,
        attempt: int,
    ) -> FlowRecoveryObservation: ...


InventoryLoader = Callable[..., FlowPartialInventory]
Materializer = Callable[..., FlowRecoveryMaterialization]


def build_targeted_replacement_prompt(job: CloudJobRecord, missing_index: int) -> str:
    if missing_index < 1 or missing_index > 6:
        raise FlowRecoveryMappingError("missing clip index is outside 1 through 6")
    segments = [
        segment for segment in job.clip_plan.segments if segment.index == missing_index
    ]
    if len(segments) != 1 or not segments[0].video_prompt:
        raise FlowRecoveryMappingError("stored prompt for missing clip is unavailable")
    original = segments[0].video_prompt
    return (
        "Create exactly one replacement video for the missing original slot. "
        f'Name only the new completed video "clip {missing_index}". '
        "Do not rename, delete, reorder, or regenerate any existing video. "
        "Use the following stored prompt verbatim:\n\n"
        f"{original}"
    )


class FlowRecoveryCoordinator:
    def __init__(
        self,
        store: CloudJobStore,
        *,
        reporter: ProgressReporter | None = None,
        inventory_loader: InventoryLoader = inspect_partial_flow_archive,
        materializer: Materializer = materialize_latest_or_merge_recovery,
        expected_width: int,
        expected_height: int,
        poll_seconds: float = 1.0,
        reconcile_timeout_seconds: float = 3600.0,
        max_recovery_attempts: int = 2,
    ):
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be non-negative")
        if reconcile_timeout_seconds <= 0:
            raise ValueError("reconcile_timeout_seconds must be positive")
        if max_recovery_attempts < 0 or max_recovery_attempts > 2:
            raise ValueError("max_recovery_attempts must be between 0 and 2")
        self.store = store
        self.reporter = reporter
        self.inventory_loader = inventory_loader
        self.materializer = materializer
        self.expected_width = expected_width
        self.expected_height = expected_height
        self.poll_seconds = poll_seconds
        self.reconcile_timeout_seconds = reconcile_timeout_seconds
        self.max_recovery_attempts = max_recovery_attempts

    def _report(self, job_id: str, milestone: str) -> None:
        if self.reporter is not None:
            self.reporter.reached(job_id, milestone)

    def recover_incomplete_batch(
        self,
        job: CloudJobRecord,
        workspace: FlowRecoveryWorkspace,
        paths: JobPaths,
    ) -> tuple[Path, ...]:
        current = self.store.patch_job(
            job.id,
            flow_generation_unresolved=False,
            flow_recovery_state=FlowRecoveryState.INVENTORY_PENDING,
        )
        try:
            capture = workspace.capture_partial_inventory(paths, attempt=0)
        except (FlowArchiveValidationError, FlowWorkspaceVerificationError) as exc:
            raise FlowRecoveryMappingError(
                f"Flow partial inventory could not be mapped safely: {exc}"
            ) from exc
        if isinstance(capture, FlowRecoveryMaterialization):
            self.store.patch_job(
                current.id,
                flow_recovery_state=FlowRecoveryState.NONE,
                flow_missing_clip_index=0,
                flow_recovery_baseline="",
            )
            self._report(current.id, "flow.inventory.6")
            return capture.paths
        inventory = capture
        current = self.store.patch_job(
            current.id,
            flow_missing_clip_index=inventory.missing_index,
            flow_recovery_baseline=inventory.baseline_digest,
            flow_recovery_state=FlowRecoveryState.READY_TO_SUBMIT,
        )
        self._report(current.id, "flow.inventory.5")
        return self._submit_until_complete(current, workspace, paths, inventory)

    def resume_unresolved_recovery(
        self,
        job: CloudJobRecord,
        workspace: FlowRecoveryWorkspace,
        paths: JobPaths,
    ) -> tuple[Path, ...]:
        current = self.store.get_job(job.id)
        if current is None:
            raise KeyError(job.id)
        if current.flow_recovery_state is FlowRecoveryState.NONE:
            raise FlowRecoveryMappingError("no durable Flow recovery is pending")
        if current.flow_recovery_state is FlowRecoveryState.INVENTORY_PENDING:
            try:
                capture = workspace.capture_partial_inventory(paths, attempt=0)
            except (FlowArchiveValidationError, FlowWorkspaceVerificationError) as exc:
                raise FlowRecoveryMappingError(
                    f"Flow partial inventory could not be mapped safely: {exc}"
                ) from exc
            if isinstance(capture, FlowRecoveryMaterialization):
                self.store.patch_job(
                    current.id,
                    flow_recovery_state=FlowRecoveryState.NONE,
                    flow_missing_clip_index=0,
                    flow_recovery_baseline="",
                )
                self._report(current.id, "flow.inventory.6")
                return capture.paths
            inventory = capture
            current = self.store.patch_job(
                current.id,
                flow_missing_clip_index=inventory.missing_index,
                flow_recovery_baseline=inventory.baseline_digest,
                flow_recovery_state=FlowRecoveryState.READY_TO_SUBMIT,
            )
            self._report(current.id, "flow.inventory.5")
        else:
            inventory = self._load_inventory(current, paths)

        if current.flow_recovery_state is FlowRecoveryState.READY_TO_SUBMIT:
            return self._submit_until_complete(current, workspace, paths, inventory)
        if current.flow_recovery_attempts < 1:
            raise FlowRecoveryMappingError("recovery attempt fence is unavailable")
        observation = self._wait_for_terminal_observation(
            workspace,
            paths,
            missing_index=current.flow_missing_clip_index,
            attempt=current.flow_recovery_attempts,
        )
        return self._resolve_observation(
            current,
            workspace,
            paths,
            inventory,
            observation,
        )

    def _load_inventory(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
    ) -> FlowPartialInventory:
        snapshot = paths.flow_snapshots_dir / "partial-0.zip"
        inventory = self.inventory_loader(
            snapshot,
            paths,
            min_size_bytes=1,
        )
        if (
            inventory.missing_index != job.flow_missing_clip_index
            or inventory.baseline_digest != job.flow_recovery_baseline
        ):
            raise FlowRecoveryMappingError("partial Flow inventory changed after restart")
        return inventory

    def _submit_until_complete(
        self,
        job: CloudJobRecord,
        workspace: FlowRecoveryWorkspace,
        paths: JobPaths,
        inventory: FlowPartialInventory,
    ) -> tuple[Path, ...]:
        current = job
        while True:
            if current.flow_recovery_attempts >= self.max_recovery_attempts:
                raise FlowRecoveryExhausted(
                    "Flow replacement retry budget exhausted"
                )
            prompt = build_targeted_replacement_prompt(
                current,
                inventory.missing_index,
            )
            workspace.prepare_targeted_replacement(
                prompt,
                missing_index=inventory.missing_index,
            )
            try:
                current = self.store.reserve_flow_recovery_attempt(
                    current.id,
                    missing_index=inventory.missing_index,
                )
            except RecoveryBudgetExhausted as exc:
                raise FlowRecoveryExhausted(
                    "Flow replacement retry budget exhausted"
                ) from exc
            workspace.submit_targeted_replacement(
                prompt,
                missing_index=inventory.missing_index,
            )
            self._report(
                current.id,
                f"flow.recovery.submitted.{current.flow_recovery_attempts}",
            )
            observation = self._wait_for_terminal_observation(
                workspace,
                paths,
                missing_index=inventory.missing_index,
                attempt=current.flow_recovery_attempts,
            )
            if observation.state is FlowRecoveryRemoteState.FAILED:
                if current.flow_recovery_attempts >= self.max_recovery_attempts:
                    raise FlowRecoveryExhausted(
                        "Flow replacement failed after two attempts"
                    )
                current = self.store.patch_job(
                    current.id,
                    flow_recovery_state=FlowRecoveryState.READY_TO_SUBMIT,
                )
                continue
            return self._resolve_observation(
                current,
                workspace,
                paths,
                inventory,
                observation,
            )

    def _wait_for_terminal_observation(
        self,
        workspace: FlowRecoveryWorkspace,
        paths: JobPaths,
        *,
        missing_index: int,
        attempt: int,
    ) -> FlowRecoveryObservation:
        deadline = time.monotonic() + self.reconcile_timeout_seconds
        while True:
            observation = workspace.reconcile_targeted_replacement(
                paths,
                missing_index=missing_index,
                attempt=attempt,
            )
            if observation.state is not FlowRecoveryRemoteState.RUNNING:
                return observation
            if time.monotonic() >= deadline:
                raise FlowRecoveryMappingError(
                    "Flow replacement state remained unresolved"
                )
            time.sleep(self.poll_seconds)

    def _resolve_observation(
        self,
        job: CloudJobRecord,
        workspace: FlowRecoveryWorkspace,
        paths: JobPaths,
        inventory: FlowPartialInventory,
        observation: FlowRecoveryObservation,
    ) -> tuple[Path, ...]:
        del workspace
        if observation.state is FlowRecoveryRemoteState.FAILED:
            raise FlowRecoveryExhausted("Flow replacement failed")
        if observation.state not in {
            FlowRecoveryRemoteState.COMPLETE_PROJECT,
            FlowRecoveryRemoteState.REPLACEMENT_ONLY,
        } or observation.snapshot_path is None:
            raise FlowRecoveryMappingError(
                "Flow replacement result could not be mapped safely"
            )
        self.store.patch_job(
            job.id,
            flow_recovery_state=FlowRecoveryState.VERIFICATION_PENDING,
        )
        result = self.materializer(
            observation.snapshot_path,
            inventory,
            paths,
            min_size_bytes=1,
            expected_width=self.expected_width,
            expected_height=self.expected_height,
        )
        self.store.patch_job(
            job.id,
            flow_recovery_state=FlowRecoveryState.NONE,
            flow_missing_clip_index=0,
            flow_recovery_baseline="",
            flow_generation_unresolved=False,
        )
        self._report(job.id, "flow.recovery.complete")
        return result.paths
