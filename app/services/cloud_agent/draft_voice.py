"""Durable, private draft narration artifacts for the Cloud Agent UI."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from app.models.cloud_agent import CloudDraftVoiceArtifact, CloudDraftVoiceRequest
from app.services import voice
from app.services.cloud_agent.errors import MediaValidationError


class DraftVoiceError(ValueError):
    """A requested prepared narration is absent, invalid, or could not be created."""


class DraftVoiceService:
    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def fingerprint(request: CloudDraftVoiceRequest) -> str:
        payload = json.dumps(
            {
                "script": request.script,
                "tts_provider": request.tts_provider,
                "voice_id": request.voice_id,
                "voice_speed": request.voice_speed,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, fingerprint: str) -> Path:
        if len(fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in fingerprint
        ):
            raise DraftVoiceError("prepared voice reference is invalid")
        return self.root / f"{fingerprint}.mp3"

    def prepare(self, request: CloudDraftVoiceRequest) -> CloudDraftVoiceArtifact:
        fingerprint = self.fingerprint(request)
        destination = self._path(fingerprint)
        if destination.is_file() and destination.stat().st_size > 0:
            return CloudDraftVoiceArtifact(fingerprint=fingerprint, reused=True)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        try:
            result = voice.tts(
                text=request.script,
                voice_name=request.voice_id,
                voice_rate=request.voice_speed,
                voice_file=str(temporary),
            )
        except Exception as exc:
            raise DraftVoiceError("draft voice generation failed") from exc

        if result is None or not temporary.is_file() or temporary.stat().st_size <= 0:
            temporary.unlink(missing_ok=True)
            raise DraftVoiceError("draft voice generation produced no audio")

        os.replace(temporary, destination)
        return CloudDraftVoiceArtifact(fingerprint=fingerprint, reused=False)

    def get(self, fingerprint: str) -> Path:
        path = self._path(fingerprint)
        if not path.is_file() or path.stat().st_size <= 0:
            raise DraftVoiceError("prepared voice is unavailable; create it again")
        return path

    def materialize(self, fingerprint: str, destination: Path) -> Path:
        source = self.get(fingerprint)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".prepared.tmp")
        shutil.copyfile(source, temporary)
        if temporary.stat().st_size <= 0:
            temporary.unlink(missing_ok=True)
            raise MediaValidationError("prepared voice copy is empty")
        os.replace(temporary, destination)
        return destination
