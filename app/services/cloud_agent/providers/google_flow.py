from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError

from app.models.cloud_agent import CloudJobRecord, ServiceSessionStatus
from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    FlowWorkspaceVerificationError,
    HumanRequiredError,
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
_GENERATED_IMAGE_ALT_RE = re.compile(
    r"^(?:generated image|รูปภาพที่สร้างขึ้น)$",
    re.IGNORECASE,
)
_VIDEO_CARD_NAME_RE = re.compile(
    r"(?:video\s+cover|ภาพปกวิดีโอ).*play(?:_circle)?",
    re.IGNORECASE,
)
_CARD_PROCESSING_RE = re.compile(
    r"(?:processing|loading|กำลังประมวลผล|กำลังโหลด)",
    re.IGNORECASE,
)
_CARD_FAILURE_RE = re.compile(
    r"(?:failed|failure|error|ล้มเหลว|ไม่สำเร็จ|ข้อผิดพลาด)",
    re.IGNORECASE,
)
_MEDIA_CARD_SELECTOR = (
    '[data-testid="virtuoso-item-list"]:visible '
    '[role="button"][tabindex="0"]'
)
_CARD_DELETE_NAME_RE = re.compile(
    r"(?:delete\s+)?(?:move to trash|ย้ายลงถังขยะ)",
    re.IGNORECASE,
)
_CARD_OVERFLOW_NAME_RE = re.compile(
    r"^more_vert\s+(?:more|เพิ่มเติม)$",
    re.IGNORECASE,
)
_COMMAND_COMPOSER_SELECTOR = '[data-slate-editor="true"][role="textbox"]:visible'
_COMMAND_SUBMIT_NAME_RE = re.compile(
    r"^(?:generate|arrow_forward\s+(?:generate|create|สร้าง))$",
    re.IGNORECASE,
)
_AGENT_RESPONSE_FEEDBACK_NAME_RE = re.compile(
    r"^thumb_up\s+(?:good response|คำตอบดี)$",
    re.IGNORECASE,
)
_EMPTY_MEDIA_NAME_RE = re.compile(
    r"(?:start creating or add media|เริ่มสร้างหรือวางสื่อ)",
    re.IGNORECASE,
)
_FATAL_APPLICATION_ERROR_RE = re.compile(
    r"(?:application error:\s*a client-side exception has occurred|"
    r"cannot read properties of undefined \(reading ['\"]service['\"]\))",
    re.IGNORECASE,
)
RENAME_CLIPS_INSTRUCTION = "เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ"
_DIRECT_LINK_RECOVERY_CYCLES = 2


