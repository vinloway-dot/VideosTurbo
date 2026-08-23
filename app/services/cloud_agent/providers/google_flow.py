from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from app.models.cloud_agent import CloudJobRecord, ServiceSessionStatus
from app.services.cloud_agent.errors import MediaValidationError
from app.services.cloud_agent.media_probe import validate_video
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers._session_detection import (
    classify_security_challenge,
)
from app.services.cloud_agent.session import SessionManager


_PROGRESS_RE = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")


def classify_google_flow_session(*, url: str, html: str) -> ServiceSessionStatus:
    """Classify observable Google Flow page state without relying on cookies."""
    challenge = classify_security_challenge(html=html)
    if challenge is not None:
        return challenge

    page_url = str(url or "").lower()
    body = str(html or "").lower()

    if "accounts.google.com" in page_url:
        return ServiceSessionStatus.SESSION_EXPIRED

    has_agent_control = 'aria-label="agent"' in body or ">agent<" in body
    has_prompt_box = 'aria-label="prompt"' in body or "prompt box" in body
    has_project_shell = (
        "/tools/flow/project/" in page_url
        and "meet your agent" in body
        and "your agent in google flow" in body
    )
    if (has_agent_control and has_prompt_box) or has_project_shell:
        return ServiceSessionStatus.READY

    if "sign in" in body or "continue with google" in body:
        return ServiceSessionStatus.SESSION_EXPIRED

    return ServiceSessionStatus.ERROR


class GoogleFlowSessionProvider(BrowserSessionProvider):
    def __init__(self, browser, *, service_url: str) -> None:
        super().__init__(
            browser,
            service="google_flow",
            service_url=service_url,
            classifier=classify_google_flow_session,
        )


class GoogleFlowClient:
    """Generate and validate the six canonical Google Flow source clips."""

    def __init__(
        self,
        browser: Any,
        sessions: SessionManager,
        *,
        service_url: str,
        generation_timeout_seconds: float = 1800.0,
        poll_seconds: float = 1.0,
        max_download_attempts: int = 3,
        expected_width: int = 1080,
        expected_height: int = 1920,
    ) -> None:
        service_url = str(service_url or "").strip()
        if not service_url:
            raise ValueError("Google Flow service URL is required")
        if generation_timeout_seconds <= 0:
            raise ValueError("generation_timeout_seconds must be positive")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be non-negative")
        if max_download_attempts <= 0:
            raise ValueError("max_download_attempts must be positive")
        if expected_width <= 0 or expected_height <= 0:
            raise ValueError("expected video dimensions must be positive")

        self.browser = browser
        self.sessions = sessions
        self.service_url = service_url
        self.generation_timeout_seconds = float(generation_timeout_seconds)
        self.poll_seconds = float(poll_seconds)
        self.max_download_attempts = int(max_download_attempts)
        self.expected_width = int(expected_width)
        self.expected_height = int(expected_height)

    def generate_and_download(
        self,
        job: CloudJobRecord,
        flow_dir: Path,
        expected_count: int = 6,
    ) -> list[Path]:
        if expected_count <= 0:
            raise ValueError("expected_count must be positive")

        self.sessions.ensure_service_ready("google_flow", job.id)
        flow_dir = Path(flow_dir)
        flow_dir.mkdir(parents=True, exist_ok=True)

        with self.browser.open("google_flow", headed=False) as context:
            page = BrowserSessionProvider._page(context)
            page.goto(self.service_url, wait_until="domcontentloaded")
            self._submit_generation(page, job.master_prompt)
            self._wait_for_generation(page, expected_count)
            downloads = page.get_by_role(
                "button",
                name=re.compile(r"download", re.IGNORECASE),
            )
            actual_count = downloads.count()
            if actual_count != expected_count:
                raise MediaValidationError(
                    f"expected {expected_count} downloadable Flow results, got {actual_count}"
                )

            paths = []
            for index in range(expected_count):
                path = flow_dir / f"clip_{index + 1:02d}.mp4"
                self._download_and_validate(downloads.nth(index), page, path)
                paths.append(path)
            return paths

    @staticmethod
    def _submit_generation(page: Any, master_prompt: str) -> None:
        page.get_by_role(
            "button",
            name=re.compile(r"^\s*agent\s*$", re.IGNORECASE),
        ).click()
        page.get_by_label(re.compile(r"prompt", re.IGNORECASE)).fill(master_prompt)
        page.get_by_role(
            "button",
            name=re.compile(r"generate", re.IGNORECASE),
        ).click()

    def _wait_for_generation(self, page: Any, expected_count: int) -> None:
        deadline = time.monotonic() + self.generation_timeout_seconds
        while True:
            progress = self._progress_for_expected_count(page.content(), expected_count)
            if progress is not None and progress >= expected_count:
                return
            if time.monotonic() >= deadline:
                raise MediaValidationError(
                    f"Google Flow generation timed out before {expected_count}/{expected_count}"
                )
            time.sleep(self.poll_seconds)

    @staticmethod
    def _progress_for_expected_count(html: str, expected_count: int) -> int | None:
        for current_text, total_text in _PROGRESS_RE.findall(str(html or "")):
            current = int(current_text)
            total = int(total_text)
            if total == expected_count:
                return current
        return None

    def _download_and_validate(self, locator: Any, page: Any, path: Path) -> None:
        last_error: MediaValidationError | None = None
        for _attempt in range(1, self.max_download_attempts + 1):
            with page.expect_download() as download_info:
                locator.click()
            download_info.value.save_as(str(path))
            try:
                validate_video(
                    path,
                    min_size_bytes=1,
                    expected_width=self.expected_width,
                    expected_height=self.expected_height,
                )
                return
            except MediaValidationError as exc:
                last_error = exc

        raise MediaValidationError(
            f"{path.name} failed validation after "
            f"{self.max_download_attempts} download attempts: {last_error}"
        ) from last_error
