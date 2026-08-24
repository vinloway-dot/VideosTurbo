from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from playwright.sync_api import Error as PlaywrightError

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


@dataclass(frozen=True)
class AgentComposer:
    """One observable, active Flow Agent composer and its submit control."""

    agent: Any
    container: Any
    prompt: Any
    generate: Any


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
        self._prepared_master_prompt = ""

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
        self.client._wait_for_stable_inventory(self.page, expected_count=0)

    def prepare_for_generation(self) -> None:
        self.preclean_and_verify_empty()

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

        self.client._submit_generation(self.page, job.master_prompt)
        self.client._wait_for_generation(self.page, expected_count)
        return self._rename_download_and_materialize(paths, expected_count)

    def prepare_agent_prompt(self, master_prompt: str) -> AgentComposer:
        prepared = self.client._prepare_agent_prompt(self.page, master_prompt)
        self._prepared_master_prompt = master_prompt
        return prepared

    def submit_prepared_generation_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...]:
        if expected_count != 6:
            raise ValueError("Google Flow workspace requires exactly six clips")
        if self._prepared_master_prompt != job.master_prompt:
            raise FlowWorkspaceVerificationError(
                "Google Flow prepared Agent prompt could not be verified"
            )
        self.client._submit_prepared_agent_prompt(
            self.page,
            self._prepared_master_prompt,
        )
        self.client._wait_for_generation(self.page, expected_count)
        return self._rename_download_and_materialize(paths, expected_count)

    def reconcile_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...]:
        del job
        if expected_count != 6:
            raise ValueError("Google Flow workspace requires exactly six clips")
        try:
            self.client._wait_for_generation(self.page, expected_count)
        except MediaValidationError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow existing generation could not be reconciled"
            ) from exc
        try:
            self.client._wait_for_stable_inventory(
                self.page,
                expected_count=expected_count,
            )
        except FlowWorkspaceVerificationError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow existing six product clips could not be verified"
            ) from exc
        return self._rename_download_and_materialize(paths, expected_count)

    def _rename_download_and_materialize(
        self,
        paths: JobPaths,
        expected_count: int,
    ) -> tuple[Path, ...]:
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
        editor_ready_timeout_seconds: float = 120.0,
        workspace_lock_timeout_seconds: float | None = None,
        poll_seconds: float = 1.0,
        settled_poll_count: int = 3,
        expected_width: int = 1080,
        expected_height: int = 1920,
    ) -> None:
        service_url = str(service_url or "").strip()
        if not service_url:
            raise ValueError("Google Flow service URL is required")
        if generation_timeout_seconds <= 0:
            raise ValueError("generation_timeout_seconds must be positive")
        if editor_ready_timeout_seconds <= 0:
            raise ValueError("editor_ready_timeout_seconds must be positive")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be non-negative")
        if settled_poll_count <= 0:
            raise ValueError("settled_poll_count must be positive")
        if expected_width <= 0 or expected_height <= 0:
            raise ValueError("expected video dimensions must be positive")

        self.browser = browser
        self.sessions = sessions
        self.service_url = service_url
        self.generation_timeout_seconds = float(generation_timeout_seconds)
        self.editor_ready_timeout_seconds = float(editor_ready_timeout_seconds)
        self.workspace_lock_timeout_seconds = (
            self.generation_timeout_seconds
            if workspace_lock_timeout_seconds is None
            else float(workspace_lock_timeout_seconds)
        )
        self.poll_seconds = float(poll_seconds)
        self.settled_poll_count = int(settled_poll_count)
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
            self._wait_for_settled_editor(page)
            yield FlowWorkspaceRun(self, page)

    @staticmethod
    def _observable_composer(agent: Any) -> Any:
        return agent.locator(
            "xpath=ancestor::div[.//textarea or .//*[@contenteditable='true']][1]"
        ).locator("textarea:visible, [contenteditable='true']:visible")

    @staticmethod
    def _agent_control(page: Any) -> Any:
        agent = page.get_by_role("button", name="Agent", exact=True)
        if not (
            agent.count() == 1
            and agent.is_visible()
            and agent.is_enabled()
        ):
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent control could not be verified"
            )
        return agent

    @classmethod
    def _active_agent_composer(cls, page: Any) -> AgentComposer:
        try:
            agent = cls._agent_control(page)
            if agent.get_attribute("aria-pressed") != "true":
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent state is not active"
                )
            container = agent.locator(
                "xpath=ancestor::div[.//textarea or "
                ".//*[@contenteditable='true']][1]"
            )
            if not (container.count() == 1 and container.is_visible()):
                raise FlowWorkspaceVerificationError(
                    "Google Flow active Agent composer could not be verified"
                )
            prompt = container.locator(
                "textarea:visible, [contenteditable='true']:visible"
            )
            if not (prompt.count() == 1 and prompt.is_visible()):
                raise FlowWorkspaceVerificationError(
                    "Google Flow active Agent prompt could not be verified"
                )
            generate = container.get_by_role(
                "button",
                name=re.compile(
                    r"^(?:generate|arrow_forward\s+(?:generate|สร้าง))$",
                    re.IGNORECASE,
                ),
            )
            if not (generate.count() == 1 and generate.is_visible()):
                raise FlowWorkspaceVerificationError(
                    "Google Flow active Agent submit control could not be verified"
                )
            return AgentComposer(
                agent=agent,
                container=container,
                prompt=prompt,
                generate=generate,
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent state could not be verified"
            ) from exc

    def _ensure_agent_active(self, page: Any) -> AgentComposer:
        """Return a verified Agent composer without toggling an active Agent off."""
        try:
            agent = self._agent_control(page)
            state = agent.get_attribute("aria-pressed")
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent state could not be verified"
            ) from exc

        if state == "true":
            return self._active_agent_composer(page)
        if state != "false":
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent state is unknown"
            )

        agent.click()
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        while True:
            try:
                return self._active_agent_composer(page)
            except FlowWorkspaceVerificationError:
                pass
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent activation could not be verified"
                )
            time.sleep(self.poll_seconds)

    @staticmethod
    def _prompt_value(prompt: Any) -> str:
        try:
            return str(prompt.input_value())
        except (AttributeError, PlaywrightError):
            return str(prompt.inner_text())

    def _is_editor_actionable(self, page: Any) -> bool:
        try:
            if page.evaluate("document.readyState") != "complete":
                return False
            agent = page.get_by_role("button", name="Agent", exact=True)
            if not (
                agent.count() == 1
                and agent.is_visible()
                and agent.is_enabled()
            ):
                return False
            composer = self._observable_composer(agent)
            if not (composer.count() == 1 and composer.is_visible()):
                return False
            media = page.get_by_role(
                "button",
                name=re.compile(r"(?:all media|สื่อทั้งหมด)", re.IGNORECASE),
            )
            media_list = page.locator('[data-testid="virtuoso-item-list"]:visible')
            return (
                media.count() == 1
                and media.is_visible()
                and media_list.count() == 1
                and media_list.is_visible()
                and page.locator('[aria-busy="true"]:visible').count() == 0
                and page.get_by_role("progressbar").count() == 0
            )
        except PlaywrightError:
            return False

    def _wait_for_settled_editor(self, page: Any) -> None:
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        while True:
            if self._is_editor_actionable(page):
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

    def _wait_for_stable_inventory(
        self,
        page: Any,
        *,
        expected_count: int | None,
    ) -> int:
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        previous_count: int | None = None
        stable_polls = 0
        while True:
            if self._is_editor_actionable(page):
                media_list = page.locator(
                    '[data-testid="virtuoso-item-list"]:visible'
                )
                count = media_list.locator(
                    '[role="button"][tabindex="0"]'
                ).count()
                stable_polls = stable_polls + 1 if count == previous_count else 1
                previous_count = count
                if stable_polls >= self.settled_poll_count and (
                    expected_count is None or count == expected_count
                ):
                    return count
            else:
                previous_count = None
                stable_polls = 0
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow empty product workspace could not be verified"
                )
            time.sleep(self.poll_seconds)

    def _submit_generation(self, page: Any, master_prompt: str) -> None:
        self._submit_agent_prompt(page, master_prompt)

    def _prepare_agent_prompt(self, page: Any, prompt_text: str) -> AgentComposer:
        composer = self._ensure_agent_active(page)
        composer.prompt.fill(prompt_text)

        verified = self._active_agent_composer(page)
        if self._prompt_value(verified.prompt) != prompt_text:
            raise FlowWorkspaceVerificationError(
                "Google Flow active Agent prompt value could not be verified"
            )
        return verified

    def _submit_prepared_agent_prompt(self, page: Any, prompt_text: str) -> Any:
        verified = self._active_agent_composer(page)
        if self._prompt_value(verified.prompt) != prompt_text:
            raise FlowWorkspaceVerificationError(
                "Google Flow active Agent prompt value could not be verified"
            )
        verified.generate.click()
        return verified.generate

    def _submit_agent_prompt(self, page: Any, prompt_text: str) -> Any:
        self._prepare_agent_prompt(page, prompt_text)
        return self._submit_prepared_agent_prompt(page, prompt_text)

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
                raise FlowWorkspaceVerificationError(
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
