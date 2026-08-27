import ast
from contextlib import nullcontext
from pathlib import Path

import pytest
import requests

from webui import cloud_agent


UI_SOURCE = Path("webui/cloud_agent.py")
MAIN_SOURCE = Path("webui/Main.py")


class ModeStreamlit:
    def __init__(self):
        self.calls = []

    def segmented_control(self, label, options, **kwargs):
        self.calls.append((label, list(options), kwargs))
        return "Research Script"


def test_script_mode_uses_approved_segmented_control_and_retained_key(monkeypatch):
    fake = ModeStreamlit()
    monkeypatch.setattr(cloud_agent, "st", fake)

    selected = cloud_agent._render_script_mode_control(
        ["Standard Script", "Research Script"],
        "Standard Script",
    )

    assert selected == "Research Script"
    assert fake.calls == [
        (
            "Script creation mode",
            ["Standard Script", "Research Script"],
            {
                "default": "Standard Script",
                "key": "cloud_agent_script_mode",
                "width": "stretch",
                "label_visibility": "collapsed",
            },
        )
    ]


def test_empty_script_editor_stays_collapsed_for_safe_default_layout(monkeypatch):
    class Column:
        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, *_args, **_kwargs):
            return False

    class Streamlit:
        def __init__(self):
            self.expanders = []

        def container(self, **_kwargs):
            return nullcontext()

        def columns(self, *_args, **_kwargs):
            return [Column(), Column()]

        def expander(self, label, **kwargs):
            self.expanders.append((label, kwargs))
            return nullcontext()

        def text_area(self, *_args, **_kwargs):
            return ""

        def caption(self, *_args, **_kwargs):
            return None

    fake = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake)

    cloud_agent._render_script_editor(
        brief=cloud_agent._BriefSelection("", 130, "en-US", "Standard Script", ""),
        ui_state={},
    )

    assert fake.expanders[0] == ("Script editor", {"expanded": False})


class ExpanderStreamlit:
    def __init__(self):
        self.calls = []

    def expander(self, label, **kwargs):
        self.calls.append((label, kwargs))
        return nullcontext()


def test_advanced_settings_are_collapsed_behind_one_disclosure(monkeypatch):
    fake = ExpanderStreamlit()
    monkeypatch.setattr(cloud_agent, "st", fake)

    with cloud_agent._advanced_settings_container():
        pass

    assert fake.calls == [("Advanced settings", {"expanded": False})]


def test_cloud_agent_ui_is_a_thin_fastapi_client_with_required_controls_and_status():
    source = UI_SOURCE.read_text(encoding="utf-8")

    for label in (
        "Video subject",
        "Target words",
        "Language",
        "Generate script",
        "Script Editor",
        "View master prompt",
        "TTS Provider",
        "Voice",
        "Speed",
        "Google Flow",
        "Canva",
        "Open Browser",
        "Continue to production",
        "Pause",
        "Resume",
        "Retry",
        "Cancel",
        "Narration Too Long",
        "shorten script",
        "reduce Target Words",
        "increase Voice Rate",
    ):
        assert label in source
    assert "/api/v1/cloud-agent/" in source
    assert "sqlite3" not in source.lower()
    assert "PersistentBrowserManager" not in source
    for placeholder in (
        "job status/history",
        "final video",
        "measured narration duration",
        "Canva playback factor",
    ):
        assert placeholder not in source


def test_cloud_agent_video_subject_uses_compact_multiline_text_area():
    tree = ast.parse(UI_SOURCE.read_text(encoding="utf-8"))
    subject_assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "subject"
            for target in node.targets
        )
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "text_area"
    )
    call = subject_assignment.value

    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert call.func.attr == "text_area"
    assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords} == {
        "key": "cloud_agent_subject",
        "height": 82,
        "placeholder": "e.g., How to cook perfect rice every time",
    }


def test_create_voice_shows_an_animated_creating_status_above_the_button():
    source = UI_SOURCE.read_text(encoding="utf-8")

    assert "voice_creation_status = st.empty()" in source
    assert 'with st.spinner("กำลังสร้างเสียง...")' in source
    assert source.index("voice_creation_status = st.empty()") < source.index(
        '"Create voice"'
    )


def test_cloud_agent_loads_tts_provider_metadata_through_fastapi(monkeypatch):
    calls = []

    def api(method, path, **_kwargs):
        calls.append((method, path))
        return [{"id": "elevenlabs", "label": "ElevenLabs TTS"}]

    monkeypatch.setattr(cloud_agent, "_api", api)

    assert cloud_agent._load_tts_provider_catalog() == [
        {"id": "elevenlabs", "label": "ElevenLabs TTS"}
    ]
    assert calls == [("GET", "tts/providers")]


def test_cloud_agent_tts_settings_payload_omits_blank_secret_unless_confirmed():
    assert cloud_agent._tts_settings_payload(
        settings={"api_key": "", "model_id": "eleven_v3"},
        secret_fields={"api_key"},
        clear_secret_fields=[],
    ) == {"settings": {"model_id": "eleven_v3"}, "clear_secret_fields": []}

    assert cloud_agent._tts_settings_payload(
        settings={"api_key": ""},
        secret_fields={"api_key"},
        clear_secret_fields=["api_key"],
    ) == {"settings": {}, "clear_secret_fields": ["api_key"]}


