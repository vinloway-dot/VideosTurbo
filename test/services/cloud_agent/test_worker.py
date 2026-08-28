import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobCreate, CloudJobStatus
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent import factory, worker as worker_module
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.worker import CloudAgentWorker
from app.services.cloud_agent.worker_process import ChildWaitResult


def _request(subject: str = "Worker test") -> CloudJobCreate:
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


class CompletingWorkflow:
    def __init__(self, store: CloudJobStore):
        self.store = store
        self.calls: list[tuple[str, str]] = []

    def run(self, job_id: str, *, worker_id: str):
        self.calls.append((job_id, worker_id))
        return self.store.patch_job(
            job_id,
            status=CloudJobStatus.COMPLETED,
            checkpoint=CloudJobCheckpoint.COMPLETED,
            current_step="completed",
            progress=100,
        )


class BlockingWorkflow:
    def __init__(self, store: CloudJobStore):
        self.store = store
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, job_id: str, *, worker_id: str):
        self.started.set()
        assert self.release.wait(timeout=3.0)
        return self.store.patch_job(
            job_id,
            status=CloudJobStatus.COMPLETED,
            checkpoint=CloudJobCheckpoint.COMPLETED,
            current_step="completed",
            progress=100,
        )


class ExplodingWorkflow:
    def run(self, job_id: str, *, worker_id: str):
        del job_id, worker_id
        raise RuntimeError("unexpected browser failure")


class RecordingStore(CloudJobStore):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.lease_renewed = threading.Event()

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        renewed = super().renew_lease(job_id, worker_id, lease_seconds)
        if renewed:
            self.lease_renewed.set()
        return renewed


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    def now(self):
        return self.current

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0):
        self.current += timedelta(seconds=seconds, minutes=minutes, hours=hours)


class FakeChildHandle:
    def __init__(self, launcher, job_id: str):
        self.launcher = launcher
        self.job_id = job_id
        self.alive = True

    def wait(self, timeout_seconds: float) -> ChildWaitResult:
        self.launcher.clock.advance(seconds=timeout_seconds)
        job = self.launcher.store.get_job(self.job_id)
        if self.launcher.complete_on_wait or (
            self.launcher.complete_after_restart and job.canva_restart_attempts > 0
        ):
            self.launcher.store.patch_job(
                self.job_id,
                status=CloudJobStatus.COMPLETED,
                checkpoint=CloudJobCheckpoint.COMPLETED,
                current_step="completed",
                progress=100,
            )
            self.alive = False
            return ChildWaitResult(exited=True, exit_code=0, progress_signal=None)
        return ChildWaitResult(exited=False, exit_code=None, progress_signal=None)

    def is_alive(self) -> bool:
        return self.alive

    def terminate_group(self, grace_seconds: float) -> bool:
        del grace_seconds
        self.launcher.events.extend(["terminate", "confirmed_stopped"])
        self.alive = False
        return True


class FakeLauncher:
    def __init__(
        self,
        store: CloudJobStore,
        clock: FakeClock,
        *,
        complete=False,
        complete_after_restart=False,
    ):
        self.store = store
        self.clock = clock
        self.complete_on_wait = complete
        self.complete_after_restart = complete_after_restart
        self.started = []
        self.events = []

    def start(self, job_id: str, worker_id: str):
        self.started.append((job_id, worker_id))
        attempt = self.store.get_job(job_id).canva_restart_attempts + 1
        self.events.append(f"start_attempt_{attempt}")
        return FakeChildHandle(self, job_id)


@dataclass
class TerminationCall:
    job_id: str
    child_stopped: bool
    reason_code: str
    stage: str


class FakeTerminationService:
    def __init__(self):
        self.calls: list[TerminationCall] = []

    def delete_stopped_job(
        self, job_id: str, *, child_stopped: bool, reason_code: str, stage: str
    ):
        self.calls.append(
            TerminationCall(job_id, child_stopped, reason_code, stage)
        )
        return None


