"""Thin Streamlit controls for the Cloud Agent FastAPI API."""

from dataclasses import dataclass

import requests
import streamlit as st

from webui import cloud_agent_ui, cloud_agent_events


API_PREFIX = "http://127.0.0.1:8080/api/v1/cloud-agent/"
API_TIMEOUT_SECONDS = 15
SESSION_CHECK_TIMEOUT_SECONDS = 45
DRAFT_TIMEOUT_SECONDS = 120
RESEARCH_DRAFT_TIMEOUT_SECONDS = 300
RESEARCH_PROVIDER_OPTIONS = [
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "models": ["openai/gpt-5.6-sol-pro", "custom"],
        "default_model": "openai/gpt-5.6-sol-pro",
        "custom_model_id": "openai/gpt-5.6-sol-pro",
        "api_key_configured": False,
    },
    {
        "id": "aihubmix",
        "label": "AIHubMix",
        "models": ["gpt-5.6-sol", "custom"],
        "default_model": "gpt-5.6-sol",
        "custom_model_id": "gpt-5.6-sol",
        "api_key_configured": False,
    },
]


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


def _verify_tts_settings_save(
    *, settings, secret_fields, clear_secret_fields, metadata
):
    """Confirm safe, observable settings after the server has saved them."""
    fields = {field["name"]: field for field in metadata.get("settings", [])}
    verified = []
    for name, value in settings.items():
        field = fields.get(name)
        if field is None:
            return (
                False,
                "Could not verify saved settings. Reload the provider settings and try again.",
            )
        label = field.get("label", name)
        if name in secret_fields:
            if not str(value).strip():
                continue
            if not field.get("configured"):
                return (
                    False,
                    "Could not verify saved settings. Reload the provider settings and try again.",
                )
            verified.append(f"{label} configured")
        elif field.get("value") != value:
            return (
                False,
                "Could not verify saved settings. Reload the provider settings and try again.",
            )
        else:
            verified.append(f"{label} = {field.get('value')}")

    for name in clear_secret_fields:
        field = fields.get(name)
        if field is None or field.get("configured"):
            return (
                False,
                "Could not verify saved settings. Reload the provider settings and try again.",
            )
        verified.append(f"{field.get('label', name)} removed")

    if not verified:
        return False, "Enter a setting to save, or explicitly remove a stored key."
    return True, f"Saved and verified: {'; '.join(verified)}"


def _clear_tts_settings_feedback():
    st.session_state.pop("cloud_agent_tts_settings_feedback", None)


def _clear_provider_feedback():
    st.session_state.pop("cloud_agent_defaults_feedback", None)
    _clear_tts_settings_feedback()


def _verify_cloud_agent_defaults_save(payload, saved_defaults):
    if all(saved_defaults.get(name) == value for name, value in payload.items()):
        return True, "Saved and verified."
    return False, "Could not verify saved defaults. Reload the page and try again."


def _save_and_verify_cloud_agent_defaults(payload):
    _save_cloud_agent_defaults(payload)
    return _verify_cloud_agent_defaults_save(payload, _load_cloud_agent_defaults())


def _fallback_research_provider_catalog():
    return list(RESEARCH_PROVIDER_OPTIONS)


def _load_research_provider_catalog():
    return _api("GET", "research/providers")


def _load_research_settings():
    return _api("GET", "research/settings")


def _thumbnail_prompt_default_settings():
    return {
        "master_prompt": "",
        "default_provider": "aihubmix",
        "aihubmix_model": "gpt-5.6-sol",
        "aihubmix_custom_model_id": "",
        "aihubmix_base_url": "https://aihubmix.com/v1",
        "openrouter_model": "openai/gpt-5.6-sol",
        "openrouter_custom_model_id": "",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
    }


def _load_thumbnail_prompt_settings():
    return _api("GET", "thumbnail-prompt/settings")


def _load_thumbnail_prompt_provider_catalog():
    return _api("GET", "thumbnail-prompt/providers")


def _thumbnail_prompt_settings_payload(ui_state):
    return {
        "master_prompt": str(ui_state.get("thumbnail_prompt_master_prompt", "") or ""),
        "default_provider": str(
            ui_state.get("thumbnail_prompt_default_provider", "aihubmix") or "aihubmix"
        ),
        "aihubmix_model": str(
            ui_state.get("thumbnail_prompt_aihubmix_model", "gpt-5.6-sol")
            or "gpt-5.6-sol"
        ),
        "aihubmix_custom_model_id": str(
            ui_state.get("thumbnail_prompt_aihubmix_custom_model_id", "") or ""
        ),
        "aihubmix_base_url": str(
            ui_state.get(
                "thumbnail_prompt_aihubmix_base_url", "https://aihubmix.com/v1"
            )
            or ""
        ),
        "openrouter_model": str(
            ui_state.get("thumbnail_prompt_openrouter_model", "openai/gpt-5.6-sol")
            or "openai/gpt-5.6-sol"
        ),
        "openrouter_custom_model_id": str(
            ui_state.get("thumbnail_prompt_openrouter_custom_model_id", "") or ""
        ),
        "openrouter_base_url": str(
            ui_state.get(
                "thumbnail_prompt_openrouter_base_url",
                "https://openrouter.ai/api/v1",
            )
            or ""
        ),
    }


def _save_thumbnail_prompt_settings(payload):
    return _api("PUT", "thumbnail-prompt/settings", json=payload)


def _save_thumbnail_prompt_api_key(provider, value):
    return _api(
        "PUT",
        f"thumbnail-prompt/providers/{provider}/api-key",
        json={"api_key": str(value or "").strip()},
    )


def _remove_thumbnail_prompt_api_key(provider):
    return _api(
        "DELETE",
        f"thumbnail-prompt/providers/{provider}/api-key",
        json={"confirmed": True},
    )


def _submit_thumbnail_prompt_api_key(provider, *, remove):
    state_key = f"thumbnail_prompt_api_key_{provider}"
    value = str(st.session_state.pop(state_key, "") or "")
    try:
        if remove:
            _remove_thumbnail_prompt_api_key(provider)
            feedback = ("success", "Thumbnail provider API key removed.")
        elif not value.strip():
            feedback = (
                "error",
                "Enter a thumbnail provider API key, or explicitly remove the stored key.",
            )
        else:
            _save_thumbnail_prompt_api_key(provider, value)
            feedback = ("success", "Thumbnail provider API key saved.")
    except requests.RequestException as exc:
        feedback = ("error", _api_error_message(exc))
    st.session_state["thumbnail_prompt_key_feedback"] = feedback


def _save_and_verify_research_settings(payload):
    saved = _api("PUT", "research/settings", json=payload)
    readback = _load_research_settings()
    if all(
        saved.get(name) == value and readback.get(name) == value
        for name, value in payload.items()
    ):
        return True, "Saved and verified."
    return (
        False,
        "Could not verify saved research settings. Reload the page and try again.",
    )


