import pytest

from app.models.cloud_agent import FlowRecoveryState
from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services.cloud_agent.flow_archive import (
    FlowPartialInventory,
    FlowRecoveryMaterialization,
)
from app.services.cloud_agent.flow_recovery import (
    FlowRecoveryCoordinator,
    FlowRecoveryExhausted,
    FlowRecoveryMappingError,
    build_targeted_replacement_prompt,
)
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.providers.google_flow import (
    FlowRecoveryObservation,
    FlowRecoveryRemoteState,
)
from app.services.cloud_agent.storage import CloudJobStorage

from .test_job_store import _request


def _job_with_prompts(store):
    plan = SixClipPlan(
        target_words=130,
        segments=[
            SixClipSegment(
                index=index,
                start_sec=(index - 1) * 10,
                end_sec=index * 10,
                video_prompt=f"EXACT PROMPT {index}\nkeep punctuation: #{index}!",
            )
            for index in range(1, 7)
        ],
    )
    return store.create_job(_request().model_copy(update={"clip_plan": plan}))


def _inventory(paths, *, missing_index=2):
    numbers = tuple(number for number in range(1, 7) if number != missing_index)
    stage = paths.flow_staging_dir / "partial-test"
    stage.mkdir(exist_ok=True)
    files = []
    for number in numbers:
        path = stage / f"clip {number}.mp4"
        path.write_bytes(f"clip-{number}".encode())
        files.append(path)
    return FlowPartialInventory(
        snapshot_path=paths.flow_snapshots_dir / "partial-0.zip",
        semantic_numbers=numbers,
        missing_index=missing_index,
        staged_files=tuple(files),
        baseline_digest="a" * 64,
    )


class RecoveryWorkspace:
    def __init__(self, store, job_id, inventory, observations):
        self.store = store
        self.job_id = job_id
        self.inventory = inventory
        self.observations = list(observations)
        self.events = []
        self.submit_calls = 0
        self.reconcile_calls = 0

    def capture_partial_inventory(self, paths, *, attempt):
        del paths, attempt
        self.events.append("capture_inventory")
        return self.inventory

    def prepare_targeted_replacement(self, prompt, *, missing_index):
        self.events.append(("prepare", missing_index, prompt))

    def submit_targeted_replacement(self, prompt, *, missing_index):
        durable = self.store.get_job(self.job_id)
        assert durable.flow_recovery_state is FlowRecoveryState.SUBMISSION_UNRESOLVED
        self.submit_calls += 1
        self.events.append(f"submit_clip_{missing_index}")

    def reconcile_targeted_replacement(self, paths, *, missing_index, attempt):
        del paths, missing_index, attempt
        self.reconcile_calls += 1
        return self.observations.pop(0)


def _coordinator(store, inventory, materialized_paths):
    def materialize(_snapshot, _inventory, _paths, **_kwargs):
        for path in materialized_paths:
            path.write_bytes(b"clip")
        return FlowRecoveryMaterialization(
            paths=materialized_paths,
            source="latest_complete_archive",
        )

    return FlowRecoveryCoordinator(
        store,
        inventory_loader=lambda *_args, **_kwargs: inventory,
        materializer=materialize,
        expected_width=1080,
        expected_height=1920,
        poll_seconds=0,
    )


def test_targeted_wrapper_contains_stored_prompt_verbatim(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _job_with_prompts(store)
    original = job.clip_plan.segments[1].video_prompt

    prompt = build_targeted_replacement_prompt(job, 2)

    assert prompt.endswith(original)
    assert prompt.count(original) == 1
    assert 'Name only the new completed video "clip 2"' in prompt


def test_missing_index_without_exact_segment_is_rejected(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _job_with_prompts(store)

    with pytest.raises(FlowRecoveryMappingError):
        build_targeted_replacement_prompt(job, 7)


def test_attempt_is_durable_before_paid_submit(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _job_with_prompts(store)
    paths = CloudJobStorage(tmp_path / "jobs").prepare(job.id)
    inventory = _inventory(paths)
    snapshot = paths.flow_snapshots_dir / "replacement-1.zip"
    snapshot.write_bytes(b"snapshot")
    workspace = RecoveryWorkspace(
        store,
        job.id,
        inventory,
        [FlowRecoveryObservation(FlowRecoveryRemoteState.COMPLETE_PROJECT, snapshot)],
    )
    coordinator = _coordinator(store, inventory, paths.flow_files)

    coordinator.recover_incomplete_batch(job, workspace, paths)

    assert workspace.submit_calls == 1
    assert workspace.events[-1] == "submit_clip_2"
    assert store.get_job(job.id).flow_recovery_attempts == 1


def test_complete_capture_skips_paid_replacement_and_clears_recovery_state(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _job_with_prompts(store)
    paths = CloudJobStorage(tmp_path / "jobs").prepare(job.id)
    for path in paths.flow_files:
        path.write_bytes(b"complete")
    complete = FlowRecoveryMaterialization(
        paths=paths.flow_files,
        source="latest_complete_archive",
    )
    workspace = RecoveryWorkspace(store, job.id, complete, [])
    coordinator = _coordinator(store, _inventory(paths), paths.flow_files)

    result = coordinator.recover_incomplete_batch(job, workspace, paths)

    assert result == paths.flow_files
    assert workspace.submit_calls == 0
    assert store.get_job(job.id).flow_recovery_state is FlowRecoveryState.NONE


def test_unresolved_attempt_reconciles_without_second_submit(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _job_with_prompts(store)
    paths = CloudJobStorage(tmp_path / "jobs").prepare(job.id)
    inventory = _inventory(paths)
    snapshot = paths.flow_snapshots_dir / "replacement-1.zip"
    snapshot.write_bytes(b"snapshot")
    store.patch_job(
        job.id,
        flow_recovery_state=FlowRecoveryState.SUBMISSION_UNRESOLVED,
        flow_recovery_attempts=1,
        flow_missing_clip_index=2,
        flow_recovery_baseline=inventory.baseline_digest,
    )
    workspace = RecoveryWorkspace(
        store,
        job.id,
        inventory,
        [FlowRecoveryObservation(FlowRecoveryRemoteState.COMPLETE_PROJECT, snapshot)],
    )

    _coordinator(store, inventory, paths.flow_files).resume_unresolved_recovery(
        store.get_job(job.id), workspace, paths
    )

    assert workspace.submit_calls == 0
    assert workspace.reconcile_calls == 1


def test_failed_replacement_submits_at_most_two_times(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = _job_with_prompts(store)
    paths = CloudJobStorage(tmp_path / "jobs").prepare(job.id)
    inventory = _inventory(paths)
    failed = FlowRecoveryObservation(FlowRecoveryRemoteState.FAILED)
    workspace = RecoveryWorkspace(store, job.id, inventory, [failed, failed])

    with pytest.raises(FlowRecoveryExhausted) as error:
        _coordinator(store, inventory, paths.flow_files).recover_incomplete_batch(
            job, workspace, paths
        )

    assert workspace.submit_calls == 2
    assert error.value.error_code == "FLOW_RECOVERY_EXHAUSTED"