def test_run_once_keeps_second_worker_from_claiming_active_job(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    blocking = BlockingWorkflow(store)
    worker_a = CloudAgentWorker(
        store,
        blocking,
        worker_id="worker-a",
        lease_seconds=30,
        lease_renew_interval_seconds=0.05,
    )
    worker_b_workflow = CompletingWorkflow(store)
    worker_b = CloudAgentWorker(store, worker_b_workflow, worker_id="worker-b")
    result: list[bool] = []

    thread = threading.Thread(target=lambda: result.append(worker_a.run_once()))
    thread.start()
    assert blocking.started.wait(timeout=2.0)

    assert worker_b.run_once() is False
    assert worker_b_workflow.calls == []

    blocking.release.set()
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert result == [True]
    completed = store.get_job(job.id)
    assert completed is not None
    assert completed.status is CloudJobStatus.COMPLETED


def test_run_once_updates_heartbeat_even_when_queue_is_empty(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    workflow = CompletingWorkflow(store)
    worker = CloudAgentWorker(store, workflow, worker_id="worker-heartbeat")

    assert store.get_worker_last_seen("worker-heartbeat") is None
    assert worker.run_once() is False
    assert store.get_worker_last_seen("worker-heartbeat") is not None
    assert workflow.calls == []


def test_worker_renews_lease_during_long_workflow_step(tmp_path):
    store = RecordingStore(str(tmp_path / "agent.sqlite3"))
    store.create_job(_request())
    workflow = BlockingWorkflow(store)
    worker = CloudAgentWorker(
        store,
        workflow,
        worker_id="worker-renew",
        lease_seconds=2,
        lease_renew_interval_seconds=0.02,
    )

    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert workflow.started.wait(timeout=2.0)
    assert store.lease_renewed.wait(timeout=2.0)

    workflow.release.set()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


def test_worker_recovers_job_after_previous_lease_expires(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    claimed = store.claim_next_job("worker-old", lease_seconds=30)
    assert claimed is not None
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_GENERATING,
        current_step="tts_generating",
        lease_until="2000-01-01T00:00:00+00:00",
    )
    workflow = CompletingWorkflow(store)
    restarted_worker = CloudAgentWorker(store, workflow, worker_id="worker-new")

    assert restarted_worker.run_once() is True
    assert workflow.calls == [(job.id, "worker-new")]


def test_worker_does_not_auto_claim_paused_or_human_required_jobs(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    paused = store.create_job(_request(subject="paused"))
    human = store.create_job(_request(subject="human"))
    store.patch_job(paused.id, status=CloudJobStatus.PAUSED)
    store.patch_job(human.id, status=CloudJobStatus.HUMAN_REQUIRED)
    workflow = CompletingWorkflow(store)
    worker = CloudAgentWorker(store, workflow, worker_id="worker-a")

    assert worker.run_once() is False
    assert workflow.calls == []


def test_worker_records_unexpected_workflow_failure_without_crashing(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    worker = CloudAgentWorker(store, ExplodingWorkflow(), worker_id="worker-a")

    assert worker.run_once() is True

    persisted = store.get_job(job.id)
    assert persisted is not None
    assert persisted.status is CloudJobStatus.HUMAN_REQUIRED
    assert persisted.error_code == "WORKER_RUNTIME_ERROR"
    assert persisted.error_message == "Cloud Agent workflow stopped: RuntimeError"


def test_default_worker_id_contains_hostname_pid_and_random_suffix(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    worker = CloudAgentWorker(store, CompletingWorkflow(store))

    hostname, pid, suffix = worker.worker_id.split(":", maxsplit=2)
    assert hostname
    assert pid.isdigit()
    assert len(suffix) == 32
    int(suffix, 16)


def test_worker_module_main_builds_and_runs_production_worker(monkeypatch):
    calls = []

    class FakeProductionWorker:
        def run_forever(self):
            calls.append("run_forever")

    monkeypatch.setattr(factory, "build_worker", FakeProductionWorker)

    worker_module.main()

    assert calls == ["run_forever"]


def test_supervisor_claims_and_child_completes_job(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    clock = FakeClock()
    launcher = FakeLauncher(store, clock, complete=True)
    worker = CloudAgentWorker(
        store,
        process_launcher=launcher,
        worker_id="worker-child",
        clock=clock,
        lease_seconds=30,
        lease_renew_interval_seconds=10,
    )

    assert worker.run_once() is True
    assert store.get_job(job.id).status is CloudJobStatus.COMPLETED
    assert launcher.started == [(job.id, "worker-child")]


def test_queued_wait_time_is_not_counted_as_active_stall(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    clock = FakeClock()
    clock.advance(hours=2)
    claimed_at = clock.now().isoformat(timespec="microseconds")
    launcher = FakeLauncher(store, clock, complete=True)
    worker = CloudAgentWorker(
        store,
        process_launcher=launcher,
        worker_id="worker-child",
        clock=clock,
    )

    worker.run_once()

    claimed = store.get_job(job.id)
    assert claimed.last_progress_at == claimed_at


def test_canva_twenty_minute_idle_stops_old_child_before_restart(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    clock = FakeClock()
    stalled_at = (clock.now() - timedelta(minutes=20)).isoformat(timespec="microseconds")
    store.patch_job(
        job.id,
        status=CloudJobStatus.CANVA_EDITING,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="canva_editing",
        last_progress_at=stalled_at,
    )
    launcher = FakeLauncher(store, clock, complete_after_restart=True)
    termination = FakeTerminationService()
    worker = CloudAgentWorker(
        store,
        process_launcher=launcher,
        termination_service=termination,
        worker_id="worker-child",
        clock=clock,
        lease_seconds=120,
        lease_renew_interval_seconds=40,
        canva_stall_seconds=1200,
        job_stall_seconds=3600,
    )

    worker.run_once()

    assert launcher.events[:4] == [
        "start_attempt_1",
        "terminate",
        "confirmed_stopped",
        "start_attempt_2",
    ]
    assert store.get_job(job.id).canva_restart_attempts == 1
    assert termination.calls == []


def test_global_hour_idle_preempts_unused_canva_budget(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    clock = FakeClock()
    stalled_at = (clock.now() - timedelta(hours=1)).isoformat(timespec="microseconds")
    store.patch_job(
        job.id,
        status=CloudJobStatus.CANVA_EDITING,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="canva_editing",
        last_progress_at=stalled_at,
        canva_restart_attempts=2,
    )
    launcher = FakeLauncher(store, clock)
    termination = FakeTerminationService()
    worker = CloudAgentWorker(
        store,
        process_launcher=launcher,
        termination_service=termination,
        worker_id="worker-child",
        clock=clock,
        canva_stall_seconds=1200,
        job_stall_seconds=3600,
    )

    worker.run_once()

    assert launcher.events == ["start_attempt_3", "terminate", "confirmed_stopped"]
    assert termination.calls == [
        TerminationCall(job.id, True, "JOB_STALLED_TIMEOUT", "canva")
    ]


def test_factory_builds_parent_supervisor_without_parent_browser(monkeypatch, tmp_path):
    monkeypatch.setitem(
        factory.config.app, "cloud_agent_db_path", str(tmp_path / "agent.sqlite3")
    )

    def fail_parent_browser(*_args, **_kwargs):
        raise AssertionError("browser must only be built inside the job child")

    monkeypatch.setattr(factory, "PersistentBrowserManager", fail_parent_browser)

    worker = factory.build_worker()

    assert worker.process_launcher is not None
    assert worker.workflow is None


def test_canva_restart_budget_exhaustion_deletes_without_sixth_attempt(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    clock = FakeClock()
    store.patch_job(
        job.id,
        status=CloudJobStatus.CANVA_EDITING,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="canva_editing",
        last_progress_at=(
            clock.now() - timedelta(minutes=20)
        ).isoformat(timespec="microseconds"),
        canva_restart_attempts=4,
    )
    launcher = FakeLauncher(store, clock)
    termination = FakeTerminationService()
    worker = CloudAgentWorker(
        store,
        process_launcher=launcher,
        termination_service=termination,
        worker_id="worker-child",
        clock=clock,
        canva_restart_retries=4,
    )

    worker.run_once()

    assert launcher.events == ["start_attempt_5", "terminate", "confirmed_stopped"]
    assert termination.calls == [
        TerminationCall(job.id, True, "CANVA_RESTART_EXHAUSTED", "canva")
    ]
