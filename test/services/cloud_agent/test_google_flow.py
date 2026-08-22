from pathlib import Path

import pytest

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers.google_flow import classify_google_flow_session


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "resources" / "cloud_agent" / "google_flow"


@pytest.mark.parametrize(
    ("fixture_name", "url", "expected"),
    [
        ("ready.html", "https://labs.google/fx/tools/flow/project/demo", ServiceSessionStatus.READY),
        ("login.html", "https://accounts.google.com/v3/signin/identifier", ServiceSessionStatus.SESSION_EXPIRED),
        ("continue_google.html", "https://labs.google/fx/tools/flow", ServiceSessionStatus.SESSION_EXPIRED),
        ("password.html", "https://accounts.google.com/v3/signin/challenge/pwd", ServiceSessionStatus.LOGIN_REQUIRED),
        ("captcha.html", "https://accounts.google.com/v3/signin/challenge", ServiceSessionStatus.CAPTCHA_REQUIRED),
        ("two_factor.html", "https://accounts.google.com/v3/signin/challenge/totp", ServiceSessionStatus.TWO_FACTOR_REQUIRED),
        ("verification.html", "https://accounts.google.com/v3/signin/challenge/ipp", ServiceSessionStatus.VERIFICATION_REQUIRED),
        ("unknown.html", "https://labs.google/fx/tools/flow", ServiceSessionStatus.ERROR),
    ],
)
def test_google_flow_session_fixture_classification(fixture_name, url, expected):
    html = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")

    assert classify_google_flow_session(url=url, html=html) is expected


def test_google_flow_challenge_wins_over_ready_marker():
    html = """
    <html><body>
      <button aria-label="Agent">Agent</button>
      <textarea aria-label="Prompt"></textarea>
      <div>2-Step Verification</div>
      <input autocomplete="one-time-code" />
    </body></html>
    """

    assert (
        classify_google_flow_session(
            url="https://labs.google/fx/tools/flow/project/demo",
            html=html,
        )
        is ServiceSessionStatus.TWO_FACTOR_REQUIRED
    )
