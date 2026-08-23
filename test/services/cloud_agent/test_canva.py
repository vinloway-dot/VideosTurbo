from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.models.cloud_agent import ServiceSessionStatus
from app.services.cloud_agent.providers import canva
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


class FakeCanvaEditorPage:
    """Deterministic boundary double for the live Task 9 editor controls."""

    def __init__(self, *, playback_verifies=True, download_completes=True):
        self.playback_verifies = playback_verifies
        self.download_completes = download_completes
        self.actions = []
        self.timeline_end_seconds = 60.0

    def goto(self, url, **kwargs):
        self.actions.append(("goto", url, kwargs))

    def upload_media(self, paths):
        self.actions.append(("upload", tuple(Path(path).name for path in paths)))

    def order_clips(self, expected_names):
        self.actions.append(("order", tuple(expected_names)))

    def select_video_clip(self, index):
        self.actions.append(("select_clip", index))

    def open_video_speed(self):
        self.actions.append(("open_video_speed",))

    def set_custom_speed(self, speed):
        self.actions.append(("set_speed", speed))

    def verify_playback_speed(self, speed):
        self.actions.append(("verify_speed", speed))
        return self.playback_verifies

    def mute_source_audio(self):
        self.actions.append(("mute_source_audio",))

    def position_narration_at_zero(self):
        self.actions.append(("narration_at_zero",))

    def bound_final_visual_end(self, target_seconds):
        self.actions.append(("bound_final_end", target_seconds))
        self.timeline_end_seconds = target_seconds

    def verify_timeline_end(self, target_seconds, tolerance_seconds):
        self.actions.append(("verify_timeline_end", target_seconds, tolerance_seconds))
        return abs(self.timeline_end_seconds - target_seconds) <= tolerance_seconds

    def generate_auto_captions(self):
        self.actions.append(("auto_captions",))

    def export_mp4_1080p(self):
        self.actions.append(("export_mp4_1080p",))

    def download_export(self, output):
        self.actions.append(("download", Path(output).name))
        if self.download_completes:
            Path(output).write_bytes(b"final-mp4")


class FakeCanvaContext:
    def __init__(self, page):
        self.pages = [page]


class FakeCanvaBrowser:
    def __init__(self, page):
        self.page = page
        self.open_calls = []

    @contextmanager
    def open(self, service, *, headed=None):
        self.open_calls.append((service, headed))
        yield FakeCanvaContext(self.page)


class FakeCanvaSessions:
    def __init__(self):
        self.calls = []

    def ensure_service_ready(self, service, job_id):
        self.calls.append((service, job_id))


def _assembly_job(*, speed=0.95, target_seconds=63.25):
    return SimpleNamespace(
        id="job-canva-123",
        canva_playback_speed=speed,
        target_final_duration_seconds=target_seconds,
    )


def _assembly_client(page):
    client_cls = getattr(canva, "CanvaAssemblyClient", None)
    assert client_cls is not None, "Task 10 Canva production client is not implemented"
    sessions = FakeCanvaSessions()
    client = client_cls(
        FakeCanvaBrowser(page),
        sessions,
        service_url="https://www.canva.com/design/demo/edit",
        timeline_tolerance_seconds=1.0,
    )
    return client, sessions


def _media(tmp_path):
    clips = []
    for index in range(1, 7):
        clip = tmp_path / f"clip_{index:02d}.mp4"
        clip.write_bytes(b"clip")
        clips.append(clip)
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")
    return clips, audio, tmp_path / "final.mp4"


def test_canva_assembly_uploads_orders_and_exports_adaptive_six_clip_job(tmp_path):
    """Catches skipped media, wrong clip order, missing playback proof, or invalid export flow."""
    page = FakeCanvaEditorPage()
    client, sessions = _assembly_client(page)
    clips, audio, output = _media(tmp_path)

    result = client.assemble_and_export(
        _assembly_job(),
        clips,
        audio,
        output,
    )

    assert result == output
    assert output.read_bytes() == b"final-mp4"
    assert sessions.calls == [("canva", "job-canva-123")]
    assert page.actions == [
        ("goto", "https://www.canva.com/design/demo/edit", {"wait_until": "domcontentloaded"}),
        ("upload", ("clip_01.mp4", "clip_02.mp4", "clip_03.mp4", "clip_04.mp4", "clip_05.mp4", "clip_06.mp4", "voice.mp3")),
        ("order", ("clip_01.mp4", "clip_02.mp4", "clip_03.mp4", "clip_04.mp4", "clip_05.mp4", "clip_06.mp4")),
        ("select_clip", 1),
        ("open_video_speed",),
        ("set_speed", 0.95),
        ("verify_speed", 0.95),
        ("select_clip", 2),
        ("open_video_speed",),
        ("set_speed", 0.95),
        ("verify_speed", 0.95),
        ("select_clip", 3),
        ("open_video_speed",),
        ("set_speed", 0.95),
        ("verify_speed", 0.95),
        ("select_clip", 4),
        ("open_video_speed",),
        ("set_speed", 0.95),
        ("verify_speed", 0.95),
        ("select_clip", 5),
        ("open_video_speed",),
        ("set_speed", 0.95),
        ("verify_speed", 0.95),
        ("select_clip", 6),
        ("open_video_speed",),
        ("set_speed", 0.95),
        ("verify_speed", 0.95),
        ("mute_source_audio",),
        ("narration_at_zero",),
        ("bound_final_end", 63.25),
        ("verify_timeline_end", 63.25, 1.0),
        ("auto_captions",),
        ("export_mp4_1080p",),
        ("download", "final.mp4"),
    ]


def test_canva_assembly_skips_playback_changes_when_speed_is_one(tmp_path):
    """Catches an unnecessary speed edit on a job whose visual timing is already 1.0x."""
    page = FakeCanvaEditorPage()
    client, _ = _assembly_client(page)
    clips, audio, output = _media(tmp_path)

    client.assemble_and_export(
        _assembly_job(speed=1.0, target_seconds=60.0),
        clips,
        audio,
        output,
    )

    assert not any(action[0] in {"open_video_speed", "set_speed", "verify_speed"} for action in page.actions)
    assert ("bound_final_end", 60.0) in page.actions


def test_canva_assembly_raises_typed_error_when_playback_cannot_be_verified(tmp_path):
    """Catches a false success when Canva does not expose post-action playback state."""
    page = FakeCanvaEditorPage(playback_verifies=False)
    client, _ = _assembly_client(page)
    clips, audio, output = _media(tmp_path)
    error_cls = getattr(canva, "CanvaPlaybackVerificationError", None)
    assert error_cls is not None, "Task 10 typed playback verification error is not implemented"

    with pytest.raises(error_cls, match="playback|timeline"):
        client.assemble_and_export(_assembly_job(), clips, audio, output)

    assert not output.exists()


def test_canva_assembly_rejects_an_export_without_a_completed_mp4_download(tmp_path):
    """Catches a false success when the final Canva Download action never yields a file."""
    page = FakeCanvaEditorPage(download_completes=False)
    client, _ = _assembly_client(page)
    clips, audio, output = _media(tmp_path)
    error_cls = getattr(canva, "CanvaDownloadVerificationError", None)
    assert error_cls is not None, "Task 10 typed download verification error is not implemented"

    with pytest.raises(error_cls, match="download|export"):
        client.assemble_and_export(_assembly_job(), clips, audio, output)

    assert ("download", "final.mp4") in page.actions
    assert not output.exists()
