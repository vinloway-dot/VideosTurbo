"""Thin Streamlit controls for the Cloud Agent FastAPI API."""

import requests
import streamlit as st


API_PREFIX = "http://127.0.0.1:8080/api/v1/cloud-agent/"
API_TIMEOUT_SECONDS = 15
SESSION_CHECK_TIMEOUT_SECONDS = 45
DRAFT_TIMEOUT_SECONDS = 120
RESEARCH_PROVIDER_OPTIONS = [
    {"id": "openrouter", "label": "OpenRouter", "api_key_configured": False},
    {"id": "aihubmix", "label": "AIHubMix", "api_key_configured": False},
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


def _verify_tts_settings_save(*, settings, secret_fields, clear_secret_fields, metadata):
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


def _save_and_verify_research_settings(payload):
    saved = _api("PUT", "research/settings", json=payload)
    readback = _load_research_settings()
    if all(saved.get(name) == value and readback.get(name) == value for name, value in payload.items()):
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
        },
        timeout=DRAFT_TIMEOUT_SECONDS,
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
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _research_model_choice(provider, settings):
    provider_key = f"{provider}_model"
    return str(settings.get(provider_key, "") or "").strip()


def _research_custom_model_id(provider, settings):
    provider_key = f"{provider}_custom_model_id"
    return str(settings.get(provider_key, "") or "").strip()


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
    prior_script = str(st.session_state.get("cloud_agent_draft_script", "") or "").strip()
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
    st.caption(
        f"Research usage: {usage if usage else 'unavailable'}"
    )
    st.caption(
        f"Research cost (USD): {cost if cost not in (None, '') else 'unavailable'}"
    )


def _render_research_sources(sources):
    if not sources:
        return
    st.caption("Sources")
    for source in sources:
        title = str(source.get("title") or source.get("url") or "Untitled source")
        url = str(source.get("url") or "").strip()
        if hasattr(st, "markdown") and url:
            st.markdown(f"- [{title}]({url})")
        elif url:
            st.caption(f"{title}: {url}")
        else:
            st.caption(title)


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


def _store_draft(draft):
    _clear_research_state()
    st.session_state["cloud_agent_script"] = draft["script"]
    st.session_state["cloud_agent_master_prompt"] = draft["master_prompt"]
    st.session_state["cloud_agent_clip_plan"] = draft["clip_plan"]
    st.session_state["cloud_agent_draft_script"] = draft["script"]