def test_tts_settings_save_verification_requires_the_server_readback_to_match():
    metadata = {
        "settings": [
            {
                "name": "api_key",
                "label": "ElevenLabs API Key",
                "kind": "password",
                "configured": True,
            },
            {
                "name": "model_id",
                "label": "ElevenLabs Model",
                "kind": "select",
                "value": "eleven_v3",
            },
        ]
    }

    assert cloud_agent._verify_tts_settings_save(
        settings={"api_key": "new-secret", "model_id": "eleven_v3"},
        secret_fields={"api_key"},
        clear_secret_fields=[],
        metadata=metadata,
    ) == (
        True,
        "Saved and verified: ElevenLabs API Key configured; ElevenLabs Model = eleven_v3",
    )

    mismatched_metadata = {
        "settings": [
            {
                "name": "api_key",
                "label": "ElevenLabs API Key",
                "kind": "password",
                "configured": True,
            },
            {
                "name": "model_id",
                "label": "ElevenLabs Model",
                "kind": "select",
                "value": "eleven_v3",
            },
        ]
    }
    assert cloud_agent._verify_tts_settings_save(
        settings={"model_id": "eleven_flash_v2_5"},
        secret_fields={"api_key"},
        clear_secret_fields=[],
        metadata=mismatched_metadata,
    ) == (
        False,
        "Could not verify saved settings. Reload the provider settings and try again.",
    )


def test_cloud_agent_defaults_save_verification_requires_exact_readback():
    payload = {
        "tts_provider": "elevenlabs",
        "voice_id": "elevenlabs:voice-1",
        "voice_speed": 1.0,
        "custom_system_prompt": "Use a calm tone.",
    }

    assert cloud_agent._verify_cloud_agent_defaults_save(payload, payload) == (
        True,
        "Saved and verified.",
    )
    assert cloud_agent._verify_cloud_agent_defaults_save(
        payload,
        {**payload, "voice_id": "different-voice"},
    ) == (
        False,
        "Could not verify saved defaults. Reload the page and try again.",
    )


def test_cloud_agent_ui_offers_explicit_secret_removal_and_voice_refresh():
    source = UI_SOURCE.read_text(encoding="utf-8")

    assert "Remove stored key" in source
    assert "clear_secret_fields" in source
    assert '"cloud_agent_tts_voices"' in source


def test_cloud_agent_language_selector_reuses_the_main_script_auto_contract():
    assert cloud_agent.SCRIPT_LANGUAGE_OPTIONS == [
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


def test_cloud_agent_language_selector_formats_the_auto_empty_value(monkeypatch):
    class Column:
        def __init__(self, parent):
            self.parent = parent

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, *_args, **_kwargs):
            return False

        def link_button(self, *_args, **_kwargs):
            return None

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, *, format_func, **_kwargs):
            if _label == "Language":
                self.parent.formatted_language_options = [
                    format_func(option) for option in options
                ]
            return options[0]

    class Streamlit:
        def __init__(self):
            self.session_state = {}
            self.formatted_language_options = []

        def subheader(self, *_args, **_kwargs):
            return None

        def text_input(self, _label, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, *, format_func, **_kwargs):
            if _label == "Language":
                self.formatted_language_options = [
                    format_func(option) for option in options
                ]
            return options[0]

        def expander(self, *_args, **_kwargs):
            return nullcontext()

        def button(self, *_args, **_kwargs):
            return False

        def columns(self, _count, **_kwargs):
            return [Column(self), Column(self), Column(self), Column(self)]

        def container(self, **_kwargs):
            return nullcontext()

        def segmented_control(self, _label, options, **_kwargs):
            return options[0]

        def text_area(self, *_args, **_kwargs):
            return ""

        def empty(self):
            return nullcontext()

        def caption(self, *_args, **_kwargs):
            return None

        def error(self, *_args, **_kwargs):
            return None

    fake_streamlit = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)

    cloud_agent.render_cloud_agent_panel()

    assert fake_streamlit.formatted_language_options[0] == (
        "Auto — detect from Video Subject"
    )


def test_cloud_agent_custom_system_prompt_is_hidden_by_default(monkeypatch):
    class Column:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, *_args, **_kwargs):
            return False

        def link_button(self, *_args, **_kwargs):
            return None

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

    class Streamlit:
        def __init__(self):
            self.session_state = {}
            self.expander_arguments = []

        def subheader(self, *_args, **_kwargs):
            return None

        def text_input(self, _label, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

        def expander(self, label, *, expanded):
            self.expander_arguments.append((label, expanded))
            return nullcontext()

        def text_area(self, *_args, **_kwargs):
            return ""

        def empty(self):
            return nullcontext()

        def button(self, *_args, **_kwargs):
            return False

        def columns(self, _count, **_kwargs):
            return [Column(), Column(), Column(), Column()]

        def container(self, **_kwargs):
            return nullcontext()

        def segmented_control(self, _label, options, **_kwargs):
            return options[0]

        def caption(self, *_args, **_kwargs):
            return None

        def error(self, *_args, **_kwargs):
            return None

    fake_streamlit = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)

    cloud_agent.render_cloud_agent_panel()

    assert ("Custom System Prompt", False) in fake_streamlit.expander_arguments


