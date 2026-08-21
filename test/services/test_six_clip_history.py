import json
from pathlib import Path

from app.models.schema import VideoParams
from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services import task_artifacts
from app.services import task as task_service
from webui import six_clip_timeline


def _plan(tmp_path: Path, *, missing_index: int | None = None) -> SixClipPlan:
    segments = []
    for index in range(1, 7):
        media_path = tmp_path / f"clip-{index}.mp4"
        if index != missing_index:
            media_path.write_bytes(b"local-media")
        segments.append(
            SixClipSegment(
                index=index,
                start_sec=(index - 1) * 10,
                end_sec=index * 10,
                title=f"Title {index}",
                narration_context=f"Narration {index}",
                video_prompt=f"Prompt {index}",
                media_kind="video",
                media_path=str(media_path),
            )
        )
    return SixClipPlan(target_words=145, segments=segments)


def test_history_persists_target_words_prompts_and_local_media_only(monkeypatch, tmp_path):
    task_root = tmp_path / "tasks"

    def fake_task_dir(task_id=None):
        path = task_root if task_id is None else task_root / str(task_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(task_artifacts.utils, "task_dir", fake_task_dir)

    plan = _plan(tmp_path)
    params = VideoParams(
        video_subject="History test",
        video_script="Narration source",
        video_terms="",
        target_words=145,
        six_clip_mode=True,
        six_clip_plan=plan,
        video_source="six_clip",
    )

    task_service.save_script_data(
        "task-history",
        params.video_script,
        [],
        params,
    )

    script_file = task_root / "task-history" / "script.json"
    raw_text = script_file.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    saved_params = payload["params"]

    assert saved_params["target_words"] == 145
    assert len(saved_params["six_clip_plan"]["segments"]) == 6
    assert saved_params["six_clip_plan"]["segments"][3]["video_prompt"] == "Prompt 4"
    assert saved_params["six_clip_plan"]["segments"][0]["media_path"] == str(
        tmp_path / "clip-1.mp4"
    )
    assert "Signature=" not in raw_text
    assert "X-Goog-Signature=" not in raw_text
    assert "https://" not in raw_text


def test_restore_plan_keeps_prompts_and_existing_local_media(tmp_path):
    plan = _plan(tmp_path)
    params = {
        "target_words": 155,
        "six_clip_mode": True,
        "six_clip_plan": plan.model_dump(mode="json"),
    }

    restored = six_clip_timeline.restore_plan_from_task_params(params)

    assert restored is not None
    assert restored.target_words == 155
    assert [segment.video_prompt for segment in restored.segments] == [
        f"Prompt {index}" for index in range(1, 7)
    ]
    assert restored.segments[0].media_kind == "video"
    assert restored.segments[0].media_path == str(tmp_path / "clip-1.mp4")


def test_restore_plan_marks_deleted_media_missing_instead_of_falling_back(tmp_path):
    plan = _plan(tmp_path, missing_index=3)
    params = {
        "target_words": 145,
        "six_clip_mode": True,
        "video_source": "six_clip",
        "six_clip_plan": plan.model_dump(mode="json"),
    }

    restored = six_clip_timeline.restore_plan_from_task_params(params)

    assert restored is not None
    missing = restored.segments[2]
    assert missing.index == 3
    assert missing.video_prompt == "Prompt 3"
    assert missing.media_kind == ""
    assert missing.media_path == ""


def test_main_restore_wires_target_words_and_six_clip_plan():
    source = Path("webui/Main.py").read_text(encoding="utf-8")

    assert 'st.session_state["target_words_input"] = int(' in source
    assert "six_clip_timeline.restore_plan_from_task_params(params)" in source
    assert "six_clip_timeline.set_session_plan(" in source
    assert '"six_clip_video_aspect_select"' in source
    assert '"six_clip_image_motion_select"' in source
