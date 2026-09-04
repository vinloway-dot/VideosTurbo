"""Dedicated Streamlit controller for the completed-video library."""

from typing import MutableMapping
from urllib.parse import urljoin, urlsplit

import requests
import streamlit as st

from webui import cloud_agent_ui


API_PREFIX = "http://127.0.0.1:8080/api/v1/cloud-agent/"
API_TIMEOUT_SECONDS = 15
VIDEO_LIBRARY_PAGE_SIZE = 10


def _api(method: str, path: str, **kwargs):
    timeout = kwargs.pop("timeout", API_TIMEOUT_SECONDS)
    response = requests.request(method, API_PREFIX + path, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json().get("data")


def _api_error_message(error: requests.RequestException) -> str:
    response = getattr(error, "response", None)
    if response is not None:
        try:
            message = response.json().get("message", "")
            if message:
                return str(message)
        except (ValueError, requests.RequestException):
            pass
    return "Cloud Agent request could not be completed."


def load_video_library(page: int) -> dict:
    """Load one public, fixed-size page of completed Cloud Agent videos."""
    return _api("GET", f"videos?page={max(1, page)}&page_size={VIDEO_LIBRARY_PAGE_SIZE}")


def delete_video(job_id: str) -> None:
    """Permanently remove one library-visible Cloud Agent video."""
    _api("DELETE", f"videos/{job_id}")


def load_video_media(final_url: str) -> bytes:
    """Fetch final media through the internal API for Streamlit marshalling."""
    parsed = urlsplit(str(final_url or ""))
    media_prefix = "/api/v1/cloud-agent/jobs/"
    path_parts = parsed.path.removeprefix(media_prefix).split("/")
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(media_prefix)
        or len(path_parts) != 2
        or not path_parts[0]
        or path_parts[1] != "final"
    ):
        raise ValueError("invalid cloud agent completed-media URL")
    response = requests.request(
        "GET", urljoin(API_PREFIX, parsed.path), timeout=API_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.content


def _video_delete_error_message(error: requests.RequestException) -> str:
    if getattr(error, "response", None) is not None:
        return (
            f"ลบวิดีโอไม่สำเร็จ: {_api_error_message(error)} "
            "กรุณารีเฟรชรายการแล้วลองอีกครั้ง"
        )
    return (
        "ไม่สามารถเชื่อมต่อเพื่อทำการลบวิดีโอได้ "
        "กรุณาตรวจสอบการเชื่อมต่อแล้วลองอีกครั้ง"
    )


def confirm_video_deletion(*, ui_state: MutableMapping, job_id: str) -> bool:
    """Delete the pending card and retain a valid library page on success."""
    if ui_state.get("cloud_agent_video_delete_pending_id") != job_id:
        return False
    try:
        delete_video(job_id)
    except requests.RequestException as exc:
        st.error(_video_delete_error_message(exc))
        return False

    ui_state["cloud_agent_video_delete_pending_id"] = ""
    page = max(1, int(ui_state.get("cloud_agent_video_library_page") or 1))
    try:
        refreshed = load_video_library(page)
    except requests.RequestException:
        st.error(
            "ลบวิดีโอสำเร็จแล้ว แต่ยังรีเฟรชรายการไม่ได้ กรุณารีเฟรชหน้าอีกครั้ง"
        )
        return True
    total_pages = max(1, int(refreshed.get("total_pages") or 1))
    ui_state["cloud_agent_video_library_page"] = min(page, total_pages)
    return True


def render_video_library(
    *, ui_state: MutableMapping, show_heading: bool = True
) -> None:
    """Render completed videos and keep pagination/deletion state page-local."""
    ui_state.setdefault("cloud_agent_video_library_page", 1)
    ui_state.setdefault("cloud_agent_video_delete_pending_id", "")
    page = max(1, int(ui_state["cloud_agent_video_library_page"] or 1))
    ui_state["cloud_agent_video_library_page"] = page
    try:
        payload = load_video_library(page)
    except requests.RequestException as exc:
        st.error(_api_error_message(exc))
        return

    def request_delete(job_id: str) -> None:
        ui_state["cloud_agent_video_delete_pending_id"] = job_id

    def confirm_delete(job_id: str) -> None:
        if confirm_video_deletion(ui_state=ui_state, job_id=job_id):
            rerun = getattr(st, "rerun", None)
            if callable(rerun):
                rerun()

    def cancel_delete(job_id: str) -> None:
        if ui_state.get("cloud_agent_video_delete_pending_id") == job_id:
            ui_state["cloud_agent_video_delete_pending_id"] = ""
            rerun = getattr(st, "rerun", None)
            if callable(rerun):
                rerun()

    def select_page(selected_page: int) -> None:
        ui_state["cloud_agent_video_library_page"] = selected_page
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun()

    try:
        cloud_agent_ui.render_video_library(
            cloud_agent_ui.video_library_view(payload),
            load_video=load_video_media,
            show_heading=show_heading,
            pending_delete_id=str(ui_state["cloud_agent_video_delete_pending_id"]),
            on_delete_request=request_delete,
            on_delete_confirm=confirm_delete,
            on_delete_cancel=cancel_delete,
            on_page=select_page,
        )
    except Exception:
        st.error("ไม่สามารถแสดงวิดีโอที่สร้างได้ชั่วคราว กรุณารีเฟรชหน้าอีกครั้ง")
