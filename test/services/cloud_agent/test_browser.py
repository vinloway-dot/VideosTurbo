from contextlib import contextmanager
from pathlib import Path

import pytest

from app.services.cloud_agent.browser import PersistentBrowserManager
from app.services.cloud_agent.browser_lock import ProfileLock
from app.services.cloud_agent.storage import CloudJobStorage


class FakePage:
    def __init__(self, html: str = "<html><body>evidence</body></html>") -> None:
        self.html = html
        self.screenshot_calls: list[dict] = []

    def screenshot(self, **kwargs) -> None:
        self.screenshot_calls.append(kwargs)
        Path(kwargs["path"]).write_bytes(b"png-evidence")

    def content(self) -> str:
        return self.html


class FakeBrowserContext:
    def __init__(self, user_data_dir: str, *, headless: bool) -> None:
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.launches: list[dict] = []
        self.contexts: list[FakeBrowserContext] = []

    def launch_persistent_context(self, user_data_dir: str, **kwargs):
        launch = {"user_data_dir": user_data_dir, **kwargs}
        self.launches.append(launch)
        context = FakeBrowserContext(user_data_dir, headless=kwargs["headless"])
        self.contexts.append(context)
        return context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakePlaywrightStarter:
    def __init__(self, chromium: FakeChromium) -> None:
        self.playwright = FakePlaywright(chromium)
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self.playwright

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exited = True


def _settings(tmp_path):
    return {
        "cloud_agent_browser_headless": True,
        "cloud_agent_google_profile_dir": str(tmp_path / "profiles" / "google-flow"),
        "cloud_agent_canva_profile_dir": str(tmp_path / "profiles" / "canva"),
        "cloud_agent_browser_lock_dir": str(tmp_path / "locks"),
    }


def _manager(tmp_path, chromium: FakeChromium, *, timeout: float = 0.2):
    starter = FakePlaywrightStarter(chromium)
    manager = PersistentBrowserManager(
        app_config=_settings(tmp_path),
        storage=CloudJobStorage(tmp_path / "jobs"),
        profile_lock=ProfileLock(tmp_path / "locks", poll_interval_seconds=0.01),
        playwright_factory=lambda: starter,
        lock_timeout_seconds=timeout,
    )
    return manager, starter


def test_open_uses_dedicated_persistent_profiles_and_headless_policy(tmp_path):
    chromium = FakeChromium()
    manager, _ = _manager(tmp_path, chromium)

    with manager.open("google_flow") as google_context:
        assert google_context.user_data_dir == str(
            tmp_path / "profiles" / "google-flow"
        )
        assert google_context.headless is True

    with manager.open("canva", headed=True) as canva_context:
        assert canva_context.user_data_dir == str(tmp_path / "profiles" / "canva")
        assert canva_context.headless is False

    assert chromium.launches == [
        {
            "user_data_dir": str(tmp_path / "profiles" / "google-flow"),
            "headless": True,
        },
        {
            "user_data_dir": str(tmp_path / "profiles" / "canva"),
            "headless": False,
        },
    ]
    assert all(context.closed for context in chromium.contexts)


def test_open_headed_false_overrides_configured_headless_value(tmp_path):
    chromium = FakeChromium()
    settings = _settings(tmp_path)
    settings["cloud_agent_browser_headless"] = False
    starter = FakePlaywrightStarter(chromium)
    manager = PersistentBrowserManager(
        app_config=settings,
        storage=CloudJobStorage(tmp_path / "jobs"),
        profile_lock=ProfileLock(tmp_path / "locks"),
        playwright_factory=lambda: starter,
    )

    with manager.open("google_flow", headed=False):
        pass

    assert chromium.launches[0]["headless"] is True


def test_reopening_service_reuses_same_persistent_profile(tmp_path):
    chromium = FakeChromium()
    manager, _ = _manager(tmp_path, chromium)

    with manager.open("google_flow"):
        pass
    with manager.open("google_flow"):
        pass

    assert [launch["user_data_dir"] for launch in chromium.launches] == [
        str(tmp_path / "profiles" / "google-flow"),
        str(tmp_path / "profiles" / "google-flow"),
    ]


def test_open_holds_same_service_profile_lock_for_context_lifetime(tmp_path):
    first_chromium = FakeChromium()
    second_chromium = FakeChromium()
    first, _ = _manager(tmp_path, first_chromium, timeout=0.2)
    second, _ = _manager(tmp_path, second_chromium, timeout=0.05)

    with first.open("google_flow"):
        with pytest.raises(TimeoutError, match="google_flow"):
            with second.open("google_flow"):
                pass

    assert second_chromium.launches == []


def test_capture_evidence_writes_job_owned_screenshot_and_html(tmp_path):
    chromium = FakeChromium()
    manager, _ = _manager(tmp_path, chromium)
    page = FakePage("<html><body>login challenge</body></html>")

    screenshot_path, html_path = manager.capture_evidence(
        "job-123",
        "google_flow",
        page,
        label="session-check",
    )

    expected_dir = tmp_path / "jobs" / "job-123" / "screenshots"
    assert screenshot_path == expected_dir / "google_flow-session-check.png"
    assert html_path == expected_dir / "google_flow-session-check.html"
    assert screenshot_path.read_bytes() == b"png-evidence"
    assert html_path.read_text(encoding="utf-8") == (
        "<html><body>login challenge</body></html>"
    )
    assert page.screenshot_calls == [
        {"path": str(screenshot_path), "full_page": True}
    ]


def test_capture_evidence_rejects_path_like_label(tmp_path):
    chromium = FakeChromium()
    manager, _ = _manager(tmp_path, chromium)

    with pytest.raises(ValueError, match="evidence label"):
        manager.capture_evidence(
            "job-123",
            "canva",
            FakePage(),
            label="../escape",
        )
