"""Typed errors owned by the Thumbnail Prompt subsystem."""


class ThumbnailPromptError(ValueError):
    """Raised when Thumbnail Prompt settings are invalid."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "THUMBNAIL_PROMPT_SETTINGS_INVALID").strip()
        self.detail = str(detail or "").strip()
        super().__init__(self.detail or self.code)
