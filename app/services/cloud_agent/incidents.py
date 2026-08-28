import sqlite3
from pathlib import Path
from uuid import uuid4

from app.models.cloud_agent import CloudJobIncident, CloudJobRecord
from app.services.cloud_agent.job_store import _CLAIMABLE_STATUSES, _utc_now


class CloudJobIncidentStore:
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
                CREATE TABLE IF NOT EXISTS cloud_agent_incidents (
                    id TEXT PRIMARY KEY,
                    former_job_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    flow_attempts INTEGER NOT NULL,
                    canva_attempts INTEGER NOT NULL,
                    message_th TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    dismissed_at TEXT NOT NULL DEFAULT '',
                    finalized INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> CloudJobIncident:
        return CloudJobIncident(
            id=row["id"],
            former_job_id=row["former_job_id"],
            subject=row["subject"],
            stage=row["stage"],
            reason_code=row["reason_code"],
            flow_attempts=row["flow_attempts"],
            canva_attempts=row["canva_attempts"],
            message_th=row["message_th"],
            created_at=row["created_at"],
            dismissed_at=row["dismissed_at"],
            finalized=bool(row["finalized"]),
        )

    def create_pending(
        self,
        job: CloudJobRecord,
        *,
        reason_code: str,
        stage: str,
        message_th: str,
    ) -> CloudJobIncident:
        incident = CloudJobIncident(
            id=str(uuid4()),
            former_job_id=job.id,
            subject=job.subject,
            stage=stage,
            reason_code=reason_code,
            flow_attempts=job.flow_recovery_attempts,
            canva_attempts=job.canva_restart_attempts,
            message_th=message_th,
            created_at=_utc_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cloud_agent_incidents (
                    id, former_job_id, subject, stage, reason_code,
                    flow_attempts, canva_attempts, message_th, created_at,
                    dismissed_at, finalized
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.id,
                    incident.former_job_id,
                    incident.subject,
                    incident.stage,
                    incident.reason_code,
                    incident.flow_attempts,
                    incident.canva_attempts,
                    incident.message_th,
                    incident.created_at,
                    incident.dismissed_at,
                    int(incident.finalized),
                ),
            )
        return incident

    def list_unread(self, *, limit: int = 20) -> tuple[CloudJobIncident, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM cloud_agent_incidents
                WHERE dismissed_at = ''
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(self._row_to_incident(row) for row in rows)

    def dismiss(self, incident_id: str) -> CloudJobIncident:
        dismissed_at = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE cloud_agent_incidents SET dismissed_at = ?
                WHERE id = ? AND dismissed_at = ''
                """,
                (dismissed_at, incident_id),
            )
            if result.rowcount != 1:
                raise KeyError(incident_id)
            row = connection.execute(
                "SELECT * FROM cloud_agent_incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        return self._row_to_incident(row)

    def finalize_and_delete_job(
        self, incident_id: str, job_id: str
    ) -> CloudJobIncident:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            incident_row = connection.execute(
                "SELECT * FROM cloud_agent_incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if incident_row is None:
                raise KeyError(incident_id)
            if incident_row["former_job_id"] != job_id:
                raise ValueError("incident belongs to another job")

            job_row = connection.execute(
                "SELECT * FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if bool(incident_row["finalized"]):
                if job_row is not None:
                    raise ValueError("finalized incident still has a job")
                connection.commit()
                return self._row_to_incident(incident_row)
            if job_row is None:
                raise KeyError(job_id)
            claimable = {status.value for status in _CLAIMABLE_STATUSES}
            if (
                job_row["status"] in claimable
                or job_row["worker_id"]
                or job_row["lease_until"]
            ):
                raise ValueError("job is still claimable")

            connection.execute(
                "UPDATE cloud_agent_incidents SET finalized = 1 WHERE id = ?",
                (incident_id,),
            )
            deleted = connection.execute(
                "DELETE FROM cloud_agent_jobs WHERE id = ?", (job_id,)
            )
            if deleted.rowcount != 1:
                raise KeyError(job_id)
            finalized_row = connection.execute(
                "SELECT * FROM cloud_agent_incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            connection.commit()
            return self._row_to_incident(finalized_row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
