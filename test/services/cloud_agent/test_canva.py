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
        self.timeline_video_count = 0

    def goto(self, url, **kwargs):
        self.actions.append(("goto", url, kwargs))

    def upload_media(self, paths):
        self.actions.append(("upload", tuple(Path(path).name for path in paths)))

    def clean_uploaded_videos(self, _names=()):
        return None

    def clean_uploaded_audio(self, _name):
        return None

    def clear_video_timeline(self):
        self.timeline_video_count = 0

    def add_uploaded_clip(self, _name):
        self.timeline_video_count += 1

    def add_uploaded_audio(self, name):
        self.actions.append(("add_uploaded_audio", name))

    def timeline_video_count_value(self):
        return self.timeline_video_count

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


class FakePreparedCanvaEditorPage(FakeCanvaEditorPage):
    """Boundary double for verified workspace preparation operations."""

    def __init__(self, *, add_succeeds=True, **kwargs):
        super().__init__(**kwargs)
        self.timeline_video_count = 0
        self.add_succeeds = add_succeeds

    def clean_uploaded_videos(self, names=()):
        self.actions.append(("clean_uploaded_videos", tuple(names)))

    def clear_video_timeline(self):
        self.actions.append(("clear_video_timeline",))
        self.timeline_video_count = 0

    def add_uploaded_clip(self, name):
        before = self.timeline_video_count
        if self.add_succeeds:
            self.timeline_video_count += 1
        self.actions.append(("add_uploaded_clip", name, before, self.timeline_video_count))

    def timeline_video_count_value(self):
        return self.timeline_video_count


class FakeManagedMediaCleanupPage(FakePreparedCanvaEditorPage):
    """Boundary double for cleanup restricted to this CloudJob's media names."""

    def clean_uploaded_videos(self, names=None):
        self.actions.append(("clean_uploaded_videos", tuple(names or ())))

    def clean_uploaded_audio(self, name=None):
        self.actions.append(("clean_uploaded_audio", name))


class FakeNoVideosTabPage:
    no_uploaded_videos_tab = True


class _ClickOnly:
    def click(self):
        return None


class _MenuItem:
    def __init__(self, visible):
        self.visible = visible

    def bounding_box(self):
        if not self.visible:
            return None
        return {"x": 1.0, "y": 1.0, "width": 10.0, "height": 10.0}


class _MenuLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    def nth(self, _index):
        return _MenuItem(self.page.menu_query_count >= 5)


class FakeDelayedMediaMenuPage:
    """Models Canva hydrating the card menu immediately after its overlay click."""

    def __init__(self):
        self.menu_query_count = 0

    def get_by_text(self, _action, *, exact):
        assert exact is True
        self.menu_query_count += 1
        return _MenuLocator(self)

    def evaluate(self, _expression, _point):
        return True


class _MissingVideosTab:
    def count(self):
        return 0


class FakeHydratedNoVideosTabPage:
    """Canva's live zero state: Uploads is ready, but the Videos subtype is absent."""

    def get_by_role(self, role, *, name, exact):
        assert (role, name, exact) == ("tab", "Uploads", True)
        return _ClickOnly()

    def locator(self, _selector):
        return _MissingVideosTab()


class _CleanupCards:
    def __init__(self, count):
        self.count_value = count

    def count(self):
        return self.count_value


class _CleanupPanel:
    def __init__(self, cards):
        self.cards = cards

    def locator(self, selector):
        assert selector == '[role="button"][aria-label]'
        return self.cards

    def get_by_role(self, role, *, name, exact):
        assert (role, exact) == ("button", True)
        assert name == "clip_01.mp4"
        return self.cards


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
        self.calls.append(("service", service, job_id))

    def ensure_open_page_ready(self, service, page, job_id):
        self.calls.append(("page", service, page, job_id))


class _VisibleUploadName:
    def __init__(self, visible: bool):
        self.visible = visible

    def count(self):
        return 1 if self.visible else 0

    def is_visible(self):
        return self.visible


