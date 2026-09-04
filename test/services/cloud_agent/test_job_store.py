import sqlite3

import pytest

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobStatus,
    FlowRecoveryState,
)
from app.models.six_clip import empty_six_clip_plan
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.errors import RecoveryBudgetExhausted


def _request(
    subject: str = "Why Saturn Has a Hexagon",
    *,
    create_canva_captions: bool = False,
) -> CloudJobCreate:
    return CloudJobCreate(
        subject=subject,
        script="A valid narration script.",
        master_prompt="Create six videos from this narration.",
        clip_plan=empty_six_clip_plan(target_words=130),
        language="English",
        target_words=130,
        tts_provider="azure-tts-v1",
        voice_id="en-US-JennyNeural-Female",
        voice_speed=1.0,
        create_canva_captions=create_canva_captions,
    )


def _create_pre_v22_database(db_path) -> None:
    plan_json = empty_six_clip_plan(target_words=130).model_dump_json()
    now = "2026-08-22T06:30:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE cloud_agent_jobs (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                script TEXT NOT NULL,
                master_prompt TEXT NOT NULL,
                clip_plan_json TEXT NOT NULL,
                language TEXT NOT NULL,
                target_words INTEGER NOT NULL,
                tts_provider TEXT NOT NULL,
                voice_id TEXT NOT NULL,
                voice_speed REAL NOT NULL,
                status TEXT NOT NULL,
                checkpoint TEXT NOT NULL,
                control_request TEXT NOT NULL,
                current_step TEXT NOT NULL,
                progress INTEGER NOT NULL,
                flow_status TEXT NOT NULL,
                canva_status TEXT NOT NULL,
                voice_file TEXT NOT NULL,
                final_video TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_message TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                lease_until TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO cloud_agent_jobs (
                id, subject, script, master_prompt, clip_plan_json, language,
                target_words, tts_provider, voice_id, voice_speed, status,
                checkpoint, control_request, current_step, progress,
                flow_status, canva_status, voice_file, final_video,
                error_code, error_message, worker_id, lease_until, created_at,
                started_at, completed_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "legacy-job",
                "Legacy subject",
                "Legacy narration.",
                "Legacy master prompt.",
                plan_json,
                "English",
                130,
                "azure-tts-v1",
                "en-US-JennyNeural-Female",
                1.0,
                CloudJobStatus.TTS_READY.value,
                CloudJobCheckpoint.TTS_READY.value,
                CloudControlRequest.NONE.value,
                "tts_ready",
                30,
                "",
                "",
                "storage/jobs/legacy-job/audio/voice.mp3",
                "",
                "",
                "",
                "",
                "",
                now,
                now,
                "",
                now,
            ),
        )


def test_create_job_persists_defaults_and_six_clip_plan_across_reopen(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))

    created = store.create_job(_request(create_canva_captions=True))

    assert created.status is CloudJobStatus.QUEUED
    assert created.checkpoint is CloudJobCheckpoint.NONE
    assert created.control_request is CloudControlRequest.NONE
    assert created.current_step == "queued"
    assert created.progress == 0
    assert created.clip_plan.model_dump() == _request().clip_plan.model_dump()
    assert created.create_canva_captions is True

    reopened = CloudJobStore(str(db_path))
    loaded = reopened.get_job(created.id)

    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.script == created.script
    assert loaded.master_prompt == created.master_prompt
    assert loaded.clip_plan.model_dump() == created.clip_plan.model_dump()
    assert loaded.create_canva_captions is True


def test_fresh_database_defaults_omitted_caption_choice_to_disabled(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))
    source = store.create_job(_request(create_canva_captions=True))

    with sqlite3.connect(db_path) as connection:
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(cloud_agent_jobs)"
            ).fetchall()
            if row[1] != "create_canva_captions"
        ]
        selected = ["?" if column == "id" else column for column in columns]
        connection.execute(
            f"INSERT INTO cloud_agent_jobs ({', '.join(columns)}) "
            f"SELECT {', '.join(selected)} FROM cloud_agent_jobs WHERE id = ?",
            ("omitted-caption-choice", source.id),
        )

    loaded = store.get_job("omitted-caption-choice")

    assert loaded is not None
    assert loaded.create_canva_captions is False


