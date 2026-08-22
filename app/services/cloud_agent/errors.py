"""Typed errors for Cloud Agent workflow boundaries."""


class MediaValidationError(ValueError):
    """Raised when a produced media artifact fails validation."""


class HumanRequiredError(RuntimeError):
    """Raised when workflow progress requires manual human recovery."""
