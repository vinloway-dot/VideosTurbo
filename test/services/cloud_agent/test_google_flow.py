from contextlib import contextmanager
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.errors import (
    FlowWorkspaceVerificationError,
    MediaValidationError,
)
from app.services.cloud_agent.storage import CloudJobStorage
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
        self.page.actions.append(("expect_download", "registered"))
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
        if self.kind == "launch":
            self.page.landing = False
            self.page.actions.append(("click", "launch"))
            return
        if self.kind == "delete_selected":
            self.page.actions.append(("click", "delete_selected"))
            if self.page.delete_requires_confirmation:
                self.page.confirmation_pending = True
            else:
                self.page.clip_names.clear()
                self.page.selected_clip_indexes.clear()
                self.page.inventory_sequence = [0, 0, 0]
                self.page.last_inventory_count = 0
            return
        if self.kind == "confirm_delete":
            self.page.actions.append(("click", "confirm_delete"))
            self.page.clip_names.clear()
            self.page.selected_clip_indexes.clear()
            self.page.confirmation_pending = False
            self.page.inventory_sequence = [0, 0, 0]
            self.page.last_inventory_count = 0
            return
        if self.kind == "generate":
            self.page.actions.append(("click", "generate"))
            if self.page.last_filled == google_flow.RENAME_CLIPS_INSTRUCTION:
                self.page.pending_clip_names = list(self.page.renamed_clip_names)
            elif self.page.generation_completion_names is not None:
                self.page.clip_names = list(self.page.generation_completion_names)
            return
        if self.kind == "bulk_download":
            assert self.page.active_download is not None
            self.page.active_download.index = 0
            self.page.actions.append(("click", "bulk_download"))
            return
        if self.kind == "download":
            assert self.page.active_download is not None
            self.page.active_download.index = self.index
            return
        self.page.actions.append(("click", self.kind))

    def check(self):
        assert self.kind == "checkbox"
        self.page.selected_clip_indexes.add(self.index)
        self.page.actions.append(("check", self.index))

    def fill(self, value):
        self.page.actions.append(("fill", self.kind, value))
        self.page.last_filled = value

    def count(self):
        if self.kind == "downloads":
            return self.page.download_count
        if self.kind == "launch":
            return int(self.page.landing)
        if self.kind == "agent":
            return int(not self.page.landing and self.page.agent_available)
        if self.kind == "checkboxes":
            return len(self.page.clip_names)
        if self.kind == "delete_selected":
            return int(
                self.page.delete_available
                and bool(self.page.clip_names)
                and len(self.page.selected_clip_indexes) == len(self.page.clip_names)
            )
        if self.kind == "empty_state":
            if self.page.empty_state_sequence:
                self.page.last_empty_state = self.page.empty_state_sequence.pop(0)
            else:
                self.page.last_empty_state = self.page.empty_state_available
            return int(not self.page.clip_names and self.page.last_empty_state)
        if self.kind in {"prompt", "composer", "generate", "bulk_download"}:
            return 1
        if self.kind == "media_control":
            return int(self.page.media_control_available)
        if self.kind == "media_list":
            if self.page.media_list_sequence:
                self.page.last_media_list_available = self.page.media_list_sequence.pop(0)
            return int(self.page.last_media_list_available)
        if self.kind == "inventory_cards":
            if self.page.inventory_sequence:
                self.page.last_inventory_count = self.page.inventory_sequence.pop(0)
            return self.page.last_inventory_count
        if self.kind == "busy":
            return int(self.page.busy)
        if self.kind == "progressbar":
            return int(self.page.progressbar)
        if self.kind == "missing":
            return 0
        if self.kind == "semantic_name":
            return self.page.clip_names.count(f"clip {self.index}")
        if self.kind in {"dialog", "confirm_delete"}:
            return int(self.page.confirmation_pending)
        raise AssertionError(f"count is unavailable for {self.kind}")

    def is_visible(self):
        if self.kind == "empty_state":
            return not self.page.clip_names and self.page.last_empty_state
        if self.kind == "media_list":
            return self.page.last_media_list_available
        return self.count() == 1

    def wait_for(self, **_kwargs):
        if not self.is_visible():
            raise AssertionError(f"{self.kind} did not become visible")

    def is_enabled(self):
        if self.kind == "generate" and self.page.agent_enabled_states:
            return self.page.agent_enabled_states.pop(0)
        return self.page.agent_ready

    def locator(self, selector):
        if self.kind == "agent" and str(selector).startswith("xpath="):
            return FakeLocator(self.page, "composer")
        if self.kind == "composer" and "textarea" in str(selector):
            return FakeLocator(self.page, "prompt")
        if self.kind == "media_list" and "role=\"button\"" in str(selector):
            return FakeLocator(self.page, "inventory_cards")
        raise AssertionError(f"unexpected nested locator: {self.kind} {selector}")

    def get_by_role(self, role, *, name=None, exact=None):
        del exact
        pattern = getattr(name, "pattern", str(name)).lower()
        if self.kind == "dialog" and role == "button" and (
            "delete" in pattern or "ลบ" in pattern
        ):
            return FakeLocator(self.page, "confirm_delete")
        raise AssertionError(f"unexpected nested role lookup: {self.kind} {role} {pattern}")

    def nth(self, index):
        if self.kind == "downloads":
            return FakeLocator(self.page, "download", index=index)
        if self.kind == "checkboxes":
            return FakeLocator(self.page, "checkbox", index=index)
        raise AssertionError(f"nth is unavailable for {self.kind}")