def test_first_completed_transition_sets_completion_time_and_terminal_fields(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())

    completed = store.patch_job(job.id, status=CloudJobStatus.COMPLETED)
    retained = store.patch_job(job.id, current_step="completed")

    assert completed.completed_at
    assert completed.checkpoint is CloudJobCheckpoint.COMPLETED
    assert completed.progress == 100
    assert retained.completed_at == completed.completed_at


def test_store_backfills_legacy_completed_row_from_updated_at(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))
    job = store.create_job(_request())
    completed = store.patch_job(job.id, status=CloudJobStatus.COMPLETED)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE cloud_agent_jobs SET completed_at = '' WHERE id = ?",
            (job.id,),
        )

    reopened = CloudJobStore(str(db_path))

    restored = reopened.get_job(job.id)
    assert restored is not None
    assert restored.completed_at == completed.updated_at


def test_cloud_job_store_round_trips_canva_workspace_fields(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_job(_request())

    updated = store.patch_job(
        created.id,
        canva_design_url="https://www.canva.com/design/DEMO/edit",
        canva_audio_card_count=1,
    )

    assert updated.canva_design_url == "https://www.canva.com/design/DEMO/edit"
    assert updated.canva_audio_card_count == 1
    reopened = CloudJobStore(str(tmp_path / "agent.sqlite3")).get_job(created.id)
    assert reopened is not None
    assert reopened.canva_design_url == "https://www.canva.com/design/DEMO/edit"
    assert reopened.canva_audio_card_count == 1


def test_pre_v22_database_is_migrated_without_losing_job(tmp_path):
    db_path = tmp_path / "legacy-agent.sqlite3"
    _create_pre_v22_database(db_path)

    store = CloudJobStore(str(db_path))
    migrated = store.get_job("legacy-job")

    assert migrated is not None
    assert migrated.script == "Legacy narration."
    assert migrated.checkpoint is CloudJobCheckpoint.TTS_READY
    assert migrated.audio_duration_seconds == 0.0
    assert migrated.canva_playback_speed == 1.0
    assert migrated.target_final_duration_seconds == 60.0
    assert migrated.flow_generation_unresolved is False
    assert migrated.flow_cleanup_unresolved is False
    assert migrated.flow_recovery_state is FlowRecoveryState.NONE
    assert migrated.flow_recovery_attempts == 0
    assert migrated.flow_workspace_retry_attempts == 0
    assert migrated.flow_missing_clip_index == 0
    assert migrated.flow_recovery_baseline == ""
    assert migrated.canva_restart_attempts == 0
    assert migrated.last_progress_at == ""
    assert migrated.last_progress_milestone == ""
    assert migrated.create_canva_captions is True
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(cloud_agent_jobs)"
            ).fetchall()
        }
    assert {
        "flow_generation_unresolved",
        "flow_cleanup_unresolved",
        "flow_recovery_state",
        "flow_recovery_attempts",
        "flow_workspace_retry_attempts",
        "flow_missing_clip_index",
        "flow_recovery_baseline",
        "canva_restart_attempts",
        "last_progress_at",
        "last_progress_milestone",
        "stage_started_at",
        "canva_attempt_started_at",
        "create_canva_captions",
    } <= columns

    updated = store.patch_job(
        migrated.id,
        audio_duration_seconds=63.25,
        canva_playback_speed=60.0 / 63.25,
        target_final_duration_seconds=63.25,
    )

    assert updated.audio_duration_seconds == pytest.approx(63.25)
    assert updated.canva_playback_speed == pytest.approx(60.0 / 63.25)
    assert updated.target_final_duration_seconds == pytest.approx(63.25)

    reopened = CloudJobStore(str(db_path)).get_job("legacy-job")
    assert reopened is not None
    assert reopened.audio_duration_seconds == pytest.approx(63.25)
    assert reopened.canva_playback_speed == pytest.approx(60.0 / 63.25)
    assert reopened.target_final_duration_seconds == pytest.approx(63.25)


