import pytest

from app.models.cloud_agent import CloudJobStatus
from app.services.cloud_agent.incidents import CloudJobIncidentStore
from app.services.cloud_agent.job_store import CloudJobStore

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
