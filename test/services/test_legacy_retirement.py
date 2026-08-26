from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.controllers.v1 import video as video_controller
from app.models.exception import HttpException
from app.services import task as task_service


def test_create_video_rejects_retired_classic_generation_without_queueing():
    request = SimpleNamespace(headers={"x-task-id": "retirement-test"})

    with patch.object(video_controller, "create_task") as create_task:
        with pytest.raises(HttpException) as raised:
            video_controller.create_video(MagicMock(), request, MagicMock())

    assert raised.value.status_code == 410
    assert raised.value.message == "LEGACY_VIDEO_GENERATION_RETIRED"
    create_task.assert_not_called()


def test_task_pipeline_rejects_retired_video_stage_without_running_local_renderer():
    params = MagicMock()

    with (
        patch.object(task_service, "_run_pipeline") as run_pipeline,
        patch.object(task_service, "_mark_task_failed", return_value={"failed": True}) as failed,
    ):
        result = task_service.start("retirement-task", params, stop_at="video")

    assert result == {"failed": True}
    failed.assert_called_once_with(
        "retirement-task", "preflight", "LEGACY_VIDEO_GENERATION_RETIRED"
    )
    run_pipeline.assert_not_called()


def test_legacy_video_material_list_endpoint_is_retired():
    request = SimpleNamespace(headers={"x-task-id": "retirement-materials"})

    with pytest.raises(HttpException) as raised:
        video_controller.get_video_materials_list(request)

    assert raised.value.status_code == 410
    assert raised.value.message == "LEGACY_VIDEO_GENERATION_RETIRED"


def test_legacy_video_material_upload_endpoint_is_retired(tmp_path):
    request = SimpleNamespace(headers={"x-task-id": "retirement-materials"})
    upload = SimpleNamespace(filename="clip.mp4", file=BytesIO(b"video"))

    with patch.object(video_controller.utils, "storage_dir", return_value=str(tmp_path)):
        with pytest.raises(HttpException) as raised:
            video_controller.upload_video_material_file(request, upload)

    assert raised.value.status_code == 410
    assert raised.value.message == "LEGACY_VIDEO_GENERATION_RETIRED"


def test_task_pipeline_no_longer_imports_retired_local_six_clip_renderer():
    source = Path("app/services/task.py").read_text(encoding="utf-8")

    assert "six_clip_media" not in source
    assert "six_clip_render" not in source


def test_retired_local_six_clip_modules_are_removed():
    for module in (
        "app/services/six_clip_media.py",
        "app/services/six_clip_render.py",
        "webui/six_clip_timeline.py",
    ):
        assert not Path(module).exists()


def test_retired_classic_webui_background_submission_module_is_removed():
    assert not Path("app/services/webui_task.py").exists()


def test_retirement_verification_document_records_all_retained_surfaces():
    source = Path("docs/task15-legacy-retirement-verification.md").read_text(
        encoding="utf-8"
    )

    for heading in ("Cloud Agent", "Music Batch", "API", "WebUI", "Worker", "Sessions"):
        assert heading in source