def _research_key_payload(value):
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return {"api_key": normalized}


def _save_research_api_key(provider, value):
    payload = _research_key_payload(value)
    if payload is None:
        return None
    return _api("PUT", f"research/providers/{provider}/api-key", json=payload)


def _remove_research_api_key(provider):
    return _api(
        "DELETE",
        f"research/providers/{provider}/api-key",
        json={"confirmed": True},
    )


def _submit_research_api_key(provider, *, remove):
    state_key = f"cloud_agent_research_api_key_{provider}"
    raw_value = str(st.session_state.pop(state_key, "") or "")
    try:
        if remove:
            _remove_research_api_key(provider)
            feedback = ("success", "Research API key removed.")
        elif _save_research_api_key(provider, raw_value) is None:
            feedback = (
                "error",
                "Enter a research API key, or explicitly remove the stored key.",
            )
        else:
            feedback = ("success", "Research API key saved.")
    except requests.RequestException as exc:
        feedback = ("error", _api_error_message(exc))
    st.session_state["cloud_agent_research_key_feedback"] = feedback


def _prepare_research_draft(
    *,
    subject,
    language,
    target_words,
    provider,
    model_choice,
    custom_model_id,
    source_urls,
    custom_system_prompt,
    allow_citations,
):
    return _api(
        "POST",
        "research/drafts",
        json={
            "subject": subject,
            "language": language,
            "target_words": target_words,
            "provider": provider,
            "model_choice": model_choice,
            "custom_model_id": custom_model_id,
            "source_urls": source_urls,
            "custom_system_prompt": custom_system_prompt,
            "allow_citations": bool(allow_citations),
        },
        timeout=RESEARCH_DRAFT_TIMEOUT_SECONDS,
    )


def _research_error_data(response):
    try:
        payload = response.json()
    except ValueError:
        return {"message": "Research request failed."}
    return {
        "message": str(payload.get("message") or "Research request failed."),
        **dict(payload.get("data") or {}),
    }


def _research_job_fields(research_draft_id):
    normalized = str(research_draft_id or "").strip()
    if not normalized:
        return {}
    return {"research_draft_id": normalized}


def _research_source_urls(value):
    values = value if isinstance(value, list | tuple) else str(value or "").splitlines()
    return [str(item or "").strip() for item in values if str(item or "").strip()]


def _research_mode_options(enabled):
    """Keep Research visible so a disabled feature can be re-enabled in its settings."""
    return ["Standard Script", "Research Script"]


def _research_url_row_count(value):
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 1
    return min(3, max(1, count))


def _research_provider_metadata(catalog, provider):
    return next(
        (item for item in catalog if item.get("id") == provider),
        next(
            (item for item in RESEARCH_PROVIDER_OPTIONS if item["id"] == provider),
            RESEARCH_PROVIDER_OPTIONS[0],
        ),
    )


def _research_model_choice(provider, settings):
    provider_key = f"{provider}_model"
    value = settings.get(
        provider_key,
        settings.get(f"cloud_agent_research_{provider_key}", ""),
    )
    return str(value or "").strip()


def _research_custom_model_id(provider, settings):
    provider_key = f"{provider}_custom_model_id"
    value = settings.get(
        provider_key,
        settings.get(f"cloud_agent_research_{provider_key}", ""),
    )
    return str(value or "").strip()


def _clear_research_state():
    for key in (
        "cloud_agent_research_draft_id",
        "cloud_agent_research_sources",
        "cloud_agent_research_accounting",
    ):
        st.session_state.pop(key, None)


def _store_research_result(draft):
    _store_draft(draft)
    st.session_state["cloud_agent_research_draft_id"] = draft["research_draft_id"]
    st.session_state["cloud_agent_research_sources"] = list(draft.get("sources") or [])
    st.session_state["cloud_agent_research_accounting"] = dict(
        draft.get("accounting") or {}
    )


def _store_refreshed_draft(draft):
    prior_script = str(
        st.session_state.get("cloud_agent_draft_script", "") or ""
    ).strip()
    retained = {
        key: st.session_state[key]
        for key in (
            "cloud_agent_research_draft_id",
            "cloud_agent_research_sources",
            "cloud_agent_research_accounting",
        )
        if key in st.session_state
    }
    _store_draft(draft)
    if retained and str(draft.get("script", "") or "").strip() == prior_script:
        st.session_state.update(retained)


def _render_research_accounting(accounting):
    normalized = dict(accounting or {})
    usage = dict(normalized.get("usage") or {})
    provider_rounds = normalized.get("provider_rounds")
    tool_calls = normalized.get("tool_calls")
    cost = normalized.get("cost")
    st.caption(
        f"Research rounds used: {provider_rounds if provider_rounds not in (None, '') else 'unavailable'} / 3"
    )
    st.caption(
        f"Research tool calls: {tool_calls if tool_calls not in (None, '') else 'unavailable'}"
    )
    st.caption(f"Research usage: {usage if usage else 'unavailable'}")
    st.caption(
        f"Research cost (USD): {cost if cost not in (None, '') else 'unavailable'}"
    )


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


def _prepare_draft_voice(*, script, tts_provider, voice_id, voice_speed):
    return _api(
        "POST",
        "draft/voice",
        json={
            "script": script,
            "tts_provider": tts_provider,
            "voice_id": voice_id,
            "voice_speed": voice_speed,
        },
        timeout=DRAFT_TIMEOUT_SECONDS,
    )


def _prepared_voice_audio(fingerprint):
    response = requests.get(
        API_PREFIX + f"draft/voices/{fingerprint}/audio", timeout=DRAFT_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    return response.content


def _cloud_agent_defaults_payload(
    *, tts_provider, voice_id, voice_speed, custom_system_prompt
):
    return {
        "tts_provider": tts_provider,
        "voice_id": voice_id,
        "voice_speed": voice_speed,
        "custom_system_prompt": custom_system_prompt,
    }


def _load_cloud_agent_defaults():
    return _api("GET", "defaults")


def _save_cloud_agent_defaults(payload):
    return _api("PUT", "defaults", json=payload)


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
    prepared_voice_fingerprint="",
    research_draft_id="",
):
    payload = {
        "subject": subject,
        "target_words": target_words,
        "language": language,
        "script": script,
        "master_prompt": master_prompt,
        "clip_plan": clip_plan,
        "tts_provider": tts_provider,
        "voice_id": voice_id,
        "voice_speed": voice_speed,
        "prepared_voice_fingerprint": prepared_voice_fingerprint,
    }
    payload.update(_research_job_fields(research_draft_id))
    return _api(
        "POST",
        "jobs",
        json=payload,
    )


