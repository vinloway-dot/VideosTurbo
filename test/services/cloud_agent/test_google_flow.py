from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.errors import MediaValidationError
from app.services.cloud_agent.providers import google_flow
from app.services.cloud_agent.providers.google_flow import classify_google_flow_session


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "resources" / "cloud_agent" / "google_flow"


@pytest.mark.parametrize(
    ("fixture_name", "url", "expected"),
    [
        ("ready.html", "https://labs.google/fx/tools/flow/project/demo", ServiceSessionStatus.READY),
        ("login.html", "https://accounts.google.com/v3/signin/identifier", ServiceSessionStatus.SESSION_EXPIRED),
        ("continue_google.html", "https://labs.google/fx/tools/flow", ServiceSessionStatus.SESSION_EXPIRED),
        ("password.html", "https://accounts.google.com/v3/signin/challenge/pwd", ServiceSessionStatus.LOGIN_REQUIRED),
        ("captcha.html", "https://accounts.google.com/v3/signin/challenge", ServiceSessionStatus.CAPTCHA_REQUIRED),
        ("two_factor.html", "https://accounts.google.com/v3/signin/challenge/totp", ServiceSessionStatus.TWO_FACTOR_REQUIRED),
        ("verification.html", "https://accounts.google.com/v3/signin/challenge/ipp", ServiceSessionStatus.VERIFICATION_REQUIRED),
        ("unknown.html", "https://labs.google/fx/tools/flow", ServiceSessionStatus.ERROR),
    ],
)
def test_google_flow_session_fixture_classification(fixture_name, url, expected):
    html = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")

    assert classify_google_flow_session(url=url, html=html) is expected


def test_google_flow_challenge_wins_over_ready_marker():
    html = """
    <html><body>
      <button aria-label="Agent">Agent</button>
      <textarea aria-label="Prompt"></textarea>
      <div>2-Step Verification</div>
      <input autocomplete="one-time-code" />
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.TWO_FACTOR_REQUIRED
    )


def test_google_flow_project_shell_ignores_passive_recaptcha_legal_notice():
    html = """
    <html><body>
      <main>
        <h1>Meet your agent</h1>
        <section>Your agent in Google Flow</section>
        <button>Agent</button>
      </main>
      <footer>
        This site is protected by <a href="privacy">reCAPTCHA</a>. The Google
        <a href="privacy">Privacy Policy</a> and
        <a href="terms">Terms of Service</a> apply.
      </footer>
      <script src="recaptcha-runtime.js"></script>
      <iframe title="reCAPTCHA" src="recaptcha-frame"></iframe>
      <textarea class="g-recaptcha-response" hidden></textarea>
      <template>Sign in</template>
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/th/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.READY
    )