class _CountedUploadName:
    def __init__(self, count: int):
        self.count_value = count

    def count(self):
        return self.count_value

    def is_visible(self):
        return self.count_value == 1


class FakeCompletedUploadPage:
    def __init__(self, visible_names):
        self.visible_names = set(visible_names)

    def content(self):
        return "Canva editor"

    def reload(self, *, wait_until):
        assert wait_until == "domcontentloaded"

    def get_by_text(self, name, *, exact):
        assert exact is True
        return _VisibleUploadName(name in self.visible_names)


class FakePartialNamedUploadPage(FakeCompletedUploadPage):
    """Models a completed-count UI with duplicate and missing visible upload names."""

    def __init__(self, name_counts):
        super().__init__(set())
        self.name_counts = dict(name_counts)

    def get_by_text(self, name, *, exact):
        assert exact is True
        return _CountedUploadName(self.name_counts.get(name, 0))


class _SequentialUploadInput:
    def __init__(self):
        self.submissions = []

    def wait_for(self, *, state, timeout):
        assert state == "visible"
        assert timeout > 0

    def set_input_files(self, paths):
        self.submissions.append(tuple(paths) if isinstance(paths, list) else (paths,))


class FakeSequentialUploadPage:
    def __init__(self):
        self.upload_input = _SequentialUploadInput()

    def get_by_role(self, role, *, name, exact):
        assert (role, name, exact) == ("tab", "Uploads", True)
        return _ClickOnly()

    def locator(self, selector):
        assert selector == 'input[type="file"]'
        return self.upload_input


class FakeUploadInventoryPage(FakeCompletedUploadPage):
    """Models the permanent Canva "By uploading" copy with unnamed video cards."""

    def content(self):
        return "Canva editor By uploading, you confirm that your content complies"


class FakeRefreshableUploadPage(FakeUploadInventoryPage):
    def __init__(self):
        super().__init__(set())
        self.reload_count = 0

    def reload(self, *, wait_until):
        assert wait_until == "domcontentloaded"
        self.reload_count += 1


class _InventoryCount:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _HydratingUploadTab:
    def __init__(self, page, panel_name):
        self.page = page
        self.panel_name = panel_name

    def wait_for(self, *, state, timeout):
        assert state == "visible"
        assert timeout > 0
        self.page.tabs_ready = True

    def count(self):
        return 1 if self.page.tabs_ready else 0

    def click(self):
        return None

    def get_attribute(self, name):
        assert name == "aria-controls"
        return f"test-tabpanel-{self.panel_name}"


class _UploadPanel:
    def __init__(self, count):
        self._count = count

    def get_by_text(self, _name, *, exact=False):
        return _InventoryCount(self._count)


class FakeHydratingUploadInventoryPage:
    def __init__(self):
        self.tabs_ready = False
        self.video_tab = _HydratingUploadTab(self, "videos")
        self.audio_tab = _HydratingUploadTab(self, "audio")
        self.video_panel = _UploadPanel(6)
        self.audio_panel = _UploadPanel(1)

    def locator(self, selector):
        selector = selector.removesuffix(":visible")
        if selector.endswith('test-tabpanel-videos"]'):
            return self.video_panel
        if selector.endswith('test-tabpanel-audio"]'):
            return self.audio_panel
        if selector.endswith('-tabpanel-videos"]'):
            return self.video_tab
        if selector.endswith('-tabpanel-audio"]'):
            return self.audio_tab
        raise AssertionError(f"unexpected selector: {selector}")

    def get_by_role(self, role, *, name, exact):
        assert role == "tab"
        assert exact is True
        assert name in {"Elements", "Uploads"}
        return _SidebarTab(self, name)


class _NoVideoUploadTab:
    def wait_for(self, *, state, timeout):
        del state, timeout
        raise canva.PlaywrightTimeoutError("Videos tab is absent after cleanup")

    def count(self):
        return 0