class _DirectLinkFatalPageError(FlowWorkspaceVerificationError):
    """A verified fatal direct project page that can be safely reloaded."""


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
        while self.client._media_card_count(self.page):
            self._delete_one_current_media_card()

        self.page.reload(wait_until="domcontentloaded")
        try:
            self.client._hydrate_project_workspace(
                self.page,
                flow_generation_unresolved=False,
            )
        except FlowWorkspaceVerificationError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow empty product workspace could not be verified"
            ) from exc
        self.client._wait_for_stable_inventory(self.page, expected_count=0)

    def prepare_for_generation(self) -> None:
        self.preclean_and_verify_empty()

    def _delete_one_current_media_card(self) -> None:
        previous_count = self.client._media_card_count(self.page)
        card = self.client._media_cards(self.page).nth(0)
        if not card.is_visible():
            raise FlowWorkspaceVerificationError(
                "Google Flow stale product card could not be selected reliably"
            )
        card.hover()

        deadline = time.monotonic() + self.client.editor_ready_timeout_seconds
        while True:
            overflow = card.get_by_role(
                "button",
                name=_CARD_OVERFLOW_NAME_RE,
            )
            if (
                overflow.count() == 1
                and overflow.is_visible()
                and overflow.is_enabled()
            ):
                overflow.click()
                break
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow stale product card menu could not be selected reliably"
                )
            time.sleep(self.client.poll_seconds)

        while True:
            delete = self.page.get_by_role("menuitem", name=_CARD_DELETE_NAME_RE)
            if delete.count() == 1 and delete.is_visible() and delete.is_enabled():
                delete.click()
                self._confirm_delete_or_wait_for_removal(previous_count)
                return
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow stale product card could not be deleted reliably"
                )
            time.sleep(self.client.poll_seconds)

    def _confirm_delete_or_wait_for_removal(self, previous_count: int) -> None:
        deadline = time.monotonic() + self.client.editor_ready_timeout_seconds
        while True:
            dialog = self.page.get_by_role("dialog")
            if dialog.count() == 1:
                confirm = dialog.get_by_role(
                    "button",
                    name=_CARD_DELETE_NAME_RE,
                )
                if confirm.count() != 1:
                    raise FlowWorkspaceVerificationError(
                        "Google Flow delete confirmation could not be verified"
                    )
                confirm.click()
            if (
                self.client._media_inventory_is_observable(self.page)
                and self.client._media_card_count(self.page) < previous_count
            ):
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
        if not self._semantic_names_are_complete(expected_count):
            response_count = self.client._agent_response_count(self.page)
            self.client._submit_agent_prompt(
                self.page,
                RENAME_CLIPS_INSTRUCTION,
            )
            self._wait_for_semantic_rename(expected_count, response_count)
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

    def _semantic_names_are_complete(self, expected_count: int) -> bool:
        if not self.client._media_inventory_is_observable(self.page):
            return False
        if self.client._media_card_count(self.page) != expected_count:
            return False
        return all(
            self.page.get_by_text(f"clip {number}", exact=True).count() == 1
            for number in range(1, expected_count + 1)
        )

    def _wait_for_semantic_rename(
        self,
        expected_count: int,
        response_count: int,
    ) -> None:
        self.client._wait_for_agent_response(self.page, response_count)
        self.page.reload(wait_until="domcontentloaded")
        self.client._hydrate_project_workspace(
            self.page,
            flow_generation_unresolved=True,
        )
        deadline = time.monotonic() + self.client.editor_ready_timeout_seconds
        while True:
            if self._semantic_names_are_complete(expected_count):
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(self.client.poll_seconds)
        raise FlowWorkspaceVerificationError(
            "Google Flow semantic clip names could not be verified"
        )

    def _verify_semantic_names(self, expected_count: int) -> None:
        if not self.client._media_inventory_is_observable(self.page):
            raise FlowWorkspaceVerificationError(
                "Google Flow semantic media inventory could not be verified"
            )
        if self.client._media_card_count(self.page) != expected_count:
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
        with self.browser.open(
            "google_flow",
            headed=None,
            lock_timeout_seconds=self.workspace_lock_timeout_seconds,
        ) as context:
            page = BrowserSessionProvider._page(context)
            page.goto(self._flow_home_url(), wait_until="domcontentloaded")
            page.goto(self.service_url, wait_until="domcontentloaded")
            self._verify_workspace_session(page, job.id)
            self._hydrate_project_workspace(
                page,
                flow_generation_unresolved=job.flow_generation_unresolved,
            )
            yield FlowWorkspaceRun(self, page)

    def _flow_home_url(self) -> str:
        parsed = urlsplit(self.service_url)
        marker = "/project/"
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or marker not in parsed.path
        ):
            raise FlowWorkspaceVerificationError(
                "Google Flow project URL could not be used for safe home warmup"
            )
        home_path = parsed.path.split(marker, 1)[0].rstrip("/")
        if not home_path:
            raise FlowWorkspaceVerificationError(
                "Google Flow project URL could not be used for safe home warmup"
            )
        return urlunsplit((parsed.scheme, parsed.netloc, home_path, "", ""))

    @staticmethod
    def _verify_workspace_session(page: Any, job_id: str) -> None:
        try:
            status = classify_google_flow_session(
                url=page.url,
                html=page.content(),
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow workspace session could not be verified"
            ) from exc

        if status is ServiceSessionStatus.READY:
            return
        if status is ServiceSessionStatus.ERROR:
            # A direct project boot can expose a vendor shell before project
            # hydration. The same owned page's bounded hydration loop decides
            # whether that shell becomes a usable editor or fails closed.
            return
        raise HumanRequiredError(
            f"google_flow session requires human recovery for job {job_id}: "
            f"{status.value}"
        )

    def _hydrate_project_workspace(
        self,
        page: Any,
        *,
        flow_generation_unresolved: bool,
    ) -> None:
        """Prove the project editor in the one owned context, with safe reloads."""
        del flow_generation_unresolved
        last_error: FlowWorkspaceVerificationError | None = None
        for recovery_cycle in range(_DIRECT_LINK_RECOVERY_CYCLES + 1):
            try:
                self._wait_for_settled_editor(page)
                return
            except (_DirectLinkFatalPageError, FlowWorkspaceVerificationError) as exc:
                last_error = exc
                if recovery_cycle >= _DIRECT_LINK_RECOVERY_CYCLES:
                    break
                page.reload(wait_until="domcontentloaded")
        raise FlowWorkspaceVerificationError(
            "Google Flow project editor could not be verified"
        ) from last_error

    @staticmethod
    def _observable_composer(agent: Any) -> Any:
        return agent.locator(
            "xpath=ancestor::div[.//textarea or .//*[@contenteditable='true']][1]"
        ).locator("textarea:visible, [contenteditable='true']:visible")

    @staticmethod
    def _media_cards(page: Any) -> Any:
        return page.locator(_MEDIA_CARD_SELECTOR)

    @classmethod
    def _media_card_count(cls, page: Any) -> int:
        return cls._media_cards(page).count()

    @staticmethod
    def _agent_control(page: Any) -> Any:
        agent = page.get_by_role("button", name="Agent", exact=True)
        if (
            agent.count() == 1
            and agent.is_visible()
            and agent.is_enabled()
        ):
            return agent

        agent_text = page.get_by_text("Agent", exact=True)
        text_backed_agent = agent_text.locator("xpath=ancestor::button[1]")
        if (
            text_backed_agent.count() == 1
            and text_backed_agent.is_visible()
            and text_backed_agent.is_enabled()
        ):
            return text_backed_agent
        raise FlowWorkspaceVerificationError(
            "Google Flow Agent control could not be verified"
        )

    @staticmethod
    def _media_inventory_is_observable(page: Any) -> bool:
        media_list = page.locator('[data-testid="virtuoso-item-list"]:visible')
        empty_state = page.get_by_text(_EMPTY_MEDIA_NAME_RE)
        return (
            media_list.count() == 1
            and media_list.is_visible()
        ) or (
            empty_state.count() == 1
            and empty_state.is_visible()
        )

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
                name=_COMMAND_SUBMIT_NAME_RE,
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

    @staticmethod
    def _fallback_command_composer(page: Any) -> AgentComposer:
        """Find the post-generation command composer when the Agent toggle is absent."""
        try:
            prompt = page.locator(_COMMAND_COMPOSER_SELECTOR)
            if not (prompt.count() == 1 and prompt.is_visible()):
                raise FlowWorkspaceVerificationError(
                    "Google Flow fallback command prompt could not be verified"
                )
            container = prompt.locator("xpath=ancestor::div[.//button][1]")
            if not (container.count() == 1 and container.is_visible()):
                raise FlowWorkspaceVerificationError(
                    "Google Flow fallback command container could not be verified"
                )
            generate = container.get_by_role(
                "button",
                name=_COMMAND_SUBMIT_NAME_RE,
            )
            if not (generate.count() == 1 and generate.is_visible()):
                raise FlowWorkspaceVerificationError(
                    "Google Flow fallback command submit control could not be verified"
                )
            return AgentComposer(
                agent=prompt,
                container=container,
                prompt=prompt,
                generate=generate,
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow fallback command composer could not be verified"
            ) from exc

    @classmethod
    def _active_or_fallback_command_composer(cls, page: Any) -> AgentComposer:
        try:
            cls._agent_control(page)
        except FlowWorkspaceVerificationError:
            return cls._fallback_command_composer(page)
        return cls._active_agent_composer(page)

    def _ensure_agent_active(self, page: Any) -> AgentComposer:
        """Return a verified Agent composer without toggling an active Agent off."""
        try:
            agent = self._agent_control(page)
        except FlowWorkspaceVerificationError:
            return self._fallback_command_composer(page)
        try:
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
            if _FATAL_APPLICATION_ERROR_RE.search(page.content()):
                return False
            try:
                self._agent_control(page)
            except FlowWorkspaceVerificationError:
                self._fallback_command_composer(page)
            return (
                self._media_inventory_is_observable(page)
                and page.locator('[aria-busy="true"]:visible').count() == 0
                and page.get_by_role("progressbar").count() == 0
            )
        except (FlowWorkspaceVerificationError, PlaywrightError):
            return False

    @staticmethod
    def _has_fatal_application_error(page: Any) -> bool:
        try:
            return bool(_FATAL_APPLICATION_ERROR_RE.search(page.content()))
        except PlaywrightError:
            return False

    def _wait_for_settled_editor(self, page: Any) -> None:
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        stable_polls = 0
        while True:
            if self._has_fatal_application_error(page):
                raise _DirectLinkFatalPageError(
                    "Google Flow direct project page has a fatal application error"
                )
            if self._is_editor_actionable(page):
                stable_polls += 1
                if stable_polls >= self.settled_poll_count:
                    return
            else:
                stable_polls = 0

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
                count = self._media_card_count(page)
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

        verified = self._active_or_fallback_command_composer(page)
        if self._prompt_value(verified.prompt) != prompt_text:
            raise FlowWorkspaceVerificationError(
                "Google Flow active Agent prompt value could not be verified"
            )
        return verified

    def _submit_prepared_agent_prompt(self, page: Any, prompt_text: str) -> Any:
        verified = self._active_or_fallback_command_composer(page)
        if self._prompt_value(verified.prompt) != prompt_text:
            raise FlowWorkspaceVerificationError(
                "Google Flow active Agent prompt value could not be verified"
            )
        verified.generate.click()
        return verified.generate

    def _submit_agent_prompt(self, page: Any, prompt_text: str) -> Any:
        self._prepare_agent_prompt(page, prompt_text)
        return self._submit_prepared_agent_prompt(page, prompt_text)

    def _wait_for_generation(self, page: Any, expected_count: int) -> None:
        deadline = time.monotonic() + self.generation_timeout_seconds
        previous_fingerprints: tuple[str, ...] | None = None
        stable_polls = 0
        while True:
            if self._generated_image_output_count(page):
                raise FlowWorkspaceVerificationError(
                    "Google Flow generated image output detected before six videos"
                )
            progress = self._progress_for_expected_count(page.content(), expected_count)
            if progress is not None and progress >= expected_count:
                return
            if self._named_completed_card_set(page, expected_count=expected_count):
                return
            fingerprints = self._completed_video_card_fingerprints(
                page,
                expected_count=expected_count,
            )
            if fingerprints is not None and fingerprints == previous_fingerprints:
                stable_polls += 1
            elif fingerprints is not None:
                stable_polls = 1
            else:
                stable_polls = 0
            previous_fingerprints = fingerprints
            if stable_polls >= self.settled_poll_count:
                return
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    f"Google Flow generation timed out before {expected_count}/{expected_count}"
                )
            time.sleep(self.poll_seconds)

    @classmethod
    def _named_completed_card_set(cls, page: Any, *, expected_count: int) -> bool:
        """Accept a completed renamed card grid when Flow omits AX video metadata."""
        try:
            if not cls._media_inventory_is_observable(page):
                return False
            if cls._media_card_count(page) != expected_count:
                return False
            if page.locator('[aria-busy="true"]:visible').count():
                return False
            if page.get_by_role("progressbar").count():
                return False
            return all(
                page.get_by_text(f"clip {number}", exact=True).count() == 1
                for number in range(1, expected_count + 1)
            )
        except PlaywrightError:
            return False

    @classmethod
    def _completed_video_card_fingerprints(
        cls,
        page: Any,
        *,
        expected_count: int,
    ) -> tuple[str, ...] | None:
        """Return stable AX card fingerprints only for a safe completed-video set."""
        if cls._media_card_count(page) != expected_count:
            return None
        if page.locator('[aria-busy="true"]:visible').count():
            return None
        if page.get_by_role("progressbar").count():
            return None

        session = None
        try:
            session = page.context.new_cdp_session(page)
            nodes = session.send("Accessibility.getFullAXTree").get("nodes", [])
            fingerprints: list[str] = []
            for node in nodes:
                if cls._ax_value(node, "role") != "button":
                    continue
                name = cls._ax_value(node, "name")
                if not _VIDEO_CARD_NAME_RE.search(name):
                    continue
                description = cls._ax_value(node, "description")
                properties = cls._ax_properties(node)
                if not description or properties.get("disabled") or properties.get("busy"):
                    return None
                if _CARD_PROCESSING_RE.search(f"{name} {description}"):
                    return None
                if _CARD_FAILURE_RE.search(f"{name} {description}"):
                    return None
                backend_node_id = node.get("backendDOMNodeId")
                if backend_node_id in {None, ""}:
                    return None
                described_node = session.send(
                    "DOM.describeNode",
                    {
                        "backendNodeId": backend_node_id,
                        "depth": -1,
                        "pierce": True,
                    },
                ).get("node")
                if not cls._dom_subtree_contains_video(described_node):
                    return None
                fingerprints.append(f"ax:{backend_node_id}")

            stable = tuple(sorted(fingerprints))
            if len(stable) != expected_count or len(set(stable)) != expected_count:
                return None
            return stable
        except PlaywrightError:
            return None
        finally:
            if session is not None:
                try:
                    session.detach()
                except PlaywrightError:
                    pass

    @classmethod
    def _dom_subtree_contains_video(cls, node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if str(node.get("nodeName", "")).upper() == "VIDEO":
            return True
        for child in node.get("children", []):
            if cls._dom_subtree_contains_video(child):
                return True
        for shadow_root in node.get("shadowRoots", []):
            if cls._dom_subtree_contains_video(shadow_root):
                return True
        return False

    @staticmethod
    def _ax_value(node: Any, field: str) -> str:
        value = node.get(field, {}) if isinstance(node, dict) else {}
        if not isinstance(value, dict):
            return ""
        return str(value.get("value", "") or "")

    @staticmethod
    def _ax_properties(node: Any) -> dict[str, Any]:
        if not isinstance(node, dict):
            return {}
        result: dict[str, Any] = {}
        for property_value in node.get("properties", []):
            if not isinstance(property_value, dict):
                continue
            name = str(property_value.get("name", "") or "")
            value = property_value.get("value", {})
            if name and isinstance(value, dict):
                result[name] = value.get("value")
        return result

    @staticmethod
    def _generated_image_output_count(page: Any) -> int:
        """Count observable generated-image cards without reading private asset data."""
        try:
            images = page.locator("img[alt]")
            return sum(
                bool(_GENERATED_IMAGE_ALT_RE.fullmatch(images.nth(index).get_attribute("alt") or ""))
                for index in range(images.count())
            )
        except PlaywrightError:
            return 0

    @staticmethod
    def _agent_response_count(page: Any) -> int:
        try:
            feedback = page.get_by_role(
                "button",
                name=_AGENT_RESPONSE_FEEDBACK_NAME_RE,
            )
            return sum(
                feedback.nth(index).is_visible()
                for index in range(feedback.count())
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent response could not be observed"
            ) from exc

    def _wait_for_agent_response(self, page: Any, response_count: int) -> None:
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        while True:
            if self._agent_response_count(page) > response_count:
                return
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent rename completion could not be verified"
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
