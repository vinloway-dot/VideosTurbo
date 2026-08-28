import pytest

from app.models.cloud_agent import CloudJobStatus
from app.services.cloud_agent.incidents import (
    CloudJobIncidentStore,
    JobTerminationService,
    JobTerminationUnsafe,
)
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage

from .test_job_store import _request


def test_incident_store_persists_and_dismisses_sanitized_incident(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    jobs = CloudJobStore(str(db_path))
    job = jobs.create_job(_request())
    incidents = CloudJobIncidentStore(str(db_path))

    created = incidents.create_pending(
        job,
        reason_code="JOB_STALLED_TIMEOUT",
        stage="canva",
        message_th="งานหยุดเกินเวลาที่กำหนด",
    )

    assert incidents.list_unread() == (created,)
    dismissed = incidents.dismiss(created.id)
    assert dismissed.dismissed_at
    assert incidents.list_unread() == ()


def test_finalize_and_delete_is_one_terminal_transaction(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    jobs = CloudJobStore(str(db_path))
    job = jobs.create_job(_request())
    jobs.patch_job(
        job.id,
        status=CloudJobStatus.HUMAN_REQUIRED,
        worker_id="",
        lease_until="",
    )
    incidents = CloudJobIncidentStore(str(db_path))
    incident = incidents.create_pending(
        jobs.get_job(job.id),
        reason_code="FLOW_RECOVERY_EXHAUSTED",
        stage="google_flow",
        message_th="สร้างคลิปทดแทนไม่สำเร็จ",
    )

    finalized = incidents.finalize_and_delete_job(incident.id, job.id)

    assert finalized.finalized is True
    assert jobs.get_job(job.id) is None
    assert incidents.list_unread() == (finalized,)


def test_finalize_refuses_a_still_claimable_job(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    jobs = CloudJobStore(str(db_path))
    job = jobs.create_job(_request())
    incidents = CloudJobIncidentStore(str(db_path))
    incident = incidents.create_pending(
        job,
        reason_code="JOB_STALLED_TIMEOUT",
        stage="google_flow",
        message_th="งานหยุดเกินเวลาที่กำหนด",
    )

    with pytest.raises(ValueError, match="still claimable"):
        incidents.finalize_and_delete_job(incident.id, job.id)

    assert jobs.get_job(job.id) is not None
    assert incidents.list_unread()[0].finalized is False


def _termination_fixture(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    jobs = CloudJobStore(str(db_path))
    job = jobs.create_job(_request())
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.prepare(job.id)
    incidents = CloudJobIncidentStore(str(db_path))
    return (
        JobTerminationService(jobs, storage, incidents),
        jobs,
        storage,
        incidents,
        job,
    )


def test_termination_refuses_to_delete_while_child_is_alive(tmp_path):
    service, jobs, storage, _incidents, job = _termination_fixture(tmp_path)

    with pytest.raises(JobTerminationUnsafe):
        service.delete_stopped_job(
            job.id,
            child_stopped=False,
            reason_code="JOB_STALLED_TIMEOUT",
            stage="canva",
        )

    assert jobs.get_job(job.id) is not None
    assert storage._paths(job.id).job_dir.exists()


def test_terminal_cleanup_deletes_local_job_but_keeps_incident(tmp_path):
    service, jobs, storage, incidents, job = _termination_fixture(tmp_path)

    incident = service.delete_stopped_job(
        job.id,
        child_stopped=True,
        reason_code="FLOW_RECOVERY_EXHAUSTED",
        stage="google_flow",
    )

    assert jobs.get_job(job.id) is None
    assert not storage._paths(job.id).job_dir.exists()
    assert incidents.list_unread()[0].id == incident.id
    assert incident.finalized is True


def test_purge_failure_keeps_unclaimable_job_and_cleanup_incident(
    tmp_path, monkeypatch
):
    service, jobs, storage, incidents, job = _termination_fixture(tmp_path)

    def fail_purge(_staged):
        raise OSError("disk failure")

    monkeypatch.setattr(storage, "purge_staged_job", fail_purge)
    incident = service.delete_stopped_job(
        job.id,
        child_stopped=True,
        reason_code="JOB_STALLED_TIMEOUT",
        stage="canva",
    )

    retained = jobs.get_job(job.id)
    assert retained.status is CloudJobStatus.HUMAN_REQUIRED
    assert retained.worker_id == ""
    assert incident.reason_code == "JOB_DELETE_CLEANUP_FAILED"
    assert incidents.list_unread()[0].reason_code == "JOB_DELETE_CLEANUP_FAILED"
