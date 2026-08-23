from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.models.cloud_agent import CloudJobRecord, ServiceSessionStatus
from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    FlowWorkspaceVerificationError,
    MediaValidationError,
)
from app.services.cloud_agent.flow_archive import materialize_flow_archive
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers._session_detection import (
    classify_security_challenge,
)
from app.services.cloud_agent.session import SessionManager
from app.services.cloud_agent.storage import JobPaths


_PROGRESS_RE = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
RENAME_CLIPS_INSTRUCTION = "เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ"


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


class FlowWorkspaceRun:
    def __init__(self, client: GoogleFlowClient, page: Any) -> None:
        self.client = client
        self.page = page

    def preclean_and_verify_empty(self) -> None:
        checkboxes = self.page.get_by_role("checkbox")
        clip_count = checkboxes.count()
        if clip_count:
            for index in range(clip_count):
                checkboxes.nth(index).check()
            delete = self.page.get_by_role(
                "button",
                name=re.compile(r"^(?:delete|ลบ|delete\s+ลบ)$", re.IGNORECASE),
            )
            if delete.count() != 1:
                raise FlowWorkspaceVerificationError(
                    "Google Flow stale product clips could not be deleted reliably"
                )
            delete.click()
            self._confirm_delete_or_wait_for_removal()

        self.page.reload(wait_until="domcontentloaded")
        deadline = time.monotonic() + self.client.generation_timeout_seconds
        while True:
            remaining = self.page.get_by_role("checkbox").count()
            empty_state = self.page.get_by_text(
                re.compile(
                    r"^(?:start creating or drop media|เริ่มสร้างหรือวางสื่อ)$",
                    re.IGNORECASE,
                ),
                exact=True,
            )
            if (
                remaining == 0
                and empty_state.count() == 1
                and empty_state.is_visible()
            ):
                return
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow empty product workspace could not be verified"
                )
            time.sleep(self.client.poll_seconds)

    def _confirm_delete_or_wait_for_removal(self) -> None:
        deadline = time.monotonic() + self.client.generation_timeout_seconds
        while True:
            dialog = self.page.get_by_role("dialog")
            if dialog.count() == 1:
                confirm = dialog.get_by_role(
                    "button",
                    name=re.compile(r"^(?:delete|ลบ|delete\s+ลบ)$", re.IGNORECASE),
                )
                if confirm.count() != 1:
                    raise FlowWorkspaceVerificationError(
                        "Google Flow delete confirmation could not be verified"
                    )
                confirm.click()
            if self.page.get_by_role("checkbox").count() == 0:
                return
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow stale product clip removal could not be verified"
                )
            time.sleep(self.client.poll_seconds)

    def cleanup_and_verify_empty(self) -> None:
        self.preclean_and_verify_empty()

    def generate_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...]:
        if expected_count != 6:
            raise ValueError("Google Flow workspace requires exactly six clips")

        self.preclean_and_verify_empty()
        self.client._submit_generation(self.page, job.master_prompt)
        self.client._wait_for_generation(self.page, expected_count)
        send = self.client._submit_agent_prompt(
            self.page,
            RENAME_CLIPS_INSTRUCTION,
        )
        self.client._wait_for_agent_completion(send)
        self.page.reload(wait_until="domcontentloaded")
        self._verify_semantic_names(expected_count)

        bulk_download = self.page.get_by_role(
            "button",
            name=re.compile(r"^download product clips$", re.IGNORECASE),
        )
        if bulk_download.count() != 1:
            raise FlowArchiveValidationError(
                "Google Flow bulk Download Product Clips action was not verified"
            )
        with self.page.expect_download() as download_info:
            bulk_download.click()
        download_info.value.save_as(str(paths.flow_archive_file))
        if not paths.flow_archive_file.is_file():
            raise FlowArchiveValidationError(
                "Google Flow bulk download did not produce an archive"
            )
        return materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=self.client.expected_width,
            expected_height=self.client.expected_height,
        )

    def _verify_semantic_names(self, expected_count: int) -> None:
        if self.page.get_by_role("checkbox").count() != expected_count:
            raise FlowWorkspaceVerificationError(
                "Google Flow semantic clip count could not be verified"
            )
        for number in range(1, expected_count + 1):
            name = self.page.get_by_text(f"clip {number}", exact=True)
            if name.count() != 1:
                raise FlowWorkspaceVerificationError(
                    "Google Flow semantic clip names could not be verified"
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
        workspace_lock_timeout_seconds: float | None = None,
        poll_seconds: float = 1.0,
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
        if expected_width <= 0 or expected_height <= 0:
            raise ValueError("expected video dimensions must be positive")

        self.browser = browser
        self.sessions = sessions
        self.service_url = service_url
        self.generation_timeout_seconds = float(generation_timeout_seconds)
        self.workspace_lock_timeout_seconds = (
            self.generation_timeout_seconds
            if workspace_lock_timeout_seconds is None
            else float(workspace_lock_timeout_seconds)
        )
        self.poll_seconds = float(poll_seconds)
        self.expected_width = int(expected_width)
        self.expected_height = int(expected_height)

    @contextmanager
    def acquire_workspace(
        self,
        job: CloudJobRecord,
    ) -> Iterator[FlowWorkspaceRun]:
        self.sessions.ensure_service_ready("google_flow", job.id)
        with self.browser.open(
            "google_flow",
            headed=False,
            lock_timeout_seconds=self.workspace_lock_timeout_seconds,
        ) as context:
            page = BrowserSessionProvider._page(context)
            page.goto(self.service_url, wait_until="domcontentloaded")
            self._enter_project_editor(page)
            yield FlowWorkspaceRun(self, page)

    def _enter_project_editor(self, page: Any) -> None:
        deadline = time.monotonic() + self.generation_timeout_seconds
        while True:
            agent = page.get_by_role("button", name="Agent", exact=True)
            if agent.count() == 1 and agent.is_visible():
                return

            for role in ("link", "button"):
                launch = page.get_by_role(
                    role,
                    name="Create with Google Flow",
                    exact=True,
                )
                if launch.count() == 1 and launch.is_visible():
                    launch.click()
                    break

            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow project editor could not be verified"
                )
            time.sleep(self.poll_seconds)

    def _submit_generation(self, page: Any, master_prompt: str) -> None:
        self._submit_agent_prompt(page, master_prompt)

    @staticmethod
    def _submit_agent_prompt(page: Any, prompt_text: str) -> Any:
        agent = page.get_by_role(
            "button",
            name=re.compile(r"^\s*agent\s*$", re.IGNORECASE),
        )
        if agent.count() != 1:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent control could not be verified"
            )
        agent.click()

        prompt = page.get_by_label(re.compile(r"prompt", re.IGNORECASE))
        if prompt.count() != 1:
            composer = agent.locator(
                "xpath=ancestor::div[.//textarea or .//*[@contenteditable='true']][1]"
            )
            prompt = composer.locator(
                "textarea:visible, [contenteditable='true']:visible"
            )
        if prompt.count() != 1:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent prompt could not be verified"
            )
        prompt.fill(prompt_text)

        send = page.get_by_role(
            "button",
            name=re.compile(
                r"^(?:generate|arrow_forward\s+(?:generate|สร้าง))$",
                re.IGNORECASE,
            ),
        )
        if send.count() != 1:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent submit control could not be verified"
            )
        send.click()
        return send

    def _wait_for_agent_completion(self, send: Any) -> None:
        deadline = time.monotonic() + self.generation_timeout_seconds
        saw_busy = False
        while True:
            enabled = send.is_enabled()
            saw_busy = saw_busy or not enabled
            if saw_busy and enabled:
                return
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent completion could not be verified"
                )
            time.sleep(self.poll_seconds)

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