def _store_job_snapshot(job, *, sync_lookup=False):
    safe_fields = (
        "id",
        "status",
        "checkpoint",
        "current_step",
        "progress",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    )
    snapshot = {name: job.get(name) for name in safe_fields if name in job}
    st.session_state["cloud_agent_job_id"] = str(snapshot.get("id") or "")
    st.session_state["cloud_agent_job_snapshot"] = snapshot
    if sync_lookup:
        st.session_state["cloud_agent_job_lookup_id"] = str(snapshot.get("id") or "")


def _start_and_store_job(inputs):
    job = _start_job(**inputs)
    _store_job_snapshot(job, sync_lookup=True)
    return job


def _restore_latest_job_if_needed(ui_state):
    if str(ui_state.get("cloud_agent_job_id") or "").strip():
        return dict(ui_state.get("cloud_agent_job_snapshot") or {})
    jobs = _api("GET", "jobs")
    if not jobs:
        return {}
    latest = dict(jobs[0])
    _store_job_snapshot(latest, sync_lookup=True)
    return latest


def _selected_job_id(ui_state, entered_job_id):
    return str(entered_job_id or ui_state.get("cloud_agent_job_id") or "").strip()


def _render_event_driven_production_status(
    *, script_ready, prepared_voice_ready, ui_state
):
    def render(snapshot):
        cloud_agent_ui.render_production_status(
            cloud_agent_ui.build_production_stages(
                script_ready=script_ready,
                prepared_voice_ready=prepared_voice_ready,
                job=snapshot,
            ),
            snapshot,
        )

    snapshot = dict(ui_state.get("cloud_agent_job_snapshot") or {})
    job_id = str(snapshot.get("id") or "").strip()
    event = cloud_agent_events.render_cloud_job_event_listener(
        "/api/v1/cloud-agent/events/stream", key="cloud-agent-events"
    )
    action = cloud_agent_events.classify_event(
        event,
        selected_job_id=job_id,
        last_event_id=str(ui_state.get("cloud_agent_last_event_id") or ""),
    )
    if event and event.get("event_id"):
        ui_state["cloud_agent_last_event_id"] = event["event_id"]
    if action in {"refresh_job", "sync"} and job_id:
        try:
            latest = _api("GET", f"jobs/{job_id}")
            _store_job_snapshot(latest)
            snapshot = dict(latest)
        except requests.RequestException:
            pass
    elif action == "refresh_app":
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun(scope="app")
    render(snapshot)


def _prepared_voice_matches(prepared_voice, *, script, provider, voice, speed):
    return bool(
        prepared_voice
        and str(prepared_voice.get("fingerprint") or "").strip()
        and all(
            prepared_voice.get(field) == value
            for field, value in (
                ("script", script),
                ("tts_provider", provider),
                ("voice_id", voice),
                ("voice_speed", speed),
            )
        )
    )


def _store_draft(draft):
    _clear_research_state()
    st.session_state["cloud_agent_script"] = draft["script"]
    st.session_state["cloud_agent_master_prompt"] = draft["master_prompt"]
    st.session_state["cloud_agent_clip_plan"] = draft["clip_plan"]
    st.session_state["cloud_agent_draft_script"] = draft["script"]


@dataclass(frozen=True)
class _BriefSelection:
    subject: str
    words: int
    language: str
    script_mode: str
    custom_system_prompt: str
    research_provider: str = ""
    research_model: str = ""


@dataclass(frozen=True)
class _GenerationSelection:
    provider: str
    voice: str
    speed: float
    prepared_voice: dict | None


def _render_script_mode_control(options, default):
    return st.segmented_control(
        "Script creation mode",
        options,
        default=default,
        key="cloud_agent_script_mode",
        width="stretch",
        label_visibility="collapsed",
    )


