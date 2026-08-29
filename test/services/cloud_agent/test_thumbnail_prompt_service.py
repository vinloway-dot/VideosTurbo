from types import SimpleNamespace
import threading

import httpx
import pytest
from pydantic import SecretStr
import toml

from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.service import ThumbnailPromptService
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)


class FakeCompletionClient:
    def __init__(self, content, *, choice_count=1):
        self.content = content
        self.choice_count = choice_count
        self.messages = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, *, model, messages):
        self.model = model
        self.messages = messages
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content))
                for _ in range(self.choice_count)
            ]
        )


class FakeSettings:
    def get_generation_snapshot(self):
        return SimpleNamespace(
            provider_id="aihubmix",
            api_key=SecretStr("thumbnail-secret"),
            model_id="thumbnail-model",
            base_url="https://thumbnail-provider.invalid/v1",
            master_prompt="Follow the thumbnail art direction.",
        )


def ready_settings():
    return FakeSettings()


def service_with_completion(tmp_path, completion):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )
    return ThumbnailPromptService(
        storage=storage,
        settings=ready_settings(),
        clients={"aihubmix": FakeCompletionClient(completion)},
    )


def test_generate_reads_one_generation_settings_snapshot(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )

    class SnapshotOnlySettings:
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def get_generation_snapshot(self):
            with self.lock:
                self.calls += 1
                return SimpleNamespace(
                    provider_id="aihubmix",
                    api_key=SecretStr("thumbnail-secret"),
                    model_id="thumbnail-model",
                    base_url="https://thumbnail-provider.invalid/v1",
                    master_prompt="Follow the thumbnail art direction.",
                )

    class LockCheckingClient(FakeCompletionClient):
        def create(self, *, model, messages):
            assert settings.lock.acquire(blocking=False)
            settings.lock.release()
            return super().create(model=model, messages=messages)

    settings = SnapshotOnlySettings()
    client = LockCheckingClient("Solar flare over Earth")
    service = ThumbnailPromptService(
        storage=storage,
        settings=settings,
        clients={"aihubmix": client},
    )

    assert service.generate_for_job("job-1") == "Solar flare over Earth"
    assert settings.calls == 1


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


@pytest.mark.parametrize(
    "completion",
    [
        "Option 1: Solar flare over Earth",
        "## Prompt\nSolar flare over Earth",
        "Choice A: Solar flare over Earth",
        "Solar flare over Earth\nAlternative: Eclipse over Earth",
    ],
)
def test_generate_rejects_labelled_or_alternative_provider_output(tmp_path, completion):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError, match="ผลลัพธ์"):
        service.generate_for_job("job-1")


@pytest.mark.parametrize(
    "completion",
    [
        "Here is your prompt: Solar flare over Earth",
        "Thumbnail prompt — Solar flare over Earth",
        "Prompt: Solar flare over Earth",
        "พรอมต์หน้าปก: เปลวสุริยะเหนือโลก",
    ],
)
def test_generate_rejects_english_and_thai_prompt_preambles(tmp_path, completion):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "completion",
    [
        "Your prompt: Solar flare over Earth",
        "Here is the thumbnail prompt: Solar flare over Earth",
        "Here is an image prompt: Solar flare over Earth",
        "นี่คือพรอมต์หน้าปก: เปลวสุริยะเหนือโลก",
        "นี่คือ Prompt หน้าปก: เปลวสุริยะเหนือโลก",
    ],
)
def test_generate_rejects_additional_anchored_prompt_preambles(tmp_path, completion):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


def test_generate_accepts_normal_prose_that_mentions_thumbnail_prompt(tmp_path):
    prompt = "Cinematic thumbnail prompt lighting with Earth at sunrise, 16:9"
    service = service_with_completion(tmp_path, prompt)

    assert service.generate_for_job("job-1") == prompt


def test_generate_rejects_lettered_list_alternatives(tmp_path):
    service = service_with_completion(tmp_path, "A. first prompt\nB. second prompt")

    with pytest.raises(ThumbnailPromptError, match="ผลลัพธ์"):
        service.generate_for_job("job-1")


@pytest.mark.parametrize(
    "completion",
    [
        "- Solar flare over Earth",
        "* Solar flare over Earth",
        "```text\nSolar flare over Earth\n```",
        "**Solar flare over Earth**",
        "First concept\n---\nSecond concept",
    ],
)
def test_generate_rejects_markdown_or_multiple_alternatives(tmp_path, completion):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "completion",
    [
        "> Solar flare over Earth",
        "`Solar flare over Earth`",
        "Solar flare over [Earth](https://example.invalid/earth)",
        "*Solar flare over Earth*",
        "_Solar flare over Earth_",
        "Solar *dramatic* flare over Earth",
        "Solar _dramatic_ flare over Earth",
        "Solar flare over Earth\nEclipse over Earth",
        "Solar flare over Earth\n\nEclipse over Earth",
    ],
)
def test_generate_rejects_non_plain_or_unlabelled_multiple_prompts(
    tmp_path, completion
):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize("choice_count", [0, 2])
def test_generate_requires_exactly_one_provider_choice(tmp_path, choice_count):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )
    service = ThumbnailPromptService(
        storage=storage,
        settings=ready_settings(),
        clients={
            "aihubmix": FakeCompletionClient(
                "Solar flare over Earth", choice_count=choice_count
            )
        },
    )

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


