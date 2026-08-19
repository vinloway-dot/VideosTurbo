from pathlib import Path

from app.services.music_batch.models import BatchState, BatchStatus, SongStatus
from app.services.music_batch.state import BatchStateStore, make_restart_directory


def test_save_uses_atomic_replace(tmp_path):
    store = BatchStateStore(tmp_path)
    state = BatchState.new_for_test()
    store.save(state)
    assert (tmp_path / "batch_state.json").exists()
    assert not (tmp_path / "batch_state.json.tmp").exists()


def test_recover_interrupted_returns_processing_to_pending(tmp_path):
    state = BatchState.new_for_test(status=BatchStatus.processing)
    state.songs[0].status = SongStatus.processing
    store = BatchStateStore(tmp_path)
    store.save(state)
    recovered = store.recover_interrupted()
    assert recovered.songs[0].status == SongStatus.pending
    assert recovered.status == BatchStatus.interrupted


def test_retry_failed_only_resets_failed_songs(tmp_path):
    state = BatchState.new_for_test(
        song_statuses=[SongStatus.completed, SongStatus.failed]
    )
    state.songs[1].latest_error = "boom"
    store = BatchStateStore(tmp_path)
    store.save(state)
    retried = store.retry_failed()
    assert retried.songs[0].status == SongStatus.completed
    assert retried.songs[1].status == SongStatus.pending
    assert retried.songs[1].latest_error is None


def test_mutate_persists_changes(tmp_path):
    store = BatchStateStore(tmp_path)
    store.save(BatchState.new_for_test())

    def update(state):
        state.songs[0].attempts += 1
        return state

    mutated = store.mutate(update)
    assert mutated.songs[0].attempts == 1
    assert store.load().songs[0].attempts == 1


def test_make_restart_directory_never_reuses_existing_run(tmp_path):
    previous = tmp_path / "batch_x"
    previous.mkdir()
    (tmp_path / "batch_x_restart_01").mkdir()
    candidate = make_restart_directory(previous)
    assert candidate == tmp_path / "batch_x_restart_02"
    assert not candidate.exists()


def test_load_round_trips_state(tmp_path):
    store = BatchStateStore(tmp_path)
    original = BatchState.new_for_test(status=BatchStatus.processing)
    store.save(original)
    loaded = store.load()
    assert loaded.model_dump() == original.model_dump()
