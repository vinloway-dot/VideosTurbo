from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from playwright.sync_api import sync_playwright

from app.config import config
from app.services.cloud_agent.browser_lock import BrowserService, ProfileLock
from app.services.cloud_agent.storage import CloudJobStorage
from app.utils.file_security import resolve_path_within_directory

_SUPPORTED_SERVICES = frozenset({"google_flow", "canva"})
_EVIDENCE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PersistentBrowserManager:
    """Own persistent Playwright profiles and serialize access per service."""

    def __init__(
        self,
        *,
        app_config: Mapping[str, Any] | None = None,
        storage: CloudJobStorage | None = None,
        profile_lock: ProfileLock | None = None,
        playwright_factory: Callable[[], Any] = sync_playwright,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        if lock_timeout_seconds < 0:
            raise ValueError("lock_timeout_seconds must be non-negative")

        self.app_config = app_config if app_config is not None else config.app
        self.storage = storage if storage is not None else CloudJobStorage()
        self.profile_lock = (
            profile_lock
            if profile_lock is not None
            else ProfileLock(self.app_config["cloud_agent_browser_lock_dir"])
        )
        self.playwright_factory = playwright_factory
        self.lock_timeout_seconds = float(lock_timeout_seconds)

    @contextmanager
    def open(
        self,
        service: BrowserService,
        *,
        headed: bool | None = None,
        lock_timeout_seconds: float | None = None,
    ) -> Iterator[Any]:
        """Open one service's dedicated persistent Chromium context under its lock."""
        profile_dir = self._profile_dir(service)
        headless = self._resolve_headless(headed)
        effective_lock_timeout = (
            self.lock_timeout_seconds
            if lock_timeout_seconds is None
            else float(lock_timeout_seconds)
        )
        if effective_lock_timeout < 0:
            raise ValueError("lock_timeout_seconds must be non-negative")

        with self.profile_lock.acquire(
            service,
            timeout_seconds=effective_lock_timeout,
        ):
            profile_dir.mkdir(parents=True, exist_ok=True)
            with self.playwright_factory() as playwright:
                browser_context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=headless,
                    channel="chrome",
                    chromium_sandbox=True,
                )
                try:
                    yield browser_context
                finally:
                    browser_context.close()

    def capture_evidence(
        self,
        job_id: str,
        service: BrowserService,
        page: Any,
        *,
        label: str,
    ) -> tuple[Path, Path]:
        """Capture screenshot and DOM HTML inside the owning job's evidence directory."""
        self._validate_service(service)
        safe_label = str(label or "").strip()
        if not _EVIDENCE_LABEL_RE.fullmatch(safe_label) or safe_label in {".", ".."}:
            raise ValueError("invalid evidence label")

        evidence_dir = self.storage.prepare(job_id).screenshots_dir
        screenshot_path = Path(
            resolve_path_within_directory(
                str(evidence_dir),
                f"{service}-{safe_label}.png",
                require_file=False,
            )
        )
        html_path = Path(
            resolve_path_within_directory(
                str(evidence_dir),
                f"{service}-{safe_label}.html",
                require_file=False,
            )
        )

        page.screenshot(path=str(screenshot_path), full_page=True)
        html_path.write_text(page.content(), encoding="utf-8")
        return screenshot_path, html_path

    def _profile_dir(self, service: BrowserService) -> Path:
        self._validate_service(service)
        config_key = {
            "google_flow": "cloud_agent_google_profile_dir",
            "canva": "cloud_agent_canva_profile_dir",
        }[service]
        profile_dir = str(self.app_config.get(config_key, "") or "").strip()
        if not profile_dir:
            raise ValueError(f"missing browser profile directory for {service}")
        return Path(profile_dir)

    def _resolve_headless(self, headed: bool | None) -> bool:
        if headed is None:
            return bool(self.app_config.get("cloud_agent_browser_headless", True))
        return not headed

    @staticmethod
    def _validate_service(service: str) -> None:
        if service not in _SUPPORTED_SERVICES:
            raise ValueError(f"unsupported browser service: {service}")
