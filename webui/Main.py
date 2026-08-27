"""Primary Streamlit entry point for retained VideosTurbo products."""

import os
import sys

import streamlit as st

# When Streamlit starts this file directly, make the repository package win over
# any unrelated third-party package also named ``app``.
root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if root_dir in sys.path:
    sys.path.remove(root_dir)
sys.path.insert(0, root_dir)

from webui import cloud_agent, cloud_agent_ui


st.set_page_config(
    page_title="VideosTurbo",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="auto",
)


def _render_application():
    """Render the retained Cloud Agent entry point."""
    cloud_agent_ui.apply_cloud_agent_theme()
    cloud_agent_ui.render_sidebar()
    cloud_agent_ui.render_page_header(
        saved=bool(st.session_state.get("cloud_agent_draft_script"))
    )
    cloud_agent.render_cloud_agent_panel()


_render_application()
