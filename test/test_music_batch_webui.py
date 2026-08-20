from pathlib import Path

from app.services.music_batch.models import BatchSettings, BatchState, BatchStatus, SongItem, SongStatus


def test_music_batch_ui_module_exports_renderer():
    from webui.music_batch import render_music_batch_page

    assert callable(render_music_batch_page)


def test_streamlit_page_registers_music_batch_without_replacing_main():
    page = Path("webui/pages/2_Music_Batch.py")
    assert page.is_file()
    assert "render_music_batch_page" in page.read_text(encoding="utf-8")

    main_source = Path("webui/Main.py").read_text(encoding="utf-8")
    assert "def _render_application():" in main_source
    assert main_source.rstrip().endswith("_render_application()")


def _state(status=BatchStatus.processing, song_statuses=None):
    statuses = song_statuses or [SongStatus.pending, SongStatus.pending]
    return BatchState(
        batch_id="progress-test",
        batch_dir=".",
        settings=BatchSettings(output_root="."),
        status=status,
        songs=[
            SongItem(
                source_path=f"song-{index + 1}.mp3",
                added_index=index,
                status=song_status,
            )
            for index, song_status in enumerate(statuses)
        ],
    )


def test_batch_progress_percent_uses_finished_and_active_song_progress():
    from webui.music_batch import _batch_progress_percent

    state = _state(song_statuses=[SongStatus.completed, SongStatus.processing])
    state.songs[0].progress = 100
    state.songs[1].progress = 40

    assert _batch_progress_percent(state) == 70


def test_batch_progress_percent_is_100_when_batch_completed():
    from webui.music_batch import _batch_progress_percent

    state = _state(
        status=BatchStatus.completed,
        song_statuses=[SongStatus.completed, SongStatus.completed],
    )

    assert _batch_progress_percent(state) == 100


def test_batch_status_text_shows_processing_percent_and_completed_label():
    from webui.music_batch import _batch_status_text

    processing = _state()
    assert _batch_status_text(processing, 35) == "กำลังทำงาน 35%"

    completed = _state(status=BatchStatus.completed)
    assert _batch_status_text(completed, 100) == "เสร็จแล้ว 100%"


def test_batch_page_requests_auto_refresh_only_while_batch_is_running():
    from webui.music_batch import _should_auto_refresh

    assert _should_auto_refresh(_state(status=BatchStatus.processing)) is True
    assert _should_auto_refresh(_state(status=BatchStatus.completed)) is False
    assert _should_auto_refresh(_state(status=BatchStatus.completed_with_failures)) is False
