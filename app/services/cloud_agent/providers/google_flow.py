from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZipFile

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.models.cloud_agent import (
    CloudJobRecord,
    FlowRecoveryState,
    ServiceSessionStatus,
)
from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    FlowBatchIncompleteError,
    FlowBrowserClosedError,
    FlowGenerationTimeoutError,
    FlowWorkspaceVerificationError,
    HumanRequiredError,
    MediaValidationError,
)
from app.services.cloud_agent.download_transport import save_download_with_url_fallback
from app.services.cloud_agent.flow_archive import (
    FlowPartialInventory,
    FlowRecoveryCapture,
    FlowRecoveryMaterialization,
    inspect_recovery_flow_archive,
    materialize_flow_archive,
    validate_flow_source_video,
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
_CARD_CLIP_TITLE_RE = re.compile(r"\bclip\s*([1-6])\b", re.IGNORECASE)
_MEDIA_CARD_SELECTOR = (
    '[data-testid="virtuoso-item-list"]:visible [role="button"][tabindex="0"]'
)
_CARD_DELETE_NAME_RE = re.compile(
    r"(?:delete\s+)?(?:move to trash|ย้ายลงถังขยะ)",
    re.IGNORECASE,
)
_CARD_OVERFLOW_NAME_RE = re.compile(
    r"^(?:more_vert\s+)?(?:more|เพิ่มเติม)$",
    re.IGNORECASE,
)
_CARD_DOWNLOAD_NAME_RE = re.compile(
    r"^(?:download\s+)?(?:download|ดาวน์โหลด)$",
    re.IGNORECASE,
)
_CARD_720P_NAME_RE = re.compile(
    r"^(?:720p(?:\s+(?:video|original\s+size|ขนาดดั้งเดิม))?"
    r"|video\s+720p|วิดีโอ\s*720p|720p\s*วิดีโอ)$",
    re.IGNORECASE,
)
_PROJECT_MENU_NAME_RE = re.compile(
    r"^(?:more_vert\s+)?(?:more options|ตัวเลือกเพิ่มเติม|more|เพิ่มเติม)$",
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
_COMMAND_STOP_NAME_RE = re.compile(
    r"^(?:(?:stop|cancel)(?:[_ ](?:circle|generation|generating))?"
    r"|(?:หยุด|ยกเลิก)(?:การสร้าง)?)$",
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
    'input:visible, textarea:visible, [contenteditable="true"]:visible, select:visible'
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
RENAME_SURVIVING_CLIPS_INSTRUCTION = "เปลี่ยนชื่อวิดีโอที่สร้างสำเร็จแต่ละรายการตามหมายเลข CLIP เดิม ห้ามเลื่อนหมายเลขเพื่อปิดช่องว่าง ห้ามตั้งชื่อซ้ำ และห้ามเปลี่ยนลำดับ"
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
    requires_active_agent: bool = True


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
    def __init__(
        self,
        browser,
        *,
        service_url: str,
        editor_ready_timeout_seconds: float = 120.0,
    ) -> None:
        if editor_ready_timeout_seconds <= 0:
            raise ValueError("editor_ready_timeout_seconds must be positive")
        super().__init__(
            browser,
            service="google_flow",
            service_url=service_url,
            classifier=classify_google_flow_session,
        )
        self.editor_ready_timeout_seconds = float(editor_ready_timeout_seconds)

    def _navigate_to_service(self, page: Any) -> None:
        try:
            page.goto(
                self.service_url,
                wait_until="domcontentloaded",
                timeout=max(1, int(self.editor_ready_timeout_seconds * 1000)),
            )
        except PlaywrightTimeoutError:
            # The base provider classifies the observable page immediately
            # afterward, including login and security-challenge states.
            return


class FlowWorkspaceRun:
    def __init__(
        self,
        client: GoogleFlowClient,
        page: Any,
        *,
        job_id: str = "",
        prefer_individual_download: bool = False,
    ) -> None:
        self.client = client
        self.page = page
        self.job_id = job_id
        self.prefer_individual_download = bool(prefer_individual_download)
        self._prepared_master_prompt = ""
        self._prepared_recovery_prompt = ""
        self._prepared_missing_index = 0
        self._prepared_generation_composer: AgentComposer | None = None
        self._prepared_recovery_composer: AgentComposer | None = None
        self._prepared_recovery_baseline_cards: tuple[Any, ...] | None = None

    def preclean_and_verify_empty(self) -> None:
        while self.client._media_card_count(self.page):
            self._delete_one_current_media_card()

        self.client._navigate_workspace_page(
            self.page,
            job_id=self.job_id,
        )
        try:
            self.client._hydrate_project_workspace(
                self.page,
                flow_generation_unresolved=False,
                job_id=self.job_id,
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
    ) -> tuple[Path, ...] | FlowPartialInventory:
        if expected_count != 6:
            raise ValueError("Google Flow workspace requires exactly six clips")

        self.client._dismiss_safe_announcement_dialog(self.page)
        self.client._submit_generation(self.page, job.master_prompt)
        self.client._wait_for_generation(self.page, expected_count)
        return self._rename_download_and_materialize(paths, expected_count)

    def prepare_agent_prompt(self, master_prompt: str) -> AgentComposer:
        prepared = self.client._prepare_agent_prompt(self.page, master_prompt)
        self._prepared_master_prompt = master_prompt
        self._prepared_generation_composer = prepared
        return prepared

    def prepare_targeted_replacement(
        self,
        prompt: str,
        *,
        missing_index: int,
    ) -> AgentComposer:
        if missing_index < 1 or missing_index > 6:
            raise ValueError("missing_index must be between 1 and 6")
        self._prepared_recovery_baseline_cards = self._pin_current_media_cards()
        prepared = self.client._prepare_agent_prompt(self.page, prompt)
        self._prepared_recovery_prompt = prompt
        self._prepared_missing_index = missing_index
        self._prepared_recovery_composer = prepared
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
        self.client._submit_prepared_agent_prompt(
            self.page,
            prompt,
            self._prepared_recovery_composer,
        )

    def capture_partial_inventory(
        self,
        paths: JobPaths,
        *,
        attempt: int,
    ) -> FlowRecoveryCapture:
        response_count = self.client._agent_response_count(self.page)
        self.client._submit_recovery_agent_prompt(
            self.page,
            RENAME_SURVIVING_CLIPS_INSTRUCTION,
        )
        self._wait_for_rename_response_then_refresh(response_count)
        self._wait_for_recovery_archive_grace()
        snapshot = paths.flow_snapshots_dir / f"partial-{attempt}.zip"
        capture = self._download_and_inspect_recovery_archive(snapshot, paths)
        if isinstance(capture, FlowRecoveryMaterialization):
            return capture
        inventory = capture
        if len(inventory.semantic_numbers) != 5:
            raise FlowArchiveValidationError(
                "Google Flow recovery archive must contain exactly five or six clips"
            )
        return inventory

    def _wait_for_recovery_archive_grace(self) -> None:
        deadline = time.monotonic() + self.client.post_refresh_grace_seconds
        while True:
            self.client._verify_workspace_session(self.page, "")
            self.client._dismiss_safe_announcement_dialog(self.page)
            actionable = self.client._is_editor_actionable(self.page)
            if time.monotonic() >= deadline:
                if actionable:
                    return
                raise FlowWorkspaceVerificationError(
                    "Google Flow project editor could not be verified during recovery grace"
                )
            time.sleep(self.client.poll_seconds)

    def _download_and_inspect_recovery_archive(
        self,
        snapshot: Path,
        paths: JobPaths,
    ) -> FlowRecoveryCapture:
        def inspect(downloaded: Path) -> FlowRecoveryCapture:
            try:
                materialized = materialize_flow_archive(
                    downloaded,
                    paths,
                    min_size_bytes=1,
                    expected_width=self.client.expected_width,
                    expected_height=self.client.expected_height,
                )
            except FlowArchiveValidationError:
                return inspect_recovery_flow_archive(
                    downloaded,
                    paths,
                    min_size_bytes=1,
                    expected_width=self.client.expected_width,
                    expected_height=self.client.expected_height,
                )
            return FlowRecoveryMaterialization(
                paths=materialized,
                source="latest_complete_archive",
            )

        return self._download_project_archive_with_fallback(snapshot, validate=inspect)

    def download_recovery_snapshot(
        self,
        paths: JobPaths,
        *,
        attempt: int,
    ) -> Path:
        if attempt < 1 or attempt > 2:
            raise ValueError("attempt must be between 1 and 2")
        snapshot = paths.flow_snapshots_dir / f"replacement-{attempt}.zip"
        self._download_project_archive_with_fallback(snapshot)
        return snapshot

    def _pin_current_media_cards(self) -> tuple[Any, ...] | None:
        try:
            cards = self.client._media_cards(self.page)
            handles = []
            for index in range(cards.count()):
                card = cards.nth(index)
                if not card.is_visible():
                    return None
                handle = card.element_handle()
                if handle is None:
                    return None
                handles.append(handle)
            return tuple(handles)
        except PlaywrightError:
            return None

    @staticmethod
    def _same_element(first: Any, second: Any) -> bool:
        try:
            return bool(
                first.evaluate(
                    "(element, other) => element === other",
                    second,
                )
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow replacement card identity could not be verified"
            ) from exc

    def _added_cards_with_preserved_baseline(
        self,
        current: list[tuple[Any, Any]],
    ) -> list[tuple[Any, Any]] | None:
        baseline = self._prepared_recovery_baseline_cards
        if baseline is None or len(current) != len(baseline) + 1:
            return None
        matched_current: set[int] = set()
        for prior in baseline:
            matches = [
                index
                for index, (_card, handle) in enumerate(current)
                if self._same_element(handle, prior)
            ]
            if len(matches) != 1 or matches[0] in matched_current:
                return None
            matched_current.add(matches[0])
        return [
            item for index, item in enumerate(current) if index not in matched_current
        ]

    @staticmethod
    def _card_is_completed_video(card: Any) -> bool:
        try:
            if not card.is_visible() or card.get_attribute("aria-busy") == "true":
                return False
            text = str(card.inner_text() or "")
            return bool(
                not _CARD_PROCESSING_RE.search(text)
                and not _CARD_FAILURE_RE.search(text)
                and card.locator("video").count() == 1
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow replacement card could not be verified"
            ) from exc

    def _replacement_card_candidate(
        self,
        missing_index: int,
    ) -> tuple[Any, Any] | None:
        cards = self.client._media_cards(self.page)
        current: list[tuple[Any, Any]] = []
        try:
            for index in range(cards.count()):
                card = cards.nth(index)
                if not card.is_visible():
                    continue
                handle = card.element_handle()
                if handle is None:
                    raise FlowWorkspaceVerificationError(
                        "Google Flow replacement card identity could not be verified"
                    )
                current.append((card, handle))
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow replacement card identity could not be verified"
            ) from exc

        titled = [
            item
            for item in current
            if item[0]
            .get_by_text(
                f"clip {missing_index}",
                exact=True,
            )
            .count()
            == 1
            and self._card_is_completed_video(item[0])
        ]
        if len(titled) == 1:
            return titled[0]
        if len(titled) > 1:
            return None
        added = self._added_cards_with_preserved_baseline(current)
        if added is None:
            return None
        completed_added = [
            item for item in added if self._card_is_completed_video(item[0])
        ]
        return completed_added[0] if len(completed_added) == 1 else None

    def _new_failed_card_observed(self) -> bool:
        baseline = self._prepared_recovery_baseline_cards
        if baseline is None:
            return False
        cards = self.client._media_cards(self.page)
        try:
            current = []
            for index in range(cards.count()):
                card = cards.nth(index)
                handle = card.element_handle()
                if handle is None:
                    return False
                current.append((card, handle))
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow replacement failure state could not be verified"
            ) from exc
        added = self._added_cards_with_preserved_baseline(current)
        return bool(
            added is not None
            and len(added) == 1
            and _CARD_FAILURE_RE.search(str(added[0][0].inner_text() or ""))
        )

    def _download_replacement_card_to(
        self,
        card: Any,
        pinned_card: Any,
        destination: Path,
        *,
        missing_index: int,
    ) -> None:
        temporary_video = (
            destination.parent / f".{destination.stem}.clip-{missing_index}.mp4"
        )
        temporary_archive = destination.parent / f".{destination.name}.tmp"
        temporary_video.unlink(missing_ok=True)
        temporary_archive.unlink(missing_ok=True)
        try:
            current = card.element_handle()
            if current is None or not self._same_element(current, pinned_card):
                raise FlowWorkspaceVerificationError(
                    "Google Flow replacement card identity could not be verified"
                )
            pinned_card.hover()
            overflow = card.get_by_role("button", name=_CARD_OVERFLOW_NAME_RE)
            if not (
                overflow.count() == 1
                and overflow.is_visible()
                and overflow.is_enabled()
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow replacement card menu could not be verified"
                )
            overflow.click()
            download = self.page.get_by_role(
                "menuitem",
                name=_CARD_DOWNLOAD_NAME_RE,
            )
            if not (
                download.count() == 1
                and download.is_visible()
                and download.is_enabled()
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow replacement card Download action could not be verified"
                )
            download.click()
            format_deadline = (
                time.monotonic() + self.client.editor_ready_timeout_seconds
            )
            while True:
                video_format = self.page.get_by_role(
                    "menuitem",
                    name=_CARD_720P_NAME_RE,
                )
                if (
                    video_format.count() == 1
                    and video_format.is_visible()
                    and video_format.is_enabled()
                ):
                    break
                if time.monotonic() >= format_deadline:
                    raise FlowWorkspaceVerificationError(
                        "Google Flow replacement video download format could not be verified"
                    )
                time.sleep(self.client.poll_seconds)
            with self.page.expect_download(
                timeout=int(
                    self.client.project_archive_download_timeout_seconds * 1000
                )
            ) as download_info:
                video_format.click()
            destination.parent.mkdir(parents=True, exist_ok=True)
            save_download_with_url_fallback(
                download_info.value,
                temporary_video,
                timeout_seconds=self.client.project_archive_download_timeout_seconds,
            )
        except PlaywrightError as exc:
            temporary_video.unlink(missing_ok=True)
            raise FlowWorkspaceVerificationError(
                "Google Flow replacement download could not be verified"
            ) from exc

        try:
            validate_flow_source_video(temporary_video, min_size_bytes=1)
            with ZipFile(temporary_archive, "w") as archive:
                archive.write(temporary_video, arcname=f"clip {missing_index}.mp4")
            temporary_archive.replace(destination)
        except (MediaValidationError, OSError) as exc:
            raise FlowArchiveValidationError(
                f"invalid Google Flow replacement download: {exc}"
            ) from exc
        finally:
            temporary_video.unlink(missing_ok=True)
            temporary_archive.unlink(missing_ok=True)
        if not destination.is_file():
            raise FlowArchiveValidationError(
                "Google Flow replacement download did not produce an archive"
            )

    def reconcile_targeted_replacement(
        self,
        paths: JobPaths,
        *,
        missing_index: int,
        attempt: int,
    ) -> FlowRecoveryObservation:
        if missing_index < 1 or missing_index > 6:
            raise ValueError("missing_index must be between 1 and 6")
        if (
            self.page.locator('[aria-busy="true"]:visible').count()
            or self.page.get_by_role("progressbar").count()
        ):
            return FlowRecoveryObservation(FlowRecoveryRemoteState.RUNNING)
        candidate = self._replacement_card_candidate(missing_index)
        if candidate is not None:
            snapshot = paths.flow_snapshots_dir / f"replacement-{attempt}.zip"
            self._download_replacement_card_to(
                candidate[0],
                candidate[1],
                snapshot,
                missing_index=missing_index,
            )
            return FlowRecoveryObservation(
                FlowRecoveryRemoteState.REPLACEMENT_ONLY,
                snapshot,
            )
        completed, failed = self.client._terminal_output_card_counts(self.page)
        if failed and self._new_failed_card_observed():
            return FlowRecoveryObservation(FlowRecoveryRemoteState.FAILED)
        semantic_numbers = self._semantic_name_numbers()
        complete_numbers = tuple(range(1, 7))
        if semantic_numbers == complete_numbers and completed >= 6:
            self._wait_for_stable_semantic_names(
                expected_numbers=complete_numbers,
            )
            snapshot = self.download_recovery_snapshot(paths, attempt=attempt)
            return FlowRecoveryObservation(
                FlowRecoveryRemoteState.COMPLETE_PROJECT,
                snapshot,
            )
        return FlowRecoveryObservation(FlowRecoveryRemoteState.AMBIGUOUS)

    def submit_prepared_generation_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...] | FlowPartialInventory:
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
            self._prepared_generation_composer,
        )
        self.client._wait_for_generation(self.page, expected_count)
        return self._rename_download_and_materialize(paths, expected_count)

    def reconcile_and_download(
        self,
        job: CloudJobRecord,
        paths: JobPaths,
        expected_count: int = 6,
    ) -> tuple[Path, ...] | FlowPartialInventory:
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
    ) -> tuple[Path, ...] | FlowPartialInventory:
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
            self._wait_for_recovery_archive_grace()
            snapshot = paths.flow_snapshots_dir / "partial-0.zip"
            capture = self._download_and_inspect_recovery_archive(
                snapshot,
                paths,
            )
            if isinstance(capture, FlowRecoveryMaterialization):
                return capture.paths
            return capture

        def materialize(downloaded: Path) -> tuple[Path, ...]:
            return materialize_flow_archive(
                downloaded,
                paths,
                min_size_bytes=1,
                expected_width=self.client.expected_width,
                expected_height=self.client.expected_height,
            )

        return self._download_project_archive_with_fallback(
            paths.flow_archive_file,
            validate=materialize,
        )

    def _download_project_archive(self, paths: JobPaths) -> None:
        self._download_project_archive_with_fallback(paths.flow_archive_file)

    def _download_project_archive_with_fallback(
        self,
        destination: Path,
        *,
        validate: Callable[[Path], Any] | None = None,
    ) -> Any:
        def validated_result() -> Any:
            return None if validate is None else validate(destination)

        try:
            self._download_project_archive_to(destination)
            return validated_result()
        except FlowBrowserClosedError:
            raise
        except FlowArchiveValidationError:
            self._download_individual_cards_archive_to(destination)
            return validated_result()

    def _semantic_number_after_hover(self, card: Any, pinned_card: Any) -> int:
        current = card.element_handle()
        if current is None or not self._same_element(current, pinned_card):
            raise FlowWorkspaceVerificationError(
                "Google Flow fallback card identity changed before title verification"
            )
        pinned_card.hover()
        deadline = time.monotonic() + self.client.editor_ready_timeout_seconds
        while True:
            numbers = set()
            for number in range(1, 7):
                title = card.get_by_text(
                    re.compile(
                        rf"^\s*clip\s*{number}(?:\s*(?:[-:—–]|\|).*)?\s*$",
                        re.IGNORECASE,
                    )
                )
                if title.count() == 1 and title.is_visible():
                    numbers.add(number)
            if len(numbers) == 1:
                current = card.element_handle()
                if current is None or not self._same_element(current, pinned_card):
                    raise FlowWorkspaceVerificationError(
                        "Google Flow fallback card identity changed during title verification"
                    )
                return numbers.pop()
            if len(numbers) > 1:
                raise FlowWorkspaceVerificationError(
                    "Google Flow card title contained ambiguous clip numbers"
                )
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow card clip title could not be verified after hover"
                )
            time.sleep(self.client.poll_seconds)

    def _download_individual_cards_archive_to(self, destination: Path) -> None:
        cards = self.client._media_cards(self.page)
        part_archives: dict[int, Path] = {}
        temporary_archive = destination.parent / f".{destination.name}.individual.tmp"
        temporary_archive.unlink(missing_ok=True)
        try:
            for index in range(cards.count()):
                card = cards.nth(index)
                if not self._card_is_completed_video(card):
                    continue
                pinned_card = card.element_handle()
                if pinned_card is None:
                    raise FlowWorkspaceVerificationError(
                        "Google Flow card identity could not be verified for fallback download"
                    )
                clip_number = self._semantic_number_after_hover(card, pinned_card)
                if clip_number in part_archives:
                    raise FlowWorkspaceVerificationError(
                        f"Google Flow card title duplicated clip {clip_number}"
                    )
                part_archive = (
                    destination.parent
                    / f".{destination.name}.individual-clip-{clip_number}.zip"
                )
                part_archive.unlink(missing_ok=True)
                self._download_replacement_card_to(
                    card,
                    pinned_card,
                    part_archive,
                    missing_index=clip_number,
                )
                part_archives[clip_number] = part_archive

            if len(part_archives) not in {5, 6}:
                raise FlowArchiveValidationError(
                    "Google Flow individual fallback must map exactly five or six clips"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            with ZipFile(temporary_archive, "w") as combined:
                for clip_number in sorted(part_archives):
                    member = f"clip {clip_number}.mp4"
                    with ZipFile(part_archives[clip_number]) as source:
                        if source.namelist() != [member]:
                            raise FlowArchiveValidationError(
                                "Google Flow individual fallback archive was ambiguous"
                            )
                        combined.writestr(member, source.read(member))
            temporary_archive.replace(destination)
        finally:
            temporary_archive.unlink(missing_ok=True)
            for part_archive in part_archives.values():
                part_archive.unlink(missing_ok=True)

        if not destination.is_file():
            raise FlowArchiveValidationError(
                "Google Flow individual fallback did not produce an archive"
            )

    def _project_download_menu_control(self) -> Any:
        controls = self.page.get_by_role(
            "button",
            name=_PROJECT_MENU_NAME_RE,
        )
        try:
            count = controls.count()
            if count == 1 and controls.is_visible() and controls.is_enabled():
                return controls
            visible = []
            for index in range(count):
                control = controls.nth(index)
                if not control.is_visible() or not control.is_enabled():
                    continue
                box = control.bounding_box()
                if box is None:
                    continue
                visible.append((float(box["x"]) + float(box["width"]), control))
        except (KeyError, TypeError, PlaywrightError) as exc:
            raise FlowArchiveValidationError(
                "Google Flow project menu could not be verified"
            ) from exc
        if not visible:
            raise FlowArchiveValidationError(
                "Google Flow project menu could not be verified"
            )
        visible.sort(key=lambda item: item[0])
        if len(visible) > 1 and visible[-1][0] == visible[-2][0]:
            raise FlowArchiveValidationError(
                "Google Flow project menu position was ambiguous"
            )
        return visible[-1][1]

    def _download_project_archive_to(self, destination: Path) -> None:
        project_menu = self._project_download_menu_control()
        project_menu.click()
        deadline = time.monotonic() + self.client.editor_ready_timeout_seconds
        while True:
            download_project = self.page.get_by_role(
                "menuitem",
                name=_PROJECT_DOWNLOAD_NAME_RE,
            )
            if (
                download_project.count() == 1
                and download_project.is_visible()
                and download_project.is_enabled()
            ):
                break
            if time.monotonic() >= deadline:
                raise FlowArchiveValidationError(
                    "Google Flow Download Project action could not be verified"
                )
            time.sleep(self.client.poll_seconds)
        try:
            with self.page.expect_download(
                timeout=int(self.client.project_archive_download_timeout_seconds * 1000)
            ) as download_info:
                download_project.click()
            download = download_info.value
        except PlaywrightTimeoutError as exc:
            raise FlowArchiveValidationError(
                "Google Flow project archive download timed out"
            ) from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_archive = destination.parent / f".{destination.name}.project.tmp"
        temporary_archive.unlink(missing_ok=True)
        try:
            save_download_with_url_fallback(
                download,
                temporary_archive,
                timeout_seconds=self.client.project_archive_download_timeout_seconds,
            )
        except PlaywrightError as exc:
            temporary_archive.unlink(missing_ok=True)
            if "target page, context or browser has been closed" in str(exc).casefold():
                raise FlowBrowserClosedError(
                    "Google Flow browser closed during project archive download"
                ) from exc
            raise FlowArchiveValidationError(
                "Google Flow project archive could not be saved"
            ) from exc
        try:
            if not temporary_archive.is_file():
                raise FlowArchiveValidationError(
                    "Google Flow project download did not produce an archive"
                )
            temporary_archive.replace(destination)
        finally:
            temporary_archive.unlink(missing_ok=True)
        if not destination.is_file():
            raise FlowArchiveValidationError(
                "Google Flow project download did not produce an archive"
            )

    def _semantic_name_numbers(self) -> tuple[int, ...]:
        media_cards = self.page.locator(_MEDIA_CARD_SELECTOR)
        return tuple(
            number
            for number in range(1, 7)
            if media_cards.get_by_text(f"clip {number}", exact=True).count() == 1
        )

    def _semantic_names_are_complete(self, expected_count: int) -> bool:
        return self._semantic_name_numbers() == tuple(range(1, expected_count + 1))

    def _wait_for_rename_response_then_refresh(self, response_count: int) -> None:
        self.client._wait_for_agent_response(self.page, response_count)
        self._refresh_and_hydrate()

    def _refresh_and_hydrate(self) -> None:
        self.client._navigate_workspace_page(
            self.page,
            job_id=self.job_id,
        )
        self.client._hydrate_project_workspace(
            self.page,
            flow_generation_unresolved=True,
            job_id=self.job_id,
        )

    def _wait_for_stable_semantic_names(
        self,
        *,
        expected_numbers: tuple[int, ...] | None = None,
        expected_count: int | None = None,
    ) -> tuple[int, ...]:
        if (expected_numbers is None) == (expected_count is None):
            raise ValueError("exactly one semantic name expectation is required")
        if expected_count is not None and not 0 <= expected_count <= 6:
            raise ValueError("expected_count must be between 0 and 6")

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
        project_archive_download_timeout_seconds: float = 300.0,
        post_refresh_grace_seconds: float = 120.0,
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
        if project_archive_download_timeout_seconds <= 0:
            raise ValueError(
                "project_archive_download_timeout_seconds must be positive"
            )
        if post_refresh_grace_seconds <= 0:
            raise ValueError("post_refresh_grace_seconds must be positive")
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
        self.project_archive_download_timeout_seconds = float(
            project_archive_download_timeout_seconds
        )
        self.post_refresh_grace_seconds = float(post_refresh_grace_seconds)
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
            self._navigate_workspace_page(
                page,
                target_url=self._flow_home_url(),
                job_id=job.id,
                allow_expected_session_expired=True,
            )
            self._navigate_workspace_page(
                page,
                target_url=self.service_url,
                job_id=job.id,
            )
            self._verify_workspace_session(page, job.id)
            self._hydrate_project_workspace(
                page,
                flow_generation_unresolved=job.flow_generation_unresolved,
                job_id=job.id,
            )
            yield FlowWorkspaceRun(
                self,
                page,
                job_id=job.id,
                prefer_individual_download=(
                    getattr(job, "flow_recovery_state", FlowRecoveryState.NONE)
                    is FlowRecoveryState.INVENTORY_PENDING
                    and getattr(job, "flow_workspace_retry_attempts", 0) > 0
                ),
            )

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
    def _is_navigation_target(current_url: str, expected_url: str) -> bool:
        expected = urlsplit(str(expected_url or ""))
        current = urlsplit(str(current_url or ""))
        return (
            current.scheme.casefold() == expected.scheme.casefold()
            and current.netloc.casefold() == expected.netloc.casefold()
            and current.path.rstrip("/") == expected.path.rstrip("/")
        )

    def _navigate_workspace_page(
        self,
        page: Any,
        *,
        target_url: str | None = None,
        job_id: str = "",
        allow_expected_session_expired: bool = False,
    ) -> None:
        expected_url = self.service_url if target_url is None else target_url
        navigation_options = {
            "wait_until": "domcontentloaded",
            "timeout": max(1, int(self.editor_ready_timeout_seconds * 1000)),
        }
        navigation_error: PlaywrightError | None = None
        try:
            if target_url is None:
                page.reload(**navigation_options)
            else:
                page.goto(target_url, **navigation_options)
        except PlaywrightError as exc:
            navigation_error = exc

        url_error: PlaywrightError | None = None
        current_url = ""
        try:
            current_url = page.url
            reached_target = self._is_navigation_target(
                current_url,
                expected_url,
            )
        except PlaywrightError as exc:
            url_error = exc
            reached_target = False

        try:
            self._verify_workspace_session(
                page,
                job_id,
                allow_session_expired=(
                    allow_expected_session_expired and reached_target
                ),
            )
        except HumanRequiredError as human_required:
            if navigation_error is not None:
                raise human_required from navigation_error
            raise
        except FlowWorkspaceVerificationError as session_error:
            url_only_status = classify_google_flow_session(
                url=current_url,
                html="",
            )
            if url_only_status not in {
                ServiceSessionStatus.READY,
                ServiceSessionStatus.ERROR,
            }:
                error = HumanRequiredError(
                    f"google_flow session requires human recovery for job {job_id}: {url_only_status.value}"
                )
                raise error from (navigation_error or session_error)
            if isinstance(navigation_error, PlaywrightTimeoutError) and reached_target:
                return
            if navigation_error is None:
                raise
            raise FlowWorkspaceVerificationError(
                "Google Flow navigation could not be verified"
            ) from navigation_error

        if url_error is not None:
            raise FlowWorkspaceVerificationError(
                "Google Flow navigation could not be verified"
            ) from (navigation_error or url_error)

        if navigation_error is not None and not isinstance(
            navigation_error,
            PlaywrightTimeoutError,
        ):
            raise FlowWorkspaceVerificationError(
                "Google Flow navigation could not be verified"
            ) from navigation_error
        if not reached_target:
            error = FlowWorkspaceVerificationError(
                "Google Flow navigation did not reach the expected URL"
            )
            if navigation_error is not None:
                raise error from navigation_error
            raise error

    @staticmethod
    def _verify_workspace_session(
        page: Any,
        job_id: str,
        *,
        allow_session_expired: bool = False,
    ) -> None:
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
        if allow_session_expired and status is ServiceSessionStatus.SESSION_EXPIRED:
            return
        raise HumanRequiredError(
            f"google_flow session requires human recovery for job {job_id}: {status.value}"
        )

    def _hydrate_project_workspace(
        self,
        page: Any,
        *,
        flow_generation_unresolved: bool,
        job_id: str = "",
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
                self._navigate_workspace_page(page, job_id=job_id)
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
                button = dialog.get_by_role("button", name=button_name, exact=True)
                if button.count() == 1 and button.is_visible() and button.is_enabled():
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
        if agent.count() == 1 and agent.is_visible() and agent.is_enabled():
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

    @classmethod
    def _media_inventory_is_observable(cls, page: Any) -> bool:
        media_list = page.locator('[data-testid="virtuoso-item-list"]:visible')
        if media_list.count() == 1 and media_list.is_visible():
            return True
        try:
            cls._active_or_fallback_command_composer(page)
        except FlowWorkspaceVerificationError:
            return False
        return True

    @classmethod
    def _active_agent_composer(cls, page: Any) -> AgentComposer:
        try:
            agent = cls._agent_control(page)
            if agent.get_attribute("aria-pressed") != "true":
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent state is not active"
                )
            container = agent.locator(
                "xpath=ancestor::div[.//textarea or .//*[@contenteditable='true']][1]"
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
            return cls._pin_composer(
                agent=agent,
                container=container,
                prompt=prompt,
                generate=generate,
            )
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow Agent state could not be verified"
            ) from exc

    @classmethod
    def _fallback_command_composer(cls, page: Any) -> AgentComposer:
        """Find the post-generation command composer when the Agent toggle is absent."""
        try:
            prompts = page.locator(_COMMAND_COMPOSER_SELECTOR)
            prompt_count = prompts.count()
            valid: list[tuple[float, float, AgentComposer]] = []
            for index in range(prompt_count):
                prompt = prompts if prompt_count == 1 else prompts.nth(index)
                container = prompt.locator("xpath=ancestor::div[.//button][1]")
                generate = container.get_by_role(
                    "button",
                    name=_COMMAND_SUBMIT_NAME_RE,
                )
                try:
                    composer = cls._pin_composer(
                        agent=prompt,
                        container=container,
                        prompt=prompt,
                        generate=generate,
                        requires_active_agent=False,
                    )
                    bounds = composer.prompt.bounding_box()
                except (FlowWorkspaceVerificationError, PlaywrightError):
                    continue
                if bounds is None:
                    continue
                right = float(bounds["x"]) + float(bounds["width"])
                bottom = float(bounds["y"]) + float(bounds["height"])
                valid.append((right, bottom, composer))
            if not valid:
                raise FlowWorkspaceVerificationError(
                    "Google Flow fallback command composer could not be verified"
                )
            ranked = sorted(
                valid, key=lambda candidate: (candidate[0], candidate[1]), reverse=True
            )
            if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
                raise FlowWorkspaceVerificationError(
                    "Google Flow fallback command composer could not be verified"
                )
            return ranked[0][2]
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow fallback command composer could not be verified"
            ) from exc

    @classmethod
    def _pin_composer(
        cls,
        *,
        agent: Any,
        container: Any,
        prompt: Any,
        generate: Any,
        requires_active_agent: bool = True,
    ) -> AgentComposer:
        try:
            if requires_active_agent and (
                agent.count() != 1
                or not agent.is_visible()
                or not agent.is_enabled()
                or agent.get_attribute("aria-pressed") != "true"
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent state could not be verified"
                )
            if not (
                prompt.count() == 1
                and prompt.is_visible()
                and container.count() == 1
                and container.is_visible()
                and generate.count() == 1
                and generate.is_visible()
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow command composer could not be verified"
                )
            handles = tuple(
                locator.element_handle()
                for locator in (agent, container, prompt, generate)
            )
            if any(handle is None for handle in handles):
                raise FlowWorkspaceVerificationError(
                    "Google Flow command composer could not be verified"
                )
            composer = AgentComposer(
                agent=handles[0],
                container=handles[1],
                prompt=handles[2],
                generate=handles[3],
                requires_active_agent=requires_active_agent,
            )
            return cls._verify_resolved_composer(composer)
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow command composer could not be verified"
            ) from exc

    @staticmethod
    def _verify_resolved_composer(
        composer: AgentComposer,
        prompt_text: str | None = None,
    ) -> AgentComposer:
        try:
            if composer.requires_active_agent and (
                not composer.agent.is_visible()
                or not composer.agent.is_enabled()
                or composer.agent.get_attribute("aria-pressed") != "true"
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent state could not be verified"
                )
            if not (
                composer.prompt.is_visible()
                and composer.container.is_visible()
                and composer.generate.is_visible()
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow command composer could not be verified"
                )
            if (
                prompt_text is not None
                and GoogleFlowClient._prompt_value(composer.prompt) != prompt_text
            ):
                raise FlowWorkspaceVerificationError(
                    "Google Flow active Agent prompt value could not be verified"
                )
            return composer
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow command composer could not be verified"
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
            raise FlowWorkspaceVerificationError("Google Flow Agent state is unknown")

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
            if page.locator('[aria-busy="true"]:visible').count() != 0:
                return False
            if page.get_by_role("progressbar").count() != 0:
                return False
            self._ensure_agent_active(page)
            return True
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
        try:
            composer.prompt.fill(prompt_text)
        except PlaywrightError as exc:
            raise FlowWorkspaceVerificationError(
                "Google Flow active Agent prompt could not be filled"
            ) from exc
        return self._verify_resolved_composer(composer, prompt_text)

    def _submit_prepared_agent_prompt(
        self,
        page: Any,
        prompt_text: str,
        composer: AgentComposer | None,
    ) -> Any:
        del page
        if composer is None:
            raise FlowWorkspaceVerificationError(
                "Google Flow prepared command composer could not be verified"
            )
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        while True:
            verified = self._verify_resolved_composer(composer, prompt_text)
            try:
                submit_enabled = verified.generate.is_enabled()
            except PlaywrightError as exc:
                raise FlowWorkspaceVerificationError(
                    "Google Flow active Agent submit control could not be verified"
                ) from exc
            if submit_enabled:
                try:
                    verified.generate.click()
                except PlaywrightError as exc:
                    raise FlowWorkspaceVerificationError(
                        "Google Flow active Agent submit control could not be verified"
                    ) from exc
                return verified.generate
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow active Agent submit control could not be verified"
                )
            time.sleep(self.poll_seconds)

    def _submit_agent_prompt(self, page: Any, prompt_text: str) -> Any:
        prepared = self._prepare_agent_prompt(page, prompt_text)
        return self._submit_prepared_agent_prompt(page, prompt_text, prepared)

    def _submit_recovery_agent_prompt(self, page: Any, prompt_text: str) -> Any:
        deadline = time.monotonic() + self.editor_ready_timeout_seconds
        while self._command_composer_is_busy(page):
            if time.monotonic() >= deadline:
                raise FlowWorkspaceVerificationError(
                    "Google Flow Agent command composer remained busy during recovery"
                )
            time.sleep(self.poll_seconds)
        return self._submit_agent_prompt(page, prompt_text)

    @classmethod
    def _command_composer_is_busy(cls, page: Any) -> bool:
        try:
            prompts = page.locator(_COMMAND_COMPOSER_SELECTOR)
            prompt_count = prompts.count()
            for index in range(prompt_count):
                prompt = prompts if prompt_count == 1 else prompts.nth(index)
                if not prompt.is_visible():
                    continue
                container = prompt.locator("xpath=ancestor::div[.//button][1]")
                stop = container.get_by_role("button", name=_COMMAND_STOP_NAME_RE)
                if stop.count() == 1 and stop.is_visible():
                    return True
        except PlaywrightError:
            return False
        return False

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
            if failed_count and completed_count + failed_count == expected_count:
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
                if (
                    not description
                    or properties.get("disabled")
                    or properties.get("busy")
                ):
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
                bool(
                    _GENERATED_IMAGE_ALT_RE.fullmatch(
                        images.nth(index).get_attribute("alt") or ""
                    )
                )
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
                feedback.nth(index).is_visible() for index in range(feedback.count())
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