def test_main_renders_cloud_agent_without_the_retired_local_generation_flow():
    source = MAIN_SOURCE.read_text(encoding="utf-8")
    application = source.split("def _render_application():", maxsplit=1)[1]

    assert "from webui import cloud_agent" in source
    assert "cloud_agent.render_cloud_agent_panel" in application
    assert "_render_six_clip_video_settings" not in application
    assert "_render_audio_settings" not in application
    assert "_render_subtitle_settings" not in application
    assert "_render_generation_controls" not in application


def test_main_source_has_no_retired_classic_video_generation_dependencies():
    source = MAIN_SOURCE.read_text(encoding="utf-8")

    assert "cloud_agent.render_cloud_agent_panel" in source
    for retired_symbol in (
        "_render_generation_controls",
        "_render_six_clip_video_settings",
        "local_video_materials_uploader",
        "stock_materials",
        "six_clip_plan",
        "six_clip_video_aspect_select",
    ):
        assert retired_symbol not in source


def test_cloud_agent_api_allows_a_canva_readiness_timeout_longer_than_the_provider_wait(
    monkeypatch,
):
    recorded = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"status": "READY"}}

    def request(*_args, **kwargs):
        recorded.update(kwargs)
        return Response()

    monkeypatch.setattr(cloud_agent.requests, "request", request)

    assert cloud_agent._api(
        "POST", "sessions/canva/check", timeout=45
    ) == {"status": "READY"}
    assert recorded["timeout"] >= 30


def test_cloud_agent_ui_formats_persisted_job_failure_for_the_operator():
    assert cloud_agent._job_error_message(
        {
            "error_code": "CANVA_UI_VERIFICATION_FAILED",
            "error_message": "Canva Uploads control cannot be verified",
        }
    ) == "CANVA_UI_VERIFICATION_FAILED: Canva Uploads control cannot be verified"


def test_successful_start_stores_job_for_production_status(monkeypatch):
    session_state = {}
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)
    monkeypatch.setattr(
        cloud_agent,
        "_start_job",
        lambda **kwargs: {
            "id": "job-123",
            "status": "QUEUED",
            "checkpoint": "NONE",
            "current_step": "queued",
            "progress": 0,
        },
    )

    cloud_agent._start_and_store_job(
        {
            "subject": "Rice",
            "target_words": 130,
            "language": "en-US",
            "script": "Ready narration",
            "master_prompt": "Ready master prompt",
            "clip_plan": {"target_words": 130, "segments": [{"index": 1}] * 6},
            "tts_provider": "elevenlabs",
            "voice_id": "voice-1",
            "voice_speed": 1.0,
            "research_draft_id": "",
            "prepared_voice_fingerprint": "",
        }
    )

    assert session_state["cloud_agent_job_id"] == "job-123"
    assert session_state["cloud_agent_job_snapshot"]["status"] == "QUEUED"


def test_pause_refreshes_snapshot_without_mutating_the_lookup_widget(monkeypatch):
    rendered_statuses = []

    class WidgetSessionState(dict):
        def __init__(self):
            super().__init__({"cloud_agent_job_lookup_id": "job-123"})
            self.instantiated_widget_keys = set()

        def __setitem__(self, key, value):
            if key in self.instantiated_widget_keys:
                raise RuntimeError(f"widget key mutated after instantiation: {key}")
            super().__setitem__(key, value)

    class Column:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def button(self, _label, *, key, **_kwargs):
            return key == "cloud_agent_pause"

        def link_button(self, *_args, **_kwargs):
            return None

    class Streamlit:
        def __init__(self):
            self.session_state = WidgetSessionState()

        def columns(self, _count, **_kwargs):
            return [Column(), Column(), Column(), Column()]

        def container(self, **_kwargs):
            return nullcontext()

        def button(self, *_args, **_kwargs):
            return False

        def expander(self, *_args, **_kwargs):
            return nullcontext()

        def text_input(self, _label, *, key, **_kwargs):
            self.session_state.instantiated_widget_keys.add(key)
            return self.session_state.get(key, "job-123")

        def caption(self, *_args, **_kwargs):
            return None

        def error(self, message):
            raise AssertionError(message)

    fake_streamlit = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)
    monkeypatch.setattr(
        cloud_agent,
        "_render_video_brief",
        lambda **_kwargs: cloud_agent._BriefSelection(
            "Rice", 130, "en-US", "Standard Script", ""
        ),
    )
    monkeypatch.setattr(
        cloud_agent,
        "_render_script_editor",
        lambda **_kwargs: ("Ready narration", "Ready master prompt"),
    )
    monkeypatch.setattr(
        cloud_agent,
        "_render_generation_setup",
        lambda **_kwargs: cloud_agent._GenerationSelection(
            "elevenlabs", "voice-1", 1.0, None
        ),
    )
    monkeypatch.setattr(
        cloud_agent.cloud_agent_ui, "render_workflow_rail", lambda *_args: None
    )
    monkeypatch.setattr(
        cloud_agent.cloud_agent_ui,
        "render_production_status",
        lambda stages, job: rendered_statuses.append((stages, job)),
    )
    monkeypatch.setattr(
        cloud_agent,
        "_api",
        lambda method, path, **_kwargs: {
            "id": "job-123",
            "status": "PAUSED",
            "checkpoint": "TTS_READY",
            "current_step": "paused",
            "progress": 40,
        }
        if (method, path) == ("POST", "jobs/job-123/pause")
        else pytest.fail(f"unexpected API call: {method} {path}"),
    )

    cloud_agent.render_cloud_agent_panel()

    assert fake_streamlit.session_state["cloud_agent_job_id"] == "job-123"
    assert fake_streamlit.session_state["cloud_agent_job_snapshot"]["status"] == "PAUSED"
    assert rendered_statuses[-1][1]["status"] == "PAUSED"


