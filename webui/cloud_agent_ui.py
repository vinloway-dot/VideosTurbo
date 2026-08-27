from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal

import streamlit as st


StageState = Literal["complete", "active", "pending", "error"]


@dataclass(frozen=True)
class StageView:
    key: str
    label: str
    state: StageState


@dataclass(frozen=True)
class ResearchSummary:
    status: str
    source_count: int
    source_links: tuple[tuple[str, str], ...]
    provider_rounds: int | None
    tool_calls: int | None


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
        st.html(
            '<div class="vt-nav-disabled" aria-disabled="true">'
            '<span class="material-symbols-rounded">folder</span> Projects'
            '</div>'
            '<div class="vt-nav-disabled" aria-disabled="true">'
            '<span class="material-symbols-rounded">settings</span> Settings'
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
    items = []
    for index, label in enumerate(labels, start=1):
        state = "active" if index == active_step else "complete" if index < active_step else "pending"
        items.append(
            f'<div class="vt-workflow__item vt-workflow__item--{state}">'
            f'<span>{index}</span><strong>{escape(label)}</strong></div>'
        )
    st.html(f'<div class="vt-workflow">{"".join(items)}</div>')


def derive_workflow_step(
    script_ready: bool,
    prepared_voice_ready: bool,
    job: dict | None,
) -> int:
    if not script_ready:
        return 1
    if not prepared_voice_ready and not (job or {}).get("id"):
        return 2
    return 3


def build_production_stages(
    *,
    script_ready: bool,
    prepared_voice_ready: bool,
    job: dict | None,
) -> tuple[StageView, ...]:
    snapshot = dict(job or {})
    checkpoint_rank = _CHECKPOINT_RANK.get(str(snapshot.get("checkpoint") or "NONE"), 0)
    complete = {
        "script": bool(script_ready),
        "voice": bool(prepared_voice_ready or checkpoint_rank >= 2),
        "flow": checkpoint_rank >= 3,
        "canva": checkpoint_rank >= 4,
        "export": checkpoint_rank >= 5,
    }
    order = ("script", "voice", "flow", "canva", "export")
    active = next((key for key in order if not complete[key]), "export")
    if snapshot.get("status") == "FAILED":
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
                else "error" if key == active and snapshot.get("status") == "FAILED"
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
    items = []
    for stage in stages:
        label = _PRODUCTION_STAGE_LABELS.get(stage.key)
        if label is None:
            continue
        state = stage.state if stage.state in {"complete", "active", "pending", "error"} else "pending"
        items.append(
            f'<li class="vt-production-status__stage vt-production-status__stage--{state}">'
            f'<span aria-hidden="true"></span><strong>{label}</strong></li>'
        )
    st.html(
        '<section class="vt-production-status" aria-label="Production status">'
        '<div class="vt-production-status__header"><strong>Production status</strong>'
        f'<span>Job {job_id} · {status}</span></div>'
        f'<ol class="vt-production-status__stages">{"".join(items)}</ol></section>'
    )