class FakeZeroVideoUploadInventoryPage(FakeHydratingUploadInventoryPage):
    """Models the verified post-clean state before current-job videos are uploaded."""

    def __init__(self):
        super().__init__()
        self.video_panel = _UploadPanel(0)

    def locator(self, selector):
        selector = selector.removesuffix(":visible")
        if selector.endswith('-tabpanel-videos"]'):
            return _NoVideoUploadTab()
        return super().locator(selector)


class _DelayedAudioUploadTab(_HydratingUploadTab):
    def __init__(self, page):
        super().__init__(page, "audio")
        self.clicked = False

    def click(self):
        assert self.page.tabs_ready is True
        self.clicked = True

    def wait_for(self, *, state, timeout):
        super().wait_for(state=state, timeout=timeout)


class FakeZeroVideoDelayedAudioTabPage(FakeZeroVideoUploadInventoryPage):
    def __init__(self):
        super().__init__()
        self.audio_tab = _DelayedAudioUploadTab(self)


class _InvisibleAudioUploadTab(_HydratingUploadTab):
    def __init__(self, page):
        super().__init__(page, "audio")

    def click(self):
        raise canva.PlaywrightTimeoutError("stale hidden audio tab cannot be clicked")


class FakeDuplicateAudioTabPage(FakeZeroVideoUploadInventoryPage):
    """Models Canva retaining a hidden stale Uploads panel behind the live one."""

    def __init__(self):
        super().__init__()
        self.hidden_audio_tab = _InvisibleAudioUploadTab(self)
        self.visible_audio_tab = _HydratingUploadTab(self, "audio")

    def locator(self, selector):
        if selector == '[role="tab"][aria-controls$="-tabpanel-audio"]':
            return self.hidden_audio_tab
        if selector == '[role="tab"][aria-controls$="-tabpanel-audio"]:visible':
            return self.visible_audio_tab
        if selector == '[role="tab"][aria-controls$="-tabpanel-videos"]:visible':
            return _NoVideoUploadTab()
        return super().locator(selector)


class _InvisibleVideoUploadTab:
    def count(self):
        return 1

    def click(self):
        raise canva.PlaywrightTimeoutError("stale hidden video tab cannot be clicked")


class _VisibleVideoUploadTab:
    def __init__(self):
        self.clicked = False

    def count(self):
        return 1

    def click(self):
        self.clicked = True

    def get_attribute(self, name):
        assert name == "aria-controls"
        return "test-tabpanel-videos"


class FakeDuplicateVideoTabPage(FakeHydratingUploadInventoryPage):
    """Models Canva retaining a hidden stale Videos tab behind the live tab."""

    def __init__(self):
        super().__init__()
        self.hidden_video_tab = _InvisibleVideoUploadTab()
        self.visible_video_tab = _VisibleVideoUploadTab()

    def locator(self, selector):
        if selector == '[role="tab"][aria-controls$="-tabpanel-videos"]':
            return self.hidden_video_tab
        if selector == '[role="tab"][aria-controls$="-tabpanel-videos"]:visible':
            return self.visible_video_tab
        return super().locator(selector)


class _SidebarTab:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    def click(self):
        if hasattr(self.page, "sidebar_clicks"):
            self.page.sidebar_clicks.append(self.name)


class _ReactivatedAudioUploadTab(_DelayedAudioUploadTab):
    def wait_for(self, *, state, timeout):
        assert self.page.sidebar_clicks == ["Elements", "Uploads"]
        super().wait_for(state=state, timeout=timeout)


