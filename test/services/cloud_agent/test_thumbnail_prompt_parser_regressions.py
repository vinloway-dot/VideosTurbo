from types import SimpleNamespace

import pytest

from app.services.cloud_agent.thumbnail_prompt import service as thumbnail_prompt_module
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.service import ThumbnailPromptService


def _provider_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


@pytest.mark.parametrize(
    "completion",
    [
        "Solar flare, Alternative: eclipse",
        "Solar flare. Alternative: eclipse",
        "Solar flare—Alternative:eclipse",
        "Solar flare/Alternative:eclipse",
        "Solar flare • Alternative: eclipse",
        "Solar flare (Alternative: eclipse)",
        'Solar flare; "Alternative: eclipse"',
        "Solar flare；Alternative：eclipse",
        "Solar flare؛ Alternative: eclipse",
        "Alternative\u200b: eclipse",
        "Solar flare; Option\u200b 2:eclipse",
        "A:first concept;B:second concept",
        "1:3 first prompt;2:4 second prompt",
        "Solar flare) Alternative: eclipse",
        "Solar flare] Alternative: eclipse",
        "Solar flare} Alternative: eclipse",
        "Solar flare & Alternative: eclipse",
        "Solar flare + Alternative: eclipse",
        "Solar flare · Alternative: eclipse",
        r"Solar flare \ Alternative: eclipse",
        'Solar flare "Alternative: eclipse"',
        "Solar flare Alternative: eclipse",
        "Solar flare\u00a0Alternative: eclipse",
        "Solar flare → Alternative: eclipse",
        "Solar flare = Alternative: eclipse",
        "Solar flare_Alternative:eclipse",
        "Solar flare # Alternative:eclipse",
        "Alternative = eclipse",
        "Primary=eclipse; Alternative=flare",
        "Alternative | eclipse",
        "Primary | solar flare | Alternative | eclipse",
        "Solar flare; Alternative, eclipse",
        "Solar flare; Alternative; eclipse",
        "Solar flare; Alternative / eclipse",
        "[1] first concept; [2] second concept",
        "1] first concept; 2] second concept",
        "1、first concept;2、second concept",
        "Option №2: eclipse",
        "Alternative II: eclipse",
        "Variant 2: eclipse",
        "Alt: eclipse",
        "Version B: eclipse",
        "Concept 2: eclipse",
        "Secondary: eclipse",
        "Backup: eclipse",
        "Alternat\u0301ive: eclipse",
    ],
)
def test_additional_explicit_alternative_bypasses_are_rejected(completion):
    assert thumbnail_prompt_module._has_alternative_marker(completion) is True

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptService._normalize_completion(_provider_response(completion))

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "completion",
    [
        "A.I.-generated portrait, cinematic",
        "U.S. city skyline, cinematic",
        "Cinematic lens; 85–mm focal length",
        "Cinematic lens; 85—mm focal length",
        "Studio portrait; 3–point lighting",
        "Studio portrait; 3—point lighting",
        "Cinematic Earth; 1920:1080 composition",
        "Astronaut 👩‍🚀 above Earth, cinematic",
        "Cinematic value 1.8.",
        "Cinematic alternative-rock singer portrait",
        "Cinematic primary-color palette",
        "Primary colors with cinematic contrast",
        "An alternative camera angle with dramatic lighting",
        "Option A typography on a futuristic control panel",
        "Cinematic prompt design with no text",
        "An alternative, cinematic camera angle",
        "Cinematic alternative/original split-screen",
        "Concept art: futuristic city above the clouds",
        "Version control-inspired neon city",
        "Primary colors: red and blue",
        "1000:1 contrast ratio, cinematic display",
        "1,000-star galaxy, cinematic",
        "1/3 composition with an 85-mm lens",
    ],
)
def test_additional_plain_prompt_prose_is_not_misclassified(completion):
    assert thumbnail_prompt_module._has_alternative_marker(completion) is False
    assert (
        ThumbnailPromptService._normalize_completion(_provider_response(completion))
        == completion
    )


@pytest.mark.parametrize(
    "completion",
    [
        "²: first prompt",
        "⑴ first prompt",
        "①:first prompt",
        "9" * 5000 + ": first prompt",
    ],
)
def test_compatibility_and_oversized_digit_markers_return_typed_invalid_response(
    completion,
):
    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptService._normalize_completion(_provider_response(completion))

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "completion",
    [
        "first\u2028second",
        "first\u2029second",
        "Solar\u202eflare",
        "Solar\u00adflare",
        "\ud800",
    ],
)
def test_disallowed_unicode_controls_return_typed_invalid_response(completion):
    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptService._normalize_completion(_provider_response(completion))

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


def test_oversized_output_is_rejected_before_unicode_normalization(monkeypatch):
    def fail_if_called(_text):
        pytest.fail("Unicode normalization must not run for oversized output")

    monkeypatch.setattr(
        thumbnail_prompt_module,
        "_canonical_validation_text",
        fail_if_called,
    )

    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptService._normalize_completion(
            _provider_response("x" * 8001)
        )

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"