def render_cloud_agent_panel():
    ui_state = getattr(st, "session_state", {})
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
    ui_state.setdefault("cloud_agent_provider", defaults["tts_provider"])
    ui_state.setdefault("cloud_agent_voice", defaults["voice_id"])
    ui_state.setdefault("cloud_agent_speed", defaults["voice_speed"])
    ui_state.setdefault(
        "cloud_agent_custom_system_prompt", defaults["custom_system_prompt"]
    )
    ui_state.setdefault("cloud_agent_script_mode", "Standard Script")
    st.subheader("Cloud Agent")
    subject = st.text_area(
        "Video Subject",
        key="cloud_agent_subject",
        height=68,
    )
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
    script_mode = "Standard Script"
    if hasattr(st, "radio"):
        script_mode = st.radio(
            "Script Creation Mode",
            ["Standard Script", "Research Script"],
            key="cloud_agent_script_mode",
        )
    if script_mode == "Standard Script":
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
    else:
        research_settings = {
            "provider": "openrouter",
            "openrouter_model": "openai/gpt-5.6-sol-pro",
            "openrouter_custom_model_id": "",
            "aihubmix_model": "gpt-5.6-sol",
            "aihubmix_custom_model_id": "",
            "custom_system_prompt": "",
        }
        research_provider_catalog = _fallback_research_provider_catalog()
        if hasattr(st, "runtime"):
            try:
                research_settings.update(_load_research_settings())
                research_provider_catalog = _load_research_provider_catalog()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
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
        research_provider_labels = {
            item["id"]: item["label"] for item in research_provider_catalog
        }
        if ui_state.get("cloud_agent_research_provider") not in research_provider_labels:
            ui_state["cloud_agent_research_provider"] = next(
                iter(research_provider_labels), "openrouter"
            )
        research_provider = st.selectbox(
            "Research Provider",
            list(research_provider_labels),
            format_func=lambda value: research_provider_labels[value],
            key="cloud_agent_research_provider",
        )
        selected_provider_metadata = next(
            (
                item
                for item in research_provider_catalog
                if item["id"] == research_provider
            ),
            {"api_key_configured": False},
        )
        with st.expander("Research Settings", expanded=False):
            openrouter_model = st.text_input(
                "OpenRouter Model",
                key="cloud_agent_research_openrouter_model",
            )
            openrouter_custom_model_id = st.text_input(
                "OpenRouter Custom Model ID",
                key="cloud_agent_research_openrouter_custom_model_id",
            )
            aihubmix_model = st.text_input(
                "AIHubMix Model",
                key="cloud_agent_research_aihubmix_model",
            )
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
                "Save Research Settings",
                key="cloud_agent_save_research_settings",
            ):
                try:
                    verified, message = _save_and_verify_research_settings(
                        {
                            "provider": research_provider,
                            "openrouter_model": openrouter_model,
                            "openrouter_custom_model_id": openrouter_custom_model_id,
                            "aihubmix_model": aihubmix_model,
                            "aihubmix_custom_model_id": aihubmix_custom_model_id,
                            "custom_system_prompt": research_custom_system_prompt,
                        }
                    )
                    if verified:
                        ui_state["cloud_agent_research_settings_feedback"] = message
                        st.rerun()
                    else:
                        st.error(message)
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
            if feedback := ui_state.get("cloud_agent_research_settings_feedback"):
                st.success(feedback)
        with st.expander("Research Provider Key", expanded=False):
            st.caption(
                f"{research_provider_labels.get(research_provider, research_provider)} API key: "
                f"{'configured' if selected_provider_metadata.get('api_key_configured') else 'not configured'}"
            )
            research_api_key = st.text_input(
                "Research API Key",
                type="password",
                key=f"cloud_agent_research_api_key_{research_provider}",
            )
            remove_research_key = bool(
                hasattr(st, "checkbox")
                and st.checkbox(
                    "Remove stored research API key",
                    key=f"cloud_agent_research_remove_key_{research_provider}",
                )
            )
            if st.button(
                "Save Research API Key",
                key="cloud_agent_save_research_api_key",
            ):
                try:
                    if remove_research_key:
                        _remove_research_api_key(research_provider)
                        ui_state["cloud_agent_research_key_feedback"] = (
                            "Research API key removed."
                        )
                        st.rerun()
                    elif _save_research_api_key(
                        research_provider, research_api_key
                    ) is None:
                        st.error(
                            "Enter a research API key, or explicitly remove the stored key."
                        )
                    else:
                        ui_state["cloud_agent_research_key_feedback"] = (
                            "Research API key saved."
                        )
                        st.rerun()
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
            if feedback := ui_state.get("cloud_agent_research_key_feedback"):
                st.success(feedback)
        source_urls_value = st.text_area(
            "Source URLs",
            key="cloud_agent_research_source_urls",
            height=100,
            help="Enter one URL per line.",
        )
        research_generation_status = st.empty()
        st.caption("Research generation may call the selected provider up to 3 rounds.")
        if st.button(
            "Generate Research Script",
            key="cloud_agent_generate_research_script",
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
                                    source_urls=_research_source_urls(source_urls_value),
                                    custom_system_prompt=research_custom_system_prompt,
                                )
                            )
                    st.rerun()
                except requests.HTTPError as exc:
                    error = _research_error_data(exc.response)
                    st.error(error["message"])
                    _render_research_accounting(error.get("accounting", {}))
                except requests.RequestException as exc:
                    st.error(_api_error_message(exc))
    script = st.text_area("Script Editor", key="cloud_agent_script")
    master_prompt = st.text_area(
        "View Master Prompt", key="cloud_agent_master_prompt", disabled=True
    )
    if script_mode == "Research Script":
        _render_research_accounting(ui_state.get("cloud_agent_research_accounting", {}))
        _render_research_sources(ui_state.get("cloud_agent_research_sources", []))
    if st.button("Refresh Draft", key="cloud_agent_refresh_draft"):
        script_for_refresh = str(st.session_state.get("cloud_agent_script", ""))
        if not script_for_refresh.strip():
            st.error("Script Editor is required before refreshing the draft.")
        else:
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
    provider_catalog = _fallback_tts_catalog()
    if hasattr(st, "runtime"):
        try:
            provider_catalog = _load_tts_provider_catalog()
        except requests.RequestException as exc:
            st.error(_api_error_message(exc))
    provider_labels = {item["id"]: item["label"] for item in provider_catalog}
    if ui_state.get("cloud_agent_provider") not in provider_labels:
        ui_state["cloud_agent_provider"] = next(iter(provider_labels), "")
    provider = st.selectbox(
        "TTS Provider", list(provider_labels),
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
    voice_labels = {item["id"]: item.get("label", item["id"]) for item in voice_options}
    voice = st.selectbox(
        "Voice", list(voice_labels) or [""],
        format_func=lambda value: voice_labels.get(value, "Select a configured voice"),
        key="cloud_agent_voice",
        on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
    )
    if feedback := ui_state.get("cloud_agent_tts_settings_feedback"):
        st.success(feedback)
    with st.expander("TTS Provider Settings", expanded=False):
        settings = {}
        secret_fields = set()
        clear_secret_fields = []
        for field in provider_metadata.get("settings", []):
            name = field["name"]
            if field["kind"] == "password":
                secret_fields.add(name)
                st.caption(f"{field['label']}: {'configured' if field.get('configured') else 'not configured'}")
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
        if provider_metadata.get("requires_explicit_voice_refresh") and st.button("Load Voices", key="cloud_agent_load_tts_voices"):
            try:
                tts_session_state["cloud_agent_tts_voices"] = _api("POST", f"tts/providers/{provider}/voices/refresh")["voices"]
                st.rerun()
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
    speed = st.number_input(
        "Speed",
        min_value=0.1,
        value=1.0,
        key="cloud_agent_speed",
        on_change=lambda: ui_state.pop("cloud_agent_defaults_feedback", None),
    )
    if feedback := ui_state.get("cloud_agent_defaults_feedback"):
        st.success(feedback)
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
    with st.expander("Cloud Agent Defaults", expanded=False):
        st.caption("Save the selected voice and Custom System Prompt for future jobs.")
        if st.button("Save All Defaults", key="cloud_agent_save_defaults"):
            try:
                verified, message = _save_and_verify_cloud_agent_defaults(
                    _cloud_agent_defaults_payload(
                        tts_provider=provider,
                        voice_id=voice,
                        voice_speed=speed,
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
    prepared_voice = tts_session_state.get("cloud_agent_prepared_voice")
    voice_creation_status = st.empty()
    if st.button("Create Voice", key="cloud_agent_create_voice"):
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
    if prepared_voice and all(
        prepared_voice.get(field) == value
        for field, value in (
            ("script", script),
            ("tts_provider", provider),
            ("voice_id", voice),
            ("voice_speed", speed),
        )
    ):
        st.caption("Prepared narration is ready and will be reused when this job starts.")
        if hasattr(st, "audio"):
            try:
                st.audio(_prepared_voice_audio(prepared_voice["fingerprint"]), format="audio/mpeg")
            except requests.RequestException as exc:
                st.error(_api_error_message(exc))
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
        research_draft_id = str(
            st.session_state.get("cloud_agent_research_draft_id", "") or ""
        )
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
                        research_draft_id=research_draft_id,
                        prepared_voice_fingerprint=(
                            prepared_voice["fingerprint"]
                            if prepared_voice
                            and all(
                                prepared_voice.get(field) == value
                                for field, value in (
                                    ("script", script),
                                    ("tts_provider", provider),
                                    ("voice_id", voice),
                                    ("voice_speed", speed),
                                )
                            )
                            else ""
                        ),
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
