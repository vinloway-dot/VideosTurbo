from pathlib import Path

from app.services import voice


ROOT_DIR = Path(__file__).parent.parent.parent
MAIN_SOURCE = (ROOT_DIR / "webui" / "Main.py").read_text(encoding="utf-8")

# Regression coverage for the current Google-recommended Gemini TTS model.

def test_gemini_tts_defaults_to_31_and_keeps_legacy_fallbacks():
    assert voice.GEMINI_TTS_DEFAULT_MODEL == "gemini-3.1-flash-tts-preview"
    assert voice.GEMINI_TTS_MODELS == (
        "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-preview-tts",
        "gemini-2.5-pro-preview-tts",
    )


def test_gemini_tts_runtime_uses_configured_model():
    assert 'config.app.get("gemini_tts_model", GEMINI_TTS_DEFAULT_MODEL)' in Path(
        voice.__file__
    ).read_text(encoding="utf-8")


def test_webui_exposes_gemini_tts_model_selector_and_preview_fingerprint():
    assert '"Gemini TTS Model"' in MAIN_SOURCE
    assert "options=voice.GEMINI_TTS_MODELS" in MAIN_SOURCE
    assert '"gemini_tts_model"' in MAIN_SOURCE
    assert '"model_id": config.app.get(' in MAIN_SOURCE
