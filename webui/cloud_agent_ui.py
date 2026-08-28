from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Callable, Literal, Mapping

import streamlit as st


StageState = Literal["complete", "active", "pending", "error"]


@dataclass(frozen=True)
class StageView:
    key: str
    label: str
    state: StageState


@dataclass(frozen=True)
class ProductionProgressView:
    percent: int
    state: Literal[
        "not_started",
        "queued",
        "working",
        "paused",
        "attention",
        "error",
        "complete",
        "cancelled",
    ]
    label: str
    detail: str


@dataclass(frozen=True)
class ResearchSummary:
    status: str
    source_count: int
    source_links: tuple[tuple[str, str], ...]
    provider_rounds: int | None
    tool_calls: int | None


@dataclass(frozen=True)
class VideoCardView:
    job_id: str
    subject: str
    completed_at: str
    final_url: str


@dataclass(frozen=True)
class VideoLibraryView:
    items: tuple[VideoCardView, ...]
    page: int
    total_pages: int
    total_items: int


def video_library_view(payload: Mapping[str, object]) -> VideoLibraryView:
    items = tuple(
        VideoCardView(
            job_id=str(item.get("job_id") or ""),
            subject=str(item.get("subject") or ""),
            completed_at=str(item.get("completed_at") or ""),
            final_url=str(item.get("final_url") or ""),
        )
        for value in payload.get("items") or ()
        if isinstance(value, Mapping)
        for item in (value,)
    )
    return VideoLibraryView(
        items=items,
        page=max(1, int(payload.get("page") or 1)),
        total_pages=max(1, int(payload.get("total_pages") or 1)),
        total_items=max(0, int(payload.get("total_items") or 0)),
    )


def render_video_library(
    view: VideoLibraryView,
    *,
    load_video: Callable[[str], bytes],
    pending_delete_id: str,
    on_delete_request: Callable[[str], None],
    on_delete_confirm: Callable[[str], None],
    on_delete_cancel: Callable[[str], None],
    on_page: Callable[[int], None],
) -> None:
    with st.container(key="cloud_agent_video_library"):
        st.html('<h2 class="vt-video-library__title">วีดีโอที่สร้าง</h2>')
        if not view.items:
            st.html(
                '<p class="vt-video-library__empty">ยังไม่มีวิดีโอที่สร้างเสร็จ</p>'
            )
        else:
            with st.container(key="cloud_agent_video_library_grid"):
                cards = view.items[:10]
                for row_start in range(0, len(cards), 5):
                    columns = st.columns(5)
                    for column, card in zip(columns, cards[row_start : row_start + 5]):
                        with column:
                            with st.container(
                                key=f"cloud_agent_video_card_{card.job_id}", border=True
                            ):
                                try:
                                    media = load_video(card.final_url)
                                    if not media:
                                        raise ValueError("empty video media")
                                    st.video(media, format="video/mp4")
                                except Exception:
                                    st.warning("วิดีโอนี้ยังเปิดไม่ได้ กรุณาลองใหม่ภายหลัง")
                                st.html(
                                    '<p class="vt-video-library-card__subject">'
                                    f"{escape(card.subject)}</p>"
                                    '<p class="vt-video-library-card__time">'
                                    f"{escape(card.completed_at)}</p>"
                                )
                                delete_requested = st.button(
                                    "ลบ",
                                    key=f"cloud_agent_delete_{card.job_id}",
                                    type="secondary",
                                    disabled=False,
                                )
                                is_pending = pending_delete_id == card.job_id
                                if delete_requested:
                                    on_delete_request(card.job_id)
                                    is_pending = True
                                if is_pending:
                                    st.warning(
                                        "การลบนี้จะลบวิดีโอและไฟล์งานของรายการนี้ออกจากที่เก็บข้อมูล "
                                        "VideosTurbo ภายในเครื่องอย่างถาวรเท่านั้น และจะไม่ลบข้อมูลจาก "
                                        "Google Flow หรือ Canva"
                                    )
                                    if st.button(
                                        "ยืนยันการลบ",
                                        key=f"cloud_agent_confirm_delete_{card.job_id}",
                                        type="primary",
                                    ):
                                        on_delete_confirm(card.job_id)
                                    if st.button(
                                        "ยกเลิก",
                                        key=f"cloud_agent_cancel_delete_{card.job_id}",
                                        type="secondary",
                                    ):
                                        on_delete_cancel(card.job_id)
        if view.total_pages > 1:
            with st.container(key="cloud_agent_video_library_pagination"):
                for page in range(1, view.total_pages + 1):
                    if st.button(
                        str(page),
                        key=f"cloud_agent_video_page_{page}",
                        disabled=page == view.page,
                    ):
                        on_page(page)


