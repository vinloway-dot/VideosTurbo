from app.models.cloud_agent import ServiceSessionStatus, SessionCheckResult
from app.services.cloud_agent.errors import HumanRequiredError
from app.services.cloud_agent.session import SessionManager


def _result(service: str, status: ServiceSessionStatus, message: str = ""):
    return SessionCheckResult(
        service=service,
        status=status,
        message=message,
        checked_at="2026-08-22T13:00:00+00:00",
    )


class SequenceProvider:
    def __init__(self, service: str, checks, repair=None):
        self.service = service
        self.checks = list(checks)
        self.repair = repair
        self.check_calls = []
        self.repair_calls = []

    def check_session(self, *, headed: bool = False, job_id: str = ""):
        self.check_calls.append(headed)
        if not self.checks:
            raise AssertionError("unexpected extra session check")
        return self.checks.pop(0)

    def repair_session(self, *, headed: bool = False, job_id: str = ""):
        self.repair_calls.append(headed)
        if self.repair is None:
            raise AssertionError("unexpected repair attempt")
        return self.repair


class OpenPageSequenceProvider(SequenceProvider):
    def __init__(self, service: str, checks, repair=None):
        super().__init__(service, checks, repair)
        self.page_check_calls = []
        self.page_repair_calls = []

    def check_open_page(self, page, *, job_id: str = ""):
        self.page_check_calls.append((page, job_id))
        if not self.checks:
            raise AssertionError("unexpected extra page session check")
        return self.checks.pop(0)

    def repair_open_page(self, page, *, job_id: str = ""):
        self.page_repair_calls.append((page, job_id))
        if self.repair is None:
            raise AssertionError("unexpected page repair attempt")
        return self.repair


def test_check_all_returns_each_service_without_attempting_repair():
    flow = SequenceProvider(
        "google_flow", [_result("google_flow", ServiceSessionStatus.READY)]
    )
    canva = SequenceProvider("canva", [_result("canva", ServiceSessionStatus.SESSION_EXPIRED)])
    manager = SessionManager({"google_flow": flow, "canva": canva})

    results = manager.check_all()

    assert results["google_flow"].status is ServiceSessionStatus.READY
    assert results["canva"].status is ServiceSessionStatus.SESSION_EXPIRED
    assert flow.repair_calls == []
    assert canva.repair_calls == []


def test_ensure_all_ready_skips_canva_without_opening_another_session():
    flow = SequenceProvider(
        "google_flow", [_result("google_flow", ServiceSessionStatus.READY)]
    )
    canva = SequenceProvider(
        "canva", [_result("canva", ServiceSessionStatus.READY)]
    )
    manager = SessionManager({"google_flow": flow, "canva": canva})

    results = manager.ensure_all_ready("job-1", skip_services=("canva",))

    assert set(results) == {"google_flow"}
    assert flow.check_calls == [False]
    assert canva.check_calls == []


def test_ensure_service_ready_returns_ready_without_repair():
    flow = SequenceProvider(
        "google_flow", [_result("google_flow", ServiceSessionStatus.READY)]
    )
    manager = SessionManager({"google_flow": flow})

    result = manager.ensure_service_ready("google_flow", job_id="job-1")

    assert result.status is ServiceSessionStatus.READY
    assert flow.check_calls == [False]
    assert flow.repair_calls == []


def test_ensure_open_page_ready_reuses_the_assembly_browser_page():
    canva = OpenPageSequenceProvider(
        "canva", [_result("canva", ServiceSessionStatus.READY)]
    )
    manager = SessionManager({"canva": canva})
    page = object()

    result = manager.ensure_open_page_ready("canva", page, job_id="job-1")

    assert result.status is ServiceSessionStatus.READY
    assert canva.page_check_calls == [(page, "job-1")]
    assert canva.check_calls == []


def test_ensure_service_ready_uses_one_safe_auto_relogin_then_verifies():
    flow = SequenceProvider(
        "google_flow",
        [
            _result("google_flow", ServiceSessionStatus.SESSION_EXPIRED),
            _result("google_flow", ServiceSessionStatus.READY),
        ],
        repair=_result("google_flow", ServiceSessionStatus.AUTO_RELOGIN),
    )
    manager = SessionManager({"google_flow": flow})

    result = manager.ensure_service_ready("google_flow", job_id="job-1")

    assert result.status is ServiceSessionStatus.READY
    assert flow.check_calls == [False, False]
    assert flow.repair_calls == [False]


def test_ensure_service_ready_never_repairs_security_challenge():
    canva = SequenceProvider(
        "canva", [_result("canva", ServiceSessionStatus.CAPTCHA_REQUIRED)]
    )
    manager = SessionManager({"canva": canva})

    try:
        manager.ensure_service_ready("canva", job_id="job-1")
    except HumanRequiredError as exc:
        assert "CAPTCHA_REQUIRED" in str(exc)
        assert "canva" in str(exc)
    else:
        raise AssertionError("expected CAPTCHA to require human recovery")

    assert canva.repair_calls == []


def test_ensure_service_ready_failed_auto_relogin_requires_human():
    canva = SequenceProvider(
        "canva",
        [
            _result("canva", ServiceSessionStatus.SESSION_EXPIRED),
            _result("canva", ServiceSessionStatus.SESSION_EXPIRED),
        ],
        repair=_result("canva", ServiceSessionStatus.AUTO_RELOGIN),
    )
    manager = SessionManager({"canva": canva})

    try:
        manager.ensure_service_ready("canva", job_id="job-1")
    except HumanRequiredError as exc:
        assert "SESSION_EXPIRED" in str(exc)
    else:
        raise AssertionError("expected unresolved expired session to require human recovery")

    assert canva.repair_calls == [False]
    assert canva.check_calls == [False, False]


def test_ensure_service_ready_network_error_is_not_treated_as_human_challenge():
    flow = SequenceProvider(
        "google_flow",
        [_result("google_flow", ServiceSessionStatus.ERROR, "network unavailable")],
    )
    manager = SessionManager({"google_flow": flow})

    try:
        manager.ensure_service_ready("google_flow", job_id="job-1")
    except RuntimeError as exc:
        assert "network unavailable" in str(exc)
    else:
        raise AssertionError("expected service error to fail readiness")

    assert flow.repair_calls == []


def test_ensure_all_ready_checks_both_services():
    flow = SequenceProvider(
        "google_flow", [_result("google_flow", ServiceSessionStatus.READY)]
    )
    canva = SequenceProvider("canva", [_result("canva", ServiceSessionStatus.READY)])
    manager = SessionManager({"google_flow": flow, "canva": canva})

    results = manager.ensure_all_ready(job_id="job-1")

    assert set(results) == {"google_flow", "canva"}
    assert all(result.status is ServiceSessionStatus.READY for result in results.values())


def test_headed_session_policy_applies_to_check_repair_and_verify():
    flow = SequenceProvider(
        "google_flow",
        [
            _result("google_flow", ServiceSessionStatus.SESSION_EXPIRED),
            _result("google_flow", ServiceSessionStatus.READY),
        ],
        repair=_result("google_flow", ServiceSessionStatus.AUTO_RELOGIN),
    )
    manager = SessionManager({"google_flow": flow}, headed=True)

    result = manager.ensure_service_ready("google_flow", job_id="job-1")

    assert result.status is ServiceSessionStatus.READY
    assert flow.check_calls == [True, True]
    assert flow.repair_calls == [True]
