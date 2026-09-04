from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers import canva, google_flow


class FakeLocator:
    def __init__(self, page, *, visible=True):
        self.page = page
        self.visible = visible

    def is_visible(self):
        return self.visible

    def click(self):
        self.page.clicks += 1

    def wait_for(self, *, state, timeout):
        self.page.wait_calls.append((state, timeout))

    def or_(self, other):
        return self if self.visible else other

    @property
    def first(self):
        return self


class DelayedCanvaShareLocator(FakeLocator):
    def __init__(self, page, *, role):
        super().__init__(page)
        self.roles = frozenset({role})

    def or_(self, other):
        combined = DelayedCanvaShareLocator(self.page, role=next(iter(self.roles)))
        combined.roles = self.roles | other.roles
        return combined

    @property
    def first(self):
        return self

    def wait_for(self, *, state, timeout):
        self.page.wait_calls.append((tuple(sorted(self.roles)), state, timeout))
        if self.page.ready_role in self.roles:
            self.page.html = _canva_ready_html()


class FakePage:
    def __init__(self, *, url, html, continue_google=False):
        self.url = url
        self.html = html
        self.continue_google = continue_google
        self.goto_calls = []
        self.clicks = 0
        self.wait_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url if self.url is None else self.url

    def content(self):
        return self.html

    def get_by_role(self, role, *, name):
        if role == "menuitem":
            return FakeLocator(self)
        assert role == "button"
        visible = self.continue_google and "continue" in str(name).lower()
        return FakeLocator(self, visible=visible)


class DelayedCanvaPage(FakePage):
    def __init__(self, *, ready_role, **kwargs):
        super().__init__(**kwargs)
        self.ready_role = ready_role

    def get_by_role(self, role, *, name):
        name_pattern = getattr(name, "pattern", str(name))
        if role in {"button", "menuitem"} and "share" in name_pattern.lower():
            return DelayedCanvaShareLocator(self, role=role)
        return super().get_by_role(role, name=name)


class NoCanvaReadyWaitPage(FakePage):
    def get_by_role(self, role, *, name):
        name_pattern = getattr(name, "pattern", str(name))
        if role in {"button", "menuitem"} and "share" in name_pattern.lower():
            raise AssertionError("login and challenge states must not wait for Share")
        return super().get_by_role(role, name=name)


class TimedOutNavigationPage(FakePage):
    def goto(self, url, **kwargs):
        super().goto(url, **kwargs)
        raise PlaywrightTimeoutError("Timeout 120000ms exceeded")


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


class BlankInitialPageContext(FakeContext):
    def __init__(self, blank_page, usable_page):
        super().__init__(blank_page)
        self.usable_page = usable_page
        self.new_page_calls = 0

    def new_page(self):
        self.new_page_calls += 1
        return self.usable_page


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


def test_provider_page_opens_a_fresh_page_when_context_only_has_about_blank():
    blank_page = FakePage(url="about:blank", html="")
    usable_page = FakePage(
        url="https://www.canva.com/design/demo/edit",
        html=_canva_ready_html(),
    )
    context = BlankInitialPageContext(blank_page, usable_page)

    page = BrowserSessionProvider._page(context)

    assert page is usable_page
    assert context.new_page_calls == 1


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
        (
            "https://labs.google/fx/tools/flow",
            {"wait_until": "domcontentloaded", "timeout": 120_000},
        )
    ]
    assert browser.evidence_calls == [("job-1", "google_flow", "session-check")]
    assert Path(result.evidence_path).name == "google_flow-session-check.png"


def test_google_flow_provider_classifies_target_after_navigation_timeout(tmp_path):
    page = TimedOutNavigationPage(
        url="https://labs.google/fx/tools/flow/project/demo",
        html=_flow_ready_html(),
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider = google_flow.GoogleFlowSessionProvider(
        browser,
        service_url="https://labs.google/fx/tools/flow/project/demo",
    )

    result = provider.check_session(job_id="job-timeout", headed=False)

    assert result.status is ServiceSessionStatus.READY
    assert page.goto_calls == [
        (
            "https://labs.google/fx/tools/flow/project/demo",
            {"wait_until": "domcontentloaded", "timeout": 120_000},
        )
    ]


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


def test_canva_provider_waits_for_delayed_share_button_before_classifying(tmp_path):
    page = DelayedCanvaPage(
        ready_role="button",
        url="https://www.canva.com/design/demo/edit",
        html="<main>Loading editor</main>",
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider = canva.CanvaSessionProvider(
        browser,
        service_url="https://www.canva.com/design/demo/edit",
    )

    result = provider.check_session(job_id="job-delayed", headed=True)

    assert result.status is ServiceSessionStatus.READY
    assert page.wait_calls == [
        (("button", "menuitem"), "visible", 180_000),
    ]


def test_canva_provider_retains_legacy_share_menuitem_ready_marker(tmp_path):
    page = DelayedCanvaPage(
        ready_role="menuitem",
        url="https://www.canva.com/design/demo/edit",
        html="<main>Loading editor</main>",
    )
    browser = FakeBrowserManager(page, tmp_path)
    provider = canva.CanvaSessionProvider(
        browser,
        service_url="https://www.canva.com/design/demo/edit",
    )

    result = provider.check_session(job_id="job-legacy-share", headed=True)

    assert result.status is ServiceSessionStatus.READY
    assert page.wait_calls == [
        (("button", "menuitem"), "visible", 180_000),
    ]


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        (
            "https://www.canva.com/login",
            "<main>Log in to Canva</main>",
            ServiceSessionStatus.SESSION_EXPIRED,
        ),
        (
            "https://www.canva.com/design/demo/edit",
            "<main>2-Step Verification</main>",
            ServiceSessionStatus.TWO_FACTOR_REQUIRED,
        ),
    ],
)
def test_canva_provider_classifies_login_and_challenge_without_waiting_for_share(
    tmp_path,
    url,
    html,
    expected,
):
    page = NoCanvaReadyWaitPage(url=url, html=html)
    browser = FakeBrowserManager(page, tmp_path)
    provider = canva.CanvaSessionProvider(
        browser,
        service_url="https://www.canva.com/design/demo/edit",
    )

    result = provider.check_session(job_id="job-human-required", headed=True)

    assert result.status is expected
    assert page.wait_calls == []


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
    assert page.goto_calls == [
        (
            "https://labs.google/fx/tools/flow",
            {"wait_until": "domcontentloaded", "timeout": 120_000},
        )
    ]


def test_provider_missing_service_url_is_configuration_error(tmp_path):
    page = FakePage(url="about:blank", html="")
    browser = FakeBrowserManager(page, tmp_path)
    provider_cls = getattr(google_flow, "GoogleFlowSessionProvider")
    provider = provider_cls(browser, service_url="")

    result = provider.check_session(job_id="job-5")

    assert result.status is ServiceSessionStatus.ERROR
    assert "URL" in result.message
    assert browser.open_calls == []