class FakePage:
    def __init__(
        self,
        *,
        progress_html,
        download_count=6,
        landing=False,
        agent_available=True,
        clip_names=None,
        empty_state_available=True,
        delete_available=True,
        prompt_label_available=True,
        generation_completion_names=None,
        renamed_clip_names=None,
        agent_enabled_states=None,
        delete_requires_confirmation=False,
        empty_state_sequence=None,
        document_ready=True,
        media_control_available=True,
        media_list_available=True,
        media_list_sequence=None,
        inventory_sequence=None,
        busy=False,
        progressbar=False,
        evaluate_errors=None,
    ):
        self.url = "about:blank"
        self.progress_html = list(progress_html)
        self.download_count = download_count
        self.download_attempts = [0 for _ in range(download_count)]
        self.goto_calls = []
        self.actions = []
        self.active_download = None
        self.landing = landing
        self.agent_available = agent_available
        self.clip_names = list(clip_names or [])
        self.empty_state_available = empty_state_available
        self.delete_available = delete_available
        self.selected_clip_indexes = set()
        self.reload_calls = []
        self.prompt_label_available = prompt_label_available
        self.generation_completion_names = generation_completion_names
        self.renamed_clip_names = list(
            renamed_clip_names
            or [f"clip {number}" for number in range(1, 7)]
        )
        self.pending_clip_names = None
        self.last_filled = ""
        self.agent_ready = True
        self.agent_enabled_states = list(agent_enabled_states or [False, True])
        self.delete_requires_confirmation = delete_requires_confirmation
        self.confirmation_pending = False
        self.empty_state_sequence = list(empty_state_sequence or [])
        self.last_empty_state = empty_state_available
        self.document_ready = document_ready
        self.media_control_available = media_control_available
        self.media_list_available = media_list_available
        self.media_list_sequence = list(media_list_sequence or [])
        self.last_media_list_available = media_list_available
        self.inventory_sequence = list(
            inventory_sequence if inventory_sequence is not None else [len(self.clip_names)]
        )
        self.last_inventory_count = self.inventory_sequence[0]
        self.busy = busy
        self.progressbar = progressbar
        self.evaluate_errors = list(evaluate_errors or [])
        self._content_index = 0

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        self.url = url

    def reload(self, **kwargs):
        self.reload_calls.append(kwargs)
        if self.pending_clip_names is not None:
            self.clip_names = self.pending_clip_names
            self.pending_clip_names = None

    def content(self):
        index = min(self._content_index, len(self.progress_html) - 1)
        html = self.progress_html[index]
        self._content_index += 1
        return html

    def evaluate(self, expression):
        assert expression == "document.readyState"
        if self.evaluate_errors:
            raise self.evaluate_errors.pop(0)
        return "complete" if self.document_ready else "interactive"

    def locator(self, selector):
        if selector == '[data-testid="virtuoso-item-list"]:visible':
            return FakeLocator(self, "media_list")
        if selector == '[aria-busy="true"]:visible':
            return FakeLocator(self, "busy")
        raise AssertionError(f"unexpected page locator: {selector}")

    def get_by_role(self, role, *, name=None, exact=None):
        del exact
        pattern = getattr(name, "pattern", str(name)).lower()
        if role in {"link", "button"} and "create with google flow" in pattern:
            return FakeLocator(self, "launch")
        if role == "button" and "download product clips" in pattern:
            return FakeLocator(self, "bulk_download")
        if role == "button" and "agent" in pattern:
            return FakeLocator(self, "agent")
        if role == "button" and ("all media" in pattern or "สื่อทั้งหมด" in pattern):
            return FakeLocator(self, "media_control")
        if role == "progressbar":
            return FakeLocator(self, "progressbar")
        if role == "button" and "generate" in pattern:
            return FakeLocator(self, "generate")
        if role == "button" and "download" in pattern:
            return FakeLocator(self, "downloads")
        if role == "checkbox":
            return FakeLocator(self, "checkboxes")
        if role == "button" and ("delete" in pattern or "ลบ" in pattern):
            return FakeLocator(self, "delete_selected")
        if role == "dialog":
            return FakeLocator(self, "dialog")
        raise AssertionError(f"unexpected role lookup: {role} {pattern}")

    def get_by_text(self, text, *, exact=None):
        del exact
        pattern = getattr(text, "pattern", str(text)).lower()
        if "start creating" in pattern or "เริ่มสร้าง" in pattern:
            return FakeLocator(self, "empty_state")
        semantic = re.fullmatch(r"clip\s+([1-6])", pattern)
        if semantic is not None:
            return FakeLocator(self, "semantic_name", index=int(semantic.group(1)))
        raise AssertionError(f"unexpected text lookup: {pattern}")

    def get_by_label(self, name):
        assert "prompt" in str(name).lower()
        return FakeLocator(
            self,
            "prompt" if self.prompt_label_available else "missing",
        )

    def expect_download(self):
        return FakeDownloadExpectation(self)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