def test_generate_keeps_normal_image_prompt_prose(tmp_path):
    prompt = (
        "Cinematic solar flare above Earth [viewed from orbit] with dramatic "
        "golden rim light, 16:9."
    )
    service = service_with_completion(tmp_path, prompt)

    assert service.generate_for_job("job-1") == prompt


@pytest.mark.parametrize(
    "completion",
    [
        "[](https://example.invalid)",
        "![](https://example.invalid/image.png)",
        "[Earth]()",
        r"[Earth\]](https://example.invalid/earth)",
        "[Earth [orbit]](https://example.invalid/earth)",
        r"![Earth\]](https://example.invalid/earth.png)",
        "[Earth]: https://example.invalid",
        "<mailto:user@example.invalid>",
    ],
)
def test_generate_rejects_markdown_links_autolinks_and_reference_definitions(
    tmp_path, completion
):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "completion",
    [
        "Cinematic <strong>Earth</strong> with dramatic rim light",
        "<div>Earth viewed from orbit</div>",
        "<!-- hidden alternative -->Solar flare over Earth",
        "<![CDATA[Earth]]>",
        "<?thumbnail style='cinematic'?>Solar flare over Earth",
    ],
)
def test_generate_rejects_raw_html_markup(tmp_path, completion):
    service = service_with_completion(tmp_path, completion)

    with pytest.raises(ThumbnailPromptError) as error:
        service.generate_for_job("job-1")

    assert error.value.code == "THUMBNAIL_PROMPT_RESPONSE_INVALID"


def test_generate_rejects_an_empty_provider_response(tmp_path):
    service = service_with_completion(tmp_path, "  ")

    with pytest.raises(ThumbnailPromptError, match="ผลลัพธ์"):
        service.generate_for_job("job-1")


def test_generate_sanitizes_client_factory_failures(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )

    def broken_factory(**_kwargs):
        raise ValueError("base URL includes private configuration")

    service = ThumbnailPromptService(
        storage=storage,
        settings=ready_settings(),
        client_factory=broken_factory,
    )

    with pytest.raises(ThumbnailPromptError, match="ผู้ให้บริการ") as error:
        service.generate_for_job("job-1")

    assert error.value.code == "PROVIDER_REQUEST_FAILED"
    assert "private configuration" not in str(error.value)


@pytest.mark.parametrize(
    ("config_key", "value"),
    [
        ("default_provider", "unknown"),
        ("aihubmix_base_url", "not-a-url"),
        (
            "aihubmix_base_url",
            "https://user:secret@example.invalid/v1",
        ),
        (
            "aihubmix_base_url",
            "https://example.invalid/v1?api_key=secret",
        ),
        (
            "aihubmix_base_url",
            "https://example.invalid/" + "x" * 2048,
        ),
        (
            "aihubmix_base_url",
            "https://example.invalid\\persisted-secret-marker/v1",
        ),
        (
            "aihubmix_base_url",
            "https://example.invalid/\x00persisted-secret-marker/v1",
        ),
    ],
)
def test_generate_rejects_invalid_provider_configuration_before_client_creation(
    tmp_path, config_key, value
):
    settings_path = tmp_path / "thumbnail_prompt" / "settings.toml"
    settings_path.parent.mkdir(mode=0o700)
    persisted = toml.dumps(
        {
            "master_prompt": "Art direction",
            "aihubmix_api_key": "secret",
            config_key: value,
        }
    )
    if "\x00" in value:
        persisted = persisted.replace("x00persisted", r"\u0000persisted")
    settings_path.write_text(persisted)
    settings_path.chmod(0o600)
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )
    factory_calls = []

    service = ThumbnailPromptService(
        storage=storage,
        settings=ThumbnailPromptSettingsService(settings_path=settings_path),
        client_factory=lambda **kwargs: factory_calls.append(kwargs),
    )

    with pytest.raises(ThumbnailPromptError):
        service.generate_for_job("job-1")

    assert factory_calls == []


def test_provider_timeout_has_explicit_phase_budget_below_ui_deadline(tmp_path):
    storage = CloudJobStorage(tmp_path / "jobs")
    storage.write_inputs(
        "job-1", script="script", master_prompt="FULL VIDEO MASTER PROMPT"
    )
    factory_calls = []

    def client_factory(**kwargs):
        factory_calls.append(kwargs)
        return FakeCompletionClient("Solar flare over Earth")

    service = ThumbnailPromptService(
        storage=storage,
        settings=ready_settings(),
        client_factory=client_factory,
    )

    service.generate_for_job("job-1")

    timeout = factory_calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 5.0
    assert timeout.read == 30.0
    assert timeout.write == 5.0
    assert timeout.pool == 5.0
    assert sum((timeout.connect, timeout.read, timeout.write, timeout.pool)) <= 45.0
    assert factory_calls[0]["max_retries"] == 0
