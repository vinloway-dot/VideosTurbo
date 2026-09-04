from app.models.cloud_agent import ServiceSessionStatus, SessionCheckResult
from app.services.cloud_agent.session import SessionManager


class JobAwareProvider:
    def __init__(self):
        self.calls = []

    def check_session(self, *, headed=False, job_id=""):
        self.calls.append((headed, job_id))
        return SessionCheckResult(
            service="google_flow",
            status=ServiceSessionStatus.READY,
            checked_at="2026-08-22T14:00:00+00:00",
        )

    def repair_session(self, *, headed=False, job_id=""):
        raise AssertionError("repair must not run for READY session")


def test_ensure_service_ready_passes_job_id_to_provider_for_evidence():
    provider = JobAwareProvider()
    manager = SessionManager({"google_flow": provider})

    result = manager.ensure_service_ready("google_flow", job_id="job-evidence")

    assert result.status is ServiceSessionStatus.READY
    assert provider.calls == [(False, "job-evidence")]
