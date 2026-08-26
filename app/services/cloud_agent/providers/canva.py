from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers._session_detection import (
    classify_security_challenge,
)
from app.services.cloud_agent.session import SessionManager


class CanvaUIVerificationError(RuntimeError):
    """Raised when an essential, observable Canva editor state cannot be proved."""

    def __init__(self, message: str, *, audio_card_count: int | None = None) -> None:
        super().__init__(message)
        self.audio_card_count = audio_card_count


class CanvaPlaybackVerificationError(CanvaUIVerificationError):
    """Raised when Canva cannot prove a playback or timeline change."""


class CanvaDownloadVerificationError(RuntimeError):
    """Raised when the final Canva download does not yield a usable MP4 artifact."""


def classify_canva_session(*, url: str, html: str) -> ServiceSessionStatus:
    """Classify observable Canva editor state without relying on cookies."""
    challenge = classify_security_challenge(html=html)
    if challenge is not None:
        return challenge

    page_url = str(url or "").lower()
    body = str(html or "").lower()

    if (
        "/login" in page_url
        or "/signup" in page_url
        or "log in or sign up" in body
        or "log in to canva" in body
        or "continue with google" in body
    ):
        return ServiceSessionStatus.SESSION_EXPIRED

    is_design_editor = "canva.com/design/" in page_url and "/edit" in page_url
    has_share_control = 'aria-label="share"' in body or ">share<" in body
    has_editor_surface = "canva editor" in body
    if is_design_editor and has_share_control and has_editor_surface:
        return ServiceSessionStatus.READY

    return ServiceSessionStatus.ERROR


