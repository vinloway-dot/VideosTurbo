import hmac
import re
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.models import ResearchUsageAccounting


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_QUERY_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "credential",
        "expires",
        "key",
        "password",
        "policy",
        "s3token",
        "secret",
        "session_token",
        "sig",
        "signature",
        "token",
        "x-amz-algorithm",
        "x-amz-credential",
        "x-amz-date",
        "x-amz-expires",
        "x-amz-security-token",
        "x-amz-signature",
        "x-goog-algorithm",
        "x-goog-credential",
        "x-goog-date",
        "x-goog-expires",
        "x-goog-signature",
        "x-goog-signedheaders",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha256_text(value: str) -> str:
    return sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def _safe_public_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""

    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""

    netloc = parsed.hostname.lower()
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    sanitized_query = urlencode(
        [
            (key, query_value)
            for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_secret_query_key(key)
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, sanitized_query, ""))


def _is_secret_query_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized in _SECRET_QUERY_KEYS
        or normalized.startswith("x-amz-")
        or normalized.startswith("x-goog-")
        or normalized.endswith("_token")
        or normalized.endswith("_signature")
        or normalized.endswith("_secret")
        or normalized.endswith("_key")
    )


class ResearchSourceDraft(BaseModel):
    url: str
    title: str = ""
    body: str = Field(default="", exclude=True)
    content_hash: str = Field(
        default="",
        max_length=64,
        validation_alias=AliasChoices("content_hash", "source_hash"),
    )

    @field_validator("title", "body", "content_hash")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("url")
    @classmethod
    def _sanitize_url(cls, value: str) -> str:
        return _safe_public_url(value)

    @model_validator(mode="after")
    def _derive_content_hash(self):
        if not self.content_hash:
            self.content_hash = sha256_text(self.body or self.url)
        return self


