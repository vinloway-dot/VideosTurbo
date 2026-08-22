from pathlib import Path

import pytest

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers.canva import classify_canva_session


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "resources" / "cloud_agent" / "canva"


@pytest.mark.parametrize(
    ("fixture_name", "url", "expected"),
    [
        ("ready.html", "https://www.canva.com/design/DEMO/edit", ServiceSessionStatus.READY),
        ("login.html", "https://www.canva.com/login", ServiceSessionStatus.SESSION_EXPIRED),
        ("continue_google.html", "https://www.canva.com/login", ServiceSessionStatus.SESSION_EXPIRED),
        ("password.html", "https://www.canva.com/login", ServiceSessionStatus.LOGIN_REQUIRED),
        ("captcha.html", "https://www.canva.com/login", ServiceSessionStatus.CAPTCHA_REQUIRED),
        ("two_factor.html", "https://www.canva.com/login", ServiceSessionStatus.TWO_FACTOR_REQUIRED),
        ("verification.html", "https://www.canva.com/login", ServiceSessionStatus.VERIFICATION_REQUIRED),
        ("unknown.html", "https://www.canva.com/design/DEMO/edit", ServiceSessionStatus.ERROR),
    ],
)
def test_canva_session_fixture_classification(fixture_name, url, expected):
    html = (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")

    assert classify_canva_session(url=url, html=html) is expected


def test_canva_challenge_wins_over_editor_ready_marker():
    html = """
    <html><body>
      <nav><button aria-label="Share">Share</button></nav>
      <main>Canva editor</main>
      <div>Verify it's you</div>
    </body></html>
    """

    assert (
        classify_canva_session(
            url="https://www.canva.com/design/DEMO/edit",
            html=html,
        )
        is ServiceSessionStatus.VERIFICATION_REQUIRED
    )
