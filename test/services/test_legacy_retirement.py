from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.controllers.v1 import video as video_controller
from app.models.exception import HttpException


def test_create_video_rejects_retired_classic_generation_without_queueing():
    request = SimpleNamespace(headers={"x-task-id": "retirement-test"})

    with patch.object(video_controller, "create_task") as create_task:
        with pytest.raises(HttpException) as raised:
            video_controller.create_video(MagicMock(), request, MagicMock())

    assert raised.value.status_code == 410
    assert raised.value.message == "LEGACY_VIDEO_GENERATION_RETIRED"
    create_task.assert_not_called()
