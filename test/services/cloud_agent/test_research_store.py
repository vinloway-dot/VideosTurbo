import sqlite3

import pytest

from app.config import config
from app.services.cloud_agent import factory
from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.models import ResearchUsageAccounting
from app.services.cloud_agent.research.store import (
    ResearchDraftStore,
    ResearchSourceDraft,
    SuccessfulResearchDraft,
    sha256_text,
)


def make_successful_draft(script_hash: str | None = None) -> SuccessfulResearchDraft:
    return SuccessfulResearchDraft(
        script_hash=script_hash or sha256_text("original narration"),
        provider="openrouter",
        model="openai/gpt-5.6-sol-pro",
        evidence_mode="url",
        system_prompt_fingerprint="s" * 64,
        source_prompt_fingerprint="p" * 64,
        usage=ResearchUsageAccounting(
            provider="openrouter",
            model="openai/gpt-5.6-sol-pro",
            input_tokens=1200,
            output_tokens=240,
            total_tokens=1440,
        ),
        estimated_cost_usd=0.12,
        sources=[
            ResearchSourceDraft(
                url="https://example.com/source",
                title="Example source",
                body="source body",
            )
        ],
    )


def test_store_persists_only_non_secret_provenance(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(
        make_successful_draft(script_hash=sha256_text("narration"))
    )

    loaded = store.get(saved.research_draft_id)

    assert loaded is not None
    assert loaded.script_hash == sha256_text("narration")
    assert "source body" not in loaded.model_dump_json()
    with sqlite3.connect(store.db_path) as connection:
        dump = "\n".join(connection.iterdump())
    assert "source body" not in dump


def test_store_sanitizes_persisted_source_urls_before_returning_or_storing(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(
        make_successful_draft().model_copy(
            update={
                "sources": [
                    ResearchSourceDraft(
                        url=(
                            "https://example.com/article/path"
                            "?X-Amz-Signature=secret-signature&token=secret-token"
                            "#private-fragment"
                        ),
                        title="Signed source",
                        body="source body",
                    )
                ]
            }
        )
    )

    loaded = store.get(saved.research_draft_id)

    assert loaded is not None
    assert loaded.sources[0].url == "https://example.com/article/path"
    assert "secret-signature" not in loaded.model_dump_json()
    assert "secret-token" not in loaded.model_dump_json()
    with sqlite3.connect(store.db_path) as connection:
        persisted = connection.execute(
            "SELECT source_url FROM research_sources WHERE research_draft_id = ?",
            (saved.research_draft_id,),
        ).fetchone()
    assert persisted == ("https://example.com/article/path",)


def test_association_rejects_changed_script(tmp_path):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")
    saved = store.save_success(make_successful_draft())

    with pytest.raises(ResearchError) as excinfo:
        store.assert_script_matches(saved.research_draft_id, "edited narration")

    assert excinfo.value.code == "RESEARCH_RESPONSE_INVALID"


def test_successful_draft_rejects_non_canonical_script_hash():
    with pytest.raises(ValueError, match="canonical lowercase SHA-256"):
        SuccessfulResearchDraft(
            script_hash=("A" * 64),
            provider="openrouter",
            model="openai/gpt-5.6-sol-pro",
            evidence_mode="url",
            system_prompt_fingerprint="s" * 64,
            source_prompt_fingerprint="p" * 64,
            usage=ResearchUsageAccounting(
                provider="openrouter",
                model="openai/gpt-5.6-sol-pro",
                input_tokens=1200,
                output_tokens=240,
                total_tokens=1440,
            ),
            estimated_cost_usd=0.12,
            sources=[],
        )


def test_source_insert_failure_rolls_back_entire_successful_draft(
    tmp_path, monkeypatch
):
    store = ResearchDraftStore(tmp_path / "cloud-agent.sqlite3")

    def raises_sqlite_error(*_args, **_kwargs):
        raise sqlite3.Error("boom")

    monkeypatch.setattr(store, "_insert_sources", raises_sqlite_error)

    with pytest.raises(sqlite3.Error, match="boom"):
        store.save_success(make_successful_draft())

    assert store.list_drafts() == []


def test_link_job_is_idempotent_and_factory_uses_configured_database(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "configured-cloud-agent.sqlite3"
    monkeypatch.setitem(config.app, "cloud_agent_db_path", str(db_path))

    store = factory.build_research_draft_store()
    saved = store.save_success(make_successful_draft())
    store.link_job(saved.research_draft_id, "job-1")
    store.link_job(saved.research_draft_id, "job-1")
    store.link_job(saved.research_draft_id, "job-2")

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT job_id FROM research_job_associations
            WHERE research_draft_id = ?
            ORDER BY job_id
            """,
            (saved.research_draft_id,),
        ).fetchall()

    assert isinstance(store, ResearchDraftStore)
    assert [row[0] for row in rows] == ["job-1", "job-2"]
