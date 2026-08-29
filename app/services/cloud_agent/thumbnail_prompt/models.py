from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ThumbnailPromptProviderMetadata(BaseModel):
    id: str
    label: str
    models: list[str]
    default_model: str
    custom_model_id: str
    base_url: str
    api_key_configured: bool


class ThumbnailPromptSettingsPayload(BaseModel):
    master_prompt: str = Field(min_length=1, max_length=8000)
    default_provider: Literal["aihubmix", "openrouter"]
    aihubmix_model: str
    aihubmix_custom_model_id: str = Field(default="", max_length=256)
    openrouter_model: str
    openrouter_custom_model_id: str = Field(default="", max_length=256)

    @field_validator(
        "master_prompt",
        "aihubmix_model",
        "aihubmix_custom_model_id",
        "openrouter_model",
        "openrouter_custom_model_id",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return str(value or "").strip()


class ThumbnailPromptSettings(BaseModel):
    """Readable settings state; the global prompt may be unconfigured."""

    master_prompt: str = Field(default="", max_length=8000)
    default_provider: Literal["aihubmix", "openrouter"]
    aihubmix_model: str
    aihubmix_custom_model_id: str = Field(default="", max_length=256)
    openrouter_model: str
    openrouter_custom_model_id: str = Field(default="", max_length=256)
