from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError

from app.models.cloud_agent import CloudJobRecord, ServiceSessionStatus
from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    FlowBatchIncompleteError,
    FlowGenerationTimeoutError,
    FlowWorkspaceVerificationError,
    HumanRequiredError,
    MediaValidationError,
)
from app.services.cloud_agent.flow_archive import (
    FlowRecoveryCapture,
    FlowRecoveryMaterialization,
    inspect_recovery_flow_archive,
    materialize_flow_archive,
)
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
_PROJECT_MENU_NAME_RE = re.compile(
    r"^more_vert\s+(?:more options|ตัวเลือกเพิ่มเติม)$",
    re.IGNORECASE,
)
_PROJECT_DOWNLOAD_NAME_RE = re.compile(
    r"^(?:download\s+)?(?:download project|ดาวน์โหลดโปรเจ็กต์)$",
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
_RENAME_COMPLETION_RESPONSE_RE = re.compile(
    r"(?:"
    r"ผมได้เปลี่ยนชื่อคลิปทั้ง\s*6\s*เป็น\s*clip\s*1\s*ถึง\s*clip\s*6"
    r".*?(?:เรียบร้อย|สำเร็จ)"
    r"|(?:renamed|have\s+renamed)\s+(?:all\s+)?six\s+clips?"
    r".*?clip\s*1.*?clip\s*6"
    r")",
    re.IGNORECASE | re.DOTALL,
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
_SAFE_ANNOUNCEMENT_BUTTON_NAMES = (
    "เริ่มต้นใช้งาน",
    "Get started",
    "Continue",
    "ดำเนินการต่อ",
    "ไปต่อ",
    "Got it",
    "รับทราบ",
    "รับทราบแล้ว",
    "เข้าใจแล้ว",
    "OK",
    "Okay",
    "Dismiss",
    "Close",
    "ปิด",
    "Not now",
    "ไม่ใช่ตอนนี้",
    "Later",
    "ภายหลัง",
)
_DIALOG_INPUT_SELECTOR = (
    'input:visible, textarea:visible, [contenteditable="true"]:visible, '
    'select:visible'
)
_BLOCKING_DIALOG_RE = re.compile(
    r"(?:sign\s*in|log\s*in|password|passkey|verify|verification|"
    r"captcha|2-step|two-factor|security|payment|billing|purchase|"
    r"delete|remove|move\s+to\s+trash|cancel\s+generation|"
    r"ลงชื่อเข้าใช้|รหัสผ่าน|ยืนยันตัวตน|ความปลอดภัย|ชำระเงิน|"
    r"เรียกเก็บเงิน|ลบ|ย้ายลงถังขยะ|ยกเลิกการสร้าง)",
    re.IGNORECASE,
)
_ANNOUNCEMENT_DIALOG_RE = re.compile(
    r"(?:google\s+flow|flow\s+has|what['’]?s\s+new|new\s+feature|"
    r"new\s+\d{2,4}p|updated?|release\s+notes?|announcement|welcome|"
    r"google\s+flow|มีการเพิ่ม|ฟีเจอร์ใหม่|มีอะไรใหม่|อัปเดต|ประกาศ|"
    r"ยินดีต้อนรับ|\d{2,4}p)",
    re.IGNORECASE,
)
RENAME_CLIPS_INSTRUCTION = "เปลี่ยนชื่อคลิปตามลำดับ ของวีดีโอ"
RENAME_SURVIVING_CLIPS_INSTRUCTION = (
    "เปลี่ยนชื่อวิดีโอที่สร้างสำเร็จแต่ละรายการตามหมายเลข CLIP เดิม "
    "ห้ามเลื่อนหมายเลขเพื่อปิดช่องว่าง ห้ามตั้งชื่อซ้ำ และห้ามเปลี่ยนลำดับ"
)
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


class FlowRecoveryRemoteState(str, Enum):
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    COMPLETE_PROJECT = "COMPLETE_PROJECT"
    REPLACEMENT_ONLY = "REPLACEMENT_ONLY"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class FlowRecoveryObservation:
    state: FlowRecoveryRemoteState
    snapshot_path: Path | None = None


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
        self._prepared_recovery_prompt = ""
        self._prepared_missing_index = 0

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

        self.client._dismiss_safe_announcement_dialog(self.page)
        self.client._submit_generation(self.page, job.master_prompt)
        self.client._wait_for_generation(self.page, expected_count)
        return self._rename_download_and_materialize(paths, expected_count)

    def prepare_agent_prompt(self, master_prompt: str) -> AgentComposer:
        prepared = self.client._prepare_agent_prompt(self.page, master_prompt)
        self._prepared_master_prompt = master_prompt
        return prepared

    def prepare_targeted_replacement(
        self,
        prompt: str,
        *,
        missing_index: int,
    ) -> AgentComposer:
        if missing_index < 1 or missing_index > 6:
            raise ValueError("missing_index must be between 1 and 6")
        prepared = self.client._prepare_agent_prompt(self.page, prompt)
        self._prepared_recovery_prompt = prompt
        self._prepared_missing_index = missing_index
        return prepared

    def submit_targeted_replacement(
        self,
        prompt: str,
        *,
        missing_index: int,
    ) -> None:
        if (
            self._prepared_recovery_prompt != prompt
            or self._prepared_missing_index != missing_index
        ):
            raise FlowWorkspaceVerificationError(
                "Google Flow targeted replacement prompt could not be verified"
            )
        self.client._dismiss_safe_announcement_dialog(self.page)
        self.client._submit_prepared_agent_prompt(self.page, prompt)

    def capture_partial_inventory(
        self,
        paths: JobPaths,
        *,
        attempt: int,
    ) -> FlowRecoveryCapture:
        response_count = self.client._agent_response_count(self.page)
        self.client._submit_agent_prompt(
            self.page,
            RENAME_SURVIVING_CLIPS_INSTRUCTION,
        )
        self._wait_for_rename_response_then_refresh(response_count)
        self._wait_for_stable_semantic_names(expected_count=5)
        snapshot = paths.flow_snapshots_dir / f"partial-{attempt}.zip"
        self._download_project_archive_to(snapshot)
        capture = inspect_recovery_flow_archive(
            snapshot,
            paths,
            min_size_bytes=1,
            expected_width=self.client.expected_width,
            expected_height=self.client.expected_height,
        )
        if isinstance(capture, FlowRecoveryMaterialization):
            return capture
        inventory = capture
        if self._semantic_name_numbers() != inventory.semantic_numbers:
            raise FlowWorkspaceVerificationError(
                "Google Flow missing clip position could not be corroborated"
            )
        return inventory

    def download_recovery_snapshot(
        self,
        paths: JobPaths,
        *,
        attempt: int,
    ) -> Path:
        if attempt < 1 or attempt > 2:
            raise ValueError("attempt must be between 1 and 2")
        snapshot = paths.flow_snapshots_dir / f"replacement-{attempt}.zip"
        self._download_project_archive_to(snapshot)
        return snapshot

    def reconcile_targeted_replacement(
        self,
        paths: JobPaths,
        *,
        missing_index: int,
        attempt: int,
    ) -> FlowRecoveryObservation:
        if missing_index < 1 or missing_index > 6:
            raise ValueError("missing_index must be between 1 and 6")
        completed, failed = self.client._terminal_output_card_counts(self.page)
        if failed:
            return FlowRecoveryObservation(FlowRecoveryRemoteState.FAILED)
        if (
            self.page.locator('[aria-busy="true"]:visible').count()
            or self.page.get_by_role("progressbar").count()
        ):
            return FlowRecoveryObservation(FlowRecoveryRemoteState.RUNNING)
        semantic_numbers = self._semantic_name_numbers()
        if semantic_numbers == tuple(range(1, 7)) and completed >= 6:
            snapshot = self.download_recovery_snapshot(paths, attempt=attempt)
            return FlowRecoveryObservation(
                FlowRecoveryRemoteState.COMPLETE_PROJECT,
                snapshot,
            )
        if semantic_numbers == (missing_index,) and completed == 1:
            snapshot = self.download_recovery_snapshot(paths, attempt=attempt)
            return FlowRecoveryObservation(
                FlowRecoveryRemoteState.REPLACEMENT_ONLY,
                snapshot,
            )
        return FlowRecoveryObservation(FlowRecoveryRemoteState.AMBIGUOUS)

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
        self.client._dismiss_safe_announcement_dialog(self.page)
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
        except (FlowBatchIncompleteError, FlowGenerationTimeoutError):
            raise
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
            if self.client._has_completed_rename_response(self.page):
                self._refresh_and_hydrate()
            else:
                response_count = self.client._agent_response_count(self.page)
                self.client._submit_agent_prompt(
                    self.page,
                    RENAME_CLIPS_INSTRUCTION,
                )
                self._wait_for_rename_response_then_refresh(response_count)
            self._wait_for_stable_semantic_names(
                expected_numbers=tuple(range(1, expected_count + 1)),
            )

        self._download_project_archive(paths)
        return materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=self.client.expected_width,
            expected_height=self.client.expected_height,
        )

    def _download_project_archive(self, paths: JobPaths) -> None:
        self._download_project_archive_to(paths.flow_archive_file)

    def _download_project_archive_to(self, destination: Path) -> None:
        project_menu = self.page.get_by_role(
            "button",
            name=_PROJECT_MENU_NAME_RE,
        )
        if not (
            project_menu.count() == 1
            and project_menu.is_visible()
            and project_menu.is_enabled()
        ):
            raise FlowArchiveValidationError(
                "Google Flow project menu could not be verified"
            )
        project_menu.click()
        download_project = self.page.get_by_role(
            "menuitem",
            name=_PROJECT_DOWNLOAD_NAME_RE,
        )
        if not (
            download_project.count() == 1
            and download_project.is_visible()
            and download_project.is_enabled()
        ):
            raise FlowArchiveValidationError(
                "Google Flow Download Project action could not be verified"
            )
        with self.page.expect_download() as download_info:
            download_project.click()
        destination.parent.mkdir(parents=True, exist_ok=True)
        download_info.value.save_as(str(destination))
        if not destination.is_file():
            raise FlowArchiveValidationError(
                "Google Flow project download did not produce an archive"
            )

    def _semantic_name_numbers(self) -> tuple[int, ...]:
        return tuple(
            number
            for number in range(1, 7)
            if self.page.get_by_text(f"clip {number}", exact=True).count() == 1
        )

    def _semantic_names_are_complete(self, expected_count: int) -> bool:
        return self._semantic_name_numbers() == tuple(range(1, expected_count + 1))

    def _wait_for_rename_response_then_refresh(self, response_count: int) -> None:
        self.client._wait_for_agent_response(self.page, response_count)
        self._refresh_and_hydrate()

    def _refresh_and_hydrate(self) -> None:
        self.page.reload(wait_until="domcontentloaded")
        self.client._hydrate_project_workspace(
            self.page,
            flow_generation_unresolved=True,
        )

    def _wait_for_stable_semantic_names(
        self,
        *,
        expected_numbers: tuple[int, ...] | None = None,
        expected_count: int | None = None,
    ) -> tuple[int, ...]:
        deadline = time.monotonic() + self.client.editor_ready_timeout_seconds
        previous: tuple[int, ...] | None = None
        while True:
            numbers = self._semantic_name_numbers()
            matches = (
                numbers == expected_numbers
                if expected_numbers is not None
                else len(numbers) == expected_count
            )
            if matches:
                if numbers == previous:
                    return numbers
                previous = numbers
            else:
                previous = None
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow semantic clip names could not be verified"
                )
            time.sleep(self.client.poll_seconds)

    def _verify_semantic_names(self, expected_count: int) -> None:
        if not self._semantic_names_are_complete(expected_count):
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
                self._dismiss_safe_announcement_dialog(page)
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
    def _dismiss_safe_announcement_dialog(page: Any) -> None:
        """Dismiss only a verified passive Flow announcement, never an action dialog."""
        try:
            dialog = page.get_by_role("dialog")
            if dialog.count() == 0:
                return
            if dialog.count() != 1 or not dialog.is_visible():
                raise HumanRequiredError(
                    "google_flow blocking dialog requires human recovery"
                )

            dialog_text = str(dialog.inner_text() or "").strip()
            has_visible_input = dialog.locator(_DIALOG_INPUT_SELECTOR).count() > 0
            if (
                not dialog_text
                or has_visible_input
                or _BLOCKING_DIALOG_RE.search(dialog_text)
                or not _ANNOUNCEMENT_DIALOG_RE.search(dialog_text)
            ):
                raise HumanRequiredError(
                    "google_flow blocking dialog requires human recovery"
                )

            for button_name in _SAFE_ANNOUNCEMENT_BUTTON_NAMES:
                button = dialog.get_by_role(
                    "button", name=button_name, exact=True
                )
                if (
                    button.count() == 1
                    and button.is_visible()
                    and button.is_enabled()
                ):
                    button.click()
                    wait_for = getattr(dialog, "wait_for", None)
                    if callable(wait_for):
                        wait_for(state="hidden", timeout=2_000)
                    if dialog.count() != 0 and dialog.is_visible():
                        raise HumanRequiredError(
                            "google_flow announcement dialog did not close"
                        )
                    return

            raise HumanRequiredError(
                "google_flow blocking dialog requires human recovery"
            )
        except HumanRequiredError:
            raise
        except PlaywrightError as exc:
            raise HumanRequiredError(
                "google_flow blocking dialog requires human recovery"
            ) from exc

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
            return cls._active_agent_composer(page)
        except FlowWorkspaceVerificationError as active_error:
            try:
                return cls._fallback_command_composer(page)
            except FlowWorkspaceVerificationError as fallback_error:
                raise active_error from fallback_error

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
            return self._active_or_fallback_command_composer(page)
        if state != "false":
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent state is unknown"
            )

        agent.click()
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        while True:
            try:
                return self._active_or_fallback_command_composer(page)
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
            completed_count, failed_count = self._terminal_output_card_counts(page)
            if (
                failed_count
                and completed_count + failed_count == expected_count
            ):
                raise FlowBatchIncompleteError(
                    completed_count=completed_count,
                    failed_count=failed_count,
                )
            progress = self._progress_for_expected_count(page.content(), expected_count)
            if progress is not None and progress >= expected_count:
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
                raise FlowGenerationTimeoutError(
                    f"Google Flow generation timed out before {expected_count}/{expected_count}"
                )
            time.sleep(self.poll_seconds)

    @classmethod
    def _terminal_output_card_counts(cls, page: Any) -> tuple[int, int]:
        if (
            page.locator('[aria-busy="true"]:visible').count()
            or page.get_by_role("progressbar").count()
        ):
            return (0, 0)
        cards = cls._media_cards(page)
        completed = 0
        failed = 0
        try:
            for index in range(cards.count()):
                card = cards.nth(index)
                if not card.is_visible() or card.get_attribute("aria-busy") == "true":
                    continue
                text = str(card.inner_text() or "")
                if _CARD_PROCESSING_RE.search(text):
                    continue
                if _CARD_FAILURE_RE.search(text):
                    failed += 1
                elif card.locator("video").count() == 1:
                    completed += 1
        except (AttributeError, PlaywrightError):
            return (0, 0)
        return completed, failed

    @classmethod
    def _failed_output_card_count(cls, page: Any) -> int:
        return cls._terminal_output_card_counts(page)[1]

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
    def _has_completed_rename_response(page: Any) -> bool:
        """Recognize a prior Agent completion only to avoid duplicate submission.

        Archive member names remain the sole proof that all six remote assets were
        renamed correctly; this check merely preserves an already-confirmed Agent
        action after an interrupted run.
        """
        try:
            return bool(_RENAME_COMPLETION_RESPONSE_RE.search(page.content()))
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent rename response could not be observed"
            ) from exc

    @staticmethod
    def _progress_for_expected_count(html: str, expected_count: int) -> int | None:
        for current_text, total_text in _PROGRESS_RE.findall(str(html or "")):
            current = int(current_text)
            total = int(total_text)
            if total == expected_count:
                return current
        return None