def research_summary(*, research_draft_id, sources, accounting) -> ResearchSummary:
    safe_links = tuple(
        (
            str(source.get("title") or source.get("url") or "Untitled source"),
            str(source.get("url") or ""),
        )
        for source in list(sources or [])
        if str(source.get("url") or "").startswith(("http://", "https://"))
    )
    safe_accounting = dict(accounting or {})
    return ResearchSummary(
        status="Research complete" if str(research_draft_id or "").strip() else "",
        source_count=len(safe_links),
        source_links=safe_links,
        provider_rounds=safe_accounting.get("provider_rounds"),
        tool_calls=safe_accounting.get("tool_calls"),
    )


def render_research_summary(summary: ResearchSummary) -> None:
    if not summary.status:
        return
    st.caption(f"{summary.status} · {summary.source_count} sources")
    for title, url in summary.source_links:
        st.link_button(title, url, width="content")
    with st.expander("Research details", expanded=False):
        st.caption(
            "Provider rounds: "
            f"{summary.provider_rounds if summary.provider_rounds is not None else 'unavailable'}"
        )
        st.caption(
            "Tool calls: "
            f"{summary.tool_calls if summary.tool_calls is not None else 'unavailable'}"
        )


_CHECKPOINT_RANK = {
    "NONE": 0,
    "PREFLIGHT_PASSED": 1,
    "TTS_READY": 2,
    "FLOW_READY": 3,
    "FINAL_VALIDATED": 4,
    "COMPLETED": 5,
}

_PRODUCTION_STAGE_LABELS = {
    "script": "Script",
    "voice": "Voice",
    "flow": "Flow",
    "canva": "Canva",
    "export": "Export",
}

_PRODUCTION_STATE_PRESENTATION = {
    "complete": ("Complete", "✓"),
    "active": ("In progress", "▶"),
    "pending": ("Pending", "○"),
    "error": ("Error", "!"),
}

_PRODUCTION_PROGRESS_BY_STEP = {
    "preflight": "กำลังตรวจความพร้อมของระบบ",
    "preflight_passed": "กำลังเตรียมเริ่มการผลิต",
    "prepared_voice_validating": "กำลังตรวจสอบเสียงที่เตรียมไว้",
    "tts_generating": "กำลังสร้างเสียงบรรยาย",
    "tts_ready": "สร้างเสียงบรรยายเสร็จแล้ว",
    "flow_reconciling": "กำลังตรวจสอบงานใน Google Flow",
    "flow_generating": "กำลังสร้างคลิปวิดีโอใน Google Flow",
    "flow_ready": "สร้างคลิปวิดีโอเสร็จแล้ว",
    "canva_assembling": "กำลังประกอบวิดีโอใน Canva",
    "validating": "กำลังตรวจสอบวิดีโอสุดท้าย",
    "final_validated": "ตรวจสอบวิดีโอสุดท้ายเสร็จแล้ว",
}


_CSS_PATH = Path(__file__).with_name("cloud_agent.css")


def has_saved_draft(session_state) -> bool:
    return bool(str(session_state.get("cloud_agent_draft_script") or "").strip())


def apply_cloud_agent_theme() -> None:
    st.html(_CSS_PATH)


def render_sidebar() -> None:
    with st.sidebar:
        st.html('<div class="vt-wordmark"><span>◆</span> VideosTurbo</div>')
        st.page_link("Main.py", label="Cloud Agent", icon=":material/auto_awesome:")
        st.page_link(
            "pages/2_Music_Batch.py",
            label="Music Batch",
            icon=":material/music_note:",
        )
        st.page_link(
            "pages/3_Settings.py",
            label="Settings",
            icon=":material/settings:",
        )
        st.html(
            '<div class="vt-nav-disabled" aria-disabled="true">'
            '<span class="vt-nav-icon" aria-hidden="true">▦</span> Projects'
            '</div>'
            '<div class="vt-system-status">'
            '<span aria-hidden="true"></span> All systems operational'
            '</div>'
        )


