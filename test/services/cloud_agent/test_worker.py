import threading

from app.models.cloud_agent import CloudJobCheckpoint, CloudJobCreate, CloudJobStatus
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.worker import CloudAgentWorker


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


class RecordingStore(CloudJobStore):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.lease_renewed = threading.Event()

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        renewed = super().renew_lease(job_id, worker_id, lease_seconds)
        if renewed:
            self.lease_renewed.set()
        return renewed


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


def test_default_worker_id_contains_hostname_pid_and_random_suffix(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    worker = CloudAgentWorker(store, CompletingWorkflow(store))

    hostname, pid, suffix = worker.worker_id.split(":", maxsplit=2)
    assert hostname
    assert pid.isdigit()
    assert len(suffix) == 32
    int(suffix, 16)
