"""Typed errors for Cloud Agent workflow boundaries."""


class MediaValidationError(ValueError):
    """Raised when a produced media artifact fails validation."""


class FlowWorkspaceVerificationError(MediaValidationError):
    """Raised when the shared Flow workspace state cannot be proven safe."""


class FlowArchiveValidationError(MediaValidationError):
    """Raised when a Flow bulk-download archive is unsafe or incomplete."""


class NarrationTooLongError(MediaValidationError):
    """Raised before Flow when six clips would require excessive slowing."""

    error_code = "NARRATION_TOO_LONG_FOR_SIX_CLIP"


class HumanRequiredError(RuntimeError):
    """Raised when workflow progress requires manual human recovery."""


class PreFlowRetryEligibilityError(ValueError):
    """Raised when a failed TTS_READY job cannot safely re-enter Flow."""