def test_job_snapshot_allow_lists_the_production_status_fields(monkeypatch):
    session_state = {}
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)

    cloud_agent._store_job_snapshot(
        {
            "id": "job-123",
            "status": "QUEUED",
            "checkpoint": "NONE",
            "current_step": "queued",
            "progress": 0,
            "api_key": "must-not-persist",
            "provider_response": {"secret": "must-not-persist"},
        }
    )

    assert session_state["cloud_agent_job_snapshot"] == {
        "id": "job-123",
        "status": "QUEUED",
        "checkpoint": "NONE",
        "current_step": "queued",
        "progress": 0,
    }


def test_prepared_voice_matches_only_its_current_generation_inputs():
    prepared_voice = {
        "fingerprint": "voice-fingerprint",
        "script": "Ready narration",
        "tts_provider": "elevenlabs",
        "voice_id": "voice-1",
        "voice_speed": 1.0,
    }

    assert cloud_agent._prepared_voice_matches(
        prepared_voice,
        script="Ready narration",
        provider="elevenlabs",
        voice="voice-1",
        speed=1.0,
    )
    assert not cloud_agent._prepared_voice_matches(
        prepared_voice,
        script="Edited narration",
        provider="elevenlabs",
        voice="voice-1",
        speed=1.0,
    )


def test_canva_check_timeout_is_shown_as_a_safe_webui_error(monkeypatch):
    class Column:
        def __init__(self, pressed_key=""):
            self.pressed_key = pressed_key

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, _label, *, key, **_kwargs):
            return key == self.pressed_key

        def link_button(self, *_args, **_kwargs):
            raise AssertionError("Open Browser must not run during a Canva check")

    class Streamlit:
        def __init__(self):
            self.errors = []

        def subheader(self, *_args, **_kwargs):
            return None

        def text_input(self, _label, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

        def expander(self, *_args, **_kwargs):
            return nullcontext()

        def text_area(self, *_args, **_kwargs):
            return ""

        def empty(self):
            return nullcontext()

        def button(self, *_args, **_kwargs):
            return False

        def columns(self, _count, **_kwargs):
            return [Column(), Column("canva-check"), Column(), Column()]

        def container(self, **_kwargs):
            return nullcontext()

        def json(self, *_args, **_kwargs):
            raise AssertionError("Canva timeout must not render a success result")

        def error(self, message):
            self.errors.append(message)

        def caption(self, *_args, **_kwargs):
            return None

        def info(self, *_args, **_kwargs):
            return None

    fake_streamlit = Streamlit()

    def request(*_args, **_kwargs):
        raise requests.ReadTimeout("Canva took longer than the UI request budget")

    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)
    monkeypatch.setattr(cloud_agent.requests, "request", request)
    monkeypatch.setattr(
        cloud_agent,
        "_render_video_brief",
        lambda **_kwargs: cloud_agent._BriefSelection("", 130, "", "Standard Script", ""),
    )

    cloud_agent.render_cloud_agent_panel()

    assert fake_streamlit.errors == ["Cloud Agent request could not be completed."]


def test_prepare_draft_calls_the_fastapi_draft_endpoint_with_editor_inputs(monkeypatch):
    recorded = {}

    def api(method, path, **kwargs):
        recorded.update(method=method, path=path, **kwargs)
        return {"script": "Draft", "master_prompt": "Prompt", "clip_plan": {}}

    monkeypatch.setattr(cloud_agent, "_api", api)

    assert cloud_agent._prepare_draft(
        subject="Why Saturn Has a Hexagon",
        language="English",
        target_words=130,
        script="Edited narration",
        custom_system_prompt="Use a documentary tone.",
    )["script"] == "Draft"
    assert recorded == {
        "method": "POST",
        "path": "draft",
        "json": {
            "subject": "Why Saturn Has a Hexagon",
            "language": "English",
            "target_words": 130,
            "script": "Edited narration",
            "custom_system_prompt": "Use a documentary tone.",
        },
        "timeout": 120,
    }


