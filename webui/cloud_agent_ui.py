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


_CHECKPOINT_RANK = {
    "NONE": 0,
    "PREFLIGHT_PASSED": 1,
    "TTS_READY": 2,
    "FLOW_READY": 3,
    "FINAL_VALIDATED": 4,
    "COMPLETED": 5,
}

_CSS_PATH = Path(__file__).with_name("cloud_agent.css")


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
    st.html('<div class="vt-breadcrumb">Workspace / Cloud Agent</div>')
    st.title("Create a video")
    st.caption("Research, write, narrate, and produce — all in one flow.")
    if saved:
        st.html('<div class="vt-saved"><span>✓</span> Saved</div>')


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
    labels = {
        "script": "Script",
        "voice": "Voice",
        "flow": "Flow",
        "canva": "Canva",
        "export": "Export",
    }
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
            label=labels[key],
            state=(
                "complete" if complete[key]
                else "error" if key == active and snapshot.get("status") == "FAILED"
                else "active" if key == active
                else "pending"
            ),
        )
        for key in order
    )