def render_page_header(*, saved: bool) -> None:
    saved_indicator = '<div class="vt-saved"><span>✓</span> Saved</div>' if saved else ""
    st.html(
        '<div class="vt-header-meta">'
        '<div class="vt-breadcrumb">Workspace&nbsp;&nbsp;/&nbsp;&nbsp;Cloud Agent</div>'
        f"{saved_indicator}"
        "</div>"
    )
    st.title("Create a video")
    st.caption("Research, write, narrate, and produce — all in one flow.")


def render_workflow_rail(active_step: int) -> None:
    labels = ("Script & Research", "Voice", "Produce")
    state_labels = {
        "complete": "Complete",
        "active": "Current",
        "pending": "Upcoming",
    }
    items = []
    for index, label in enumerate(labels, start=1):
        state = (
            "active"
            if index == active_step
            else "complete"
            if index < active_step
            else "pending"
        )
        state_label = state_labels[state]
        current = ' aria-current="step"' if state == "active" else ""
        items.append(
            '<div role="listitem" '
            f'class="vt-workflow__item vt-workflow__item--{state}" '
            f'aria-label="{escape(label)}: {state_label}"{current}>'
            f'<span class="vt-workflow__number" aria-hidden="true">{index}</span>'
            f'<strong>{escape(label)}</strong>'
            f'<span class="vt-workflow__state">{state_label}</span></div>'
        )
    workflow_label = (
        "Video creation workflow, complete"
        if active_step > len(labels)
        else "Video creation workflow"
    )
    st.html(
        f'<div class="vt-workflow" role="list" aria-label="{workflow_label}">'
        f'{"".join(items)}</div>'
    )


def derive_workflow_step(
    script_ready: bool,
    prepared_voice_ready: bool,
    job: dict | None,
) -> int:
    snapshot = dict(job or {})
    status = str(snapshot.get("status") or "").upper()
    checkpoint = str(snapshot.get("checkpoint") or "").upper()
    if status == "COMPLETED" or checkpoint == "COMPLETED":
        return 4
    if str(snapshot.get("id") or "").strip():
        return 3
    if not script_ready:
        return 1
    if not prepared_voice_ready:
        return 2
    return 3


def build_production_progress(job: dict | None) -> ProductionProgressView:
    snapshot = dict(job or {})
    status = str(snapshot.get("status") or "").upper()
    current_step = str(snapshot.get("current_step") or "").lower()
    try:
        percent = int(snapshot.get("progress") or 0)
    except (TypeError, ValueError):
        percent = 0
    percent = max(0, min(100, percent))

    if not str(snapshot.get("id") or "").strip():
        return ProductionProgressView(0, "not_started", "ยังไม่เริ่ม", "สร้างสคริปต์แล้วเริ่มการผลิต")
    if status == "QUEUED":
        return ProductionProgressView(0, "queued", "รอคิว", "รอ Worker รับงานเพื่อเริ่มการผลิต")
    if status == "PAUSED":
        return ProductionProgressView(percent, "paused", "หยุดชั่วคราว", "งานถูกหยุดชั่วคราว")
    if status == "HUMAN_REQUIRED":
        return ProductionProgressView(percent, "attention", "ต้องดำเนินการ", "งานต้องการการตรวจสอบก่อนทำต่อ")
    if status == "FAILED":
        return ProductionProgressView(percent, "error", "เกิดข้อผิดพลาด", "งานหยุดเนื่องจากข้อผิดพลาด")
    if status == "CANCELLED":
        return ProductionProgressView(percent, "cancelled", "ยกเลิกแล้ว", "งานนี้ถูกยกเลิก")
    if status == "COMPLETED" or str(snapshot.get("checkpoint") or "").upper() == "COMPLETED":
        return ProductionProgressView(100, "complete", "เสร็จสมบูรณ์", "วิดีโอพร้อมใช้งาน")
    return ProductionProgressView(
        percent,
        "working",
        "กำลังทำงาน",
        _PRODUCTION_PROGRESS_BY_STEP.get(current_step, "กำลังดำเนินการผลิตวิดีโอ"),
    )


