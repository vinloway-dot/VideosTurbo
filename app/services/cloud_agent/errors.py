"""Typed errors for Cloud Agent workflow boundaries."""


class MediaValidationError(ValueError):
    """Raised when a produced media artifact fails validation."""


class NarrationTooLongError(MediaValidationError):
    """Raised before Flow when six clips would require excessive slowing."""

    error_code = "NARRATION_TOO_LONG_FOR_SIX_CLIP"


class HumanRequiredError(RuntimeError):
    """Raised when workflow progress requires manual human recovery."""
