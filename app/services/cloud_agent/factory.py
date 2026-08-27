"""Production composition root for the checkpointed Cloud Agent."""

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
from app.services.cloud_agent.retry import PreFlowRetryService
from app.services.cloud_agent.worker import CloudAgentWorker
from app.services.cloud_agent.workflow import CloudAgentWorkflow


def build_workflow() -> CloudAgentWorkflow:
    """Build the Cloud Agent from the process's existing ``config.app`` mapping."""
    app_config = config.app
    storage = CloudJobStorage()
    store = CloudJobStore(str(app_config["cloud_agent_db_path"]))
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
        canva_min_playback_speed=float(app_config["cloud_agent_canva_min_playback_speed"]),
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
        canva_min_playback_speed=float(app_config["cloud_agent_canva_min_playback_speed"]),
    )


def build_cloud_tts_settings_service() -> CloudTTSSettingsService:
    """Compose safe TTS settings from the existing process configuration."""
    return CloudTTSSettingsService()


def build_draft_voice_service() -> DraftVoiceService:
    storage = CloudJobStorage()
    return DraftVoiceService(storage.root.parent / "draft-voices")


def build_worker() -> CloudAgentWorker:
    """Build one durable worker from the process's existing ``config.app`` mapping."""
    app_config = config.app
    workflow = build_workflow()
    return CloudAgentWorker(
        workflow.store,
        workflow,
        worker_id=None,
        lease_seconds=int(app_config["cloud_agent_worker_lease_seconds"]),
        poll_seconds=float(app_config["cloud_agent_worker_poll_seconds"]),
    )