def build_production_stages(
    *,
    script_ready: bool,
    prepared_voice_ready: bool,
    job: dict | None,
) -> tuple[StageView, ...]:
    snapshot = dict(job or {})
    status = str(snapshot.get("status") or "").upper()
    checkpoint = str(snapshot.get("checkpoint") or "NONE").upper()
    job_exists = bool(str(snapshot.get("id") or "").strip())
    job_complete = status == "COMPLETED" or checkpoint == "COMPLETED"
    checkpoint_rank = 5 if job_complete else _CHECKPOINT_RANK.get(checkpoint, 0)
    complete = {
        "script": bool(script_ready or job_exists or checkpoint_rank >= 1),
        "voice": bool(prepared_voice_ready or checkpoint_rank >= 2),
        "flow": checkpoint_rank >= 3,
        "canva": checkpoint_rank >= 4,
        "export": checkpoint_rank >= 5,
    }
    order = ("script", "voice", "flow", "canva", "export")
    if status == "QUEUED":
        return tuple(
            StageView(
                key=key,
                label=_PRODUCTION_STAGE_LABELS[key],
                state="complete" if complete[key] else "pending",
            )
            for key in order
        )
    active = next((key for key in order if not complete[key]), "export")
    if status == "FAILED":
        current = str(snapshot.get("current_step") or "")
        active = (
            "canva" if "canva" in current
            else "flow" if "flow" in current
            else "voice" if "tts" in current or "voice" in current
            else "export" if "export" in current or "validat" in current
            else active
        )
    return tuple(
        StageView(
            key=key,
            label=_PRODUCTION_STAGE_LABELS[key],
            state=(
                "complete" if complete[key]
                else "error" if key == active and status == "FAILED"
                else "active" if key == active
                else "pending"
            ),
        )
        for key in order
    )


def render_production_status(stages: tuple[StageView, ...], job: dict | None) -> None:
    snapshot = dict(job or {})
    job_id = escape(str(snapshot.get("id") or "Not started"))
    status = escape(str(snapshot.get("status") or "Not started"))
    progress = build_production_progress(snapshot)
    progress_label = escape(progress.label)
    progress_detail = escape(progress.detail)
    progress_aria = escape(f"{progress.label}: {progress.detail}")
    items = []
    for stage in stages:
        label = _PRODUCTION_STAGE_LABELS.get(stage.key)
        if label is None:
            continue
        state = (
            stage.state
            if stage.state in {"complete", "active", "pending", "error"}
            else "pending"
        )
        state_label, indicator = _PRODUCTION_STATE_PRESENTATION[state]
        current = ' aria-current="step"' if state in {"active", "error"} else ""
        items.append(
            f'<li class="vt-production-status__stage vt-production-status__stage--{state}" '
            f'aria-label="{label}: {state_label}"{current}>'
            '<span class="vt-production-status__indicator" aria-hidden="true">'
            f"{indicator}</span><strong>{label}</strong>"
            f'<span class="vt-production-status__state">{state_label}</span></li>'
        )
    st.html(
        '<section class="vt-production-status" aria-label="Production status">'
        '<div class="vt-production-status__header"><strong>Production status</strong>'
        f'<span>Job {job_id} · {status}</span></div>'
        f'<div class="vt-production-status__progress vt-production-status__progress--{progress.state}">'
        '<div class="vt-production-status__progress-header">'
        f'<span class="vt-production-status__progress-label">{progress_label}</span>'
        f'<strong>{progress.percent}%</strong></div>'
        f'<div class="vt-production-status__progress-track" role="progressbar" '
        f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{progress.percent}" '
        f'aria-valuetext="{progress_aria}">'
        f'<span style="width: {progress.percent}%"></span></div>'
        f'<p>{progress_detail}</p></div>'
        f'<ol class="vt-production-status__stages">{"".join(items)}</ol></section>'
    )