def test_google_flow_active_captcha_wins_over_project_shell():
    html = """
    <html><body>
      <main>
        <h1>Meet your agent</h1>
        <div class="g-recaptcha">Confirm you're not a robot</div>
      </main>
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/th/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.CAPTCHA_REQUIRED
    )


class FakeDownload:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    def save_as(self, path):
        self.page.download_attempts[self.index] += 1
        Path(path).write_bytes(f"video-{self.index + 1}".encode())


class FakeDownloadExpectation:
    def __init__(self, page):
        self.page = page
        self.index = None

    def __enter__(self):
        self.page.active_download = self
        return self

    def __exit__(self, exc_type, exc, tb):
        self.page.active_download = None

    @property
    def value(self):
        assert self.index is not None
        return FakeDownload(self.page, self.index)


class FakeLocator:
    def __init__(self, page, kind, index=None):
        self.page = page
        self.kind = kind
        self.index = index

    def click(self):
        if self.kind == "download":
            assert self.page.active_download is not None
            self.page.active_download.index = self.index
            return
        self.page.actions.append(("click", self.kind))

    def fill(self, value):
        self.page.actions.append(("fill", self.kind, value))

    def count(self):
        assert self.kind == "downloads"
        return self.page.download_count

    def nth(self, index):
        assert self.kind == "downloads"
        return FakeLocator(self.page, "download", index=index)


class FakePage:
    def __init__(self, *, progress_html, download_count=6):
        self.url = "about:blank"
        self.progress_html = list(progress_html)
        self.download_count = download_count
        self.download_attempts = [0 for _ in range(download_count)]
        self.goto_calls = []
        self.actions = []
        self.active_download = None
        self._content_index = 0

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url

    def content(self):
        index = min(self._content_index, len(self.progress_html) - 1)
        html = self.progress_html[index]
        self._content_index += 1
        return html

    def get_by_role(self, role, *, name):
        pattern = getattr(name, "pattern", str(name)).lower()
        if role == "button" and "agent" in pattern:
            return FakeLocator(self, "agent")
        if role == "button" and "generate" in pattern:
            return FakeLocator(self, "generate")
        if role == "button" and "download" in pattern:
            return FakeLocator(self, "downloads")
        raise AssertionError(f"unexpected role lookup: {role} {pattern}")

    def get_by_label(self, name):
        assert "prompt" in str(name).lower()
        return FakeLocator(self, "prompt")

    def expect_download(self):
        return FakeDownloadExpectation(self)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


class FakeBrowserManager:
    def __init__(self, page):
        self.page = page
        self.open_calls = []

    @contextmanager
    def open(self, service, *, headed=None):
        self.open_calls.append((service, headed))
        yield FakeContext(self.page)


class FakeSessionManager:
    def __init__(self):
        self.calls = []

    def ensure_service_ready(self, service, job_id):
        self.calls.append((service, job_id))
        return object()


def _job():
    return SimpleNamespace(
        id="job-123",
        master_prompt="Create six chronological videos about Saturn's hexagon.",
    )


def _client(page, *, timeout_seconds=30.0, max_download_attempts=3):
    client_cls = getattr(google_flow, "GoogleFlowClient", None)
    assert client_cls is not None, "Task 8 Google Flow production client is not implemented"
    sessions = FakeSessionManager()
    client = client_cls(
        FakeBrowserManager(page),
        sessions,
        service_url="https://labs.google/fx/tools/flow/project/demo",
        generation_timeout_seconds=timeout_seconds,
        poll_seconds=0.0,
        max_download_attempts=max_download_attempts,
        expected_width=1080,
        expected_height=1920,
    )
    return client, sessions


def test_google_flow_generation_uses_agent_mode_prompt_and_observable_progress(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=[
            "<div>Generation progress 2 / 6</div>",
            "<div>Generation progress 4 / 6</div>",
            "<div>Generation progress 6 / 6</div>",
        ]
    )
    client, sessions = _client(page)
    validations = []
    monkeypatch.setattr(
        google_flow,
        "validate_video",
        lambda path, **kwargs: validations.append((Path(path), kwargs)),
    )

    result = client.generate_and_download(_job(), tmp_path / "flow")

    assert sessions.calls == [("google_flow", "job-123")]
    assert page.goto_calls == [
        (
            "https://labs.google/fx/tools/flow/project/demo",
            {"wait_until": "domcontentloaded"},
        )
    ]
    assert page.actions[:3] == [
        ("click", "agent"),
        ("fill", "prompt", _job().master_prompt),
        ("click", "generate"),
    ]
    assert [path.name for path in result] == [f"clip_{index:02d}.mp4" for index in range(1, 7)]
    assert [path.name for path, _ in validations] == [path.name for path in result]
    assert page.download_attempts == [1, 1, 1, 1, 1, 1]


def test_google_flow_selective_retry_redownloads_only_invalid_clip(monkeypatch, tmp_path):
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, _ = _client(page)
    validation_attempts = {}

    def validate(path, **_kwargs):
        name = Path(path).name
        validation_attempts[name] = validation_attempts.get(name, 0) + 1
        if name == "clip_03.mp4" and validation_attempts[name] == 1:
            raise MediaValidationError("corrupt clip")

    monkeypatch.setattr(google_flow, "validate_video", validate)

    result = client.generate_and_download(_job(), tmp_path / "flow")

    assert len(result) == 6
    assert page.download_attempts == [1, 1, 2, 1, 1, 1]
    assert validation_attempts["clip_03.mp4"] == 2


def test_google_flow_retry_budget_is_bounded(monkeypatch, tmp_path):
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, _ = _client(page, max_download_attempts=2)

    def always_invalid(path, **_kwargs):
        if Path(path).name == "clip_02.mp4":
            raise MediaValidationError("still corrupt")

    monkeypatch.setattr(google_flow, "validate_video", always_invalid)

    with pytest.raises(MediaValidationError, match="clip_02.mp4"):
        client.generate_and_download(_job(), tmp_path / "flow")

    assert page.download_attempts == [1, 2, 0, 0, 0, 0]


def test_google_flow_generation_timeout_is_bounded(monkeypatch, tmp_path):
    page = FakePage(progress_html=["<div>Generation progress 2 / 6</div>"])
    client, _ = _client(page, timeout_seconds=1.0)
    clock = iter([0.0, 0.5, 1.1])
    monkeypatch.setattr(google_flow.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(MediaValidationError, match="timed out"):
        client.generate_and_download(_job(), tmp_path / "flow")

    assert page.download_attempts == [0, 0, 0, 0, 0, 0]


def test_google_flow_requires_exactly_six_downloadable_results(monkeypatch, tmp_path):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        download_count=5,
    )
    client, _ = _client(page)
    monkeypatch.setattr(google_flow, "validate_video", lambda *_args, **_kwargs: None)

    with pytest.raises(MediaValidationError, match="exactly six|expected 6"):
        client.generate_and_download(_job(), tmp_path / "flow")
