"""Typed errors for Cloud Agent workflow boundaries."""


class MediaValidationError(ValueError):
    """Raised when a produced media artifact fails validation."""


class FlowWorkspaceVerificationError(MediaValidationError):
    """Raised when the shared Flow workspace state cannot be proven safe."""


class FlowGenerationTimeoutError(FlowWorkspaceVerificationError):
    """Raised when a paid Flow batch needs archive reconciliation after timeout."""


class FlowArchiveValidationError(MediaValidationError):
    """Raised when a Flow bulk-download archive is unsafe or incomplete."""


class FlowBatchIncompleteError(MediaValidationError):
    """Raised when Flow exposes a terminal failed output in a six-clip batch."""

    def __init__(self, *, completed_count: int, failed_count: int):
        self.completed_count = int(completed_count)
        self.failed_count = int(failed_count)
        super().__init__(
            "Google Flow batch ended with "
            f"{self.completed_count} completed and {self.failed_count} failed clips"
        )


class NarrationTooLongError(MediaValidationError):
    """Raised before Flow when six clips would require excessive slowing."""

    error_code = "NARRATION_TOO_LONG_FOR_SIX_CLIP"


class HumanRequiredError(RuntimeError):
    """Raised when workflow progress requires manual human recovery."""


class PreFlowRetryEligibilityError(ValueError):
    """Raised when a failed TTS_READY job cannot safely re-enter Flow."""


class RecoveryBudgetExhausted(RuntimeError):
    """Raised before a recovery attempt could exceed its durable budget."""
