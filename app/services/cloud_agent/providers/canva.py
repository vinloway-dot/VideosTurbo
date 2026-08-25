from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers._browser_session import BrowserSessionProvider
from app.services.cloud_agent.providers._session_detection import (
    classify_security_challenge,
)
from app.services.cloud_agent.session import SessionManager


class CanvaUIVerificationError(RuntimeError):
    """Raised when an essential, observable Canva editor state cannot be proved."""


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

        self.sessions.ensure_service_ready("canva", job.id)
        with self.browser.open("canva", headed=True) as context:
            page = BrowserSessionProvider._page(context)
            page.goto(self.service_url, wait_until="domcontentloaded")
            self._upload_media(page, [*clip_paths, audio_path])
            self._order_clips(page, [clip.name for clip in clip_paths])
            if speed < 1.0:
                for index in range(1, 7):
                    self._set_and_verify_playback(page, index, speed)
            self._mute_source_audio(page)
            self._position_narration_at_zero(page)
            self._bound_final_visual_end(page, target_seconds)
            self._verify_timeline_end(page, target_seconds)
            self._generate_auto_captions(page)
            self._export_mp4_1080p(page)
            self._download_export(page, output_path)
        return output_path

    @staticmethod
    def _validate_media_inputs(clips: list[Path], audio: Path) -> None:
        if len(clips) != 6:
            raise ValueError("Canva assembly requires exactly six video clips")
        if len({clip.name for clip in clips}) != 6:
            raise ValueError("Canva assembly clip names must be unique")
        missing = [path.name for path in [*clips, audio] if not path.is_file()]
        if missing:
            raise ValueError(f"Canva assembly media files are missing: {', '.join(missing)}")

    def _upload_media(self, page: Any, paths: list[Path]) -> None:
        if hasattr(page, "upload_media"):
            page.upload_media(paths)
            return
        page.get_by_role("tab", name="Uploads", exact=True).click()
        audio_name = paths[-1].name
        baseline_inventory = self._upload_inventory(page, audio_name)
        upload_input = page.locator('input[type="file"]')
        upload_input.wait_for(
            state="visible",
            timeout=int(self.export_timeout_seconds * 1000),
        )
        upload_input.set_input_files([str(path) for path in paths])
        self._wait_for_upload_completion(
            page,
            [path.name for path in paths],
            baseline_inventory=baseline_inventory,
        )

    def _upload_inventory(self, page: Any, audio_name: str) -> tuple[int, int]:
        video_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-videos"]')
        audio_tab = page.locator('[role="tab"][aria-controls$="-tabpanel-audio"]')
        ready_timeout = int(self.export_timeout_seconds * 1000)
        try:
            video_tab.wait_for(state="visible", timeout=ready_timeout)
            audio_tab.wait_for(state="visible", timeout=ready_timeout)
        except PlaywrightTimeoutError as exc:
            raise CanvaUIVerificationError("Canva upload media tabs cannot be found") from exc
        if video_tab.count() != 1 or audio_tab.count() != 1:
            raise CanvaUIVerificationError("Canva upload media tabs cannot be found")

        video_tab.click()
        video_panel_id = video_tab.get_attribute("aria-controls")
        audio_tab.click()
        audio_panel_id = audio_tab.get_attribute("aria-controls")
        if not video_panel_id or not audio_panel_id:
            raise CanvaUIVerificationError("Canva upload media panels cannot be found")

        video_panel = page.locator(f'[role="tabpanel"][id="{video_panel_id}"]')
        audio_panel = page.locator(f'[role="tabpanel"][id="{audio_panel_id}"]')
        return (
            video_panel.get_by_text(re.compile(r"^\d+(?:\.\d+)?s$")).count(),
            audio_panel.get_by_text(audio_name, exact=True).count(),
        )

    def _wait_for_upload_completion(
        self,
        page: Any,
        expected_names: list[str],
        *,
        baseline_inventory: tuple[int, int] | None = None,
    ) -> None:
        deadline = time.monotonic() + self.export_timeout_seconds
        while True:
            body = page.content().lower()
            failed = any(marker in body for marker in ("upload failed", "retry upload", "unsupported file"))
            if failed:
                raise CanvaUIVerificationError("Canva reported that an upload failed")
            names_visible = all(
                page.get_by_text(name, exact=True).count() == 1
                and page.get_by_text(name, exact=True).is_visible()
                for name in expected_names
            )
            inventory_complete = False
            if baseline_inventory is not None:
                video_before, audio_before = baseline_inventory
                video_after, audio_after = self._upload_inventory(page, expected_names[-1])
                inventory_complete = video_after >= video_before + 6 and audio_after >= audio_before + 1
            if names_visible or inventory_complete:
                return
            if time.monotonic() >= deadline:
                raise CanvaUIVerificationError("Canva upload completion could not be verified")
            time.sleep(self.poll_seconds)

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
