"""Settings page for Research, TTS providers, API keys, and defaults."""

import requests
import streamlit as st

from webui import cloud_agent, cloud_agent_ui


st.set_page_config(
    page_title="VideosTurbo Settings",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="auto",
)


def _render_settings_page():
    cloud_agent_ui.apply_cloud_agent_theme()
    cloud_agent_ui.render_sidebar()
    st.title("Settings")
    st.caption("Manage Research, TTS providers, API keys, and Cloud Agent defaults.")

    ui_state = st.session_state
    defaults = {
        "tts_provider": "azure-tts-v1",
        "voice_id": "",
        "voice_speed": 1.0,
        "custom_system_prompt": "",
    }
    research_settings = {
        "enabled": True,
        "provider": "openrouter",
        "openrouter_model": "openai/gpt-5.6-sol-pro",
        "openrouter_custom_model_id": "openai/gpt-5.6-sol-pro",
        "aihubmix_model": "gpt-5.6-sol",
        "aihubmix_custom_model_id": "gpt-5.6-sol",
        "custom_system_prompt": "",
    }
    research_provider_catalog = cloud_agent._fallback_research_provider_catalog()
    provider_catalog = cloud_agent._fallback_tts_catalog()
    if hasattr(st, "runtime"):
        try:
            defaults.update(cloud_agent._load_cloud_agent_defaults())
            research_settings.update(cloud_agent._load_research_settings())
            research_provider_catalog = cloud_agent._load_research_provider_catalog()
            provider_catalog = cloud_agent._load_tts_provider_catalog()
        except requests.RequestException as exc:
            st.error(cloud_agent._api_error_message(exc))

    provider_labels = {item["id"]: item["label"] for item in provider_catalog}
    provider = str(defaults.get("tts_provider") or "")
    if provider not in provider_labels:
        provider = next(iter(provider_labels), "")
    provider_metadata = {"voices": [], "settings": []}
    if hasattr(st, "runtime") and provider:
        try:
            provider_metadata = cloud_agent._api("GET", f"tts/providers/{provider}")
        except requests.RequestException as exc:
            st.error(cloud_agent._api_error_message(exc))

    ui_state.setdefault("cloud_agent_script_mode", "Research Script")
    ui_state.setdefault("cloud_agent_provider", provider)
    ui_state.setdefault("cloud_agent_voice", defaults.get("voice_id", ""))
    ui_state.setdefault("cloud_agent_speed", defaults.get("voice_speed", 1.0))
    ui_state.setdefault(
        "cloud_agent_custom_system_prompt", defaults.get("custom_system_prompt", "")
    )
    for key, value in (
        ("cloud_agent_research_provider", research_settings["provider"]),
        (
            "cloud_agent_research_openrouter_model",
            research_settings["openrouter_model"],
        ),
        (
            "cloud_agent_research_openrouter_custom_model_id",
            research_settings["openrouter_custom_model_id"],
        ),
        ("cloud_agent_research_aihubmix_model", research_settings["aihubmix_model"]),
        (
            "cloud_agent_research_aihubmix_custom_model_id",
            research_settings["aihubmix_custom_model_id"],
        ),
        (
            "cloud_agent_research_custom_system_prompt",
            research_settings["custom_system_prompt"],
        ),
    ):
        ui_state.setdefault(key, value)

    with st.container(key="cloud_agent_settings_page", border=True):
        st.subheader("Research and generation settings")
        cloud_agent._render_advanced_settings(
            ui_state=ui_state,
            defaults=defaults,
            research_settings=research_settings,
            research_provider_catalog=research_provider_catalog,
            provider=provider,
            provider_metadata=provider_metadata,
            include_research=True,
        )


_render_settings_page()
