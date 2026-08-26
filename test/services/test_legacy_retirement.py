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