def test_flow_ready_and_unresolved_cleanup_are_persisted_together(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))
    created = store.create_job(_request())

    updated = store.patch_job(
        created.id,
        status=CloudJobStatus.FLOW_READY,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="flow_ready",
        progress=60,
        flow_cleanup_unresolved=True,
    )

    assert updated.flow_cleanup_unresolved is True
    reopened = CloudJobStore(str(db_path)).get_job(created.id)
    assert reopened is not None
    assert reopened.status is CloudJobStatus.FLOW_READY
    assert reopened.checkpoint is CloudJobCheckpoint.FLOW_READY
    assert reopened.current_step == "flow_ready"
    assert reopened.progress == 60
    assert reopened.flow_cleanup_unresolved is True

    resolved = store.patch_job(created.id, flow_cleanup_unresolved=False)
    assert resolved.flow_cleanup_unresolved is False
    assert resolved.checkpoint is CloudJobCheckpoint.FLOW_READY


def test_generation_fence_defaults_and_atomic_state_transitions(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))
    created = store.create_job(_request())

    assert created.flow_generation_unresolved is False
    reopened = CloudJobStore(str(db_path)).get_job(created.id)
    assert reopened is not None
    assert reopened.flow_generation_unresolved is False

    fenced = store.patch_job(
        created.id,
        status=CloudJobStatus.FLOW_GENERATING,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="flow_generating",
        progress=35,
        flow_generation_unresolved=True,
    )

    assert fenced.status is CloudJobStatus.FLOW_GENERATING
    assert fenced.checkpoint is CloudJobCheckpoint.TTS_READY
    assert fenced.flow_generation_unresolved is True
    reopened = CloudJobStore(str(db_path)).get_job(created.id)
    assert reopened is not None
    assert reopened.flow_generation_unresolved is True

    ready = store.patch_job(
        created.id,
        status=CloudJobStatus.FLOW_READY,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
        current_step="flow_ready",
        progress=60,
        flow_generation_unresolved=False,
        flow_cleanup_unresolved=True,
    )

    assert ready.status is CloudJobStatus.FLOW_READY
    assert ready.checkpoint is CloudJobCheckpoint.FLOW_READY
    assert ready.flow_generation_unresolved is False
    assert ready.flow_cleanup_unresolved is True
    reopened = CloudJobStore(str(db_path)).get_job(created.id)
    assert reopened is not None
    assert reopened.flow_generation_unresolved is False
    assert reopened.flow_cleanup_unresolved is True


