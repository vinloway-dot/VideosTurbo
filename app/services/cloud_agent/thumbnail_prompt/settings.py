"""Platform-neutral Thumbnail Prompt settings boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError


_REQUIRED_POSIX_CAPABILITIES = ("O_DIRECTORY", "O_NOFOLLOW")
_PLATFORM_SUPPORTED = os.name == "posix" and all(
    hasattr(os, capability) for capability in _REQUIRED_POSIX_CAPABILITIES
)


def _platform_unsupported_error() -> ThumbnailPromptError:
    return ThumbnailPromptError(
        "THUMBNAIL_PROMPT_PLATFORM_UNSUPPORTED",
        "Thumbnail Prompt settings are unavailable on this platform.",
    )


def ensure_thumbnail_prompt_platform_supported() -> None:
    """Fail closed before any Thumbnail Prompt filesystem or provider work."""
    if not _PLATFORM_SUPPORTED:
        raise _platform_unsupported_error()


if _PLATFORM_SUPPORTED:
    from app.services.cloud_agent.thumbnail_prompt._settings_posix import (  # noqa: F401
        ThumbnailPromptSettingsService,
        fcntl,
    )
else:

    class ThumbnailPromptSettingsService:
        """Fail-closed placeholder on platforms without the POSIX backend."""

        DEFAULT_PROVIDER_ID = "aihubmix"
        KEY_NAMES = {
            "aihubmix": "aihubmix_api_key",
            "openrouter": "openrouter_api_key",
        }

        def __init__(self, *, settings_path: Path) -> None:
            self._settings_path = Path(settings_path)

        @property
        def settings_path(self) -> Path:
            return self._settings_path

        @staticmethod
        def _unsupported(*_args, **_kwargs) -> NoReturn:
            raise _platform_unsupported_error()

        list_providers = _unsupported
        get_provider = _unsupported
        get_settings = _unsupported
        get_configured_provider_id = _unsupported
        update_settings = _unsupported
        set_api_key = _unsupported
        remove_api_key = _unsupported
        get_api_key_for_generation = _unsupported
        resolve_model = _unsupported
        get_base_url_for_generation = _unsupported
        get_generation_snapshot = _unsupported


__all__ = [
    "ThumbnailPromptSettingsService",
    "ensure_thumbnail_prompt_platform_supported",
]
