"""Thin Streamlit controls for the Cloud Agent FastAPI API."""

import requests
import streamlit as st


API_PREFIX = "http://127.0.0.1:8080/api/v1/cloud-agent/"
API_TIMEOUT_SECONDS = 15
SESSION_CHECK_TIMEOUT_SECONDS = 45
DRAFT_TIMEOUT_SECONDS = 120


# Keep this list aligned with the retired main generation UI.  An empty value
# preserves the existing LLM behavior: generate in the Video Subject's language.
SCRIPT_LANGUAGE_OPTIONS = [
    ("Auto — detect from Video Subject", ""),
    ("zh-CN", "zh-CN"),
    ("zh-HK", "zh-HK"),
    ("zh-TW", "zh-TW"),
    ("de-DE", "de-DE"),
    ("en-US", "en-US"),
    ("es-ES", "es-ES"),
    ("fr-FR", "fr-FR"),
    ("ru-RU", "ru-RU"),
    ("vi-VN", "vi-VN"),
    ("th-TH", "th-TH"),
    ("tr-TR", "tr-TR"),
]


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


def _load_tts_provider_catalog():
    return _api("GET", "tts/providers")


def _tts_settings_payload(*, settings, secret_fields, clear_secret_fields):
    return {
        "settings": {
            name: value
            for name, value in settings.items()
            if name not in secret_fields or str(value).strip()
        },
        "clear_secret_fields": list(clear_secret_fields),
    }


def _fallback_tts_catalog():
    return [
        {"id": value, "label": label}
        for value, label in (
            ("azure-tts-v1", "Azure TTS V1"),
            ("azure-tts-v2", "Azure TTS V2"),
            ("siliconflow", "SiliconFlow TTS"),
            ("gemini-tts", "Google Gemini TTS"),
            ("mimo-tts", "Xiaomi MiMo TTS"),
            ("minimax-tts", "MiniMax TTS"),
            ("elevenlabs", "ElevenLabs TTS"),
            ("chatterbox", "Chatterbox TTS"),
        )
    ]


def _job_error_message(job):
    code = str(job.get("error_code", "") or "").strip()
    message = str(job.get("error_message", "") or "").strip()
    if code and message:
        return f"{code}: {message}"
    return code or message


def _prepare_draft(*, subject, language, target_words, script, custom_system_prompt):
    return _api(
        "POST",
        "draft",
        json={
            "subject": subject,
            "language": language,
            "target_words": target_words,
            "script": script,
            "custom_system_prompt": custom_system_prompt,
        },
        timeout=DRAFT_TIMEOUT_SECONDS,
    )


def _open_browser_url(service):
    service_id = {"google-flow": "google_flow", "canva": "canva"}[service]
    return _api("GET", f"sessions/{service_id}/open-browser")["url"]


def _start_job(
    *,
    subject,
    target_words,
    language,
    script,
    master_prompt,
    clip_plan,
    tts_provider,
    voice_id,
    voice_speed,
):
    return _api(
        "POST",
        "jobs",
        json={
            "subject": subject,
            "target_words": target_words,
            "language": language,
            "script": script,
            "master_prompt": master_prompt,
            "clip_plan": clip_plan,
            "tts_provider": tts_provider,
            "voice_id": voice_id,
            "voice_speed": voice_speed,
        },
    )


def _store_draft(draft):
    st.session_state["cloud_agent_script"] = draft["script"]
    st.session_state["cloud_agent_master_prompt"] = draft["master_prompt"]
    st.session_state["cloud_agent_clip_plan"] = draft["clip_plan"]
    st.session_state["cloud_agent_draft_script"] = draft["script"]


