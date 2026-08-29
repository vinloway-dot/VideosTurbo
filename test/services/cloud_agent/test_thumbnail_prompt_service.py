from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.service import ThumbnailPromptService


class FakeCompletionClient:
    def __init__(self, content):
        self.content = content
        self.messages = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, *, model, messages):
        self.model = model
        self.messages = messages
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeSettings:
    def get_configured_provider_id(self):
        return "aihubmix"

    def get_api_key_for_generation(self, provider_id):
        assert provider_id == "aihubmix"
        return SecretStr("thumbnail-secret")

    def resolve_model(self, provider_id):
        assert provider_id == "aihubmix"
        return "thumbnail-model"

    def get_provider(self, provider_id):
        assert provider_id == "aihubmix"
        return SimpleNamespace(base_url="https://thumbnail-provider.invalid/v1")

    def get_settings(self):
        return SimpleNamespace(master_prompt="Follow the thumbnail art direction.")


def ready_settings():
    return FakeSettings()


def service_with_completion(tmp_path, completion):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs("job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT")
    return ThumbnailPromptService(
        storage=storage,
        settings=ready_settings(),
        clients={"aihubmix": FakeCompletionClient(completion)},
    )


def test_generate_uses_full_saved_master_prompt_and_returns_one_plain_prompt(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-1")
    paths.master_prompt_file.write_text("FULL VIDEO MASTER PROMPT", encoding="utf-8")
    client = FakeCompletionClient("Solar flare over Earth, dramatic golden light, 16:9")
    service = ThumbnailPromptService(
        storage=storage,
        settings=ready_settings(),
        clients={"aihubmix": client},
    )

    result = service.generate_for_job("job-1")

    assert result == "Solar flare over Earth, dramatic golden light, 16:9"
    assert "FULL VIDEO MASTER PROMPT" in client.messages[-1]["content"]
    assert "analysis" not in result.lower()
    assert client.model == "thumbnail-model"


def test_generate_rejects_empty_or_multichoice_provider_output(tmp_path):
    service = service_with_completion(tmp_path, "Option 1: one\nOption 2: two")

    with pytest.raises(ThumbnailPromptError, match="ผลลัพธ์"):
        service.generate_for_job("job-1")


def test_generate_rejects_an_empty_provider_response(tmp_path):
    service = service_with_completion(tmp_path, "  ")

    with pytest.raises(ThumbnailPromptError, match="ผลลัพธ์"):
        service.generate_for_job("job-1")