class FakeReactivatingUploadInventoryPage(FakeZeroVideoUploadInventoryPage):
    """Models Canva requiring a side-panel transition to reveal Uploads media tabs."""

    def __init__(self):
        super().__init__()
        self.sidebar_clicks = []
        self.audio_tab = _ReactivatedAudioUploadTab(self)

    def get_by_role(self, role, *, name, exact):
        assert role == "tab"
        assert exact is True
        assert name in {"Elements", "Uploads"}
        return _SidebarTab(self, name)


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
    assert sessions.calls == [("page", "canva", page, "job-canva-123")]
    assert page.actions == [
        (
            "goto",
            "https://www.canva.com/design/demo/edit",
            {"wait_until": "domcontentloaded", "timeout": 180_000},
        ),
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
        ("add_uploaded_audio", "voice.mp3"),
        ("mute_source_audio",),
        ("narration_at_zero",),
        ("bound_final_end", 63.25),
        ("verify_timeline_end", 63.25, 1.0),
        ("auto_captions",),
        ("export_mp4_1080p",),
        ("download", "final.mp4"),
    ]


def test_canva_job_session_allows_bounded_time_for_new_design_editor_navigation(
    tmp_path,
):
    """Catches the browser's 30-second default aborting a new Canva design before it hydrates."""
    page = FakeCanvaEditorPage()
    client, _ = _assembly_client(page)
    clips, audio, output = _media(tmp_path)

    client.assemble_and_export(_assembly_job(), clips, audio, output)

    assert page.actions[0] == (
        "goto",
        "https://www.canva.com/design/demo/edit",
        {"wait_until": "domcontentloaded", "timeout": 180_000},
    )


def test_canva_assembly_prepares_clean_workspace_before_upload_and_adds_clips_in_order(
    tmp_path,
):
    """Catches upload or timeline insertion before the workspace is observably clean."""
    page = FakePreparedCanvaEditorPage()
    client, _ = _assembly_client(page)
    clips, audio, output = _media(tmp_path)

    client.assemble_and_export(_assembly_job(), clips, audio, output)

    actions = page.actions
    assert actions[:9] == [
        (
            "goto",
            "https://www.canva.com/design/demo/edit",
            {"wait_until": "domcontentloaded", "timeout": 180_000},
        ),
        (
            "clean_uploaded_videos",
            (
                "clip_01.mp4",
                "clip_02.mp4",
                "clip_03.mp4",
                "clip_04.mp4",
                "clip_05.mp4",
                "clip_06.mp4",
            ),
        ),
        ("clear_video_timeline",),
        ("upload", ("clip_01.mp4", "clip_02.mp4", "clip_03.mp4", "clip_04.mp4", "clip_05.mp4", "clip_06.mp4", "voice.mp3")),
        ("add_uploaded_clip", "clip_01.mp4", 0, 1),
        ("add_uploaded_clip", "clip_02.mp4", 1, 2),
        ("add_uploaded_clip", "clip_03.mp4", 2, 3),
        ("add_uploaded_clip", "clip_04.mp4", 3, 4),
        ("add_uploaded_clip", "clip_05.mp4", 4, 5),
    ]
    assert actions[9] == ("add_uploaded_clip", "clip_06.mp4", 5, 6)


def test_canva_assembly_cleans_only_current_job_video_and_audio_names_before_upload(
    tmp_path,
):
    """Catches account-wide cleanup and stale narration surviving into a new job."""
    page = FakeManagedMediaCleanupPage()
    client, _ = _assembly_client(page)
    clips, audio, output = _media(tmp_path)

    client.assemble_and_export(_assembly_job(), clips, audio, output)

    assert page.actions[1:5] == [
        (
            "clean_uploaded_videos",
            (
                "clip_01.mp4",
                "clip_02.mp4",
                "clip_03.mp4",
                "clip_04.mp4",
                "clip_05.mp4",
                "clip_06.mp4",
            ),
        ),
        ("clean_uploaded_audio", "voice.mp3"),
        ("clear_video_timeline",),
        (
            "upload",
            (
                "clip_01.mp4",
                "clip_02.mp4",
                "clip_03.mp4",
                "clip_04.mp4",
                "clip_05.mp4",
                "clip_06.mp4",
                "voice.mp3",
            ),
        ),
    ]


