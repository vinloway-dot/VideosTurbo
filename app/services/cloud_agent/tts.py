from pathlib import Path

from app.models.cloud_agent import CloudJobRecord
from app.services import voice
from app.services.cloud_agent.errors import MediaValidationError


_EXPLICIT_VOICE_PROVIDER_PREFIXES = {
    "elevenlabs:": "elevenlabs",
    "gemini:": "gemini",
    "siliconflow:": "siliconflow",
    "mimo:": "mimo",
    "minimax:": "minimax",
    "chatterbox:": "chatterbox",
}


def _provider_family(provider: str) -> str | None:
    """Normalize only provider names that the existing voice router can identify."""
    normalized = str(provider or "").strip().lower().replace("_", "-")
    for family in _EXPLICIT_VOICE_PROVIDER_PREFIXES.values():
        if family in normalized:
            return family
    if "azure" in normalized or "edge" in normalized:
        return "azure"
    return None


def _voice_provider_family(voice_id: str) -> str:
    normalized = str(voice_id or "").strip().lower()
    for prefix, family in _EXPLICIT_VOICE_PROVIDER_PREFIXES.items():
        if normalized.startswith(prefix):
            return family

    # The existing voice.tts router uses unprefixed voice names for Azure/Edge
    # (including Azure v2), so keep the Cloud Agent adapter aligned with it.
    return "azure"


def _validate_provider_voice_consistency(job: CloudJobRecord) -> None:
    expected_family = _provider_family(job.tts_provider)
    if expected_family is None:
        # Older/custom callers may persist provider labels that are not part of
        # the voice router's public naming scheme. Do not invent a new registry
        # here; the existing voice.tts router remains authoritative.
        return

    actual_family = _voice_provider_family(job.voice_id)
    if expected_family != actual_family:
        raise ValueError(
            "tts_provider and voice_id refer to different TTS providers: "
            f"tts_provider={job.tts_provider!r}, voice_id={job.voice_id!r}"
        )


class ExistingVoiceTTSClient:
    """Thin Cloud Agent adapter around the repository's existing TTS router."""

    def generate(self, job: CloudJobRecord, output_path: Path) -> Path:
        _validate_provider_voice_consistency(job)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = voice.tts(
                text=job.script,
                voice_name=job.voice_id,
                voice_rate=job.voice_speed,
                voice_file=str(output_path),
            )
        except Exception as exc:
            raise MediaValidationError(f"TTS generation failed: {exc}") from exc

        if result is None:
            raise MediaValidationError("TTS generation failed: voice.tts returned no result")
        if not output_path.is_file():
            raise MediaValidationError(
                f"TTS generation did not produce canonical voice.mp3: {output_path}"
            )
        if output_path.stat().st_size <= 0:
            raise MediaValidationError(
                f"TTS generation produced an empty canonical voice.mp3: {output_path}"
            )

        # Duration policy deliberately stays out of this adapter. The workflow
        # validates the canonical file with ffprobe, keeps decimal duration, and
        # applies Adaptive Six-Clip timing before any Google Flow credit is used.
        return output_path