def test_list_jobs_is_newest_first_and_supports_pagination(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    first = store.create_job(_request(subject="first"))
    second = store.create_job(_request(subject="second"))

    first_page = store.list_jobs(limit=1, offset=0)
    second_page = store.list_jobs(limit=1, offset=1)

    assert [job.id for job in first_page] == [second.id]
    assert [job.id for job in second_page] == [first.id]


def test_patch_job_updates_status_checkpoint_and_progress(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_job(_request())

    updated = store.patch_job(
        created.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
        progress=25,
        voice_file="storage/jobs/job/audio/voice.mp3",
    )

    assert updated.status is CloudJobStatus.TTS_READY
    assert updated.checkpoint is CloudJobCheckpoint.TTS_READY
    assert updated.current_step == "tts_ready"
    assert updated.progress == 25
    assert updated.voice_file.endswith("voice.mp3")


def test_resume_job_rejects_non_resumable_state_without_mutating_progress(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_job(_request())
    before = store.get_job(created.id)
    assert before is not None

    with pytest.raises(ValueError, match="not resumable"):
        store.resume_job(created.id)

    after = store.get_job(created.id)
    assert after is not None
    assert after.status is before.status
    assert after.current_step == before.current_step
    assert after.last_progress_at == before.last_progress_at
    assert after.last_progress_milestone == before.last_progress_milestone


def test_claim_is_exclusive_until_owner_releases_lease(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_job(_request())

    claimed = store.claim_next_job("worker-a", lease_seconds=60)

    assert claimed is not None
    assert claimed.id == created.id
    assert claimed.worker_id == "worker-a"
    assert claimed.lease_until
    assert store.claim_next_job("worker-b", lease_seconds=60) is None
    assert store.release_lease(created.id, "worker-b") is False
    assert store.release_lease(created.id, "worker-a") is True


def test_renew_lease_only_succeeds_for_current_owner(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_job(_request())
    claimed = store.claim_next_job("worker-a", lease_seconds=60)
    assert claimed is not None
    first_lease = claimed.lease_until

    assert store.renew_lease(created.id, "worker-b", lease_seconds=120) is False
    assert store.renew_lease(created.id, "worker-a", lease_seconds=120) is True

    renewed = store.get_job(created.id)
    assert renewed is not None
    assert renewed.worker_id == "worker-a"
    assert renewed.lease_until > first_lease


def test_expired_lease_can_be_reclaimed_by_another_worker(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    created = store.create_job(_request())
    claimed = store.claim_next_job("worker-a", lease_seconds=60)
    assert claimed is not None

    store.patch_job(
        created.id,
        status=CloudJobStatus.FLOW_DOWNLOADING,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="flow_downloading",
        lease_until="2000-01-01T00:00:00+00:00",
    )

    reclaimed = store.claim_next_job("worker-b", lease_seconds=60)

    assert reclaimed is not None
    assert reclaimed.id == created.id
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.status is CloudJobStatus.FLOW_DOWNLOADING
    assert reclaimed.checkpoint is CloudJobCheckpoint.TTS_READY


def test_paused_human_required_and_terminal_jobs_are_never_auto_claimed(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    blocked_statuses = [
        CloudJobStatus.DRAFT,
        CloudJobStatus.SCRIPT_READY,
        CloudJobStatus.PROMPT_READY,
        CloudJobStatus.PAUSED,
        CloudJobStatus.HUMAN_REQUIRED,
        CloudJobStatus.COMPLETED,
        CloudJobStatus.FAILED,
        CloudJobStatus.CANCELLED,
    ]

    for status in blocked_statuses:
        job = store.create_job(_request(subject=status.value))
        store.patch_job(job.id, status=status)

    assert store.claim_next_job("worker-a", lease_seconds=60) is None


def test_worker_heartbeat_persists_and_can_return_latest_worker(tmp_path):
    db_path = tmp_path / "agent.sqlite3"
    store = CloudJobStore(str(db_path))

    store.update_worker_heartbeat("worker-a", now="2026-08-22T06:30:00+00:00")
    store.update_worker_heartbeat("worker-b", now="2026-08-22T06:31:00+00:00")

    reopened = CloudJobStore(str(db_path))
    assert reopened.get_worker_last_seen("worker-a") == "2026-08-22T06:30:00+00:00"
    assert reopened.get_worker_last_seen() == "2026-08-22T06:31:00+00:00"


def test_flow_attempt_is_reserved_before_caller_can_submit(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())

    reserved = store.reserve_flow_recovery_attempt(job.id, missing_index=2)

    assert reserved.flow_recovery_attempts == 1
    assert reserved.flow_missing_clip_index == 2
    assert reserved.flow_recovery_state is FlowRecoveryState.SUBMISSION_UNRESOLVED
    assert store.get_job(job.id) == reserved


def test_flow_attempt_budget_stops_after_two(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())

    store.reserve_flow_recovery_attempt(job.id, missing_index=2)
    store.patch_job(job.id, flow_recovery_state=FlowRecoveryState.READY_TO_SUBMIT)
    store.reserve_flow_recovery_attempt(job.id, missing_index=2)

    with pytest.raises(RecoveryBudgetExhausted):
        store.reserve_flow_recovery_attempt(job.id, missing_index=2)


def test_flow_workspace_retry_budget_is_reserved_durably_before_reopen(tmp_path):
    """Catches retries reopening Flow without consuming their durable budget."""
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    claimed = store.claim_next_job("worker-a", lease_seconds=60)
    assert claimed is not None
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
    )

    first = store.reserve_flow_workspace_retry(
        job.id,
        delay_seconds=30.0,
        worker_id="worker-a",
    )
    assert first is not None
    first_opening = store.begin_flow_workspace_retry_opening(
        job.id,
        worker_id="worker-a",
    )
    assert first_opening is not None
    second = store.reserve_flow_workspace_retry(
        job.id,
        delay_seconds=120.0,
        worker_id="worker-a",
    )
    assert second is not None

    assert first.flow_workspace_retry_attempts == 1
    assert second.flow_workspace_retry_attempts == 2
    assert first.flow_workspace_retry_not_before
    assert second.flow_workspace_retry_not_before > first.flow_workspace_retry_not_before
    second_opening = store.begin_flow_workspace_retry_opening(
        job.id,
        worker_id="worker-a",
    )
    assert second_opening is not None
    with pytest.raises(RecoveryBudgetExhausted):
        store.reserve_flow_workspace_retry(
            job.id,
            delay_seconds=120.0,
            worker_id="worker-a",
        )


def test_flow_workspace_retry_opening_transition_requires_current_lease_owner(
    tmp_path,
):
    """Only the worker owning the lease may consume a reserved reopen."""
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    claimed = store.claim_next_job("worker-new", lease_seconds=60)
    assert claimed is not None
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
    )
    reserved = store.reserve_flow_workspace_retry(
        job.id,
        delay_seconds=30.0,
        worker_id="worker-new",
    )
    assert reserved is not None

    assert (
        store.begin_flow_workspace_retry_opening(
            job.id,
            worker_id="worker-old",
        )
        is None
    )
    unchanged = store.get_job(job.id)
    assert unchanged.current_step == "flow_workspace_retrying"
    assert unchanged.flow_workspace_retry_not_before == reserved.flow_workspace_retry_not_before

    opening = store.begin_flow_workspace_retry_opening(
        job.id,
        worker_id="worker-new",
    )
    assert opening.current_step == "flow_workspace_retry_opening"
    assert opening.flow_workspace_retry_not_before == ""


def test_flow_workspace_retry_reservation_cannot_overwrite_new_lease_owner(
    tmp_path,
):
    """A stale child cannot consume retry budget after lease handoff."""
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    claimed = store.claim_next_job("worker-old", lease_seconds=60)
    assert claimed is not None
    store.patch_job(
        job.id,
        status=CloudJobStatus.TTS_READY,
        checkpoint=CloudJobCheckpoint.TTS_READY,
        current_step="tts_ready",
        worker_id="worker-new",
        lease_until="2026-08-28T23:59:59.999999+00:00",
    )
    store.patch_job(
        job.id,
        status=CloudJobStatus.HUMAN_REQUIRED,
        current_step="human_required",
        error_code="HUMAN_REQUIRED",
    )

    reserved = store.reserve_flow_workspace_retry(
        job.id,
        delay_seconds=30.0,
        worker_id="worker-old",
    )

    assert reserved is None
    unchanged = store.get_job(job.id)
    assert unchanged.status is CloudJobStatus.HUMAN_REQUIRED
    assert unchanged.current_step == "human_required"
    assert unchanged.flow_workspace_retry_attempts == 0
    assert unchanged.worker_id == "worker-new"


def test_canva_restart_budget_stops_after_four(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())
    store.patch_job(
        job.id,
        status=CloudJobStatus.FLOW_READY,
        checkpoint=CloudJobCheckpoint.FLOW_READY,
    )

    for expected in range(1, 5):
        assert store.reserve_canva_restart(job.id).canva_restart_attempts == expected

    with pytest.raises(RecoveryBudgetExhausted):
        store.reserve_canva_restart(job.id)


def test_mark_progress_only_advances_for_a_new_milestone(tmp_path):
    store = CloudJobStore(str(tmp_path / "agent.sqlite3"))
    job = store.create_job(_request())

    first = store.mark_progress(
        job.id,
        "flow.inventory.5",
        at="2026-08-28T00:00:00+00:00",
    )
    repeated = store.mark_progress(
        job.id,
        "flow.inventory.5",
        at="2026-08-28T00:01:00+00:00",
    )

    assert first.last_progress_at == "2026-08-28T00:00:00+00:00"
    assert repeated.last_progress_at == first.last_progress_at