def test_canva_clean_uploaded_videos_accepts_missing_videos_tab_as_verified_zero_state():
    """Catches treating Canva's absent Videos tab as an error after a successful cleanup."""
    client, _ = _assembly_client(FakeCanvaEditorPage())

    client._clean_uploaded_videos(FakeNoVideosTabPage())


def test_canva_clean_uploaded_videos_accepts_hydrated_missing_tab_without_export_timeout(
    monkeypatch,
):
    """Catches making a known zero-video UI state wait for the 180-second export timeout."""
    client, _ = _assembly_client(FakeCanvaEditorPage())
    clock = iter((0.0, 11.0))
    monkeypatch.setattr(canva.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(canva.time, "sleep", lambda _seconds: None)

    client._clean_uploaded_videos(FakeHydratedNoVideosTabPage())


def test_canva_cleanup_reopens_transiently_missing_videos_tab_after_a_delete(monkeypatch):
    """Catches treating Canva's post-delete panel hydration gap as a verified empty state."""
    client, _ = _assembly_client(FakeCanvaEditorPage())
    cards = _CleanupCards(2)
    populated_panel = _CleanupPanel(cards)
    empty_panel = _CleanupPanel(_CleanupCards(0))
    opened_panels = iter(
        (populated_panel, None, populated_panel, empty_panel, empty_panel)
    )
    deleted = []

    monkeypatch.setattr(client, "_open_uploaded_videos", lambda _page: next(opened_panels))

    def delete_one(_page, current_cards):
        deleted.append(current_cards.count())
        current_cards.count_value -= 1

    monkeypatch.setattr(client, "_delete_uploaded_video_card", delete_one)

    client._clean_uploaded_videos(object(), ("clip_01.mp4",))

    assert deleted == [2, 1]


def test_canva_cleanup_accepts_missing_videos_tab_only_after_retrying_post_delete(
    monkeypatch,
):
    """Catches rejecting Canva's observed zero-video state after the final delete."""
    client, _ = _assembly_client(FakeCanvaEditorPage())
    cards = _CleanupCards(1)
    populated_panel = _CleanupPanel(cards)
    opened_panels = iter((populated_panel, None, None))

    monkeypatch.setattr(client, "_open_uploaded_videos", lambda _page: next(opened_panels))
    monkeypatch.setattr(
        client,
        "_delete_uploaded_video_card",
        lambda _page, current_cards: setattr(current_cards, "count_value", 0),
    )

    client._clean_uploaded_videos(object())


def test_canva_open_uploaded_videos_uses_visible_tab_when_hidden_stale_tab_remains():
    """Catches selecting Canva's hidden retained Videos tab instead of the live control."""
    client, _ = _assembly_client(FakeCanvaEditorPage())
    page = FakeDuplicateVideoTabPage()

    panel = client._open_uploaded_videos(page)

    assert panel is page.video_panel
    assert page.visible_video_tab.clicked is True


def test_canva_waits_for_card_menu_hydration_before_verifying_move_to_trash():
    """Catches treating the immediate post-overlay menu hydration window as a hard failure."""
    client, _ = _assembly_client(FakeCanvaEditorPage())
    client.poll_seconds = 0.0

    trash, trash_box = client._verified_trash_menu_item(FakeDelayedMediaMenuPage())

    assert trash is not None
    assert trash_box == {"x": 1.0, "y": 1.0, "width": 10.0, "height": 10.0}


def test_canva_add_uploaded_clips_fails_closed_when_one_click_does_not_add_exactly_one_video(
    tmp_path,
):
    """Catches advancing to later clips when Canva did not add the selected card."""
    page = FakePreparedCanvaEditorPage(add_succeeds=False)
    client, _ = _assembly_client(page)
    client.export_timeout_seconds = 0.0
    client.poll_seconds = 0.0
    clips, _, _ = _media(tmp_path)

    with pytest.raises(canva.CanvaUIVerificationError, match="timeline video count"):
        client._add_uploaded_clips(page, [path.name for path in clips])

    assert page.actions == [("add_uploaded_clip", "clip_01.mp4", 0, 0)]


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


def test_canva_upload_completion_accepts_observable_media_without_processing_text():
    page = FakeCompletedUploadPage(
        {
            "clip_01.mp4",
            "clip_02.mp4",
            "clip_03.mp4",
            "clip_04.mp4",
            "clip_05.mp4",
            "clip_06.mp4",
            "voice.mp3",
        }
    )
    client, _ = _assembly_client(FakeCanvaEditorPage())

    client._wait_for_upload_completion(
        page,
        [
            "clip_01.mp4",
            "clip_02.mp4",
            "clip_03.mp4",
            "clip_04.mp4",
            "clip_05.mp4",
            "clip_06.mp4",
            "voice.mp3",
        ],
    )


def test_canva_uploads_all_canonical_media_in_one_submission_then_verifies_as_a_set(
    tmp_path, monkeypatch
):
    """Catches breaking Canva's supported batch upload into independent partial uploads."""
    page = FakeSequentialUploadPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())
    clips, audio, _ = _media(tmp_path)
    inventory = iter(((0, 0),) * 8)
    verified = []
    monkeypatch.setattr(client, "_upload_inventory", lambda _page, _audio: next(inventory))
    monkeypatch.setattr(
        client,
        "_wait_for_upload_completion",
        lambda _page, names, **_kwargs: verified.append(tuple(names)),
    )

    client._upload_media(page, [*clips, audio])

    assert page.upload_input.submissions == [tuple(str(path) for path in [*clips, audio])]
    assert verified == [tuple(path.name for path in [*clips, audio])]


