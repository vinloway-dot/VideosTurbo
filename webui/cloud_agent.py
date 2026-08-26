"""Thin Streamlit controls for the Cloud Agent FastAPI API."""

import requests
import streamlit as st


API_PREFIX = "http://127.0.0.1:8080/api/v1/cloud-agent/"
API_TIMEOUT_SECONDS = 15
SESSION_CHECK_TIMEOUT_SECONDS = 45


def _api(method, path, **kwargs):
    timeout = kwargs.pop("timeout", API_TIMEOUT_SECONDS)
    response = requests.request(method, API_PREFIX + path, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response.json().get("data")


def _api_error_message(error):
    response = getattr(error, "response", None)
    if response is not None:
        try:
            message = response.json().get("message", "")
            if message:
                return str(message)
        except (ValueError, requests.RequestException):
            pass
    return "Cloud Agent request could not be completed."


def render_cloud_agent_panel():
    st.subheader("Cloud Agent")
    subject = st.text_input("Video Subject", key="cloud_agent_subject")
    words = st.number_input("Target Words", min_value=1, value=130, key="cloud_agent_words")
    language = st.text_input("Language", value="English", key="cloud_agent_language")
    script = st.text_area("Script Editor", key="cloud_agent_script")
    master_prompt = st.text_area("View Master Prompt", key="cloud_agent_master_prompt")
    provider = st.text_input("TTS Provider", value="azure-tts-v1", key="cloud_agent_provider")
    voice = st.text_input("Voice", key="cloud_agent_voice")
    speed = st.number_input("Speed", min_value=0.1, value=1.0, key="cloud_agent_speed")
    if st.button("Generate Script", key="cloud_agent_generate_script"):
        st.info("Generate Script is prepared in the existing editor.")
    controls = st.columns(4)
    for service, column in (("google-flow", controls[0]), ("canva", controls[1])):
        if column.button("Google Flow" if service == "google-flow" else "Canva", key=f"{service}-check"):
            try:
                st.json(
                    _api(
                        "POST",
                        f"sessions/{service}/check",
                        timeout=SESSION_CHECK_TIMEOUT_SECONDS,
                    )
                )
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
        if column.button("Open Browser", key=f"{service}-open"):
            st.link_button("Open Browser", _api("GET", f"sessions/{service}/open-browser")["url"])
    if controls[2].button("Start", key="cloud_agent_start"):
        st.json(_api("POST", "jobs", json={"subject": subject, "target_words": words, "language": language, "script": script, "master_prompt": master_prompt, "tts_provider": provider, "voice_id": voice, "voice_speed": speed}))
    job_id = st.text_input("Job ID", key="cloud_agent_job_id")
    for action in ("Pause", "Resume", "Retry", "Cancel"):
        if controls[3].button(action, key=f"cloud_agent_{action.lower()}") and job_id:
            try:
                st.json(_api("POST", f"jobs/{job_id}/{action.lower()}"))
                if action == "Retry":
                    st.caption("Flow failed before generation. Existing narration will be reused.")
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    st.caption("job status/history")
    st.caption("final video")
    st.caption("measured narration duration")
    st.caption("Canva playback factor")
    st.caption("Narration Too Long: shorten script; reduce Target Words; increase Voice Rate")