class FakeBrowserManager:
    def __init__(self, page):
        self.page = page
        self.open_calls = []
        self.context_is_open = False

    @contextmanager
    def open(self, service, *, headed=None, lock_timeout_seconds=None):
        self.open_calls.append((service, headed, lock_timeout_seconds))
        self.context_is_open = True
        try:
            yield FakeContext(self.page)
        finally:
            self.context_is_open = False


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


def _client(
    page,
    *,
    timeout_seconds=30.0,
    workspace_lock_timeout_seconds=None,
    editor_ready_timeout_seconds=None,
    settled_poll_count=None,
):
    client_cls = getattr(google_flow, "GoogleFlowClient", None)
    assert client_cls is not None, "Task 8 Google Flow production client is not implemented"
    sessions = FakeSessionManager()
    kwargs = {
        "service_url": "https://labs.google/fx/tools/flow/project/demo",
        "generation_timeout_seconds": timeout_seconds,
        "poll_seconds": 0.0,
        "expected_width": 1080,
        "expected_height": 1920,
    }
    if workspace_lock_timeout_seconds is not None:
        kwargs["workspace_lock_timeout_seconds"] = workspace_lock_timeout_seconds
    if editor_ready_timeout_seconds is not None:
        kwargs["editor_ready_timeout_seconds"] = editor_ready_timeout_seconds
    if settled_poll_count is not None:
        kwargs["settled_poll_count"] = settled_poll_count
    client = client_cls(FakeBrowserManager(page), sessions, **kwargs)
    return client, sessions