def test_google_flow_open_browser_uses_the_api_service_identifier(monkeypatch):
    recorded = {}

    def api(method, path, **kwargs):
        recorded.update(method=method, path=path, **kwargs)
        return {"url": "https://remote-browser.example"}

    monkeypatch.setattr(cloud_agent, "_api", api)

    assert cloud_agent._open_browser_url("google-flow") == "https://remote-browser.example"
    assert recorded == {
        "method": "GET",
        "path": "sessions/google_flow/open-browser",
    }


def test_start_job_sends_the_draft_clip_plan_required_by_the_api(monkeypatch):
    recorded = {}

    def api(method, path, **kwargs):
        recorded.update(method=method, path=path, **kwargs)
        return {"id": "job-123"}

    clip_plan = {"target_words": 130, "segments": [{"index": 1}] * 6}
    monkeypatch.setattr(cloud_agent, "_api", api)

    assert cloud_agent._start_job(
        subject="Why Saturn Has a Hexagon",
        target_words=130,
        language="English",
        script="Ready narration",
        master_prompt="Ready prompt",
        clip_plan=clip_plan,
        tts_provider="azure-tts-v1",
        voice_id="en-AU-NatashaNeural-Female",
        voice_speed=1.0,
        prepared_voice_fingerprint="a" * 64,
    ) == {"id": "job-123"}
    assert recorded["method"] == "POST"
    assert recorded["path"] == "jobs"
    assert recorded["json"]["clip_plan"] == clip_plan
    assert recorded["json"]["script"] == "Ready narration"
    assert recorded["json"]["prepared_voice_fingerprint"] == "a" * 64


def test_prepare_draft_voice_posts_the_full_script_and_selected_voice(monkeypatch):
    recorded = {}

    def api(method, path, **kwargs):
        recorded.update(method=method, path=path, **kwargs)
        return {"fingerprint": "f" * 64, "reused": False}

    monkeypatch.setattr(cloud_agent, "_api", api)

    assert cloud_agent._prepare_draft_voice(
        script="The entire narration.",
        tts_provider="elevenlabs",
        voice_id="elevenlabs:P9NVJuTccNIK9usP8iEI:001",
        voice_speed=1.0,
    )["fingerprint"] == "f" * 64
    assert recorded == {
        "method": "POST",
        "path": "draft/voice",
        "json": {
            "script": "The entire narration.",
            "tts_provider": "elevenlabs",
            "voice_id": "elevenlabs:P9NVJuTccNIK9usP8iEI:001",
            "voice_speed": 1.0,
        },
        "timeout": 120,
    }


def test_cloud_agent_defaults_payload_keeps_voice_and_custom_system_prompt_together():
    assert cloud_agent._cloud_agent_defaults_payload(
        tts_provider="elevenlabs",
        voice_id="elevenlabs:P9NVJuTccNIK9usP8iEI:001",
        voice_speed=1.1,
        custom_system_prompt="Write in a calm documentary tone.",
    ) == {
        "tts_provider": "elevenlabs",
        "voice_id": "elevenlabs:P9NVJuTccNIK9usP8iEI:001",
        "voice_speed": 1.1,
        "custom_system_prompt": "Write in a calm documentary tone.",
    }


def test_cloud_agent_ui_exposes_visible_individual_save_controls_for_voice_and_prompt():
    source = UI_SOURCE.read_text(encoding="utf-8")

    assert "Save TTS Provider & Voice Default" in source
    assert "Save Custom System Prompt" in source
    assert "Saved and verified" in source


def test_research_mode_offers_fastapi_only_controls_and_shared_editor_handoff():
    source = UI_SOURCE.read_text(encoding="utf-8")

    for label in (
        "Standard Script",
        "Research Script",
        "Source URLs",
        "Generate research script",
        "cloud_agent_research_sources",
    ):
        assert label in source
    assert "sqlite3" not in source.lower()
    assert "PersistentBrowserManager" not in source


def test_research_error_data_reads_safe_message_code_and_accounting():
    class Response:
        def json(self):
            return {
                "status": 422,
                "message": "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง",
                "data": {
                    "code": "URL_REQUIRED",
                    "accounting": {"provider_rounds": 0},
                },
            }

    assert cloud_agent._research_error_data(Response()) == {
        "message": "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง",
        "code": "URL_REQUIRED",
        "accounting": {"provider_rounds": 0},
    }


def test_research_settings_save_requires_exact_server_readback(monkeypatch):
    responses = iter(
        [
            {
                "provider": "openrouter",
                "openrouter_model": "openai/gpt-5.6-sol-pro",
                "openrouter_custom_model_id": "",
                "aihubmix_model": "gpt-5.6-sol",
                "aihubmix_custom_model_id": "",
                "custom_system_prompt": "Use source evidence first.",
            },
            {
                "provider": "aihubmix",
                "openrouter_model": "openai/gpt-5.6-sol-pro",
                "openrouter_custom_model_id": "",
                "aihubmix_model": "gpt-5.6-sol",
                "aihubmix_custom_model_id": "",
                "custom_system_prompt": "Use source evidence first.",
            },
        ]
    )

    def api(method, path, **_kwargs):
        assert (method, path) in {
            ("PUT", "research/settings"),
            ("GET", "research/settings"),
        }
        return next(responses)

    monkeypatch.setattr(cloud_agent, "_api", api)

    assert cloud_agent._save_and_verify_research_settings(
        {
            "provider": "openrouter",
            "openrouter_model": "openai/gpt-5.6-sol-pro",
            "openrouter_custom_model_id": "",
            "aihubmix_model": "gpt-5.6-sol",
            "aihubmix_custom_model_id": "",
            "custom_system_prompt": "Use source evidence first.",
        }
    ) == (
        False,
        "Could not verify saved research settings. Reload the page and try again.",
    )


