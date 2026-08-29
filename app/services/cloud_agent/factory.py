"""Production composition root for the checkpointed Cloud Agent."""

from pathlib import Path

from app.config import config
from app.services.cloud_agent.browser import PersistentBrowserManager
from app.services.cloud_agent.job_store import CloudJobStore
from app.services.cloud_agent.preflight import PreflightManager
from app.services.cloud_agent.providers.canva import (
    CanvaAssemblyClient,
    CanvaSessionProvider,
)
from app.services.cloud_agent.providers.google_flow import (
    GoogleFlowClient,
    GoogleFlowSessionProvider,
)
from app.services.cloud_agent.session import SessionManager
from app.services.cloud_agent.storage import CloudJobStorage
from app.services.cloud_agent.tts import ExistingVoiceTTSClient
from app.services.cloud_agent.tts_settings import CloudTTSSettingsService
from app.services.cloud_agent.draft_voice import DraftVoiceService
from app.services.cloud_agent.defaults import CloudAgentDefaultsService
from app.services.cloud_agent.retry import PreFlowRetryService
from app.services.cloud_agent.worker import CloudAgentWorker
from app.services.cloud_agent.event_dispatcher import (
    CloudJobEventDispatcher,
    RequestsJobEventTransport,
)
from app.services.cloud_agent.job_events import EventPublishingCloudJobStore
from app.services.cloud_agent.workflow import CloudAgentWorkflow
from app.services.cloud_agent.research.adapters import (
    AIHubMixToolCallingAdapter,
    OpenRouterToolCallingAdapter,
)
from app.services.cloud_agent.research.runtime import ResearchToolRuntime
from app.services.cloud_agent.research.service import ResearchScriptService
from app.services.cloud_agent.research.settings import ResearchSettingsService
from app.services.cloud_agent.research.store import ResearchDraftStore
from app.services.cloud_agent.thumbnail_prompt.service import ThumbnailPromptService
from app.services.cloud_agent.thumbnail_prompt.settings import (
    ThumbnailPromptSettingsService,
)
from app.utils import utils


def build_workflow(*, store: CloudJobStore | None = None) -> CloudAgentWorkflow:
    """Build the Cloud Agent from the process's existing ``config.app`` mapping."""
    app_config = config.app
    storage = CloudJobStorage()
    store = store or CloudJobStore(str(app_config["cloud_agent_db_path"]))
    browser = PersistentBrowserManager(app_config=app_config, storage=storage)
    sessions = build_session_manager(browser=browser)
    preflight = PreflightManager(
        store,
        storage,
        sessions,
        min_free_disk_gb=float(app_config["cloud_agent_min_free_disk_gb"]),
    )

    return CloudAgentWorkflow(
        store,
        storage,
        preflight,
        ExistingVoiceTTSClient(),
        GoogleFlowClient(
            browser,
            sessions,
            service_url=app_config["cloud_agent_flow_url"],
            expected_width=int(app_config["cloud_agent_expected_width"]),
            expected_height=int(app_config["cloud_agent_expected_height"]),
        ),
        CanvaAssemblyClient(
            browser,
            sessions,
            service_url=app_config["cloud_agent_canva_template_url"],
            timeline_tolerance_seconds=float(
                app_config["cloud_agent_final_duration_tolerance_seconds"]
            ),
        ),
        tts_min_duration=float(app_config["cloud_agent_tts_min_duration_seconds"]),
        canva_min_playback_speed=float(
            app_config["cloud_agent_canva_min_playback_speed"]
        ),
        final_duration_tolerance_seconds=float(
            app_config["cloud_agent_final_duration_tolerance_seconds"]
        ),
        final_min_size_bytes=int(app_config["cloud_agent_final_min_size_bytes"]),
        expected_width=int(app_config["cloud_agent_expected_width"]),
        expected_height=int(app_config["cloud_agent_expected_height"]),
    )


