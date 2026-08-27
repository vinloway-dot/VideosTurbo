from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ResearchDraftRequest(BaseModel):
    subject: str
    language: str = ""
    target_words: int = Field(default=130, ge=40, le=400)
    provider: Literal["openrouter", "aihubmix"]
    model_choice: str = Field(max_length=256)
    custom_model_id: str = Field(default="", max_length=256)
    source_urls: list[str] = Field(default_factory=list)
    custom_system_prompt: str = Field(default="", max_length=8000)

    @field_validator(
        "subject",
        "language",
        "model_choice",
        "custom_model_id",
        "custom_system_prompt",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def validate_urls_and_model(self):
        if not self.subject or not self.model_choice:
            raise ValueError("subject and model_choice must not be blank")
        return self


class ResearchUsageAccounting(BaseModel):
    provider: str
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