def test_blank_research_key_is_not_sent_as_replacement():
    assert cloud_agent._research_key_payload("") is None
    assert cloud_agent._research_key_payload(" new-key ") == {"api_key": "new-key"}


def test_research_payload_forwards_citation_toggle_and_bounded_timeout(monkeypatch):
    recorded = {}

    def api(method, path, **kwargs):
        recorded.update(method=method, path=path, **kwargs)
        return {"research_draft_id": "draft-1"}

    monkeypatch.setattr(cloud_agent, "_api", api)
    cloud_agent._prepare_research_draft(
        subject="Topic",
        language="English",
        target_words=130,
        provider="openrouter",
        model_choice="openai/gpt-5.6-sol-pro",
        custom_model_id="",
        source_urls=["https://example.com/article"],
        custom_system_prompt="",
        allow_citations=True,
    )

    assert recorded["timeout"] >= 300
    assert recorded["json"]["allow_citations"] is True


def test_research_mode_and_url_row_helpers_enforce_explicit_ui_bounds():
    assert cloud_agent._research_mode_options(False) == ["Standard Script"]
    assert cloud_agent._research_mode_options(True) == [
        "Standard Script",
        "Research Script",
    ]
    assert cloud_agent._research_url_row_count(0) == 1
    assert cloud_agent._research_url_row_count(2) == 2
    assert cloud_agent._research_url_row_count(99) == 3
    assert cloud_agent._research_source_urls(
        [" https://one.example ", "", "https://two.example"]
    ) == ["https://one.example", "https://two.example"]


def test_research_model_options_come_from_provider_catalog():
    provider = cloud_agent._fallback_research_provider_catalog()[0]

    assert provider["models"] == ["openai/gpt-5.6-sol-pro", "custom"]
    assert provider["default_model"] == "openai/gpt-5.6-sol-pro"
    assert provider["custom_model_id"] == "openai/gpt-5.6-sol-pro"


def test_research_model_selection_reads_streamlit_widget_state():
    widget_state = {
        "cloud_agent_research_aihubmix_model": "gpt-5.6-sol",
        "cloud_agent_research_aihubmix_custom_model_id": "vendor/custom-model",
    }

    assert cloud_agent._research_model_choice("aihubmix", widget_state) == "gpt-5.6-sol"
    assert (
        cloud_agent._research_custom_model_id("aihubmix", widget_state)
        == "vendor/custom-model"
    )


def test_research_key_submit_removes_raw_secret_before_api_call(monkeypatch):
    state_key = "cloud_agent_research_api_key_openrouter"
    session_state = {state_key: "raw-secret"}
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)

    def save(provider, value):
        assert provider == "openrouter"
        assert value == "raw-secret"
        assert state_key not in session_state
        return {"api_key_configured": True}

    monkeypatch.setattr(cloud_agent, "_save_research_api_key", save)

    cloud_agent._submit_research_api_key("openrouter", remove=False)

    assert state_key not in session_state
    assert "raw-secret" not in repr(session_state)


def test_store_draft_clears_stale_research_provenance(monkeypatch):
    session_state = {
        "cloud_agent_research_draft_id": "draft-1",
        "cloud_agent_research_sources": [{"url": "https://example.com"}],
        "cloud_agent_research_accounting": {"provider_rounds": 2},
    }
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)

    cloud_agent._store_draft(
        {
            "script": "Standard narration",
            "master_prompt": "Standard prompt",
            "clip_plan": {"target_words": 130, "segments": [{"index": 1}] * 6},
        }
    )

    assert "cloud_agent_research_draft_id" not in session_state
    assert "cloud_agent_research_sources" not in session_state
    assert "cloud_agent_research_accounting" not in session_state


def test_edit_then_refresh_clears_research_association_but_keeps_shared_workflow(monkeypatch):
    session_state = {
        "cloud_agent_script": "Edited narration",
        "cloud_agent_draft_script": "Original research narration",
        "cloud_agent_research_draft_id": "draft-1",
        "cloud_agent_research_sources": [{"url": "https://example.com"}],
        "cloud_agent_research_accounting": {"provider_rounds": 2},
    }
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)

    cloud_agent._store_refreshed_draft(
        {
            "script": "Edited narration",
            "master_prompt": "Refreshed prompt",
            "clip_plan": {"target_words": 130, "segments": [{"index": 1}] * 6},
        }
    )

    assert "cloud_agent_research_draft_id" not in session_state
    assert session_state["cloud_agent_script"] == "Edited narration"
    assert session_state["cloud_agent_draft_script"] == "Edited narration"