def build_session_manager(
    *, browser: PersistentBrowserManager | None = None
) -> SessionManager:
    """Build the existing provider-based session control boundary from ``config.app``."""
    app_config = config.app
    browser = browser or PersistentBrowserManager(app_config=app_config)
    return SessionManager(
        {
            "google_flow": GoogleFlowSessionProvider(
                browser,
                service_url=app_config["cloud_agent_flow_url"],
            ),
            "canva": CanvaSessionProvider(
                browser,
                service_url=app_config["cloud_agent_canva_template_url"],
            ),
        },
        headed=not bool(app_config["cloud_agent_browser_headless"]),
    )


def build_pre_flow_retry_service() -> PreFlowRetryService:
    """Compose retry validation from the existing application configuration."""
    app_config = config.app
    return PreFlowRetryService(
        CloudJobStore(str(app_config["cloud_agent_db_path"])),
        CloudJobStorage(),
        tts_min_duration=float(app_config["cloud_agent_tts_min_duration_seconds"]),
        canva_min_playback_speed=float(
            app_config["cloud_agent_canva_min_playback_speed"]
        ),
    )


def build_cloud_tts_settings_service() -> CloudTTSSettingsService:
    """Compose safe TTS settings from the existing process configuration."""
    return CloudTTSSettingsService()


def build_thumbnail_prompt_settings_service() -> ThumbnailPromptSettingsService:
    """Compose the isolated Thumbnail Prompt settings boundary."""
    storage_root = Path(utils.storage_dir())
    return ThumbnailPromptSettingsService(
        settings_path=storage_root / "thumbnail_prompt" / "settings.toml"
    )


def build_thumbnail_prompt_service() -> ThumbnailPromptService:
    """Compose Thumbnail Prompt generation from its dedicated settings only."""
    return ThumbnailPromptService(
        storage=CloudJobStorage(),
        settings=build_thumbnail_prompt_settings_service(),
    )


def build_draft_voice_service() -> DraftVoiceService:
    storage = CloudJobStorage()
    return DraftVoiceService(storage.root.parent / "draft-voices")


def build_cloud_agent_defaults_service() -> CloudAgentDefaultsService:
    return CloudAgentDefaultsService(
        {item.id for item in build_cloud_tts_settings_service().list_providers()}
    )


def build_research_settings_service() -> ResearchSettingsService:
    return ResearchSettingsService()


def build_research_draft_store() -> ResearchDraftStore:
    return ResearchDraftStore(str(config.app["cloud_agent_db_path"]))


def build_research_script_service() -> ResearchScriptService:
    return ResearchScriptService(
        runtime=ResearchToolRuntime(),
        settings=build_research_settings_service(),
        store=build_research_draft_store(),
        adapters={
            "openrouter": OpenRouterToolCallingAdapter(),
            "aihubmix": AIHubMixToolCallingAdapter(),
        },
    )


def build_worker() -> CloudAgentWorker:
    """Build one durable worker from the process's existing ``config.app`` mapping."""
    app_config = config.app
    transport = RequestsJobEventTransport(
        app_config.get(
            "cloud_agent_event_intake_url",
            "http://127.0.0.1:8080/api/v1/cloud-agent/internal/events",
        ),
        timeout_seconds=float(
            app_config.get("cloud_agent_event_delivery_timeout_seconds", 0.5)
        ),
    )
    dispatcher = CloudJobEventDispatcher(
        transport=transport.send,
        queue_size=int(app_config.get("cloud_agent_event_queue_size", 128)),
    )
    event_store = EventPublishingCloudJobStore(
        str(app_config["cloud_agent_db_path"]), sink=dispatcher
    )
    workflow = build_workflow(store=event_store)
    return CloudAgentWorker(
        workflow.store,
        workflow,
        worker_id=None,
        lease_seconds=int(app_config["cloud_agent_worker_lease_seconds"]),
        poll_seconds=float(app_config["cloud_agent_worker_poll_seconds"]),
    )