def test_canva_upload_completion_rejects_complete_count_with_partial_duplicate_names(
    monkeypatch,
):
    """Catches accepting a six-card count when Canva exposes a missing or duplicate clip."""
    names = [*(f"clip_{index:02d}.mp4" for index in range(1, 7)), "voice.mp3"]
    page = FakePartialNamedUploadPage(
        {
            "clip_01.mp4": 2,
            "clip_02.mp4": 1,
            "clip_03.mp4": 1,
            "clip_04.mp4": 2,
            "clip_05.mp4": 0,
            "clip_06.mp4": 1,
            "voice.mp3": 1,
        }
    )
    client, _ = _assembly_client(FakeCanvaEditorPage())
    client.export_timeout_seconds = 0.0
    client.poll_seconds = 0.0
    monkeypatch.setattr(client, "_upload_inventory", lambda _page, _audio_name: (6, 1))

    with pytest.raises(canva.CanvaUIVerificationError, match="upload completion"):
        client._wait_for_upload_completion(page, names, baseline_inventory=(0, 0))


def test_canva_upload_completion_accepts_card_scoped_media_when_global_audio_is_duplicated(
    monkeypatch,
):
    """Catches rejecting a completed upload because account-global voice text is duplicated."""
    names = [*(f"clip_{index:02d}.mp4" for index in range(1, 7)), "voice.mp3"]
    page = FakePartialNamedUploadPage({name: 2 for name in names})
    client, _ = _assembly_client(FakeCanvaEditorPage())
    monkeypatch.setattr(client, "_upload_inventory", lambda _page, _audio_name: (6, 20))
    monkeypatch.setattr(
        client, "_uploaded_media_cards_complete", lambda _page, _names, _audio: True
    )

    client._wait_for_upload_completion(page, names, baseline_inventory=(0, 19))


def test_canva_upload_inventory_waits_for_hydrated_media_tabs():
    """Catches querying Canva's video/audio tabs before their post-click hydration."""
    page = FakeHydratingUploadInventoryPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())

    assert client._upload_inventory(page, "voice.mp3") == (6, 1)


def test_canva_upload_inventory_accepts_missing_videos_tab_after_pre_clean():
    """Catches pre-clean zero state blocking the upload baseline before new clips exist."""
    page = FakeZeroVideoUploadInventoryPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())

    assert client._upload_inventory(page, "voice.mp3") == (0, 1)


