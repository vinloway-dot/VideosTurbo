from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.workflow import CloudAgentWorkflow


class WorkerAwarePreflight:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def ensure_ready(self, job_id: str, *, worker_id: str):
        self.calls.append((job_id, worker_id))
        self.store.patch_job(job_id, control_request=CloudControlRequest.PAUSE)


class NeverTTS:
    def generate(self, job, output_path):
        raise AssertionError("TTS must not run after preflight requests pause")


class NeverFlow:
    def acquire_workspace(self, job):
        raise AssertionError("Flow must not run")


class NeverCanva:
    def assemble_and_export(self, job, clips, audio, output):
        raise AssertionError("Canva must not run")


def test_workflow_passes_claimed_worker_id_into_preflight(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    request = CloudJobCreate(
        subject="Worker context",
        script="A valid narration script.",
        master_prompt="Create six videos from this narration.",
        clip_plan=empty_six_clip_plan(target_words=130),
        language="English",
        target_words=130,
        tts_provider="azure-tts-v1",
        voice_id="en-US-JennyNeural-Female",
        voice_speed=1.0,
    )
    created = store.create_job(request)
    worker_id = "worker-context"
    store.update_worker_heartbeat(worker_id)
    claimed = store.claim_next_job(worker_id, lease_seconds=60)
    assert claimed is not None
    assert claimed.id == created.id

    preflight = WorkerAwarePreflight(store)
    workflow = CloudAgentWorkflow(
        store,
        CloudJobStorage(tmp_path / "jobs"),
        preflight,
        NeverTTS(),
        NeverFlow(),
        NeverCanva(),
        tts_min_duration=1.0,
        canva_min_playback_speed=0.85,
        final_duration_tolerance_seconds=1.0,
        final_min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    result = workflow.run(created.id, worker_id=worker_id)

    assert preflight.calls == [(created.id, worker_id)]
    assert result.status is CloudJobStatus.PAUSED
    assert result.checkpoint is CloudJobCheckpoint.PREFLIGHT_PASSED