def test_unchanged_refresh_retains_research_association(monkeypatch):
    session_state = {
        "cloud_agent_draft_script": "Research narration",
        "cloud_agent_research_draft_id": "draft-1",
        "cloud_agent_research_sources": [{"url": "https://example.com"}],
        "cloud_agent_research_accounting": {"provider_rounds": 2},
    }
    monkeypatch.setattr(cloud_agent.st, "session_state", session_state)

    cloud_agent._store_refreshed_draft(
        {
            "script": "Research narration",
            "master_prompt": "Research prompt",
            "clip_plan": {"target_words": 130, "segments": [{"index": 1}] * 6},
        }
    )

    assert session_state["cloud_agent_research_draft_id"] == "draft-1"
    assert session_state["cloud_agent_research_sources"] == [
        {"url": "https://example.com"}
    ]


def test_start_job_sends_optional_research_draft_id_when_present(monkeypatch):
    recorded = {}

    def api(method, path, **kwargs):
        recorded.update(method=method, path=path, **kwargs)
        return {"id": "job-123"}

    monkeypatch.setattr(cloud_agent, "_api", api)

    cloud_agent._start_job(
        subject="Research start",
        target_words=130,
        language="English",
        script="Ready narration",
        master_prompt="Ready prompt",
        clip_plan={"target_words": 130, "segments": [{"index": 1}] * 6},
        tts_provider="azure-tts-v1",
        voice_id="en-US-JennyNeural-Female",
        voice_speed=1.0,
        prepared_voice_fingerprint="f" * 64,
        research_draft_id="draft-1",
    )

    assert recorded["json"]["research_draft_id"] == "draft-1"


def test_research_failure_never_stores_draft(monkeypatch):
    class Response:
        def __init__(self):
            self.status_code = 422

        def json(self):
            return {
                "message": "กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง",
                "data": {
                    "code": "URL_REQUIRED",
                    "accounting": {"provider_rounds": 0},
                },
            }

    class Column:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, *_args, **_kwargs):
            return False

        def link_button(self, *_args, **_kwargs):
            return None

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

        def caption(self, *_args, **_kwargs):
            return None

    class Spinner:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class Streamlit:
        def __init__(self):
            self.session_state = {}
            self.errors = []
            self.captions = []
            self.radios = []
            self.checkboxes = []
            self.text_area_calls = []

        def subheader(self, *_args, **_kwargs):
            return None

        def text_input(self, _label, **kwargs):
            return self.session_state.get(kwargs.get("key", ""), kwargs.get("value", ""))

        def text_area(self, label, **kwargs):
            self.text_area_calls.append((label, kwargs))
            if label == "Video subject":
                return "Research-backed draft"
            if label == "Source URLs":
                return ""
            return self.session_state.get(kwargs.get("key", ""), "")

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

        def segmented_control(self, _label, options, **_kwargs):
            self.radios.append(tuple(options))
            return "Research Script"

        def expander(self, *_args, **_kwargs):
            return nullcontext()

        def button(self, label, **_kwargs):
            return label == "Generate research script"

        def columns(self, _count, **_kwargs):
            return [Column(), Column(), Column(), Column()]

        def empty(self):
            return self

        def container(self, **_kwargs):
            return nullcontext()

        def spinner(self, _label):
            return Spinner()

        def caption(self, message):
            self.captions.append(message)

        def error(self, message):
            self.errors.append(message)

        def success(self, *_args, **_kwargs):
            return None

        def info(self, *_args, **_kwargs):
            return None

        def warning(self, *_args, **_kwargs):
            return None

        def checkbox(self, label, **kwargs):
            self.checkboxes.append((label, kwargs))
            return False

        def link_button(self, *_args, **_kwargs):
            return None

        def json(self, *_args, **_kwargs):
            return None

        def rerun(self):
            raise AssertionError("rerun must not happen on research failure")

        def audio(self, *_args, **_kwargs):
            return None

    prepared = {}

    def prepare_research_draft(**kwargs):
        prepared.update(kwargs)
        error = requests.HTTPError("research failed")
        error.response = Response()
        raise error

    fake_streamlit = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)
    monkeypatch.setattr(cloud_agent, "_prepare_research_draft", prepare_research_draft)
    monkeypatch.setattr(
        cloud_agent,
        "_store_draft",
        lambda _draft: pytest.fail("research failure must preserve the existing editor"),
    )

    cloud_agent.render_cloud_agent_panel()

    assert fake_streamlit.errors == ["กรุณาใส่ URL อย่างน้อยหนึ่งแหล่ง"]
    assert (
        "อนุญาตให้ใส่อ้างอิงในสคริปต์",
        {"key": "cloud_agent_research_allow_citations", "value": False},
    ) in fake_streamlit.checkboxes
    assert prepared["allow_citations"] is False
    assert (
        "Script",
        {
                "key": "cloud_agent_script",
                "height": 120,
                "label_visibility": "collapsed",
        },
    ) in fake_streamlit.text_area_calls