def _render_video_brief(
    *, ui_state, defaults, research_settings, research_provider_catalog
):
    language_labels = {
        language_code: label for label, language_code in SCRIPT_LANGUAGE_OPTIONS
    }
    with st.container(key="cloud_agent_brief_card", border=True):
        st.subheader("Video brief")
        subject = st.text_area(
            "Video subject",
            key="cloud_agent_subject",
            height=82,
            placeholder="e.g., How to cook perfect rice every time",
        )
        brief_columns = st.columns([1.8, 0.55, 0.75], gap="medium")
        words = brief_columns[1].number_input(
            "Target words", min_value=1, value=130, key="cloud_agent_words"
        )
        language = brief_columns[2].selectbox(
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
                on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
            )
            if st.button(
                "Save Custom System Prompt",
                key="cloud_agent_save_custom_system_prompt",
            ):
                try:
                    verified, message = _save_and_verify_cloud_agent_defaults(
                        _cloud_agent_defaults_payload(
                            tts_provider=defaults["tts_provider"],
                            voice_id=defaults["voice_id"],
                            voice_speed=defaults["voice_speed"],
                            custom_system_prompt=custom_system_prompt,
                        )
                    )
                    if verified:
                        ui_state["cloud_agent_defaults_feedback"] = message
                        st.rerun()
                    else:
                        st.error(message)
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
        research_enabled = bool(research_settings.get("enabled", True))
        script_mode_options = _research_mode_options(research_enabled)
        if ui_state.get("cloud_agent_script_mode") not in script_mode_options:
            ui_state["cloud_agent_script_mode"] = "Standard Script"
        script_mode = _render_script_mode_control(
            script_mode_options,
            ui_state.get("cloud_agent_script_mode", "Standard Script"),
        )
        research_provider = ""
        research_model = ""
        if script_mode == "Standard Script":
            if st.button(
                "Generate script",
                key="cloud_agent_generate_script",
                type="primary",
                icon=":material/edit_note:",
                width="stretch",
            ):
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
        else:
            if not research_enabled:
                st.caption(
                    "Research is disabled. Open Advanced settings and save Research Settings to enable it."
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
                (
                    "cloud_agent_research_aihubmix_model",
                    research_settings["aihubmix_model"],
                ),
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
            research_provider_labels = {
                item["id"]: item["label"] for item in research_provider_catalog
            }
            if (
                ui_state.get("cloud_agent_research_provider")
                not in research_provider_labels
            ):
                ui_state["cloud_agent_research_provider"] = next(
                    iter(research_provider_labels), "openrouter"
                )
            research_provider = st.selectbox(
                "Research Provider",
                list(research_provider_labels),
                format_func=lambda value: research_provider_labels[value],
                key="cloud_agent_research_provider",
            )
            with st.container(key="cloud_agent_research_source_area"):
                st.caption("Up to 3 sources · Direct webpages and PDFs")
                research_allow_citations = bool(
                    st.checkbox(
                        "อนุญาตให้ใส่อ้างอิงในสคริปต์",
                        value=False,
                        key="cloud_agent_research_allow_citations",
                    )
                )
                source_url_count = _research_url_row_count(
                    st.number_input(
                        "Number of Source URLs",
                        min_value=1,
                        max_value=3,
                        value=1,
                        step=1,
                        key="cloud_agent_research_source_url_count",
                    )
                )
                source_url_values = [
                    st.text_input(
                        f"Source URL {index}",
                        key=f"cloud_agent_research_source_url_{index}",
                    )
                    for index in range(1, source_url_count + 1)
                ]
            research_generation_status = st.empty()
            st.caption(
                "Research generation may call the selected provider up to 3 rounds."
            )
            if st.button(
                "Generate research script",
                key="cloud_agent_generate_research_script",
                type="primary",
                icon=":material/auto_awesome:",
                width="stretch",
                disabled=not research_enabled,
            ):
                if not subject.strip():
                    st.error("Video Subject is required.")
                else:
                    try:
                        with research_generation_status.container():
                            with st.spinner("Generating research-backed script..."):
                                _store_research_result(
                                    _prepare_research_draft(
                                        subject=subject,
                                        language=language,
                                        target_words=words,
                                        provider=research_provider,
                                        model_choice=_research_model_choice(
                                            research_provider, ui_state
                                        ),
                                        custom_model_id=_research_custom_model_id(
                                            research_provider, ui_state
                                        ),
                                        source_urls=_research_source_urls(
                                            source_url_values
                                        ),
                                        custom_system_prompt=str(
                                            ui_state.get(
                                                "cloud_agent_research_custom_system_prompt",
                                                "",
                                            )
                                            or ""
                                        ),
                                        allow_citations=research_allow_citations,
                                    )
                                )
                        st.rerun()
                    except requests.HTTPError as exc:
                        error = _research_error_data(exc.response)
                        st.error(error["message"])
                        _render_research_accounting(error.get("accounting", {}))
                    except requests.RequestException as exc:
                        st.error(_api_error_message(exc))
        research_model = (
            _research_model_choice(research_provider, ui_state)
            if research_provider
            else ""
        )
        return _BriefSelection(
            subject=subject,
            words=words,
            language=language,
            script_mode=str(script_mode or "Standard Script"),
            custom_system_prompt=custom_system_prompt,
            research_provider=research_provider,
            research_model=research_model,
        )


def _refresh_script_editor(*, subject, language, words, custom_system_prompt):
    script_for_refresh = str(st.session_state.get("cloud_agent_script", ""))
    if not script_for_refresh.strip():
        st.error("Script Editor is required before refreshing the draft.")
        return
    try:
        _store_refreshed_draft(
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


def _render_script_editor(*, brief, ui_state):
    with st.container(key="cloud_agent_script_card", border=True):
        script = str(ui_state.get("cloud_agent_script") or "")
        master_prompt = str(ui_state.get("cloud_agent_master_prompt") or "")
        with st.expander("Script editor", expanded=bool(script)):
            title_row = st.columns([1, 0.24], vertical_alignment="center")
            title_row[0].subheader("Script editor")
            if title_row[1].button(
                "Regenerate",
                key="cloud_agent_refresh_draft",
                icon=":material/refresh:",
                width="stretch",
            ):
                _refresh_script_editor(
                    subject=brief.subject,
                    language=brief.language,
                    words=brief.words,
                    custom_system_prompt=brief.custom_system_prompt,
                )
            if brief.script_mode == "Research Script":
                summary = cloud_agent_ui.research_summary(
                    research_draft_id=ui_state.get("cloud_agent_research_draft_id"),
                    sources=ui_state.get("cloud_agent_research_sources", []),
                    accounting=ui_state.get("cloud_agent_research_accounting", {}),
                )
                cloud_agent_ui.render_research_summary(summary)
            script = st.text_area(
                "Script",
                key="cloud_agent_script",
                height=120,
                label_visibility="collapsed",
            )
            with st.expander("View master prompt", expanded=False):
                master_prompt = st.text_area(
                    "Master prompt",
                    key="cloud_agent_master_prompt",
                    disabled=True,
                    label_visibility="collapsed",
                )
            st.caption(f"{len(script.split())} words")
        return script, master_prompt


def _advanced_settings_container():
    return st.expander("Advanced settings", expanded=False)


def _render_settings_research_provider_selector(
    *, ui_state, research_settings, research_provider_catalog
):
    provider_labels = {item["id"]: item["label"] for item in research_provider_catalog}
    state_key = "cloud_agent_settings_research_provider"
    provider = str(ui_state.get(state_key) or research_settings["provider"])
    if provider not in provider_labels:
        provider = next(iter(provider_labels), "")
    ui_state[state_key] = provider
    provider_ids = list(provider_labels)
    return st.selectbox(
        "Research Provider",
        provider_ids,
        index=provider_ids.index(provider) if provider in provider_ids else 0,
        format_func=lambda value: provider_labels[value],
        key=state_key,
    )


def _render_settings_tts_provider_selector(*, ui_state, defaults, provider_catalog):
    provider_labels = {item["id"]: item["label"] for item in provider_catalog}
    state_key = "cloud_agent_settings_tts_provider"
    provider = str(ui_state.get(state_key) or defaults["tts_provider"])
    if provider not in provider_labels:
        provider = next(iter(provider_labels), "")
    ui_state[state_key] = provider
    provider_ids = list(provider_labels)
    return st.selectbox(
        "TTS Provider",
        provider_ids,
        index=provider_ids.index(provider) if provider in provider_ids else 0,
        format_func=lambda value: provider_labels[value],
        key=state_key,
    )


def _render_thumbnail_prompt_settings(*, ui_state, settings, provider_catalog):
    """Render settings owned solely by the thumbnail-prompt subsystem."""
    provider_by_id = {item["id"]: item for item in provider_catalog}
    provider_ids = tuple(
        provider_id
        for provider_id in ("aihubmix", "openrouter")
        if provider_id in provider_by_id
    )
    if not provider_ids:
        st.error("Thumbnail provider metadata is unavailable.")
        return

    for key, value in settings.items():
        ui_state.setdefault(f"thumbnail_prompt_{key}", value)

    selected_provider = str(
        ui_state.get("thumbnail_prompt_default_provider") or provider_ids[0]
    )
    if selected_provider not in provider_ids:
        selected_provider = provider_ids[0]
        ui_state["thumbnail_prompt_default_provider"] = selected_provider
    selected_metadata = provider_by_id[selected_provider]
    model_key = f"thumbnail_prompt_{selected_provider}_model"
    custom_model_key = f"thumbnail_prompt_{selected_provider}_custom_model_id"
    models = tuple(selected_metadata.get("models") or ())
    if ui_state.get(model_key) not in models:
        ui_state[model_key] = selected_metadata.get("default_model", "")

    with st.container(key="thumbnail_prompt_settings", border=True):
        st.subheader("Thumbnail Master Prompt")
        st.text_area(
            "Thumbnail Master Prompt",
            key="thumbnail_prompt_master_prompt",
            max_chars=8000,
        )
        selected_provider = st.selectbox(
            "Default Thumbnail Provider",
            options=provider_ids,
            format_func=lambda provider: provider_by_id[provider].get(
                "label", provider
            ),
            key="thumbnail_prompt_default_provider",
        )
        selected_metadata = provider_by_id[selected_provider]
        model_key = f"thumbnail_prompt_{selected_provider}_model"
        custom_model_key = f"thumbnail_prompt_{selected_provider}_custom_model_id"
        model = st.selectbox(
            f"{selected_metadata.get('label', selected_provider)} Model",
            options=tuple(selected_metadata.get("models") or ()),
            key=model_key,
        )
        if model == "custom":
            st.text_input(
                f"{selected_metadata.get('label', selected_provider)} Custom Model ID",
                key=custom_model_key,
            )
        st.text_input(
            f"{selected_metadata.get('label', selected_provider)} Base URL",
            key=f"thumbnail_prompt_{selected_provider}_base_url",
        )
        if st.button(
            "Save Thumbnail Prompt Settings",
            key="thumbnail_prompt_save_settings",
        ):
            try:
                saved_settings = _save_thumbnail_prompt_settings(
                    _thumbnail_prompt_settings_payload(ui_state)
                )
                for key, value in saved_settings.items():
                    ui_state[f"thumbnail_prompt_{key}"] = value
                ui_state["thumbnail_prompt_settings_feedback"] = (
                    "Thumbnail settings saved."
                )
                st.rerun()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
        if feedback := ui_state.get("thumbnail_prompt_settings_feedback"):
            st.success(feedback)

        st.caption(
            f"{selected_metadata.get('label', selected_provider)} API key: "
            f"{'configured' if selected_metadata.get('api_key_configured') else 'not configured'}"
        )
        api_key_state_key = f"thumbnail_prompt_api_key_{selected_provider}"
        st.text_input(
            "Thumbnail Provider API Key",
            type="password",
            key=api_key_state_key,
        )
        remove_key = bool(
            hasattr(st, "checkbox")
            and st.checkbox(
                "Remove stored thumbnail provider API key",
                key=f"thumbnail_prompt_remove_key_{selected_provider}",
            )
        )
        st.button(
            "Save Thumbnail Provider API Key",
            key="thumbnail_prompt_save_api_key",
            on_click=_submit_thumbnail_prompt_api_key,
            args=(selected_provider,),
            kwargs={"remove": remove_key},
        )
        if feedback := ui_state.get("thumbnail_prompt_key_feedback"):
            feedback_kind, feedback_message = feedback
            (st.success if feedback_kind == "success" else st.error)(feedback_message)


def _render_advanced_settings(
    *,
    ui_state,
    defaults,
    research_settings,
    research_provider_catalog,
    provider,
    provider_metadata,
    include_research: bool | None = None,
    include_tts: bool = True,
    research_provider_state_key: str = "cloud_agent_research_provider",
):
    should_render_research = (
        ui_state.get("cloud_agent_script_mode") == "Research Script"
        if include_research is None
        else include_research
    )
    if should_render_research:
        research_provider = str(ui_state.get(research_provider_state_key, "") or "")
        research_provider_labels = {
            item["id"]: item["label"] for item in research_provider_catalog
        }
        selected_provider_metadata = next(
            (
                item
                for item in research_provider_catalog
                if item["id"] == research_provider
            ),
            {"api_key_configured": False},
        )
        openrouter_metadata = _research_provider_metadata(
            research_provider_catalog, "openrouter"
        )
        aihubmix_metadata = _research_provider_metadata(
            research_provider_catalog, "aihubmix"
        )
        for provider_id, metadata in (
            ("openrouter", openrouter_metadata),
            ("aihubmix", aihubmix_metadata),
        ):
            model_key = f"cloud_agent_research_{provider_id}_model"
            if ui_state.get(model_key) not in metadata["models"]:
                ui_state[model_key] = metadata["default_model"]

        st.caption("Research Settings")
        openrouter_model = st.selectbox(
            "OpenRouter Model",
            options=openrouter_metadata["models"],
            key="cloud_agent_research_openrouter_model",
        )
        openrouter_custom_model_id = str(
            ui_state.get("cloud_agent_research_openrouter_custom_model_id", "") or ""
        )
        if openrouter_model == "custom":
            openrouter_custom_model_id = st.text_input(
                "OpenRouter Custom Model ID",
                key="cloud_agent_research_openrouter_custom_model_id",
            )
        aihubmix_model = st.selectbox(
            "AIHubMix Model",
            options=aihubmix_metadata["models"],
            key="cloud_agent_research_aihubmix_model",
        )
        aihubmix_custom_model_id = str(
            ui_state.get("cloud_agent_research_aihubmix_custom_model_id", "") or ""
        )
        if aihubmix_model == "custom":
            aihubmix_custom_model_id = st.text_input(
                "AIHubMix Custom Model ID",
                key="cloud_agent_research_aihubmix_custom_model_id",
            )
        research_custom_system_prompt = st.text_area(
            "Research Custom System Prompt",
            key="cloud_agent_research_custom_system_prompt",
            max_chars=8000,
            help="Leave blank to use the research system default prompt.",
        )
        if st.button(
            "Save Research Settings", key="cloud_agent_save_research_settings"
        ):
            try:
                verified, message = _save_and_verify_research_settings(
                    {
                        "enabled": True,
                        "provider": research_provider,
                        "openrouter_model": openrouter_model,
                        "openrouter_custom_model_id": openrouter_custom_model_id,
                        "aihubmix_model": aihubmix_model,
                        "aihubmix_custom_model_id": aihubmix_custom_model_id,
                        "custom_system_prompt": research_custom_system_prompt,
                    }
                )
                if verified:
                    ui_state["cloud_agent_research_provider"] = research_provider
                    ui_state["cloud_agent_research_settings_feedback"] = message
                    st.rerun()
                else:
                    st.error(message)
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
        if feedback := ui_state.get("cloud_agent_research_settings_feedback"):
            st.success(feedback)

        st.caption("Research Provider Key")
        st.caption(
            f"{research_provider_labels.get(research_provider, research_provider)} API key: "
            f"{'configured' if selected_provider_metadata.get('api_key_configured') else 'not configured'}"
        )
        research_api_key_state_key = f"cloud_agent_research_api_key_{research_provider}"
        st.text_input(
            "Research API Key", type="password", key=research_api_key_state_key
        )
        remove_research_key = bool(
            hasattr(st, "checkbox")
            and st.checkbox(
                "Remove stored research API key",
                key=f"cloud_agent_research_remove_key_{research_provider}",
            )
        )
        st.button(
            "Save Research API Key",
            key="cloud_agent_save_research_api_key",
            on_click=_submit_research_api_key,
            args=(research_provider,),
            kwargs={"remove": remove_research_key},
        )
        if feedback := ui_state.get("cloud_agent_research_key_feedback"):
            feedback_kind, feedback_message = feedback
            (st.success if feedback_kind == "success" else st.error)(feedback_message)

    if not include_tts:
        return

    if feedback := ui_state.get("cloud_agent_tts_settings_feedback"):
        st.success(feedback)
    st.caption("TTS Provider Settings")
    settings = {}
    secret_fields = set()
    clear_secret_fields = []
    for field in provider_metadata.get("settings", []):
        name = field["name"]
        if field["kind"] == "password":
            secret_fields.add(name)
            st.caption(
                f"{field['label']}: "
                f"{'configured' if field.get('configured') else 'not configured'}"
            )
            settings[name] = st.text_input(
                field["label"],
                type="password",
                key=f"cloud_tts_{provider}_{name}",
                on_change=_clear_tts_settings_feedback,
            )
            if hasattr(st, "checkbox") and st.checkbox(
                f"Remove stored key: {field['label']}",
                key=f"cloud_tts_remove_{provider}_{name}",
                on_change=_clear_tts_settings_feedback,
            ):
                clear_secret_fields.append(name)
        elif field["kind"] == "select":
            settings[name] = st.selectbox(
                field["label"],
                field.get("choices", []),
                key=f"cloud_tts_{provider}_{name}",
                on_change=_clear_tts_settings_feedback,
            )
        elif field["kind"] == "voice_list":
            settings[name] = st.text_input(
                field["label"],
                value=", ".join(field.get("value") or []),
                key=f"cloud_tts_{provider}_{name}",
                on_change=_clear_tts_settings_feedback,
            )
        else:
            settings[name] = st.text_input(
                field["label"],
                value=str(field.get("value") or ""),
                key=f"cloud_tts_{provider}_{name}",
                on_change=_clear_tts_settings_feedback,
            )
    if st.button("Save TTS Settings", key="cloud_agent_save_tts_settings"):
        try:
            _api(
                "PUT",
                f"tts/providers/{provider}/settings",
                json=_tts_settings_payload(
                    settings=settings,
                    secret_fields=secret_fields,
                    clear_secret_fields=clear_secret_fields,
                ),
            )
            verified, message = _verify_tts_settings_save(
                settings=settings,
                secret_fields=secret_fields,
                clear_secret_fields=clear_secret_fields,
                metadata=_api("GET", f"tts/providers/{provider}"),
            )
            if verified:
                ui_state["cloud_agent_tts_settings_feedback"] = message
                st.rerun()
            else:
                st.error(message)
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    tts_session_state = getattr(st, "session_state", {})
    if provider_metadata.get("requires_explicit_voice_refresh") and st.button(
        "Load Voices", key="cloud_agent_load_tts_voices"
    ):
        try:
            tts_session_state["cloud_agent_tts_voices"] = _api(
                "POST", f"tts/providers/{provider}/voices/refresh"
            )["voices"]
            st.rerun()
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))

    if feedback := ui_state.get("cloud_agent_defaults_feedback"):
        st.success(feedback)
    st.caption("Cloud Agent Defaults")
    speed = float(ui_state.get("cloud_agent_speed", 1.0))
    voice = str(ui_state.get("cloud_agent_voice", "") or "")
    if st.button(
        "Save TTS Provider & Voice Default",
        key="cloud_agent_save_voice_default",
    ):
        try:
            verified, message = _save_and_verify_cloud_agent_defaults(
                _cloud_agent_defaults_payload(
                    tts_provider=provider,
                    voice_id=voice,
                    voice_speed=speed,
                    custom_system_prompt=defaults["custom_system_prompt"],
                )
            )
            if verified:
                ui_state["cloud_agent_defaults_feedback"] = message
                st.rerun()
            else:
                st.error(message)
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    st.caption("Save the selected voice and Custom System Prompt for future jobs.")
    if st.button("Save All Defaults", key="cloud_agent_save_defaults"):
        try:
            verified, message = _save_and_verify_cloud_agent_defaults(
                _cloud_agent_defaults_payload(
                    tts_provider=provider,
                    voice_id=voice,
                    voice_speed=speed,
                    custom_system_prompt=str(
                        ui_state.get("cloud_agent_custom_system_prompt", "") or ""
                    ),
                )
            )
            if verified:
                ui_state["cloud_agent_defaults_feedback"] = message
                st.rerun()
            else:
                st.error(message)
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    if st.button("Reset Defaults", key="cloud_agent_reset_defaults"):
        try:
            _api("POST", "defaults/reset")
            for key in (
                "cloud_agent_provider",
                "cloud_agent_voice",
                "cloud_agent_speed",
                "cloud_agent_custom_system_prompt",
            ):
                ui_state.pop(key, None)
            st.rerun()
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))


