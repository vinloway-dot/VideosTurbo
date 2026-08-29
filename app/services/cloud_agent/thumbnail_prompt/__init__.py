"""Dedicated configuration boundary for Thumbnail Prompt providers."""

from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.models import (
    ThumbnailPromptProviderMetadata,
    ThumbnailPromptSettings,
    ThumbnailPromptSettingsPayload,
)
from app.services.cloud_agent.thumbnail_prompt.settings import ThumbnailPromptSettingsService

__all__ = [
    "ThumbnailPromptError",
    "ThumbnailPromptProviderMetadata",
    "ThumbnailPromptSettings",
    "ThumbnailPromptSettingsPayload",
    "ThumbnailPromptSettingsService",
]