def test_google_flow_workspace_context_holds_production_profile_lock():
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, sessions = _client(page, timeout_seconds=45.0)
    browser = client.browser

    acquire_workspace = getattr(client, "acquire_workspace", None)
    assert acquire_workspace is not None, "Flow workspace context is not implemented"
    with acquire_workspace(_job()) as workspace:
        assert browser.context_is_open is True
        assert workspace.page is page
        assert sessions.calls == [("google_flow", "job-123")]

    assert browser.context_is_open is False
    assert browser.open_calls == [("google_flow", False, 45.0)]
    assert page.goto_calls == [
        (
            "https://labs.google/fx/tools/flow/project/demo",
            {"wait_until": "domcontentloaded"},
        )
    ]


def test_google_flow_workspace_lock_timeout_can_differ_from_generation_timeout():
    page = FakePage(progress_html=["<div>Generation progress 6 / 6</div>"])
    client, _ = _client(
        page,
        timeout_seconds=45.0,
        workspace_lock_timeout_seconds=90.0,
    )

    with client.acquire_workspace(_job()):
        pass

    assert client.browser.open_calls == [("google_flow", False, 90.0)]


def test_google_flow_workspace_enters_project_from_observable_landing_control():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        landing=True,
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page

    assert ("click", "launch") in page.actions
    assert page.landing is False


def test_google_flow_workspace_requires_observable_project_editor(monkeypatch):
    page = FakePage(
        progress_html=["<div>Loading</div>"],
        agent_available=False,
    )
    client, _ = _client(page, timeout_seconds=1.0)
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job()):
            pass


def test_google_flow_editor_readiness_timeout_is_independent_from_generation(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Loading</div>"],
        agent_available=False,
        document_ready=False,
        media_control_available=False,
        media_list_available=False,
    )
    client, _ = _client(
        page,
        timeout_seconds=1800.0,
        editor_ready_timeout_seconds=1.0,
    )
    clock = iter([0.0, 0.5, 1.1])
    monkeypatch.setattr(google_flow.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(FlowWorkspaceVerificationError, match="project editor"):
        with client.acquire_workspace(_job()):
            pass


def test_google_flow_editor_readiness_retries_destroyed_navigation_context():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        evaluate_errors=[
            PlaywrightError(
                "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
            )
        ],
    )
    client, _ = _client(page, editor_ready_timeout_seconds=1.0)

    with client.acquire_workspace(_job()) as workspace:
        assert workspace.page is page