class CanvaSessionProvider(BrowserSessionProvider):
    _READY_TIMEOUT_MS = 30_000

    def __init__(self, browser, *, service_url: str) -> None:
        super().__init__(
            browser,
            service="canva",
            service_url=service_url,
            classifier=classify_canva_session,
        )

    def _wait_for_observable_state(self, page: Any) -> None:
        try:
            page.get_by_role(
                "menuitem",
                name=re.compile(r"^\s*share\s*$", re.IGNORECASE),
            ).wait_for(state="visible", timeout=self._READY_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # Classification below still distinguishes login/challenge/error states.
            pass


class CanvaAssemblyClient:
    """Assemble the canonical six clips in Canva and return its exported MP4."""

    _VIDEO_START_EDGE = '[role="slider"][aria-label="Trimming, start edge"]'
    _VIDEO_END_EDGE = '[role="slider"][aria-label="Trimming, end edge"]'
    _WORKSPACE_TAB_HYDRATION_SECONDS = 30.0
    _MEDIA_MENU_HYDRATION_SECONDS = 5.0
    _EDITOR_NAVIGATION_TIMEOUT_SECONDS = 180.0

    def __init__(
        self,
        browser: Any,
        sessions: SessionManager,
        *,
        service_url: str,
        timeline_tolerance_seconds: float = 1.0,
        export_timeout_seconds: float = 180.0,
        poll_seconds: float = 0.5,
    ) -> None:
        service_url = str(service_url or "").strip()
        if not service_url:
            raise ValueError("Canva service URL is required")
        if timeline_tolerance_seconds < 0:
            raise ValueError("timeline_tolerance_seconds must be non-negative")
        if export_timeout_seconds <= 0:
            raise ValueError("export_timeout_seconds must be positive")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be non-negative")

        self.browser = browser
        self.sessions = sessions
        self.service_url = service_url
        self.timeline_tolerance_seconds = float(timeline_tolerance_seconds)
        self.export_timeout_seconds = float(export_timeout_seconds)
        self.poll_seconds = float(poll_seconds)

    def assemble_and_export(
        self,
        job: Any,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path:
        with self.open_job_session(job) as session:
            return session.assemble_and_export(job, clips, audio, output)

    def _assemble_open_page(
        self,
        page: Any,
        job: Any,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path:
        clip_paths = [Path(clip) for clip in clips]
        audio_path = Path(audio)
        output_path = Path(output)
        self._validate_media_inputs(clip_paths, audio_path)
        speed = float(job.canva_playback_speed)
        target_seconds = float(job.target_final_duration_seconds)
        if not 0 < speed <= 1:
            raise ValueError("Canva playback speed must be within (0, 1]")
        if target_seconds <= 0:
            raise ValueError("Canva target duration must be positive")

        clip_names = [clip.name for clip in clip_paths]
        self._clean_uploaded_videos(page, clip_names)
        self._clean_uploaded_audio(page, audio_path.name)
        self._clear_video_timeline(page)
        self._upload_media(page, [*clip_paths, audio_path])
        self._add_uploaded_clips(page, clip_names)
        self._order_clips(page, clip_names)
        if speed < 1.0:
            for index in range(1, 7):
                self._set_and_verify_playback(page, index, speed)
        self._add_uploaded_audio(page, audio_path.name)
        self._mute_source_audio(page)
        self._position_narration_at_zero(page)
        self._bound_final_visual_end(page, target_seconds)
        self._verify_timeline_end(page, target_seconds)
        self._generate_auto_captions(page)
        self._export_mp4_1080p(page)
        self._download_export(page, output_path)
        return output_path

    @contextmanager
    def open_job_session(self, job: Any):
        """Own exactly one Canva browser context for assembly and post-final cleanup."""
        job_id = str(getattr(job, "id", job))
        persisted_url = str(getattr(job, "canva_design_url", "") or "").strip()
        destination_url = persisted_url or self.service_url
        with self.browser.open("canva", headed=True) as context:
            page = BrowserSessionProvider._page(context)
            page.goto(
                destination_url,
                wait_until="domcontentloaded",
                timeout=int(self._EDITOR_NAVIGATION_TIMEOUT_SECONDS * 1000),
            )
            self.sessions.ensure_open_page_ready("canva", page, job_id)
            yield _CanvaJobSession(self, page, self._editor_url(page))

    @staticmethod
    def _editor_url(page: Any) -> str:
        editor_url = str(getattr(page, "url", "") or "").strip()
        parsed = urlparse(editor_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc.endswith("canva.com")
            or re.fullmatch(r"/design/[^/]+(?:/[^/]+)?/edit", parsed.path) is None
        ):
            raise CanvaUIVerificationError("Canva editor URL cannot be verified")
        return editor_url

    @staticmethod
    def _validate_media_inputs(clips: list[Path], audio: Path) -> None:
        if len(clips) != 6:
            raise ValueError("Canva assembly requires exactly six video clips")
        if len({clip.name for clip in clips}) != 6:
            raise ValueError("Canva assembly clip names must be unique")
        missing = [path.name for path in [*clips, audio] if not path.is_file()]
        if missing:
            raise ValueError(f"Canva assembly media files are missing: {', '.join(missing)}")

    def clean_workspace(self, job_id: str) -> None:
        """Return this configured workspace's Uploads → Videos surface to zero cards."""
        with self.open_job_session(job_id) as session:
            session.clean_workspace(job_id)

    def _clean_open_page(self, page: Any) -> None:
        self._clean_uploaded_videos(
            page,
            tuple(f"clip_{index:02d}.mp4" for index in range(1, 7)),
        )
        self._clean_uploaded_audio(page, "voice.mp3")

    def _clean_uploaded_videos(self, page: Any, names: list[str] | tuple[str, ...] = ()) -> None:
        """Delete only this job's named video uploads using card-scoped trash menus."""
        if getattr(page, "no_uploaded_videos_tab", False):
            return
        if hasattr(page, "clean_uploaded_videos"):
            page.clean_uploaded_videos(names)
            return

        panel = self._open_uploaded_videos(page)
        if panel is None:
            return
        for name in names:
            while True:
                cards = panel.get_by_role("button", name=name, exact=True)
                if cards.count() == 0:
                    break
                self._delete_uploaded_video_card(page, cards)
                panel = self._open_uploaded_videos(page)
                if panel is None:
                    panel = self._open_uploaded_videos(page)
                    if panel is None:
                        return

        panel = self._open_uploaded_videos(page)
        if panel is not None and any(
            panel.get_by_role("button", name=name, exact=True).count() != 0
            for name in names
        ):
            raise CanvaUIVerificationError("Canva named uploaded videos could not be cleaned to zero")

    def _clean_uploaded_audio(self, page: Any, audio_name: str) -> None:
        """Delete only stale instances of this job's canonical narration upload."""
        if hasattr(page, "clean_uploaded_audio"):
            page.clean_uploaded_audio(audio_name)
            return

        panel = self._open_uploaded_audio(page)
        if panel is None:
            return
        while True:
            cards = panel.get_by_role("button", name=f"Apply audio: {audio_name}", exact=True)
            if cards.count() == 0:
                break
            self._delete_uploaded_audio_card(page, cards, audio_name)
            panel = self._open_uploaded_audio(page)
            if panel is None:
                return

        panel = self._open_uploaded_audio(page)
        if (
            panel is not None
            and panel.get_by_role("button", name=f"Apply audio: {audio_name}", exact=True).count() != 0
        ):
            raise CanvaUIVerificationError("Canva named uploaded audio could not be cleaned to zero")

    def _open_uploaded_videos(self, page: Any) -> Any | None:
        page.get_by_role("tab", name="Uploads", exact=True).click()
        video_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-videos"]:visible')
        deadline = time.monotonic() + self._WORKSPACE_TAB_HYDRATION_SECONDS
        while video_tab.count() == 0:
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_seconds)
        if video_tab.count() != 1:
            raise CanvaUIVerificationError("Canva uploaded Videos tab is ambiguous")
        video_tab.click()
        panel_id = video_tab.get_attribute("aria-controls")
        if not panel_id:
            raise CanvaUIVerificationError("Canva uploaded Videos panel cannot be found")
        return page.locator(f'[role="tabpanel"][id="{panel_id}"]')

    def _open_uploaded_audio(self, page: Any) -> Any | None:
        page.get_by_role("tab", name="Uploads", exact=True).click()
        audio_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-audio"]:visible')
        deadline = time.monotonic() + self._WORKSPACE_TAB_HYDRATION_SECONDS
        while audio_tab.count() == 0:
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_seconds)
        if audio_tab.count() != 1:
            raise CanvaUIVerificationError("Canva uploaded Audio tab is ambiguous")
        audio_tab.click()
        panel_id = audio_tab.get_attribute("aria-controls")
        if not panel_id:
            raise CanvaUIVerificationError("Canva uploaded Audio panel cannot be found")
        return page.locator(f'[role="tabpanel"][id="{panel_id}"]')

    def _delete_uploaded_video_card(self, page: Any, cards: Any) -> None:
        card, card_box = self._first_visible_box(cards)
        if card is None or card_box is None:
            raise CanvaUIVerificationError("Canva uploaded video card cannot be found")
        name = card.get_attribute("aria-label")
        if not name:
            raise CanvaUIVerificationError("Canva uploaded video card has no semantic name")
        page.mouse.move(card_box["x"] + card_box["width"] / 2, card_box["y"] + card_box["height"] / 2)
        time.sleep(self.poll_seconds)
        overlay = self._card_details_overlay(page, name, card_box)
        if overlay is None:
            raise CanvaUIVerificationError("Canva card-scoped details overlay cannot be verified")
        _, overlay_box = overlay
        page.mouse.click(
            overlay_box["x"] + overlay_box["width"] / 2,
            overlay_box["y"] + overlay_box["height"] / 2,
        )
        trash, trash_box = self._verified_trash_menu_item(page)
        before = cards.count()
        page.mouse.click(
            trash_box["x"] + trash_box["width"] / 2,
            trash_box["y"] + trash_box["height"] / 2,
        )
        deadline = time.monotonic() + self.export_timeout_seconds
        while cards.count() >= before:
            if time.monotonic() >= deadline:
                raise CanvaUIVerificationError("Canva video deletion postcondition cannot be verified")
            time.sleep(self.poll_seconds)

    def _delete_uploaded_audio_card(self, page: Any, cards: Any, audio_name: str) -> None:
        card, card_box = self._first_visible_box(cards)
        if card is None or card_box is None:
            raise CanvaUIVerificationError("Canva uploaded audio card cannot be found")
        page.mouse.move(card_box["x"] + card_box["width"] / 2, card_box["y"] + card_box["height"] / 2)
        time.sleep(self.poll_seconds)
        overlay = self._card_details_overlay(page, audio_name, card_box)
        if overlay is None:
            raise CanvaUIVerificationError("Canva audio-card details overlay cannot be verified")
        _, overlay_box = overlay
        page.mouse.click(
            overlay_box["x"] + overlay_box["width"] / 2,
            overlay_box["y"] + overlay_box["height"] / 2,
        )
        delete = page.get_by_role("button", name="Delete", exact=True)
        delete_box = self._first_visible_box(delete)[1]
        if delete.count() != 1 or delete_box is None or not self._is_hit_testable(page, delete_box):
            raise CanvaUIVerificationError("Canva audio Delete action cannot be verified")
        before = cards.count()
        page.mouse.click(
            delete_box["x"] + delete_box["width"] / 2,
            delete_box["y"] + delete_box["height"] / 2,
        )
        deadline = time.monotonic() + self.export_timeout_seconds
        while cards.count() >= before:
            if time.monotonic() >= deadline:
                raise CanvaUIVerificationError("Canva audio deletion postcondition cannot be verified")
            time.sleep(self.poll_seconds)

    @staticmethod
    def _first_visible_box(locator: Any) -> tuple[Any | None, dict[str, float] | None]:
        for index in range(locator.count()):
            candidate = locator.nth(index)
            box = candidate.bounding_box()
            if box is not None and box["width"] > 0 and box["height"] > 0:
                return candidate, box
        return None, None

    def _card_details_overlay(self, page: Any, name: str, card_box: dict[str, float]) -> tuple[Any, dict[str, float]] | None:
        overlays = page.get_by_role("button", name=f'Show details for “{name}”', exact=True)
        for index in range(overlays.count()):
            candidate = overlays.nth(index)
            box = candidate.bounding_box()
            if box is None or not self._boxes_overlap(card_box, box):
                continue
            if self._is_hit_testable(page, box):
                return candidate, box
        return None

    @staticmethod
    def _boxes_overlap(first: dict[str, float], second: dict[str, float]) -> bool:
        return (
            min(first["x"] + first["width"], second["x"] + second["width"])
            > max(first["x"], second["x"])
            and min(first["y"] + first["height"], second["y"] + second["height"])
            > max(first["y"], second["y"])
        )

    @staticmethod
    def _is_hit_testable(page: Any, box: dict[str, float]) -> bool:
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        return bool(
            page.evaluate(
                "(point) => document.elementFromPoint(point.x, point.y) !== null",
                {"x": x, "y": y},
            )
        )

    def _verified_trash_menu_item(self, page: Any) -> tuple[Any, dict[str, float]]:
        deadline = time.monotonic() + min(
            self._MEDIA_MENU_HYDRATION_SECONDS,
            self.export_timeout_seconds,
        )
        while True:
            menu_items = {
                action: self._first_visible_box(page.get_by_text(action, exact=True))
                for action in ("Details", "Download", "Move", "Move to Trash")
            }
            trash, trash_box = menu_items["Move to Trash"]
            if (
                all(item is not None for item, _box in menu_items.values())
                and trash is not None
                and trash_box is not None
                and self._is_hit_testable(page, trash_box)
            ):
                return trash, trash_box
            if time.monotonic() >= deadline:
                raise CanvaUIVerificationError("Canva media-card menu cannot be verified")
            time.sleep(self.poll_seconds)

    def _clear_video_timeline(self, page: Any) -> None:
        if hasattr(page, "clear_video_timeline"):
            page.clear_video_timeline()
            return
        starts = page.locator(self._VIDEO_START_EDGE)
        while starts.count() > 0:
            before = starts.count()
            starts.nth(0).locator("xpath=..").click()
            page.keyboard.press("Delete")
            deadline = time.monotonic() + self.export_timeout_seconds
            while starts.count() >= before:
                if time.monotonic() >= deadline:
                    raise CanvaUIVerificationError("Canva video timeline cannot be cleared")
                time.sleep(self.poll_seconds)

    def _add_uploaded_clips(self, page: Any, expected_names: list[str]) -> None:
        if len(expected_names) != 6:
            raise ValueError("Canva timeline insertion requires exactly six clip names")
        panel = None if hasattr(page, "add_uploaded_clip") else self._open_uploaded_videos(page)
        if panel is None and not hasattr(page, "add_uploaded_clip"):
            raise CanvaUIVerificationError("Canva uploaded Videos panel cannot be found")
        for name in expected_names:
            before = self._timeline_video_count(page)
            if hasattr(page, "add_uploaded_clip"):
                page.add_uploaded_clip(name)
            else:
                card = panel.get_by_role("button", name=name, exact=True)
                if card.count() != 1:
                    raise CanvaUIVerificationError("Canva uploaded clip card is ambiguous")
                card.click()
            deadline = time.monotonic() + self.export_timeout_seconds
            while self._timeline_video_count(page) == before:
                if time.monotonic() >= deadline:
                    raise CanvaUIVerificationError("Canva timeline video count did not increase by one")
                time.sleep(self.poll_seconds)
            after = self._timeline_video_count(page)
            if after != before + 1:
                raise CanvaUIVerificationError("Canva timeline video count did not increase by one")

    def _timeline_video_count(self, page: Any) -> int:
        if hasattr(page, "timeline_video_count_value"):
            return int(page.timeline_video_count_value())
        return page.locator(self._VIDEO_START_EDGE).count()

    def _upload_media(self, page: Any, paths: list[Path]) -> None:
        if hasattr(page, "upload_media"):
            page.upload_media(paths)
            return
        page.get_by_role("tab", name="Uploads", exact=True).click()
        video_paths = [
            path for path in paths if path.suffix.lower() in {".mp4", ".mov", ".webm"}
        ]
        audio_paths = [path for path in paths if path not in video_paths]
        if len(audio_paths) != 1:
            raise ValueError("Canva upload requires exactly one canonical audio file")
        audio_path = audio_paths[0]
        baseline_inventory = self._upload_inventory(page, audio_path.name)
        upload_input = page.locator('input[type="file"]')
        upload_input.wait_for(
            state="visible",
            timeout=int(self.export_timeout_seconds * 1000),
        )
        upload_input.set_input_files([str(path) for path in video_paths])
        self._wait_for_upload_completion(
            page,
            [path.name for path in video_paths],
            baseline_inventory=baseline_inventory,
            audio_name=None,
        )
        audio_baseline = self._upload_inventory(page, audio_path.name)
        upload_input.set_input_files([str(audio_path)])
        self._wait_for_upload_completion(
            page,
            [audio_path.name],
            baseline_inventory=audio_baseline,
            audio_name=audio_path.name,
        )

    def _upload_inventory(self, page: Any, audio_name: str) -> tuple[int, int] | None:
        page.get_by_role("tab", name="Elements", exact=True).click()
        page.get_by_role("tab", name="Uploads", exact=True).click()
        video_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-videos"]:visible')
        audio_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-audio"]:visible')
        photos_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-photos"]:visible')
        if video_tab.count() == 0 and audio_tab.count() == 0 and photos_tab.count() == 1:
            return None
        ready_timeout = int(self.export_timeout_seconds * 1000)
        try:
            audio_tab.wait_for(state="visible", timeout=ready_timeout)
            audio_tab.click()
        except PlaywrightTimeoutError as exc:
            raise CanvaUIVerificationError("Canva upload media tabs cannot be found") from exc
        if video_tab.count() > 1 or audio_tab.count() != 1:
            raise CanvaUIVerificationError("Canva upload media tabs cannot be found")

        audio_panel_id = audio_tab.get_attribute("aria-controls")
        if not audio_panel_id:
            raise CanvaUIVerificationError("Canva upload media panels cannot be found")
        audio_panel = page.locator(f'[role="tabpanel"][id="{audio_panel_id}"]')
        audio_count = audio_panel.get_by_role(
            "button", name=f"Apply audio: {audio_name}", exact=True
        ).count()
        if video_tab.count() == 0:
            return (
                0,
                audio_count,
            )
        video_tab.click()
        video_panel_id = video_tab.get_attribute("aria-controls")
        if not video_panel_id:
            raise CanvaUIVerificationError("Canva upload media panels cannot be found")
        video_panel = page.locator(f'[role="tabpanel"][id="{video_panel_id}"]')
        return (
            video_panel.get_by_text(re.compile(r"^\d+(?:\.\d+)?s$")).count(),
            audio_count,
        )

    def _wait_for_upload_completion(
        self,
        page: Any,
        expected_names: list[str],
        *,
        baseline_inventory: tuple[int, int] | None = None,
        audio_name: str | None = None,
    ) -> None:
        deadline = time.monotonic() + self.export_timeout_seconds
        refreshed = False
        while True:
            body = page.content().lower()
            failed = any(marker in body for marker in ("upload failed", "retry upload", "unsupported file"))
            if failed:
                raise CanvaUIVerificationError("Canva reported that an upload failed")
            named_uploads = [page.get_by_text(name, exact=True) for name in expected_names]
            names_observable = any(upload.count() > 0 for upload in named_uploads)
            names_visible = all(
                upload.count() == 1 and upload.is_visible() for upload in named_uploads
            )
            inventory_complete = False
            if baseline_inventory is not None:
                video_before, audio_before = baseline_inventory
                video_after, audio_after = self._upload_inventory(
                    page, audio_name or expected_names[-1]
                )
                expected_video_count = sum(
                    Path(name).suffix.lower() in {".mp4", ".mov", ".webm"}
                    for name in expected_names
                )
                expected_audio_count = len(expected_names) - expected_video_count
                inventory_complete = (
                    video_after >= video_before + expected_video_count
                    and audio_after >= audio_before + expected_audio_count
                )
            cards_complete = self._uploaded_media_cards_complete(
                page, expected_names, audio_name
            )
            if (baseline_inventory is None and (names_visible or cards_complete)) or (
                inventory_complete
                and (
                    cards_complete
                    or (not names_observable or names_visible)
                )
            ):
                return
            if time.monotonic() >= deadline:
                if not refreshed:
                    page.reload(wait_until="domcontentloaded")
                    refreshed = True
                    deadline = time.monotonic() + self.export_timeout_seconds
                    continue
                raise CanvaUIVerificationError("Canva upload completion could not be verified")
            time.sleep(self.poll_seconds)

    def _uploaded_media_cards_complete(
        self, page: Any, expected_names: list[str], audio_name: str | None
    ) -> bool:
        video_names = [
            name
            for name in expected_names
            if Path(name).suffix.lower() in {".mp4", ".mov", ".webm"}
        ]
        if not video_names and not audio_name:
            return False
        try:
            if video_names:
                video_panel = self._open_uploaded_videos(page)
                if video_panel is None or any(
                    video_panel.get_by_role("button", name=name, exact=True).count() != 1
                    for name in video_names
                ):
                    return False
            if not audio_name:
                return True
            audio_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-audio"]:visible')
            if audio_tab.count() != 1:
                return False
            audio_tab.click()
            audio_panel_id = audio_tab.get_attribute("aria-controls")
            if not audio_panel_id:
                return False
            audio_panel = page.locator(f'[role="tabpanel"][id="{audio_panel_id}"]')
            return (
                audio_panel.get_by_role(
                    "button", name=f"Apply audio: {audio_name}", exact=True
                ).count()
                >= 1
            )
        except Exception:
            return False

    def _order_clips(self, page: Any, expected_names: list[str]) -> None:
        if hasattr(page, "order_clips"):
            page.order_clips(expected_names)
            return
        starts = page.locator(self._VIDEO_START_EDGE)
        if starts.count() < 6:
            raise CanvaUIVerificationError("Canva six-clip timeline cannot be found for ordering")
        start_values = [self._slider_seconds(starts.nth(index)) for index in range(6)]
        if start_values != sorted(start_values) or len(set(start_values)) != 6:
            raise CanvaUIVerificationError("Canva clip ordering 1 through 6 cannot be verified")

    def _set_and_verify_playback(self, page: Any, index: int, speed: float) -> None:
        self._select_video_clip(page, index)
        if hasattr(page, "open_video_speed"):
            page.open_video_speed()
            page.set_custom_speed(speed)
            verified = page.verify_playback_speed(speed)
        else:
            try:
                page.get_by_role("button", name="Speed", exact=True).click()
                panel = page.locator('[aria-label="Video Speed"]')
                if panel.count() != 1:
                    raise CanvaPlaybackVerificationError("Canva Video Speed panel cannot be found")
                control = panel.locator('input[role="spinbutton"]')
                control.fill(str(speed))
                control.press("Enter")
                verified = abs(float(control.input_value()) - speed) <= 0.001
            except CanvaPlaybackVerificationError:
                raise
            except Exception as exc:
                raise CanvaPlaybackVerificationError(
                    "Canva playback control cannot be found or verified"
                ) from exc
        if not verified:
            raise CanvaPlaybackVerificationError(
                "Canva playback or resulting timeline state cannot be verified"
            )

    def _select_video_clip(self, page: Any, index: int) -> None:
        if hasattr(page, "select_video_clip"):
            page.select_video_clip(index)
            return
        clips = page.locator(self._VIDEO_START_EDGE)
        if clips.count() < index:
            raise CanvaPlaybackVerificationError("Canva video timeline clip cannot be found")
        clips.nth(index - 1).locator("xpath=..").click()

    def _mute_source_audio(self, page: Any) -> None:
        if hasattr(page, "mute_source_audio"):
            page.mute_source_audio()
            return
        for index in range(1, 7):
            self._select_video_clip(page, index)
            volume = page.get_by_role("button", name="Volume", exact=True)
            volume.click()
            control = page.locator('input[role="spinbutton"][aria-label="Volume"]')
            control.fill("0")
            control.press("Enter")
            if control.input_value() not in {"0", "0.0", "0.00"}:
                raise CanvaUIVerificationError("Canva source-video audio mute cannot be verified")

    def _add_uploaded_audio(self, page: Any, audio_name: str) -> None:
        if hasattr(page, "add_uploaded_audio"):
            page.add_uploaded_audio(audio_name)
            return
        before = page.locator(self._VIDEO_START_EDGE).count()
        page.get_by_role("tab", name="Elements", exact=True).click()
        panel = self._open_uploaded_audio(page)
        if panel is None:
            raise CanvaUIVerificationError("Canva uploaded Audio panel cannot be found")
        audio = panel.get_by_role("button", name=f"Apply audio: {audio_name}", exact=True)
        audio_card_count = audio.count()
        if audio_card_count != 1:
            raise CanvaUIVerificationError(
                f"Canva narration audio cards: {audio_card_count}",
                audio_card_count=audio_card_count,
            )
        audio.click()
        deadline = time.monotonic() + self.export_timeout_seconds
        while page.locator(self._VIDEO_START_EDGE).count() <= before:
            if time.monotonic() >= deadline:
                raise CanvaUIVerificationError("Canva narration was not added to the timeline")
            time.sleep(self.poll_seconds)

    def _position_narration_at_zero(self, page: Any) -> None:
        if hasattr(page, "position_narration_at_zero"):
            page.position_narration_at_zero()
            return
        starts = page.locator(self._VIDEO_START_EDGE)
        if starts.count() < 7 or self._slider_seconds(starts.nth(6)) != 0:
            raise CanvaUIVerificationError("Canva narration cannot be verified at timeline time 0")

    def _bound_final_visual_end(self, page: Any, target_seconds: float) -> None:
        if hasattr(page, "bound_final_visual_end"):
            page.bound_final_visual_end(target_seconds)
            return
        ends = page.locator(self._VIDEO_END_EDGE)
        if ends.count() < 6:
            raise CanvaUIVerificationError("Canva final visual end cannot be found")
        edge = ends.nth(5)
        current = self._slider_seconds(edge)
        if abs(current - target_seconds) > self.timeline_tolerance_seconds:
            raise CanvaUIVerificationError("Canva final visual end cannot be bounded safely")

    def _verify_timeline_end(self, page: Any, target_seconds: float) -> None:
        if hasattr(page, "verify_timeline_end"):
            verified = page.verify_timeline_end(target_seconds, self.timeline_tolerance_seconds)
        else:
            ends = page.locator(self._VIDEO_END_EDGE)
            verified = ends.count() >= 6 and (
                abs(self._slider_seconds(ends.nth(5)) - target_seconds)
                <= self.timeline_tolerance_seconds
            )
        if not verified:
            raise CanvaPlaybackVerificationError(
                "Canva resulting playback or timeline state cannot be verified"
            )

    def _generate_auto_captions(self, page: Any) -> None:
        if hasattr(page, "generate_auto_captions"):
            page.generate_auto_captions()
            return
        self._select_video_clip(page, 1)
        page.get_by_role("button", name="Captions", exact=True).click()
        page.get_by_role("button", name="Generate captions", exact=True).click()

    def _export_mp4_1080p(self, page: Any) -> None:
        if hasattr(page, "export_mp4_1080p"):
            page.export_mp4_1080p()
            return
        page.get_by_role("menuitem", name="Share", exact=True).click()
        page.get_by_label("Download", exact=True).click()
        file_type = page.get_by_role("combobox", name="File type", exact=True)
        if "mp4" not in file_type.inner_text().lower():
            raise CanvaUIVerificationError("Canva MP4 Video export option cannot be verified")
        resolution = page.get_by_role(
            "radio", name="Width 1080 by height 1920 pixels", exact=True
        )
        if resolution.get_attribute("aria-checked") != "true":
            resolution.click()
        if resolution.get_attribute("aria-checked") != "true":
            raise CanvaUIVerificationError("Canva 1080 by 1920 export option cannot be verified")

    def _download_export(self, page: Any, output: Path) -> None:
        if hasattr(page, "download_export"):
            page.download_export(output)
        else:
            final_download = page.get_by_role("button", name="Download", exact=True)
            if final_download.count() != 1:
                raise CanvaDownloadVerificationError("Canva final Download control is ambiguous")
            try:
                with page.expect_download(timeout=int(self.export_timeout_seconds * 1000)) as info:
                    final_download.click()
                    self._wait_for_export_state(page)
                download = info.value
                suggested_name = str(download.suggested_filename or "")
                if not suggested_name.lower().endswith(".mp4"):
                    raise CanvaDownloadVerificationError("Canva final download is not an MP4")
                output.parent.mkdir(parents=True, exist_ok=True)
                download.save_as(str(output))
            except CanvaDownloadVerificationError:
                raise
            except Exception as exc:
                raise CanvaDownloadVerificationError(
                    "Canva final Download did not complete"
                ) from exc
        if not output.is_file() or output.stat().st_size <= 0:
            raise CanvaDownloadVerificationError(
                "Canva final download did not produce a completed MP4 artifact"
            )

    def _wait_for_export_state(self, page: Any) -> None:
        deadline = time.monotonic() + self.export_timeout_seconds
        while True:
            body = page.content().lower()
            if any(marker in body for marker in ("download failed", "export failed", "error exporting")):
                raise CanvaDownloadVerificationError("Canva reported an export failure")
            if any(marker in body for marker in ("preparing", "processing", "exporting", "downloading")):
                return
            if time.monotonic() >= deadline:
                raise CanvaDownloadVerificationError(
                    "Canva export state could not be observed before download completion"
                )
            time.sleep(self.poll_seconds)

    @staticmethod
    def _slider_seconds(slider: Any) -> float:
        value = slider.get_attribute("aria-valuenow")
        if value is None:
            raise CanvaUIVerificationError("Canva timeline value cannot be read")
        return float(value) / 1_000_000


class _CanvaJobSession:
    """Page-bound Canva operations; its owner closes the browser context once."""

    def __init__(self, client: CanvaAssemblyClient, page: Any, editor_url: str) -> None:
        self.client = client
        self.page = page
        self.editor_url = editor_url

    def assemble_and_export(
        self,
        job: Any,
        clips: list[Path],
        audio: Path,
        output: Path,
    ) -> Path:
        return self.client._assemble_open_page(self.page, job, clips, audio, output)

    def clean_workspace(self, job_id: str) -> None:
        del job_id
        self.client._clean_open_page(self.page)
