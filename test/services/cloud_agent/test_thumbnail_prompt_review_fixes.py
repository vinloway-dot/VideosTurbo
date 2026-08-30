import asyncio
import os
import stat
import time
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.thumbnail_prompt import service as thumbnail_prompt_module
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.service import ThumbnailPromptService
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)


class _Settings:
    def get_generation_snapshot(self):
        return SimpleNamespace(
            provider_id="aihubmix",
            api_key=SecretStr("thumbnail-secret"),
            model_id="thumbnail-model",
            base_url="https://thumbnail-provider.invalid/v1",
            master_prompt="Follow the thumbnail art direction.",
        )


class _SlowAsyncCompletionClient:
    def __init__(self):
        self.cancelled = False
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    async def create(self, *, model, messages):
        del model, messages
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Solar flare over Earth")
                )
            ]
        )


def _provider_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


@pytest.mark.parametrize(
    "completion",
    [
        "Α) first concept; Β) second concept",
        "А) first concept; В) second concept",
        "Solar flare Alternative, eclipse",
        "Solar flare Alternative; eclipse",
        "Solar flare Alternative / eclipse",
        "Solar flare Alternative… eclipse",
        "Solar flare Option 2, eclipse",
    ],
)
def test_review_parser_rejects_confusable_and_standalone_options(completion):
    with pytest.raises(ThumbnailPromptError) as error:
        ThumbnailPromptService._normalize_completion(_provider_response(completion))

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "completion",
    [
        "1899: Victorian London under gaslight",
        "2200: futuristic orbital city",
        "16∶9 aspect ratio, cinematic",
        "½ composition with an 85-mm lens",
        "1٬000-star galaxy",
        "An alternative, cinematic camera angle",
        "Cinematic alternative/original split-screen",
    ],
)
def test_review_parser_accepts_safe_year_and_unicode_numeric_prose(completion):
    assert (
        ThumbnailPromptService._normalize_completion(_provider_response(completion))
        == completion
    )


def test_review_provider_deadline_cancels_async_request_before_ui_timeout(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )
    client = _SlowAsyncCompletionClient()
    service = ThumbnailPromptService(
        storage=storage,
        settings=_Settings(),
        clients={"aihubmix": client},
        provider_deadline_seconds=0.02,
    )

    started = time.monotonic()
    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")
    elapsed = time.monotonic() - started

    assert error.value.code == "PROVIDER_TIMEOUT"
    assert client.cancelled is True
    assert elapsed < 0.5
    assert 0 < thumbnail_prompt_module.PROVIDER_DEADLINE_SECONDS < 60


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics required")
def test_review_master_prompt_is_created_private_and_unsafe_mode_is_rejected(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )

    assert stat.S_IMODE(paths.master_prompt_file.stat().st_mode) == 0o600

    paths.master_prompt_file.chmod(0o644)
    with pytest.raises(ValueError, match="master prompt"):
        storage.read_master_prompt("job-1")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics required")
def test_review_master_prompt_rejects_writable_job_and_input_directories(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )

    paths.input_dir.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="master prompt"):
            storage.read_master_prompt("job-1")
    finally:
        paths.input_dir.chmod(0o755)

    paths.job_dir.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="master prompt"):
            storage.read_master_prompt("job-1")
    finally:
        paths.job_dir.chmod(0o755)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics required")
def test_review_settings_reject_nonsticky_group_or_world_writable_ancestor(tmp_path):
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o700)
    settings_dir = unsafe_parent / "thumbnail_prompt"
    settings_dir.mkdir(mode=0o700)
    settings_path = settings_dir / "settings.toml"
    settings_path.write_text('master_prompt = "Create one thumbnail prompt."\n')
    settings_path.chmod(0o600)
    unsafe_parent.chmod(0o777)

    try:
        settings = ThumbnailPromptSettingsService(
            settings_path=settings_path
        ).get_settings()
    finally:
        unsafe_parent.chmod(0o700)

    assert settings.configuration_error is not None
    assert "unsafe" in settings.configuration_error.casefold()
