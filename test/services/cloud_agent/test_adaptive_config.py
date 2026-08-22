import tomllib
from pathlib import Path

from app.config import config


def _load_example_app_config() -> dict:
    config_path = Path(__file__).resolve().parents[3] / "config.example.toml"
    return tomllib.loads(config_path.read_text(encoding="utf-8"))["app"]


def test_cloud_agent_v22_defaults_remove_fixed_tts_ceiling():
    defaults = config.CLOUD_AGENT_DEFAULTS

    assert defaults["cloud_agent_tts_min_duration_seconds"] == 1
    assert defaults["cloud_agent_canva_min_playback_speed"] == 0.85
    assert defaults["cloud_agent_final_duration_tolerance_seconds"] == 1.0
    assert "cloud_agent_tts_max_duration_seconds" not in defaults


def test_cloud_agent_example_config_matches_adaptive_timing_defaults():
    app_config = _load_example_app_config()

    assert app_config["cloud_agent_tts_min_duration_seconds"] == 1
    assert app_config["cloud_agent_canva_min_playback_speed"] == 0.85
    assert app_config["cloud_agent_final_duration_tolerance_seconds"] == 1.0
    assert "cloud_agent_tts_max_duration_seconds" not in app_config