def test_canva_upload_inventory_waits_for_audio_tab_before_selecting_it():
    """Catches clicking Canva's Audio tab before its Uploads panel hydrates."""
    page = FakeZeroVideoDelayedAudioTabPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())

    assert client._upload_inventory(page, "voice.mp3") == (0, 1)


def test_canva_upload_inventory_uses_visible_audio_tab_when_stale_panel_remains():
    """Catches selecting Canva's hidden stale Audio tab before the live panel."""
    page = FakeDuplicateAudioTabPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())

    assert client._upload_inventory(page, "voice.mp3") == (0, 1)


def test_canva_upload_inventory_reactivates_uploads_before_reading_media_tabs():
    """Catches Canva keeping Uploads media tabs hidden when its sidebar is already selected."""
    page = FakeReactivatingUploadInventoryPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())

    assert client._upload_inventory(page, "voice.mp3") == (0, 1)


def test_canva_upload_completion_accepts_scoped_media_card_increase_when_videos_omit_filenames(
    monkeypatch,
):
    """Catches treating Canva's permanent "By uploading" help copy as upload activity."""
    page = FakeUploadInventoryPage(set())
    client, _ = _assembly_client(FakeCanvaEditorPage())
    client.export_timeout_seconds = 0.01
    client.poll_seconds = 0.0
    inventories = iter([(11, 4), (17, 5)])
    monkeypatch.setattr(
        client,
        "_upload_inventory",
        lambda _page, _audio_name: next(inventories),
        raising=False,
    )

    client._wait_for_upload_completion(
        page,
        ["clip_01.mp4", "clip_02.mp4", "clip_03.mp4", "clip_04.mp4", "clip_05.mp4", "clip_06.mp4", "voice.mp3"],
        baseline_inventory=(11, 4),
    )


def test_canva_upload_completion_refreshes_once_before_rejecting_partial_progress(
    monkeypatch,
):
    """Catches rejecting Canva uploads that finish only after its post-upload refresh."""
    page = FakeRefreshableUploadPage()
    client, _ = _assembly_client(FakeCanvaEditorPage())
    client.export_timeout_seconds = 0.0
    client.poll_seconds = 0.0
    inventories = iter(((2, 1), (6, 1)))
    monkeypatch.setattr(client, "_upload_inventory", lambda _page, _audio: next(inventories))

    client._wait_for_upload_completion(
        page,
        [*(f"clip_{index:02d}.mp4" for index in range(1, 7)), "voice.mp3"],
        baseline_inventory=(0, 0),
        audio_name="voice.mp3",
    )

    assert page.reload_count == 1


def test_canva_upload_completion_rejects_unscoped_names_when_audio_panel_did_not_gain_voice(
    monkeypatch,
):
    """Catches accepting stale/global filename text when Canva has not uploaded narration."""
    expected = ["clip_01.mp4", "clip_02.mp4", "clip_03.mp4", "clip_04.mp4", "clip_05.mp4", "clip_06.mp4", "voice.mp3"]
    page = FakeCompletedUploadPage(set(expected))
    client, _ = _assembly_client(FakeCanvaEditorPage())
    client.export_timeout_seconds = 0.0
    monkeypatch.setattr(client, "_upload_inventory", lambda _page, _audio_name: (6, 0))

    with pytest.raises(canva.CanvaUIVerificationError, match="upload completion"):
        client._wait_for_upload_completion(page, expected, baseline_inventory=(0, 0))


def test_canva_upload_completion_fails_closed_when_any_media_name_is_absent():
    page = FakeCompletedUploadPage({"clip_01.mp4"})
    client, _ = _assembly_client(FakeCanvaEditorPage())
    client.export_timeout_seconds = 0.0
    error_cls = getattr(canva, "CanvaUIVerificationError", None)
    assert error_cls is not None

    with pytest.raises(error_cls, match="upload completion"):
        client._wait_for_upload_completion(page, ["clip_01.mp4", "voice.mp3"])