def test_start_button_forwards_stored_research_draft_id(monkeypatch):
    recorded = {}
    rendered_statuses = []

    class Column:
        def __init__(self, pressed_key=""):
            self.pressed_key = pressed_key

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, _label, *, key, **_kwargs):
            return key == self.pressed_key

        def link_button(self, *_args, **_kwargs):
            return None

    class Streamlit:
        def __init__(self):
            self.session_state = {
                "cloud_agent_script": "Ready narration",
                "cloud_agent_draft_script": "Ready narration",
                "cloud_agent_master_prompt": "Ready prompt",
                "cloud_agent_clip_plan": {"target_words": 130, "segments": [{"index": 1}] * 6},
                "cloud_agent_research_draft_id": "draft-1",
                "cloud_agent_voice": "voice-1",
            }
            self.rendered_text_inputs = []

        def subheader(self, *_args, **_kwargs):
            return None

        def text_input(self, label, **kwargs):
            self.rendered_text_inputs.append(label)
            return self.session_state.get(kwargs.get("key", ""), kwargs.get("value", ""))

        def text_area(self, label, **kwargs):
            if label == "Video Subject":
                return "Research start"
            return self.session_state.get(kwargs.get("key", ""), "")

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

        def radio(self, _label, options, **_kwargs):
            return "Standard Script"

        def expander(self, *_args, **_kwargs):
            return nullcontext()

        def button(self, _label, *, key, **_kwargs):
            return key == "cloud_agent_start"

        def columns(self, _count, **_kwargs):
            return [Column(), Column(), Column(), Column()]

        def container(self, **_kwargs):
            return nullcontext()

        def empty(self):
            return nullcontext()

        def caption(self, *_args, **_kwargs):
            return None

        def error(self, message):
            raise AssertionError(message)

        def success(self, *_args, **_kwargs):
            return None

        def json(self, *_args, **_kwargs):
            return None

    def start_job(**kwargs):
        recorded.update(kwargs)
        return {
            "id": "job-123",
            "status": "QUEUED",
            "checkpoint": "NONE",
            "current_step": "queued",
            "progress": 0,
        }

    fake_streamlit = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)
    monkeypatch.setattr(cloud_agent, "_start_job", start_job)
    monkeypatch.setattr(
        cloud_agent,
        "_render_video_brief",
        lambda **_kwargs: cloud_agent._BriefSelection("Research start", 130, "", "Standard Script", ""),
    )
    monkeypatch.setattr(
        cloud_agent.cloud_agent_ui,
        "render_production_status",
        lambda stages, job: rendered_statuses.append((stages, job)),
    )

    cloud_agent.render_cloud_agent_panel()

    assert recorded["research_draft_id"] == "draft-1"
    assert rendered_statuses[-1][1]["status"] == "QUEUED"


def test_standard_mode_hides_research_only_controls(monkeypatch):
    class Column:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def subheader(self, *_args, **_kwargs):
            return None

        def button(self, *_args, **_kwargs):
            return False

        def link_button(self, *_args, **_kwargs):
            return None

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, _label, options, **_kwargs):
            return options[0]

    class Streamlit:
        def __init__(self):
            self.session_state = {}
            self.selectbox_labels = []
            self.text_input_labels = []
            self.text_area_labels = []
            self.expander_labels = []
            self.button_labels = []
            self.captions = []

        def subheader(self, *_args, **_kwargs):
            return None

        def text_input(self, label, **kwargs):
            self.text_input_labels.append(label)
            return self.session_state.get(kwargs.get("key", ""), kwargs.get("value", ""))

        def text_area(self, label, **kwargs):
            self.text_area_labels.append(label)
            return self.session_state.get(kwargs.get("key", ""), "")

        def number_input(self, _label, **kwargs):
            return kwargs["value"]

        def selectbox(self, label, options, **_kwargs):
            self.selectbox_labels.append(label)
            return options[0]

        def segmented_control(self, _label, options, **_kwargs):
            return "Standard Script"

        def expander(self, label, **_kwargs):
            self.expander_labels.append(label)
            return nullcontext()

        def button(self, label, **_kwargs):
            self.button_labels.append(label)
            return False

        def columns(self, _count, **_kwargs):
            return [Column(), Column(), Column(), Column()]

        def container(self, **_kwargs):
            return nullcontext()

        def empty(self):
            return nullcontext()

        def caption(self, message):
            self.captions.append(message)

        def error(self, *_args, **_kwargs):
            return None

        def success(self, *_args, **_kwargs):
            return None

    fake_streamlit = Streamlit()
    monkeypatch.setattr(cloud_agent, "st", fake_streamlit)

    cloud_agent.render_cloud_agent_panel()

    assert "Research Provider" not in fake_streamlit.selectbox_labels
    assert "Research API Key" not in fake_streamlit.text_input_labels
    assert "Source URLs" not in fake_streamlit.text_area_labels
    assert "Research Settings" not in fake_streamlit.expander_labels
    assert "Research Provider Key" not in fake_streamlit.expander_labels
    assert "Save Research Settings" not in fake_streamlit.button_labels
    assert "Save Research API Key" not in fake_streamlit.button_labels
    assert "Generate Research Script" not in fake_streamlit.button_labels
    assert "Research generation may call the selected provider up to 3 rounds." not in fake_streamlit.captions
