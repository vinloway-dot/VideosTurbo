"""Dedicated page for completed VideosTurbo productions."""

import streamlit as st

from webui import cloud_agent_ui, completed_videos


st.set_page_config(
    page_title="วีดีโอที่สร้าง · VideosTurbo",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="auto",
)


def _render_completed_videos_page() -> None:
    cloud_agent_ui.apply_cloud_agent_theme()
    cloud_agent_ui.render_sidebar()
    st.title("วีดีโอที่สร้าง")
    st.caption("รวมวิดีโอที่ผลิตเสร็จแล้ว เรียงจากรายการล่าสุด")
    completed_videos.render_video_library(
        ui_state=st.session_state,
        show_heading=False,
    )


_render_completed_videos_page()
