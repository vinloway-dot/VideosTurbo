import os
import shutil

import pytest

from app.models.cloud_agent import (
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.video_library import (
    CloudVideoLibraryService,
    VideoLibraryNotFoundError,
)


def _request() -> CloudJobCreate:
    return CloudJobCreate(
        subject="Library test",
        script="A valid narration script for testing.",
        master_prompt="Create six chronological clips.",
        clip_plan=empty_six_clip_plan(),
        language="English",
        target_words=130,
        tts_provider="test",
        voice_id="voice",
    )


def _completed_job(
    store: CloudJobStore,
    *,
    job_id: str,
    completed_at: str,
    checkpoint: CloudJobCheckpoint = CloudJobCheckpoint.COMPLETED,
):
    job = store.create_job(_request(), status=CloudJobStatus.COMPLETED)
    store.patch_job(
        job.id,
        checkpoint=checkpoint,
        completed_at=completed_at,
        final_video="final.mp4",
    )
    if job.id != job_id:
        with store._connect() as connection:
            connection.execute(
                "UPDATE cloud_agent_jobs SET id = ? WHERE id = ?", (job_id, job.id)
            )
    return store.get_job(job_id)


def _queued_job(store: CloudJobStore):
    return store.create_job(_request())


def _library_service(tmp_path):
    store = CloudJobStore(str(tmp_path / "jobs.sqlite3"))
    storage = CloudJobStorage(tmp_path / "jobs")
    return CloudVideoLibraryService(store=store, storage=storage), store, storage


def _write_final(store: CloudJobStore, storage: CloudJobStorage, job):
    paths = storage.prepare(job.id)
    paths.final_file.write_bytes(b"mp4")
    return paths, store.patch_job(job.id, final_video=str(paths.final_file))


def test_library_filters_missing_final_files_before_pagination(tmp_path):
    service, store, storage = _library_service(tmp_path)
    visible = _completed_job(
        store, job_id="visible", completed_at="2026-08-28T12:00:00+00:00"
    )
    _paths, visible = _write_final(store, storage, visible)
    _completed_job(store, job_id="missing", completed_at="2026-08-28T13:00:00+00:00")

    page = service.list_videos(page=1, page_size=10)

    assert [item.job_id for item in page.items] == [visible.id]
    assert (page.total_items, page.total_pages) == (1, 1)


def test_library_orders_visible_items_and_returns_empty_out_of_range_page(tmp_path):
    service, store, storage = _library_service(tmp_path)
    older = _completed_job(
        store, job_id="older", completed_at="2026-08-28T10:00:00+00:00"
    )
    newer = _completed_job(
        store, job_id="newer", completed_at="2026-08-28T11:00:00+00:00"
    )
    _older_paths, _older = _write_final(store, storage, older)
    _newer_paths, _newer = _write_final(store, storage, newer)

    page = service.list_videos(page=2, page_size=10)

    assert page.items == ()
    assert (page.total_items, page.total_pages) == (2, 1)


def test_library_deletion_removes_visible_job_record_and_artifacts(tmp_path):
    service, store, storage = _library_service(tmp_path)
    job = _completed_job(
        store, job_id="visible", completed_at="2026-08-28T12:00:00+00:00"
    )
    paths, job = _write_final(store, storage, job)

    service.delete_video(job.id)

    assert store.get_job(job.id) is None
    assert not paths.job_dir.exists()


def test_library_deletion_refuses_noncompleted_job_even_when_final_file_exists(tmp_path):
    service, store, storage = _library_service(tmp_path)
    job = _queued_job(store)
    paths, job = _write_final(store, storage, job)

    with pytest.raises(VideoLibraryNotFoundError):
        service.delete_video(job.id)

    assert store.get_job(job.id) is not None
    assert paths.final_file.exists()


def test_library_deletion_restores_artifacts_when_record_delete_fails(tmp_path, monkeypatch):
    service, store, storage = _library_service(tmp_path)
    job = _completed_job(
        store, job_id="visible", completed_at="2026-08-28T12:00:00+00:00"
    )
    paths, job = _write_final(store, storage, job)

    def fail_delete(_job_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "delete_job", fail_delete)

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.delete_video(job.id)

    assert store.get_job(job.id) is not None
    assert paths.final_file.exists()


def test_completed_final_candidates_are_sorted_by_completion_then_id(tmp_path):
    store = CloudJobStore(str(tmp_path / "jobs.sqlite3"))
    older = _completed_job(
        store, job_id="a", completed_at="2026-08-28T10:00:00+00:00"
    )
    newer = _completed_job(
        store, job_id="b", completed_at="2026-08-28T10:00:00+00:00"
    )
    _completed_job(
        store,
        job_id="not-final",
        completed_at="2026-08-28T12:00:00+00:00",
        checkpoint=CloudJobCheckpoint.TTS_READY,
    )
    _queued_job(store)
    assert [job.id for job in store.list_completed_final_candidates()] == [
        newer.id,
        older.id,
    ]


def test_staging_and_purging_reject_symlinked_deleting_root(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.prepare("job-a")
    external = tmp_path / "external"
    external.mkdir()
    victim = external / "victim"
    victim.mkdir()
    (victim / "important.txt").write_text("keep", encoding="utf-8")
    deleting = storage.root / ".deleting"
    deleting.symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError):
        storage.stage_job_artifacts("job-a")
    with pytest.raises(ValueError):
        storage.purge_staged_job(deleting / "victim")
    assert (victim / "important.txt").exists()


def _swap_deleting_root_to_external(storage, external, original_rename):
    deleting = storage.root / ".deleting"
    displaced = storage.root / ".deleting-displaced"
    original_rename(deleting, displaced)
    deleting.symlink_to(external, target_is_directory=True)


def test_stage_rejects_deleting_root_swapped_to_external_symlink_at_rename(
    tmp_path, monkeypatch
):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-a")
    paths.final_file.write_bytes(b"mp4")
    (storage.root / ".deleting").mkdir()
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "important.txt"
    sentinel.write_text("keep", encoding="utf-8")
    original_rename = os.rename
    swapped = False

    def swap_then_rename(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            _swap_deleting_root_to_external(storage, external, original_rename)
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "rename", swap_then_rename)

    with pytest.raises(ValueError, match="deleting directory changed"):
        storage.stage_job_artifacts("job-a")

    assert paths.final_file.exists()
    assert list(external.iterdir()) == [sentinel]


def test_restore_does_not_move_external_data_when_deleting_root_is_swapped(
    tmp_path, monkeypatch
):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-a")
    paths.final_file.write_bytes(b"mp4")
    staged = storage.stage_job_artifacts("job-a")
    external = tmp_path / "external"
    external.mkdir()
    external_victim = external / staged.name
    external_victim.mkdir()
    sentinel = external_victim / "important.txt"
    sentinel.write_text("keep", encoding="utf-8")
    original_rename = os.rename
    swapped = False

    def swap_then_rename(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            _swap_deleting_root_to_external(storage, external, original_rename)
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "rename", swap_then_rename)

    storage.restore_staged_job("job-a", staged)

    assert paths.final_file.read_bytes() == b"mp4"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_purge_does_not_delete_external_data_when_deleting_root_is_swapped(
    tmp_path, monkeypatch
):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.prepare("job-a").final_file.write_bytes(b"mp4")
    staged = storage.stage_job_artifacts("job-a")
    external = tmp_path / "external"
    external.mkdir()
    external_victim = external / staged.name
    external_victim.mkdir()
    sentinel = external_victim / "important.txt"
    sentinel.write_text("keep", encoding="utf-8")
    original_rename = os.rename
    original_rmtree = shutil.rmtree
    swapped = False

    def swap_then_rmtree(target, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            _swap_deleting_root_to_external(storage, external, original_rename)
        return original_rmtree(target, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", swap_then_rmtree)

    storage.purge_staged_job(staged)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_purge_missing_staged_target_and_storage_root_is_a_no_op(tmp_path):
    storage = CloudJobStorage(tmp_path / "missing-jobs")

    storage.purge_staged_job(storage.root / ".deleting" / "job-a-staged")

    assert not storage.root.exists()


def test_stage_job_artifacts_moves_only_its_job_and_rejects_escape(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    target = storage.prepare("job-a")
    sibling = storage.prepare("job-b")
    target.final_file.write_bytes(b"mp4")
    sibling.final_file.write_bytes(b"mp4")
    staged = storage.stage_job_artifacts("job-a")
    assert not target.job_dir.exists()
    assert staged.is_dir()
    assert sibling.final_file.exists()
    with pytest.raises(ValueError):
        storage.stage_job_artifacts("../outside")


def test_final_video_validation_requires_the_canonical_existing_file(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-a")
    paths.final_file.write_bytes(b"mp4")
    assert storage.has_valid_final_video("job-a", str(paths.final_file))
    assert not storage.has_valid_final_video("job-a", str(paths.job_dir / "missing.mp4"))
    assert not storage.has_valid_final_video("job-a", "../outside.mp4")


def test_staged_job_can_be_restored_or_purged_only_under_deleting_root(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.prepare("job-a").final_file.write_bytes(b"mp4")
    staged = storage.stage_job_artifacts("job-a")
    storage.restore_staged_job("job-a", staged)
    assert storage.prepare("job-a").final_file.exists()
    staged = storage.stage_job_artifacts("job-a")
    storage.purge_staged_job(staged)
    assert not staged.exists()
    with pytest.raises(ValueError):
        storage.purge_staged_job(tmp_path)


def test_delete_job_removes_one_record_and_rejects_missing_id(tmp_path):
    store = CloudJobStore(str(tmp_path / "jobs.sqlite3"))
    job = store.create_job(_request())
    store.delete_job(job.id)
    assert store.get_job(job.id) is None
    with pytest.raises(KeyError):
        store.delete_job(job.id)