def _render_generation_setup(
    *,
    ui_state,
    defaults,
    research_settings,
    research_provider_catalog,
    script,
    script_mode,
    research_provider,
    research_model,
):
    provider_catalog = _fallback_tts_catalog()
    if hasattr(st, "runtime"):
        try:
            provider_catalog = _load_tts_provider_catalog()
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    provider_labels = {item["id"]: item["label"] for item in provider_catalog}
    if ui_state.get("cloud_agent_provider") not in provider_labels:
        ui_state["cloud_agent_provider"] = next(iter(provider_labels), "")

    with st.container(key="cloud_agent_generation_setup_card", border=True):
        st.subheader("Generation setup")
        if script_mode == "Research Script":
            st.caption(f"Research provider · {research_provider}")
            st.caption(f"Model · {research_model}")
        provider = st.selectbox(
            "TTS Provider",
            list(provider_labels),
            format_func=lambda value: provider_labels[value],
            key="cloud_agent_provider",
            on_change=_clear_provider_feedback,
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
        saved_voice = str(ui_state.get("cloud_agent_voice", "") or "")
        if saved_voice and all(item["id"] != saved_voice for item in voice_options):
            voice_options = [*voice_options, {"id": saved_voice, "label": saved_voice}]
        voice_labels = {
            item["id"]: item.get("label", item["id"]) for item in voice_options
        }
        voice = st.selectbox(
            "Voice",
            list(voice_labels) or [""],
            format_func=lambda value: voice_labels.get(
                value, "Select a configured voice"
            ),
            key="cloud_agent_voice",
            on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
        )
        speed = st.number_input(
            "Speed",
            min_value=0.1,
            value=1.0,
            key="cloud_agent_speed",
            on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
        )
        with _advanced_settings_container():
            _render_advanced_settings(
                ui_state=ui_state,
                defaults=defaults,
                research_settings=research_settings,
                research_provider_catalog=research_provider_catalog,
                provider=provider,
                provider_metadata=provider_metadata,
            )
        prepared_voice = tts_session_state.get("cloud_agent_prepared_voice")
        voice_creation_status = st.empty()
        if st.button(
            "Create voice",
            key="cloud_agent_create_voice",
            icon=":material/audio_file:",
            width="stretch",
        ):
            if not script.strip():
                st.error("Script Editor is required before creating narration.")
            elif not voice.strip():
                st.error("Voice is required before creating narration.")
            else:
                try:
                    with voice_creation_status.container():
                        with st.spinner("กำลังสร้างเสียง..."):
                            prepared_voice = _prepare_draft_voice(
                                script=script,
                                tts_provider=provider,
                                voice_id=voice,
                                voice_speed=speed,
                            )
                    tts_session_state["cloud_agent_prepared_voice"] = {
                        **prepared_voice,
                        "script": script,
                        "tts_provider": provider,
                        "voice_id": voice,
                        "voice_speed": speed,
                    }
                    st.rerun()
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
        if _prepared_voice_matches(
            prepared_voice,
            script=script,
            provider=provider,
            voice=voice,
            speed=speed,
        ):
            st.caption(
                "Prepared narration is ready and will be reused when this job starts."
            )
            try:
                st.markdown("**Audio preview**")
                st.audio(
                    _prepared_voice_audio(prepared_voice["fingerprint"]),
                    format="audio/mpeg",
                )
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
        return _GenerationSelection(
            provider=provider,
            voice=voice,
            speed=float(speed),
            prepared_voice=prepared_voice,
        )


def _production_draft_is_current(*, brief, script, master_prompt, ui_state):
    clip_plan = ui_state.get("cloud_agent_clip_plan")
    try:
        plan_target_words = int((clip_plan or {}).get("target_words"))
    except (TypeError, ValueError):
        plan_target_words = 0
    return bool(
        script
        and master_prompt.strip()
        and clip_plan
        and str(ui_state.get("cloud_agent_draft_script", "")).strip() == script
        and plan_target_words == brief.words
    )


def _prepare_production_draft(*, brief, script, master_prompt, ui_state):
    normalized_script = str(script or "").strip()
    if _production_draft_is_current(
        brief=brief,
        script=normalized_script,
        master_prompt=master_prompt,
        ui_state=ui_state,
    ):
        return {
            "script": normalized_script,
            "master_prompt": master_prompt,
            "clip_plan": ui_state["cloud_agent_clip_plan"],
            "research_draft_id": str(
                ui_state.get("cloud_agent_research_draft_id", "") or ""
            ),
            "pending_state": None,
        }

    with st.spinner("กำลังเตรียมสคริปต์และแผนการผลิต..."):
        if normalized_script:
            draft = _prepare_draft(
                subject=brief.subject,
                language=brief.language,
                target_words=brief.words,
                script=normalized_script,
                custom_system_prompt=brief.custom_system_prompt,
            )
            retain_research = (
                str(draft.get("script", "")).strip()
                == str(ui_state.get("cloud_agent_draft_script", "")).strip()
            )
            research_draft_id = (
                str(ui_state.get("cloud_agent_research_draft_id", "") or "")
                if retain_research
                else ""
            )
        elif brief.script_mode == "Research Script":
            source_count = _research_url_row_count(
                ui_state.get("cloud_agent_research_source_url_count", 1)
            )
            source_urls = _research_source_urls(
                [
                    ui_state.get(f"cloud_agent_research_source_url_{index}", "")
                    for index in range(1, source_count + 1)
                ]
            )
            draft = _prepare_research_draft(
                subject=brief.subject,
                language=brief.language,
                target_words=brief.words,
                provider=brief.research_provider,
                model_choice=brief.research_model,
                custom_model_id=_research_custom_model_id(
                    brief.research_provider, ui_state
                ),
                source_urls=source_urls,
                custom_system_prompt=str(
                    ui_state.get("cloud_agent_research_custom_system_prompt", "") or ""
                ),
                allow_citations=bool(
                    ui_state.get("cloud_agent_research_allow_citations", False)
                ),
            )
            research_draft_id = str(draft["research_draft_id"])
        else:
            draft = _prepare_draft(
                subject=brief.subject,
                language=brief.language,
                target_words=brief.words,
                script="",
                custom_system_prompt=brief.custom_system_prompt,
            )
            research_draft_id = ""

    research_sources = []
    research_accounting = {}
    if research_draft_id:
        research_sources = list(
            draft.get("sources")
            if "sources" in draft
            else ui_state.get("cloud_agent_research_sources") or []
        )
        research_accounting = dict(
            draft.get("accounting")
            if "accounting" in draft
            else ui_state.get("cloud_agent_research_accounting") or {}
        )
    pending_state = {
        "script": str(draft["script"]).strip(),
        "master_prompt": str(draft["master_prompt"]).strip(),
        "clip_plan": draft["clip_plan"],
        "research_draft_id": research_draft_id,
        "research_sources": research_sources,
        "research_accounting": research_accounting,
    }

    return {
        **pending_state,
        "pending_state": pending_state,
    }


def _apply_pending_production_draft(ui_state):
    pending = ui_state.pop("cloud_agent_pending_production_draft", None)
    if not isinstance(pending, dict):
        return
    _store_draft(pending)
    research_draft_id = str(pending.get("research_draft_id", "") or "")
    if research_draft_id:
        ui_state["cloud_agent_research_draft_id"] = research_draft_id
        ui_state["cloud_agent_research_sources"] = list(
            pending.get("research_sources") or []
        )
        ui_state["cloud_agent_research_accounting"] = dict(
            pending.get("research_accounting") or {}
        )


def _render_start_action(*, brief, script, master_prompt, generation, ui_state):
    if st.button(
        "Continue to production",
        key="cloud_agent_start",
        type="primary",
        icon=":material/arrow_forward:",
        icon_position="right",
        width="stretch",
    ):
        provider = generation.provider
        voice = generation.voice
        speed = generation.speed
        prepared_voice = generation.prepared_voice
        if not brief.subject.strip():
            st.error("Video Subject is required before starting the job.")
        elif not voice.strip():
            st.error("Voice is required before starting the job.")
        else:
            try:
                draft = _prepare_production_draft(
                    brief=brief,
                    script=script,
                    master_prompt=master_prompt,
                    ui_state=ui_state,
                )
                resolved_script = draft["script"]
                job = _start_and_store_job(
                    {
                        "subject": brief.subject,
                        "target_words": brief.words,
                        "language": brief.language,
                        "script": resolved_script,
                        "master_prompt": draft["master_prompt"],
                        "clip_plan": draft["clip_plan"],
                        "tts_provider": provider,
                        "voice_id": voice,
                        "voice_speed": speed,
                        "research_draft_id": draft["research_draft_id"],
                        "prepared_voice_fingerprint": (
                            str(prepared_voice.get("fingerprint") or "")
                            if _prepared_voice_matches(
                                prepared_voice,
                                script=resolved_script,
                                provider=provider,
                                voice=voice,
                                speed=speed,
                            )
                            else ""
                        ),
                    }
                )
                if message := _job_error_message(job):
                    st.error(message)
                if draft["pending_state"]:
                    ui_state["cloud_agent_pending_production_draft"] = draft[
                        "pending_state"
                    ]
                    rerun = getattr(st, "rerun", None)
                    if callable(rerun):
                        rerun()
                return job
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    return None


def render_cloud_agent_panel():
    ui_state = getattr(st, "session_state", {})
    _apply_pending_production_draft(ui_state)
    defaults = {
        "tts_provider": "azure-tts-v1",
        "voice_id": "",
        "voice_speed": 1.0,
        "custom_system_prompt": "",
    }
    if hasattr(st, "runtime"):
        try:
            defaults.update(_load_cloud_agent_defaults())
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    research_settings = {
        "enabled": True,
        "provider": "openrouter",
        "openrouter_model": "openai/gpt-5.6-sol-pro",
        "openrouter_custom_model_id": "openai/gpt-5.6-sol-pro",
        "aihubmix_model": "gpt-5.6-sol",
        "aihubmix_custom_model_id": "gpt-5.6-sol",
        "custom_system_prompt": "",
    }
    research_provider_catalog = _fallback_research_provider_catalog()
    if hasattr(st, "runtime"):
        try:
            research_settings.update(_load_research_settings())
            research_provider_catalog = _load_research_provider_catalog()
        except requests.RequestException:
            research_settings["enabled"] = False
    ui_state.setdefault("cloud_agent_provider", defaults["tts_provider"])
    ui_state.setdefault("cloud_agent_voice", defaults["voice_id"])
    ui_state.setdefault("cloud_agent_speed", defaults["voice_speed"])
    ui_state.setdefault(
        "cloud_agent_custom_system_prompt", defaults["custom_system_prompt"]
    )
    ui_state.setdefault("cloud_agent_script_mode", "Standard Script")
    prepared_voice = ui_state.get("cloud_agent_prepared_voice")
    script_ready = bool(str(ui_state.get("cloud_agent_draft_script") or "").strip())
    prepared_voice_ready = _prepared_voice_matches(
        prepared_voice,
        script=str(ui_state.get("cloud_agent_script") or ""),
        provider=str(ui_state.get("cloud_agent_provider") or ""),
        voice=str(ui_state.get("cloud_agent_voice") or ""),
        speed=float(ui_state.get("cloud_agent_speed") or 1.0),
    )
    job_snapshot = dict(ui_state.get("cloud_agent_job_snapshot") or {})
    if (
        hasattr(st, "runtime")
        and not str(ui_state.get("cloud_agent_job_id") or "").strip()
    ):
        try:
            job_snapshot = _restore_latest_job_if_needed(ui_state)
        except requests.RequestException:
            pass

    workflow_slot = st.container(key="cloud_agent_workflow_slot")
    with st.container(key="cloud_agent_workspace"):
        workspace = st.columns([1.85, 1], gap="large", vertical_alignment="top")
        with workspace[0]:
            brief = _render_video_brief(
                ui_state=ui_state,
                defaults=defaults,
                research_settings=research_settings,
                research_provider_catalog=research_provider_catalog,
            )
            script, master_prompt = _render_script_editor(
                brief=brief, ui_state=ui_state
            )
        with workspace[1]:
            generation = _render_generation_setup(
                ui_state=ui_state,
                defaults=defaults,
                research_settings=research_settings,
                research_provider_catalog=research_provider_catalog,
                script=script,
                script_mode=brief.script_mode,
                research_provider=brief.research_provider,
                research_model=brief.research_model,
            )
            started_job = _render_start_action(
                brief=brief,
                script=script,
                master_prompt=master_prompt,
                generation=generation,
                ui_state=ui_state,
            )
            if started_job is not None:
                job_snapshot = dict(ui_state.get("cloud_agent_job_snapshot") or {})
    production_status_slot = st.container(key="cloud_agent_production_status_slot")
    with st.expander("Job controls", expanded=False):
        readiness_controls = st.columns(2)
        for service, column in (
            ("google-flow", readiness_controls[0]),
            ("canva", readiness_controls[1]),
        ):
            label = "Google Flow" if service == "google-flow" else "Canva"
            if column.button(label, key=f"{service}-check"):
                try:
                    _api(
                        "POST",
                        f"sessions/{service}/check",
                        timeout=SESSION_CHECK_TIMEOUT_SECONDS,
                    )
                    st.caption(f"{label} readiness check completed.")
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
            if column.button("Open Browser", key=f"{service}-open"):
                try:
                    column.link_button("Open Browser", _open_browser_url(service))
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))

        if not str(ui_state.get("cloud_agent_job_lookup_id") or "").strip():
            ui_state["cloud_agent_job_lookup_id"] = str(
                ui_state.get("cloud_agent_job_id") or ""
            )
        job_id = st.text_input("Job ID", key="cloud_agent_job_lookup_id")
        selected_job_id = _selected_job_id(ui_state, job_id)
        action_controls = st.columns(4)
        for action, column in zip(
            ("Pause", "Resume", "Retry", "Cancel"), action_controls
        ):
            if (
                column.button(
                    action,
                    key=f"cloud_agent_{action.lower()}",
                    disabled=not bool(selected_job_id),
                )
                and selected_job_id
            ):
                try:
                    job = _api("POST", f"jobs/{selected_job_id}/{action.lower()}")
                    _store_job_snapshot(job)
                    job_snapshot = dict(ui_state.get("cloud_agent_job_snapshot") or {})
                    if action == "Retry":
                        st.caption(
                            "Flow failed before generation. Existing narration will be reused."
                        )
                    if message := _job_error_message(job):
                        st.error(message)
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
        if (
            st.button(
                "Load job",
                key="cloud_agent_load_job",
                disabled=not bool(selected_job_id),
            )
            and selected_job_id
        ):
            try:
                job = _api("GET", f"jobs/{selected_job_id}")
                _store_job_snapshot(job)
                job_snapshot = dict(ui_state.get("cloud_agent_job_snapshot") or {})
                if message := _job_error_message(job):
                    st.error(message)
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
        st.caption(
            "Narration Too Long: shorten script; reduce Target Words; increase Voice Rate"
        )
    with workflow_slot:
        cloud_agent_ui.render_workflow_rail(
            cloud_agent_ui.derive_workflow_step(
                script_ready,
                prepared_voice_ready,
                job_snapshot,
            )
        )
    with production_status_slot:
        _render_event_driven_production_status(
            script_ready=script_ready,
            prepared_voice_ready=prepared_voice_ready,
            ui_state=ui_state,
        )
