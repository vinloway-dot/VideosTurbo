import sqlite3
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from app.models.cloud_agent import (
    CloudControlRequest,
    CloudJobCheckpoint,
    CloudJobCreate,
    CloudJobRecord,
    CloudJobStatus,
    FlowRecoveryState,
)
from app.models.six_clip import SixClipPlan
from app.services.cloud_agent.errors import RecoveryBudgetExhausted


_CLAIMABLE_STATUSES = (
    CloudJobStatus.QUEUED,
    CloudJobStatus.PREFLIGHT,
    CloudJobStatus.PREFLIGHT_PASSED,
    CloudJobStatus.TTS_GENERATING,
    CloudJobStatus.TTS_READY,
    CloudJobStatus.FLOW_GENERATING,
    CloudJobStatus.FLOW_DOWNLOADING,
    CloudJobStatus.FLOW_READY,
    CloudJobStatus.CANVA_UPLOADING,
    CloudJobStatus.CANVA_EDITING,
    CloudJobStatus.CAPTIONING,
    CloudJobStatus.EXPORTING,
    CloudJobStatus.DOWNLOADING_FINAL,
    CloudJobStatus.VALIDATING,
    CloudJobStatus.FINAL_VALIDATED,
)

_COMPATIBLE_COLUMNS = {
    "audio_duration_seconds": "REAL NOT NULL DEFAULT 0",
    "canva_playback_speed": "REAL NOT NULL DEFAULT 1",
    "target_final_duration_seconds": "REAL NOT NULL DEFAULT 60",
    "flow_generation_unresolved": "INTEGER NOT NULL DEFAULT 0",
    "flow_cleanup_unresolved": "INTEGER NOT NULL DEFAULT 0",
    "canva_design_url": "TEXT NOT NULL DEFAULT ''",
    "canva_audio_card_count": "INTEGER NOT NULL DEFAULT -1",
    "create_canva_captions": "INTEGER NOT NULL DEFAULT 0",
    "last_progress_at": "TEXT NOT NULL DEFAULT ''",
    "last_progress_milestone": "TEXT NOT NULL DEFAULT ''",
    "stage_started_at": "TEXT NOT NULL DEFAULT ''",
    "flow_recovery_attempts": "INTEGER NOT NULL DEFAULT 0",
    "flow_workspace_retry_attempts": "INTEGER NOT NULL DEFAULT 0",
    "flow_workspace_retry_not_before": "TEXT NOT NULL DEFAULT ''",
    "flow_missing_clip_index": "INTEGER NOT NULL DEFAULT 0",
    "flow_recovery_state": "TEXT NOT NULL DEFAULT 'NONE'",
    "flow_recovery_baseline": "TEXT NOT NULL DEFAULT ''",
    "canva_restart_attempts": "INTEGER NOT NULL DEFAULT 0",
    "canva_attempt_started_at": "TEXT NOT NULL DEFAULT ''",
}

_MUTABLE_COLUMNS = {
    "status",
    "checkpoint",
    "control_request",
    "current_step",
    "progress",
    "flow_status",
    "canva_status",
    "voice_file",
    "audio_duration_seconds",
    "canva_playback_speed",
    "target_final_duration_seconds",
    "flow_generation_unresolved",
    "flow_cleanup_unresolved",
    "canva_design_url",
    "canva_audio_card_count",
    "last_progress_at",
    "last_progress_milestone",
    "stage_started_at",
    "flow_recovery_attempts",
    "flow_workspace_retry_attempts",
    "flow_workspace_retry_not_before",
    "flow_missing_clip_index",
    "flow_recovery_state",
    "flow_recovery_baseline",
    "canva_restart_attempts",
    "canva_attempt_started_at",
    "final_video",
    "error_code",
    "error_message",
    "worker_id",
    "lease_until",
    "started_at",
    "completed_at",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _lease_until(seconds: int) -> str:
    if seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="microseconds"
    )


def _db_value(value):
    return value.value if isinstance(value, Enum) else value


class CloudJobStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cloud_agent_jobs (
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
                    create_canva_captions INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    checkpoint TEXT NOT NULL,
                    control_request TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    flow_status TEXT NOT NULL,
                    canva_status TEXT NOT NULL,
                    voice_file TEXT NOT NULL,
                    audio_duration_seconds REAL NOT NULL DEFAULT 0,
                    canva_playback_speed REAL NOT NULL DEFAULT 1,
                    target_final_duration_seconds REAL NOT NULL DEFAULT 60,
                    flow_generation_unresolved INTEGER NOT NULL DEFAULT 0,
                    flow_cleanup_unresolved INTEGER NOT NULL DEFAULT 0,
                    canva_design_url TEXT NOT NULL DEFAULT '',
                    canva_audio_card_count INTEGER NOT NULL DEFAULT -1,
                    last_progress_at TEXT NOT NULL DEFAULT '',
                    last_progress_milestone TEXT NOT NULL DEFAULT '',
                    stage_started_at TEXT NOT NULL DEFAULT '',
                    flow_recovery_attempts INTEGER NOT NULL DEFAULT 0,
                    flow_workspace_retry_attempts INTEGER NOT NULL DEFAULT 0,
                    flow_workspace_retry_not_before TEXT NOT NULL DEFAULT '',
                    flow_missing_clip_index INTEGER NOT NULL DEFAULT 0,
                    flow_recovery_state TEXT NOT NULL DEFAULT 'NONE',
                    flow_recovery_baseline TEXT NOT NULL DEFAULT '',
                    canva_restart_attempts INTEGER NOT NULL DEFAULT 0,
                    canva_attempt_started_at TEXT NOT NULL DEFAULT '',
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
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(cloud_agent_jobs)"
                ).fetchall()
            }
            legacy_jobs_need_caption_backfill = (
                "create_canva_captions" not in existing_columns
            )
            for column, definition in _COMPATIBLE_COLUMNS.items():
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE cloud_agent_jobs ADD COLUMN {column} {definition}"
                    )
            if legacy_jobs_need_caption_backfill:
                connection.execute(
                    "UPDATE cloud_agent_jobs SET create_canva_captions = 1"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cloud_agent_workers (
                    worker_id TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """UPDATE cloud_agent_jobs
                   SET completed_at = updated_at
                   WHERE status = ? AND completed_at = ''""",
                (CloudJobStatus.COMPLETED.value,),
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CloudJobRecord:
        return CloudJobRecord(
            id=row["id"],
            subject=row["subject"],
            script=row["script"],
            master_prompt=row["master_prompt"],
            clip_plan=SixClipPlan.model_validate_json(row["clip_plan_json"]),
            language=row["language"],
            target_words=row["target_words"],
            tts_provider=row["tts_provider"],
            voice_id=row["voice_id"],
            voice_speed=row["voice_speed"],
            create_canva_captions=bool(row["create_canva_captions"]),
            status=CloudJobStatus(row["status"]),
            checkpoint=CloudJobCheckpoint(row["checkpoint"]),
            control_request=CloudControlRequest(row["control_request"]),
            current_step=row["current_step"],
            progress=row["progress"],
            flow_status=row["flow_status"],
            canva_status=row["canva_status"],
            voice_file=row["voice_file"],
            audio_duration_seconds=row["audio_duration_seconds"],
            canva_playback_speed=row["canva_playback_speed"],
            target_final_duration_seconds=row["target_final_duration_seconds"],
            flow_generation_unresolved=bool(row["flow_generation_unresolved"]),
            flow_cleanup_unresolved=bool(row["flow_cleanup_unresolved"]),
            canva_design_url=row["canva_design_url"],
            canva_audio_card_count=row["canva_audio_card_count"],
            last_progress_at=row["last_progress_at"],
            last_progress_milestone=row["last_progress_milestone"],
            stage_started_at=row["stage_started_at"],
            flow_recovery_attempts=row["flow_recovery_attempts"],
            flow_workspace_retry_attempts=row["flow_workspace_retry_attempts"],
            flow_workspace_retry_not_before=row["flow_workspace_retry_not_before"],
            flow_missing_clip_index=row["flow_missing_clip_index"],
            flow_recovery_state=FlowRecoveryState(row["flow_recovery_state"]),
            flow_recovery_baseline=row["flow_recovery_baseline"],
            canva_restart_attempts=row["canva_restart_attempts"],
            canva_attempt_started_at=row["canva_attempt_started_at"],
            final_video=row["final_video"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            worker_id=row["worker_id"],
            lease_until=row["lease_until"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            updated_at=row["updated_at"],
        )

    def create_job(
        self,
        request: CloudJobCreate,
        *,
        status: CloudJobStatus = CloudJobStatus.QUEUED,
    ) -> CloudJobRecord:
        now = _utc_now()
        job_id = str(uuid4())
        record = CloudJobRecord(
            **request.model_dump(),
            id=job_id,
            status=status,
            checkpoint=CloudJobCheckpoint.NONE,
            control_request=CloudControlRequest.NONE,
            current_step="queued",
            progress=0,
            flow_status="",
            canva_status="",
            voice_file="",
            final_video="",
            error_code="",
            error_message="",
            worker_id="",
            lease_until="",
            created_at=now,
            started_at="",
            completed_at="",
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cloud_agent_jobs (
                    id, subject, script, master_prompt, clip_plan_json, language,
                    target_words, tts_provider, voice_id, voice_speed,
                    create_canva_captions, status,
                    checkpoint, control_request, current_step, progress,
                    flow_status, canva_status, voice_file, audio_duration_seconds,
                    canva_playback_speed, target_final_duration_seconds,
                    flow_generation_unresolved, flow_cleanup_unresolved,
                    canva_design_url, canva_audio_card_count,
                    last_progress_at, last_progress_milestone, stage_started_at,
                    flow_recovery_attempts, flow_workspace_retry_attempts,
                    flow_workspace_retry_not_before,
                    flow_missing_clip_index,
                    flow_recovery_state, flow_recovery_baseline,
                    canva_restart_attempts, canva_attempt_started_at,
                    final_video, error_code,
                    error_message, worker_id, lease_until, created_at, started_at,
                    completed_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    record.id,
                    record.subject,
                    record.script,
                    record.master_prompt,
                    record.clip_plan.model_dump_json(),
                    record.language,
                    record.target_words,
                    record.tts_provider,
                    record.voice_id,
                    record.voice_speed,
                    record.create_canva_captions,
                    record.status.value,
                    record.checkpoint.value,
                    record.control_request.value,
                    record.current_step,
                    record.progress,
                    record.flow_status,
                    record.canva_status,
                    record.voice_file,
                    record.audio_duration_seconds,
                    record.canva_playback_speed,
                    record.target_final_duration_seconds,
                    record.flow_generation_unresolved,
                    record.flow_cleanup_unresolved,
                    record.canva_design_url,
                    record.canva_audio_card_count,
                    record.last_progress_at,
                    record.last_progress_milestone,
                    record.stage_started_at,
                    record.flow_recovery_attempts,
                    record.flow_workspace_retry_attempts,
                    record.flow_workspace_retry_not_before,
                    record.flow_missing_clip_index,
                    record.flow_recovery_state.value,
                    record.flow_recovery_baseline,
                    record.canva_restart_attempts,
                    record.canva_attempt_started_at,
                    record.final_video,
                    record.error_code,
                    record.error_message,
                    record.worker_id,
                    record.lease_until,
                    record.created_at,
                    record.started_at,
                    record.completed_at,
                    record.updated_at,
                ),
            )
        return record

    def get_job(self, job_id: str) -> CloudJobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_jobs(self, limit: int = 50, offset: int = 0) -> list[CloudJobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM cloud_agent_jobs
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_completed_final_candidates(self) -> list[CloudJobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM cloud_agent_jobs
                   WHERE status = ? AND checkpoint IN (?, ?)
                   ORDER BY completed_at DESC, id DESC""",
                (
                    CloudJobStatus.COMPLETED.value,
                    CloudJobCheckpoint.FINAL_VALIDATED.value,
                    CloudJobCheckpoint.COMPLETED.value,
                ),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def delete_job(self, job_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def patch_job(self, job_id: str, **changes) -> CloudJobRecord:
        if not changes:
            existing = self.get_job(job_id)
            if existing is None:
                raise KeyError(job_id)
            return existing

        unknown = set(changes) - _MUTABLE_COLUMNS
        if unknown:
            raise ValueError(f"unsupported job fields: {sorted(unknown)}")

        existing = self.get_job(job_id)
        if existing is None:
            raise KeyError(job_id)

        now = _utc_now()
        next_status = changes.get("status", existing.status)
        if not isinstance(next_status, CloudJobStatus):
            next_status = CloudJobStatus(next_status)
        completing = (
            existing.status is not CloudJobStatus.COMPLETED
            and next_status is CloudJobStatus.COMPLETED
        )
        if completing:
            changes["checkpoint"] = CloudJobCheckpoint.COMPLETED
            changes["progress"] = 100
            if not str(changes.get("completed_at") or existing.completed_at).strip():
                changes["completed_at"] = now
        elif (
            next_status is CloudJobStatus.COMPLETED
            and not str(changes.get("completed_at") or existing.completed_at).strip()
        ):
            changes["completed_at"] = now

        candidate_data = existing.model_dump()
        candidate_data.update(changes)
        candidate_data["updated_at"] = now
        candidate = CloudJobRecord.model_validate(candidate_data)

        columns = [*changes.keys(), "updated_at"]
        values = [_db_value(getattr(candidate, column)) for column in columns]
        assignments = ", ".join(f"{column} = ?" for column in columns)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE cloud_agent_jobs SET {assignments} WHERE id = ?",
                (*values, job_id),
            )
        return candidate

    def resume_job(self, job_id: str) -> CloudJobRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            status = CloudJobStatus(row["status"])
            if status not in {
                CloudJobStatus.PAUSED,
                CloudJobStatus.HUMAN_REQUIRED,
            }:
                raise ValueError(
                    f"cloud agent job is not resumable from status {status.value}"
                )

            now = _utc_now()
            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET status = ?, current_step = ?, control_request = ?,
                    error_code = '', error_message = '',
                    last_progress_at = ?, last_progress_milestone = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    CloudJobStatus.QUEUED.value,
                    "queued",
                    CloudControlRequest.NONE.value,
                    now,
                    "operator.resumed",
                    now,
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_progress(
        self,
        job_id: str,
        milestone: str,
        *,
        at: str | datetime | None = None,
    ) -> CloudJobRecord:
        normalized = str(milestone or "").strip()
        if not normalized:
            raise ValueError("milestone must not be blank")
        if len(normalized) > 128:
            raise ValueError("milestone is too long")
        if isinstance(at, datetime):
            occurred_at = at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        else:
            occurred_at = str(at or _utc_now())

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["last_progress_milestone"] != normalized:
                connection.execute(
                    """
                    UPDATE cloud_agent_jobs
                    SET last_progress_at = ?, last_progress_milestone = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (occurred_at, normalized, _utc_now(), job_id),
                )
            updated = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_flow_recovery_attempt(
        self, job_id: str, *, missing_index: int
    ) -> CloudJobRecord:
        if missing_index < 1 or missing_index > 6:
            raise ValueError("missing_index must be between 1 and 6")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["flow_recovery_attempts"] >= 2:
                raise RecoveryBudgetExhausted("Flow recovery budget exhausted")
            if row["flow_missing_clip_index"] not in (0, missing_index):
                raise ValueError("missing clip index changed during recovery")
            now = _utc_now()
            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET flow_recovery_attempts = flow_recovery_attempts + 1,
                    flow_missing_clip_index = ?, flow_recovery_state = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    missing_index,
                    FlowRecoveryState.SUBMISSION_UNRESOLVED.value,
                    now,
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_canva_restart(self, job_id: str) -> CloudJobRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if (
                CloudJobCheckpoint(row["checkpoint"])
                is not CloudJobCheckpoint.FLOW_READY
            ):
                raise ValueError("Canva restart requires FLOW_READY checkpoint")
            if row["canva_restart_attempts"] >= 4:
                raise RecoveryBudgetExhausted("Canva restart budget exhausted")
            now = _utc_now()
            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET canva_restart_attempts = canva_restart_attempts + 1,
                    canva_attempt_started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_flow_workspace_retry(
        self,
        job_id: str,
        *,
        delay_seconds: float,
        worker_id: str,
        enter_inventory_recovery: bool = False,
    ) -> CloudJobRecord | None:
        """Reserve one bounded Flow reopen, optionally fencing downloaded inventory."""
        delay = float(delay_seconds)
        if delay <= 0:
            raise ValueError("Flow workspace retry delay must be positive")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            now = _utc_now()
            status = CloudJobStatus(row["status"])
            active_step = row["current_step"]
            recovery_state = FlowRecoveryState(row["flow_recovery_state"])
            unresolved = bool(row["flow_generation_unresolved"])
            safe_ready_state = (
                status is CloudJobStatus.TTS_READY
                and active_step in {"tts_ready", "flow_workspace_retry_opening"}
            ) or (status is CloudJobStatus.QUEUED and active_step == "queued")
            safe_inventory_state = (
                status is CloudJobStatus.FLOW_GENERATING
                and active_step in {"flow_generating", "flow_reconciling"}
                and (
                    (
                        recovery_state is FlowRecoveryState.INVENTORY_PENDING
                        and not unresolved
                    )
                    or (
                        enter_inventory_recovery
                        and recovery_state
                        in {
                            FlowRecoveryState.NONE,
                            FlowRecoveryState.INVENTORY_PENDING,
                        }
                    )
                )
            )
            eligible = (
                CloudJobCheckpoint(row["checkpoint"]) is CloudJobCheckpoint.TTS_READY
                and (safe_ready_state or safe_inventory_state)
                and CloudControlRequest(row["control_request"])
                is CloudControlRequest.NONE
                and row["worker_id"] == worker_id
                and bool(row["worker_id"])
                and bool(row["lease_until"])
                and row["lease_until"] > now
                and (not unresolved or enter_inventory_recovery)
                and recovery_state
                in {FlowRecoveryState.NONE, FlowRecoveryState.INVENTORY_PENDING}
            )
            if not eligible:
                connection.commit()
                return None
            if row["flow_workspace_retry_attempts"] >= 2:
                raise RecoveryBudgetExhausted("Flow workspace retry budget exhausted")
            not_before = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            ).isoformat(timespec="microseconds")
            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET flow_workspace_retry_attempts = flow_workspace_retry_attempts + 1,
                    flow_workspace_retry_not_before = ?,
                    status = ?, current_step = ?, progress = 30,
                    flow_generation_unresolved = ?, flow_recovery_state = ?,
                    error_code = '', error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    not_before,
                    CloudJobStatus.TTS_READY.value,
                    "flow_workspace_retrying",
                    0 if enter_inventory_recovery else int(unresolved),
                    (
                        FlowRecoveryState.INVENTORY_PENDING.value
                        if enter_inventory_recovery
                        else recovery_state.value
                    ),
                    now,
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def begin_flow_workspace_retry_opening(
        self,
        job_id: str,
        *,
        worker_id: str,
    ) -> CloudJobRecord | None:
        """Atomically consume a reserved reopen only for its live lease owner."""
        now = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET current_step = ?, flow_workspace_retry_not_before = '',
                    updated_at = ?
                WHERE id = ?
                  AND checkpoint = ?
                  AND status = ?
                  AND current_step = ?
                  AND control_request = ?
                  AND worker_id = ?
                  AND worker_id <> ''
                  AND lease_until <> ''
                  AND lease_until > ?
                """,
                (
                    "flow_workspace_retry_opening",
                    now,
                    job_id,
                    CloudJobCheckpoint.TTS_READY.value,
                    CloudJobStatus.TTS_READY.value,
                    "flow_workspace_retrying",
                    CloudControlRequest.NONE.value,
                    worker_id,
                    now,
                ),
            )
            if result.rowcount != 1:
                connection.commit()
                return None
            updated = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(updated)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_next_job(
        self, worker_id: str, lease_seconds: int
    ) -> CloudJobRecord | None:
        now = _utc_now()
        new_lease_until = _lease_until(lease_seconds)
        placeholders = ", ".join("?" for _ in _CLAIMABLE_STATUSES)
        status_values = tuple(status.value for status in _CLAIMABLE_STATUSES)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT * FROM cloud_agent_jobs
                WHERE status IN ({placeholders})
                  AND (worker_id = '' OR lease_until = '' OR lease_until <= ?)
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (*status_values, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            started_at = row["started_at"] or now
            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET worker_id = ?, lease_until = ?, started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_id, new_lease_until, started_at, now, row["id"]),
            )
            claimed_row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
            connection.commit()
            return self._row_to_record(claimed_row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def requeue_pre_flow_retry(
        self,
        job_id: str,
        *,
        voice_file: str,
        audio_duration_seconds: float,
        canva_playback_speed: float,
        target_final_duration_seconds: float,
    ) -> CloudJobRecord:
        """Atomically requeue only the exact durable pre-Flow failure state."""
        now = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("cloud agent job not found")
            current = self._row_to_record(row)
            if not (
                current.status is CloudJobStatus.FAILED
                and current.checkpoint is CloudJobCheckpoint.TTS_READY
                and current.error_code == "FLOW_WORKSPACE_VERIFICATION_FAILED"
                and not current.flow_generation_unresolved
                and not current.flow_cleanup_unresolved
                and not current.worker_id
                and not current.lease_until
            ):
                raise ValueError("job is not in a retryable pre-Flow state")

            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET status = ?, current_step = ?, control_request = ?,
                    voice_file = ?, audio_duration_seconds = ?,
                    canva_playback_speed = ?, target_final_duration_seconds = ?,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    CloudJobStatus.QUEUED.value,
                    "queued",
                    CloudControlRequest.NONE.value,
                    voice_file,
                    audio_duration_seconds,
                    canva_playback_speed,
                    target_final_duration_seconds,
                    "",
                    "",
                    now,
                    job_id,
                ),
            )
            retried_row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(retried_row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def requeue_flow_inventory_retry(
        self,
        job_id: str,
        *,
        voice_file: str,
        audio_duration_seconds: float,
        canva_playback_speed: float,
        target_final_duration_seconds: float,
    ) -> CloudJobRecord:
        """Atomically requeue the exact failed inventory-recovery boundary."""
        now = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("cloud agent job not found")
            current = self._row_to_record(row)
            if not (
                current.status is CloudJobStatus.FAILED
                and current.checkpoint is CloudJobCheckpoint.TTS_READY
                and current.error_code == "FLOW_ARCHIVE_VALIDATION_FAILED"
                and current.flow_recovery_state is FlowRecoveryState.INVENTORY_PENDING
                and current.flow_recovery_attempts == 0
                and current.flow_missing_clip_index == 0
                and not current.flow_recovery_baseline
                and not current.flow_generation_unresolved
                and not current.flow_cleanup_unresolved
                and not current.worker_id
                and not current.lease_until
            ):
                raise ValueError("job is not in a retryable Flow recovery state")

            connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET status = ?, current_step = ?, control_request = ?,
                    voice_file = ?, audio_duration_seconds = ?,
                    canva_playback_speed = ?, target_final_duration_seconds = ?,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    CloudJobStatus.QUEUED.value,
                    "queued",
                    CloudControlRequest.NONE.value,
                    voice_file,
                    audio_duration_seconds,
                    canva_playback_speed,
                    target_final_duration_seconds,
                    "",
                    "",
                    now,
                    job_id,
                ),
            )
            retried_row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_record(retried_row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = _utc_now()
        new_lease_until = _lease_until(lease_seconds)
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET lease_until = ?, updated_at = ?
                WHERE id = ? AND worker_id = ? AND worker_id <> ''
                """,
                (new_lease_until, now, job_id, worker_id),
            )
        return result.rowcount == 1

    def release_lease(self, job_id: str, worker_id: str) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE cloud_agent_jobs
                SET worker_id = '', lease_until = '', updated_at = ?
                WHERE id = ? AND worker_id = ? AND worker_id <> ''
                """,
                (now, job_id, worker_id),
            )
        return result.rowcount == 1

    def update_worker_heartbeat(
        self, worker_id: str, *, now: str | None = None
    ) -> None:
        last_seen = now or _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cloud_agent_workers (worker_id, last_seen)
                VALUES (?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET last_seen = excluded.last_seen
                """,
                (worker_id, last_seen),
            )

    def get_worker_last_seen(self, worker_id: str | None = None) -> str | None:
        with self._connect() as connection:
            if worker_id is None:
                row = connection.execute(
                    """
                    SELECT last_seen FROM cloud_agent_workers
                    ORDER BY last_seen DESC, worker_id DESC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT last_seen FROM cloud_agent_workers WHERE worker_id = ?",
                    (worker_id,),
                ).fetchone()
        return row["last_seen"] if row is not None else None