def test_flow_workspace_transient_empty_inventory_cannot_pass_generation_gate(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        inventory_sequence=[0, 0, 2, 2],
    )
    client, _ = _client(
        page,
        timeout_seconds=1.0,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    clock = iter([0.0, 0.0, 0.2, 0.4, 1.1])
    monkeypatch.setattr(google_flow.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty product workspace"):
            workspace.prepare_for_generation()

    assert ("click", "generate") not in page.actions


def test_flow_workspace_stable_settled_empty_inventory_passes_generation_gate():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        inventory_sequence=[0, 0, 0],
    )
    client, _ = _client(page, settled_poll_count=3)

    with client.acquire_workspace(_job()) as workspace:
        workspace.prepare_for_generation()

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert ("click", "generate") not in page.actions


def test_flow_workspace_preclean_deletes_stale_clips_then_verifies_empty():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["stale-a", "stale-b", "stale-c"],
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        preclean = getattr(workspace, "preclean_and_verify_empty", None)
        assert preclean is not None, "Flow workspace pre-clean is not implemented"
        preclean()

    assert page.actions[-4:] == [
        ("check", 0),
        ("check", 1),
        ("check", 2),
        ("click", "delete_selected"),
    ]
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert page.clip_names == []


def test_flow_workspace_preclean_refreshes_even_when_already_empty():
    page = FakePage(progress_html=["<div>Ready</div>"], clip_names=[])
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert not any(action[0] == "click" for action in page.actions)


def test_flow_workspace_preclean_confirms_observable_delete_dialog():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=["stale-a", "stale-b"],
        delete_requires_confirmation=True,
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert ("click", "delete_selected") in page.actions
    assert ("click", "confirm_delete") in page.actions
    assert page.clip_names == []


def test_flow_workspace_preclean_blocks_generation_when_inventory_is_unverifiable(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        media_list_sequence=[True, False, False],
    )
    client, _ = _client(page, timeout_seconds=1.0)
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty"):
            workspace.preclean_and_verify_empty()

    assert ("click", "generate") not in page.actions


def test_flow_workspace_preclean_waits_for_stable_empty_inventory_after_reload():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        inventory_sequence=[2, 0, 0, 0],
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        workspace.preclean_and_verify_empty()

    assert page.inventory_sequence == []


def test_flow_generation_does_not_implicitly_preclean_remote_workspace(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=["stale-remote-asset"],
        generation_completion_names=[f"draft-{number}" for number in range(1, 7)],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job()) as workspace:
        workspace.generate_and_download(_job(), paths)

    assert not any(action[0] == "check" for action in page.actions)
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_renames_out_of_order_results_and_bulk_downloads_zip(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=[
            "<div>Generation progress 2 / 6</div>",
            "<div>Generation progress 6 / 6</div>",
        ],
        generation_completion_names=[
            "draft-4",
            "draft-1",
            "draft-6",
            "draft-2",
            "draft-5",
            "draft-3",
        ],
        renamed_clip_names=[
            "clip 4",
            "clip 1",
            "clip 6",
            "clip 2",
            "clip 5",
            "clip 3",
        ],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    materializer_calls = []

    def materialize(archive_path, job_paths, **kwargs):
        materializer_calls.append((Path(archive_path), job_paths, kwargs))
        return job_paths.flow_files

    monkeypatch.setattr(google_flow, "materialize_flow_archive", materialize, raising=False)

    with client.acquire_workspace(_job()) as workspace:
        generate = getattr(workspace, "generate_and_download", None)
        assert generate is not None, "Shared Flow generation is not implemented"
        result = generate(_job(), paths)

    assert result == paths.flow_files
    assert ("fill", "prompt", _job().master_prompt) in page.actions
    assert (
        "fill",
        "prompt",
        "เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ",
    ) in page.actions
    assert page.clip_names == [
        "clip 4",
        "clip 1",
        "clip 6",
        "clip 2",
        "clip 5",
        "clip 3",
    ]
    expect_index = page.actions.index(("expect_download", "registered"))
    click_index = page.actions.index(("click", "bulk_download"))
    assert expect_index < click_index
    assert page.download_attempts == [1, 0, 0, 0, 0, 0]
    assert materializer_calls == [
        (
            paths.flow_archive_file,
            paths,
            {
                "min_size_bytes": 1,
                "expected_width": 1080,
                "expected_height": 1920,
            },
        )
    ]


def test_flow_workspace_reconciles_existing_six_without_new_generation(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=[f"clip {number}" for number in range(1, 7)],
        inventory_sequence=[6, 6, 6],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda _archive, job_paths, **_kwargs: job_paths.flow_files,
    )

    with client.acquire_workspace(_job()) as workspace:
        result = workspace.reconcile_and_download(_job(), paths)

    assert result == paths.flow_files
    assert not any(
        action[:3] == ("fill", "prompt", _job().master_prompt)
        for action in page.actions
    )
    assert not any(action[0] == "check" for action in page.actions)
    assert ("click", "delete_selected") not in page.actions


def test_flow_workspace_partial_reconciliation_retains_remote_results(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 2 / 6</div>"],
        clip_names=["partial-a", "partial-b"],
        inventory_sequence=[2, 2, 2],
    )
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="reconcil"):
            workspace.reconcile_and_download(_job(), paths)

    assert not any(action[0] == "check" for action in page.actions)
    assert ("click", "delete_selected") not in page.actions
    assert ("click", "generate") not in page.actions


def test_flow_workspace_reconciliation_requires_stable_exact_six_before_rename(
    monkeypatch,
    tmp_path,
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        clip_names=["partial-a", "partial-b"],
        inventory_sequence=[2, 2, 2],
    )
    client, _ = _client(
        page,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda *_args, **_kwargs: paths.flow_files,
    )

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="six product clips"):
            workspace.reconcile_and_download(_job(), paths)

    assert not any(
        action[:3] == ("fill", "prompt", google_flow.RENAME_CLIPS_INSTRUCTION)
        for action in page.actions
    )
    assert ("click", "bulk_download") not in page.actions
    assert ("click", "delete_selected") not in page.actions
    assert ("click", "generate") not in page.actions