def render_cloud_agent_panel():
    st.subheader("Cloud Agent")
    subject = st.text_input("Video Subject", key="cloud_agent_subject")
    words = st.number_input("Target Words", min_value=1, value=130, key="cloud_agent_words")
    language_labels = {
        language_code: label for label, language_code in SCRIPT_LANGUAGE_OPTIONS
    }
    language = st.selectbox(
        "Language",
        options=list(language_labels),
        format_func=lambda value: language_labels[value],
        key="cloud_agent_language",
    )
    with st.expander("Custom System Prompt", expanded=False):
        custom_system_prompt = st.text_area(
            "Custom System Prompt",
            key="cloud_agent_custom_system_prompt",
            max_chars=8000,
            help="Leave blank to use the system default script prompt.",
        )
    if st.button("Generate Script", key="cloud_agent_generate_script"):
        if not subject.strip():
            st.error("Video Subject is required.")
        else:
            try:
                _store_draft(
                    _prepare_draft(
                        subject=subject,
                        language=language,
                        target_words=words,
                        script="",
                        custom_system_prompt=custom_system_prompt,
                    )
                )
                st.rerun()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    if st.button("Refresh Draft", key="cloud_agent_refresh_draft"):
        script_for_refresh = str(st.session_state.get("cloud_agent_script", ""))
        if not script_for_refresh.strip():
            st.error("Script Editor is required before refreshing the draft.")
        else:
            try:
                _store_draft(
                    _prepare_draft(
                        subject=subject,
                        language=language,
                        target_words=words,
                        script=script_for_refresh,
                        custom_system_prompt=custom_system_prompt,
                    )
                )
                st.rerun()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    script = st.text_area("Script Editor", key="cloud_agent_script")
    master_prompt = st.text_area(
        "View Master Prompt", key="cloud_agent_master_prompt", disabled=True
    )
    provider_catalog = _fallback_tts_catalog()
    if hasattr(st, "runtime"):
        try:
            provider_catalog = _load_tts_provider_catalog()
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    provider_labels = {item["id"]: item["label"] for item in provider_catalog}
    provider = st.selectbox(
        "TTS Provider", list(provider_labels),
        format_func=lambda value: provider_labels[value], key="cloud_agent_provider",
    )
    provider_metadata = {"voices": [], "settings": []}
    if hasattr(st, "runtime"):
        try:
            provider_metadata = _api("GET", f"tts/providers/{provider}")
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    tts_session_state = getattr(st, "session_state", {})
    voice_options = tts_session_state.get(
        "cloud_agent_tts_voices", provider_metadata.get("voices", [])
    )
    voice_labels = {item["id"]: item.get("label", item["id"]) for item in voice_options}
    voice = st.selectbox(
        "Voice", list(voice_labels) or [""],
        format_func=lambda value: voice_labels.get(value, "Select a configured voice"),
        key="cloud_agent_voice",
    )
    with st.expander("TTS Provider Settings", expanded=False):
        settings = {}
        secret_fields = set()
        clear_secret_fields = []
        for field in provider_metadata.get("settings", []):
            name = field["name"]
            if field["kind"] == "password":
                secret_fields.add(name)
                st.caption(f"{field['label']}: {'configured' if field.get('configured') else 'not configured'}")
                settings[name] = st.text_input(field["label"], type="password", key=f"cloud_tts_{provider}_{name}")
                if hasattr(st, "checkbox") and st.checkbox(
                    f"Remove stored key: {field['label']}",
                    key=f"cloud_tts_remove_{provider}_{name}",
                ):
                    clear_secret_fields.append(name)
            elif field["kind"] == "select":
                settings[name] = st.selectbox(field["label"], field.get("choices", []), key=f"cloud_tts_{provider}_{name}")
            else:
                settings[name] = st.text_input(field["label"], value=str(field.get("value") or ""), key=f"cloud_tts_{provider}_{name}")
        if st.button("Save TTS Settings", key="cloud_agent_save_tts_settings"):
            try:
                _api("PUT", f"tts/providers/{provider}/settings", json=_tts_settings_payload(settings=settings, secret_fields=secret_fields, clear_secret_fields=clear_secret_fields))
                st.rerun()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
        if provider_metadata.get("requires_explicit_voice_refresh") and st.button("Load Voices", key="cloud_agent_load_tts_voices"):
            try:
                tts_session_state["cloud_agent_tts_voices"] = _api("POST", f"tts/providers/{provider}/voices/refresh")["voices"]
                st.rerun()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    speed = st.number_input("Speed", min_value=0.1, value=1.0, key="cloud_agent_speed")
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
            st.link_button("Open Browser", _open_browser_url(service))
    if controls[2].button("Start", key="cloud_agent_start"):
        clip_plan = st.session_state.get("cloud_agent_clip_plan")
        draft_script = str(st.session_state.get("cloud_agent_draft_script", ""))
        if not clip_plan or draft_script != script.strip():
            st.error("Generate or refresh the draft before starting the job.")
        elif not voice.strip():
            st.error("Voice is required before starting the job.")
        else:
            try:
                st.json(
                    _start_job(
                        subject=subject,
                        target_words=words,
                        language=language,
                        script=script,
                        master_prompt=master_prompt,
                        clip_plan=clip_plan,
                        tts_provider=provider,
                        voice_id=voice,
                        voice_speed=speed,
                    )
                )
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    job_id = st.text_input("Job ID", key="cloud_agent_job_id")
    for action in ("Pause", "Resume", "Retry", "Cancel"):
        if controls[3].button(action, key=f"cloud_agent_{action.lower()}") and job_id:
            try:
                st.json(_api("POST", f"jobs/{job_id}/{action.lower()}"))
                if action == "Retry":
                    st.caption("Flow failed before generation. Existing narration will be reused.")
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    if job_id.strip():
        try:
            job = _api("GET", f"jobs/{job_id.strip()}")
            st.json(
                {
                    key: job.get(key)
                    for key in (
                        "status",
                        "checkpoint",
                        "current_step",
                        "progress",
                        "error_code",
                        "error_message",
                    )
                }
            )
            if message := _job_error_message(job):
                st.error(message)
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    st.caption("job status/history")
    st.caption("final video")
    st.caption("measured narration duration")
    st.caption("Canva playback factor")
    st.caption("Narration Too Long: shorten script; reduce Target Words; increase Voice Rate")