class SuccessfulResearchDraft(BaseModel):
    research_draft_id: str = Field(default_factory=lambda: uuid4().hex, max_length=64)
    script_hash: str = Field(max_length=64)
    provider: str
    model: str
    evidence_mode: Literal["source_evidence + model_knowledge"] = (
        "source_evidence + model_knowledge"
    )
    editable_prompt_fingerprint: str = Field(
        default="",
        max_length=64,
        validation_alias=AliasChoices(
            "editable_prompt_fingerprint",
            "source_prompt_fingerprint",
        ),
    )
    invariant_prompt_fingerprint: str = Field(
        default="",
        max_length=64,
        validation_alias=AliasChoices(
            "invariant_prompt_fingerprint",
            "system_prompt_fingerprint",
        ),
    )
    tool_calls: int = Field(default=0, ge=0)
    provider_rounds: int = Field(default=0, ge=0)
    usage: ResearchUsageAccounting
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    sources: list[ResearchSourceDraft] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @field_validator(
        "research_draft_id",
        "script_hash",
        "provider",
        "model",
        "evidence_mode",
        "editable_prompt_fingerprint",
        "invariant_prompt_fingerprint",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _validate_usage_provider_and_hash(self):
        if self.usage.provider != self.provider or self.usage.model != self.model:
            raise ValueError("usage provider/model must match the persisted draft")
        if not _SHA256_HEX_RE.fullmatch(self.script_hash):
            raise ValueError("script_hash must be a canonical lowercase SHA-256 hex digest")
        return self


class PersistedResearchSource(BaseModel):
    research_source_id: str
    research_draft_id: str
    position: int = Field(ge=0)
    url: str
    title: str = ""
    content_hash: str
    created_at: str


class PersistedResearchDraft(BaseModel):
    research_draft_id: str
    script_hash: str
    provider: str
    model: str
    evidence_mode: str
    source_count: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    provider_rounds: int = Field(ge=0)
    usage: ResearchUsageAccounting
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    editable_prompt_fingerprint: str = ""
    invariant_prompt_fingerprint: str = ""
    created_at: str
    updated_at: str
    sources: list[PersistedResearchSource] = Field(default_factory=list)


class ResearchDraftStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_drafts (
                    research_draft_id TEXT PRIMARY KEY,
                    script_hash TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    evidence_mode TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    cost_available INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    provider_rounds INTEGER NOT NULL DEFAULT 0,
                    editable_prompt_fingerprint TEXT NOT NULL DEFAULT '',
                    invariant_prompt_fingerprint TEXT NOT NULL DEFAULT '',
                    system_prompt_fingerprint TEXT NOT NULL DEFAULT '',
                    source_prompt_fingerprint TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_sources (
                    research_source_id TEXT PRIMARY KEY,
                    research_draft_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(research_draft_id) REFERENCES research_drafts(research_draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            self._ensure_draft_columns(connection)
            connection.execute(
                """
                UPDATE research_drafts
                SET evidence_mode = 'source_evidence + model_knowledge'
                WHERE evidence_mode != 'source_evidence + model_knowledge'
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_job_associations (
                    research_draft_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (research_draft_id, job_id),
                    FOREIGN KEY(research_draft_id) REFERENCES research_drafts(research_draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_research_sources_draft_position
                ON research_sources (research_draft_id, position)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_research_job_associations_job
                ON research_job_associations (job_id, created_at)
                """
            )

    def _ensure_draft_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(research_drafts)").fetchall()
        }
        migrating_legacy_cost = "cost_available" not in columns
        additions = {
            "cost_available": "INTEGER NOT NULL DEFAULT 0",
            "tool_calls": "INTEGER NOT NULL DEFAULT 0",
            "provider_rounds": "INTEGER NOT NULL DEFAULT 0",
            "editable_prompt_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "invariant_prompt_fingerprint": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE research_drafts ADD COLUMN {name} {definition}"
                )
        if migrating_legacy_cost:
            connection.execute(
                """
                UPDATE research_drafts
                SET cost_available = 1
                WHERE estimated_cost_usd > 0
                  AND estimated_cost_usd <= 1.7976931348623157e308
                """
            )

    def save_success(self, draft: SuccessfulResearchDraft) -> PersistedResearchDraft:
        persisted = SuccessfulResearchDraft.model_validate(draft)
        now = persisted.created_at or utc_now()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO research_drafts (
                        research_draft_id, script_hash, provider, model, evidence_mode,
                        source_count, input_tokens, output_tokens, total_tokens,
                        estimated_cost_usd, cost_available, tool_calls, provider_rounds,
                        editable_prompt_fingerprint, invariant_prompt_fingerprint,
                        system_prompt_fingerprint, source_prompt_fingerprint,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        persisted.research_draft_id,
                        persisted.script_hash,
                        persisted.provider,
                        persisted.model,
                        persisted.evidence_mode,
                        len(persisted.sources),
                        persisted.usage.input_tokens,
                        persisted.usage.output_tokens,
                        persisted.usage.total_tokens,
                        persisted.estimated_cost_usd or 0.0,
                        int(persisted.estimated_cost_usd is not None),
                        persisted.tool_calls,
                        persisted.provider_rounds,
                        persisted.editable_prompt_fingerprint,
                        persisted.invariant_prompt_fingerprint,
                        persisted.invariant_prompt_fingerprint,
                        persisted.editable_prompt_fingerprint,
                        now,
                        now,
                    ),
                )
                self._insert_sources(connection, persisted.research_draft_id, persisted.sources)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        loaded = self.get(persisted.research_draft_id)
        if loaded is None:
            raise RuntimeError("persisted research draft was not found after commit")
        return loaded

    def _insert_sources(
        self,
        connection: sqlite3.Connection,
        research_draft_id: str,
        sources: list[ResearchSourceDraft],
    ) -> None:
        created_at = utc_now()
        for position, source in enumerate(sources):
            connection.execute(
                """
                INSERT INTO research_sources (
                    research_source_id, research_draft_id, position, source_url,
                    source_title, source_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    research_draft_id,
                    position,
                    source.url,
                    source.title,
                    source.content_hash,
                    created_at,
                ),
            )

    def get(self, research_draft_id: str) -> PersistedResearchDraft | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM research_drafts WHERE research_draft_id = ?
                """,
                (research_draft_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_draft(connection, row)

    def list_drafts(
        self, limit: int = 50, offset: int = 0
    ) -> list[PersistedResearchDraft]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_drafts
                ORDER BY created_at DESC, research_draft_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [self._row_to_draft(connection, row) for row in rows]

    def assert_script_matches(
        self, research_draft_id: str, script: str
    ) -> PersistedResearchDraft:
        draft = self.get(research_draft_id)
        if draft is None or not hmac.compare_digest(
            draft.script_hash, sha256_text(script)
        ):
            raise ResearchError(
                "RESEARCH_RESPONSE_INVALID", "draft/script hash mismatch"
            )
        return draft

    def link_job(self, research_draft_id: str, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO research_job_associations (
                    research_draft_id, job_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (research_draft_id, str(job_id or "").strip(), utc_now()),
            )

    def _row_to_draft(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> PersistedResearchDraft:
        source_rows = connection.execute(
            """
            SELECT * FROM research_sources
            WHERE research_draft_id = ?
            ORDER BY position ASC, research_source_id ASC
            """,
            (row["research_draft_id"],),
        ).fetchall()
        return PersistedResearchDraft(
            research_draft_id=row["research_draft_id"],
            script_hash=row["script_hash"],
            provider=row["provider"],
            model=row["model"],
            evidence_mode=row["evidence_mode"],
            source_count=row["source_count"],
            tool_calls=row["tool_calls"],
            provider_rounds=row["provider_rounds"],
            usage=ResearchUsageAccounting(
                provider=row["provider"],
                model=row["model"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                total_tokens=row["total_tokens"],
            ),
            estimated_cost_usd=(
                row["estimated_cost_usd"] if bool(row["cost_available"]) else None
            ),
            editable_prompt_fingerprint=row["editable_prompt_fingerprint"],
            invariant_prompt_fingerprint=row["invariant_prompt_fingerprint"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sources=[
                PersistedResearchSource(
                    research_source_id=source_row["research_source_id"],
                    research_draft_id=source_row["research_draft_id"],
                    position=source_row["position"],
                    url=_safe_public_url(source_row["source_url"]),
                    title=source_row["source_title"],
                    content_hash=source_row["source_hash"],
                    created_at=source_row["created_at"],
                )
                for source_row in source_rows
            ],
        )