def test_flow_workspace_rejects_duplicate_semantic_names_before_bulk_download(
    monkeypatch, tmp_path
):
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        generation_completion_names=[f"draft-{number}" for number in range(1, 7)],
        renamed_clip_names=["clip 1", "clip 1", "clip 2", "clip 3", "clip 4", "clip 5"],
    )
    client, _ = _client(page)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    monkeypatch.setattr(
        google_flow,
        "materialize_flow_archive",
        lambda *_args, **_kwargs: paths.flow_files,
        raising=False,
    )

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="semantic"):
            workspace.generate_and_download(_job(), paths)

    assert ("click", "bulk_download") not in page.actions


def test_google_flow_agent_prompt_uses_observable_composer_when_locale_has_no_prompt_label():
    page = FakePage(
        progress_html=["<div>Generation progress 6 / 6</div>"],
        prompt_label_available=False,
    )
    client, _ = _client(page)

    client._submit_generation(page, _job().master_prompt)

    assert ("fill", "prompt", _job().master_prompt) in page.actions
    assert ("click", "generate") in page.actions


def test_flow_workspace_cleanup_reuses_delete_and_observable_empty_verification():
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[f"clip {number}" for number in range(1, 7)],
    )
    client, _ = _client(page)

    with client.acquire_workspace(_job()) as workspace:
        cleanup = getattr(workspace, "cleanup_and_verify_empty", None)
        assert cleanup is not None, "Flow post-job cleanup is not implemented"
        cleanup()

    assert page.clip_names == []
    assert page.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_flow_workspace_cleanup_raises_when_zero_state_cannot_be_verified(monkeypatch):
    page = FakePage(
        progress_html=["<div>Ready</div>"],
        clip_names=[],
        empty_state_available=False,
        inventory_sequence=[1, 1, 1],
    )
    client, _ = _client(
        page,
        timeout_seconds=30.0,
        editor_ready_timeout_seconds=1.0,
    )
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty"):
            workspace.cleanup_and_verify_empty()


def test_google_flow_generation_timeout_is_bounded(monkeypatch, tmp_path):
    page = FakePage(progress_html=["<div>Generation progress 2 / 6</div>"])
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with pytest.raises(MediaValidationError, match="timed out"):
        with client.acquire_workspace(_job()) as workspace:
            workspace.generate_and_download(_job(), paths)

    assert ("click", "bulk_download") not in page.actions


def test_google_flow_generation_timeout_is_typed_workspace_failure_after_submit(
    monkeypatch,
    tmp_path,
):
    page = FakePage(progress_html=["<div>Generation progress 2 / 6</div>"])
    client, _ = _client(page, timeout_seconds=1.0)
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="timed out"):
            workspace.generate_and_download(_job(), paths)

    assert ("click", "generate") in page.actions
    assert ("click", "bulk_download") not in page.actions


def test_post_reload_settled_inventory_uses_editor_timeout_not_generation_timeout(
    monkeypatch,
):
    page = FakePage(
        progress_html=["<div>Loading</div>"],
        media_list_sequence=[True, False],
        inventory_sequence=[0, 0, 0],
    )
    client, _ = _client(
        page,
        timeout_seconds=1800.0,
        editor_ready_timeout_seconds=1.0,
        settled_poll_count=3,
    )
    clock = [-0.5]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(google_flow.time, "monotonic", monotonic)
    monkeypatch.setattr(google_flow.time, "sleep", lambda _seconds: None)

    with client.acquire_workspace(_job()) as workspace:
        with pytest.raises(FlowWorkspaceVerificationError, match="empty"):
            workspace.prepare_for_generation()

    assert clock[0] <= 1.5
