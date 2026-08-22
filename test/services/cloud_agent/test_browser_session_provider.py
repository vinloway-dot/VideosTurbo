from contextlib import contextmanager
from pathlib import Path

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers import canva, google_flow


class FakeLocator:
    def __init__(self, page, *, visible=True):
        self.page = page
        self.visible = visible

    def is_visible(self):
        return self.visible

    def click(self):
        self.page.clicks += 1


class FakePage:
    def __init__(self, *, url, html, continue_google=False):
        self.url = url
        self.html = html
        self.continue_google = continue_google
        self.goto_calls = []
        self.clicks = 0

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url if self.url is None else self.url

    def content(self):
        return self.html

    def get_by_role(self, role, *, name):
        assert role == "button"
        visible = self.continue_google and "continue" in str(name).lower()
        return FakeLocator(self, visible=visible)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


class FakeBrowserManager:
    def __init__(self, page, tmp_path):
        self.page = page
        self.tmp_path = tmp_path
        self.open_calls = []
        self.evidence_calls = []

    @contextmanager
    def open(self, service, *, headed=None):
        self.open_calls.append((service, headed))
        yield FakeContext(self.page)

    def capture_evidence(self, job_id, service, page, *, label):
        self.evidence_calls.append((job_id, service, label))
        root = self.tmp_path / job_id
        root.mkdir(parents=True, exist_ok=True)
        png = root / f"{service}-{label}.png"
        html = root / f"{service}-{label}.html"
        png.write_bytes(b"png")
        html.write_text(page.content(), encoding="utf-8")
        return png, html


def _flow_ready_html():
    return '<button aria-label="Agent">Agent</button><textarea aria-label="Prompt"></textarea>'


def _canva_ready_html():
    return '<main>Canva Editor</main><button aria-label="Share">Share</button>'


def test_google_flow_provider_opens_real_target_and_captures_job_evidence(tmp_path):
    page = FakePage(
        url="https://labs.google/fx/tools/flow/project/demo",
        html=_flow_ready_html(),
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider_cls = getattr(google_flow, "GoogleFlowSessionProvider")
    provider = provider_cls(browser, service_url="https://labs.google/fx/tools/flow")

    result = provider.check_session(job_id="job-1", headed=False)

    assert result.status is ServiceSessionStatus.READY
    assert browser.open_calls == [("google_flow", False)]
    assert page.goto_calls == [
        ("https://labs.google/fx/tools/flow", {"wait_until": "domcontentloaded"})
    ]
    assert browser.evidence_calls == [("job-1", "google_flow", "session-check")]
    assert Path(result.evidence_path).name == "google_flow-session-check.png"


def test_canva_provider_opens_template_and_captures_job_evidence(tmp_path):
    page = FakePage(
        url="https://www.canva.com/design/demo/edit",
        html=_canva_ready_html(),
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider_cls = getattr(canva, "CanvaSessionProvider")
    provider = provider_cls(
        browser,
        service_url="https://www.canva.com/design/demo/edit",
    )

    result = provider.check_session(job_id="job-2", headed=True)

    assert result.status is ServiceSessionStatus.READY
    assert browser.open_calls == [("canva", True)]
    assert browser.evidence_calls == [("job-2", "canva", "session-check")]


def test_provider_safe_repair_clicks_only_continue_with_google(tmp_path):
    page = FakePage(
        url="https://www.canva.com/login",
        html='<button>Continue with Google</button>',
        continue_google=True,
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider_cls = getattr(canva, "CanvaSessionProvider")
    provider = provider_cls(browser, service_url="https://www.canva.com/design/demo/edit")

    result = provider.repair_session(job_id="job-3", headed=False)

    assert result.status is ServiceSessionStatus.AUTO_RELOGIN
    assert page.clicks == 1
    assert browser.evidence_calls == [("job-3", "canva", "session-repair")]


def test_provider_never_clicks_through_security_challenge(tmp_path):
    page = FakePage(
        url="https://accounts.google.com/v3/signin/challenge/totp",
        html='<div>2-Step Verification</div><input autocomplete="one-time-code" />',
        continue_google=True,
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider_cls = getattr(google_flow, "GoogleFlowSessionProvider")
    provider = provider_cls(browser, service_url="https://labs.google/fx/tools/flow")

    result = provider.repair_session(job_id="job-4", headed=False)

    assert result.status is ServiceSessionStatus.TWO_FACTOR_REQUIRED
    assert page.clicks == 0


def test_provider_missing_service_url_is_configuration_error(tmp_path):
    page = FakePage(url="about:blank", html="")
    browser = FakeBrowserManager(page, tmp_path)
    provider_cls = getattr(google_flow, "GoogleFlowSessionProvider")
    provider = provider_cls(browser, service_url="")

    result = provider.check_session(job_id="job-5")

    assert result.status is ServiceSessionStatus.ERROR
    assert "URL" in result.message
    assert browser.open_calls == []
