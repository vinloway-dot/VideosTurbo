from pathlib import Path

from app.models.schema import VideoParams
from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services import task


def _plan(tmp_path: Path, *, ready: bool = True) -> SixClipPlan:
    segments = []
    for index in range(1, 7):
        media_path = ""
        media_kind = ""
        if ready:
            source = tmp_path / f"clip-{index}.mp4"
            source.write_bytes(b"x")
            media_path = str(source)
            media_kind = "video"
        segments.append(
            SixClipSegment(
                index=index,
                start_sec=(index - 1) * 10,
                end_sec=index * 10,
                title=f"Clip {index}",
                narration_context="n",
                video_prompt="p",
                media_kind=media_kind,
                media_path=media_path,
            )
        )
    return SixClipPlan(target_words=130, segments=segments)


def _params(plan: SixClipPlan) -> VideoParams:
    return VideoParams(
        video_subject="Subject",
        video_script="Narration",
        six_clip_mode=True,
        six_clip_plan=plan,
        bgm_type="",
        subtitle_enabled=False,
    )


def _stub_state(monkeypatch):
    monkeypatch.setattr(task.sm.state, "update_task", lambda *args, **kwargs: True)
    monkeypatch.setattr(task.sm.state, "get_task", lambda *args, **kwargs: None)


def test_missing_media_fails_before_script_or_tts(monkeypatch, tmp_path):
    _stub_state(monkeypatch)
    params = _params(_plan(tmp_path, ready=False))
    failures = []

    monkeypatch.setattr(
        task,
        "generate_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("script generation must not run before media preflight")
        ),
    )
    monkeypatch.setattr(
        task,
        "_mark_task_failed",
        lambda task_id, stage, error, details=None: failures.append((stage, error))
        or {"failed_stage": stage, "error": error},
    )

    result = task._run_pipeline("task-1", params)

    assert result["failed_stage"] == "preflight"
    assert "Clip 1" in result["error"]
    assert "Clip 6" in result["error"]
    assert failures


def test_six_clip_mode_rejects_narration_longer_than_sixty_seconds(
    monkeypatch, tmp_path
):
    _stub_state(monkeypatch)
    params = _params(_plan(tmp_path, ready=True))
    failures = []

    monkeypatch.setattr(task, "generate_script", lambda *args, **kwargs: "Narration")
    monkeypatch.setattr(task, "save_script_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        task,
        "generate_audio",
        lambda *args, **kwargs: ("audio.mp3", 61.2, None),
    )
    monkeypatch.setattr(
        task,
        "generate_subtitle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("subtitle generation must not run after overlong narration")
        ),
    )
    monkeypatch.setattr(
        task,
        "_mark_task_failed",
        lambda task_id, stage, error, details=None: failures.append((stage, error))
        or {"failed_stage": stage, "error": error},
    )

    result = task._run_pipeline("task-2", params)

    assert result["failed_stage"] == "audio"
    assert "60" in result["error"]
    assert "Target Words" in result["error"]
    assert failures


def test_six_clip_mode_bypasses_stock_downloader_and_uses_fixed_renderer(
    monkeypatch, tmp_path
):
    _stub_state(monkeypatch)
    params = _params(_plan(tmp_path, ready=True))
    combined = tmp_path / "combined-1.mp4"
    combined.write_bytes(b"combined")
    prepared = [str(tmp_path / f"prepared-{i}.mp4") for i in range(1, 7)]
    for value in prepared:
        Path(value).write_bytes(b"prepared")
    calls = []

    monkeypatch.setattr(task, "generate_script", lambda *args, **kwargs: "Narration")
    monkeypatch.setattr(task, "save_script_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        task,
        "generate_audio",
        lambda *args, **kwargs: ("audio.mp3", 50, None),
    )
    monkeypatch.setattr(task, "generate_subtitle", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        task,
        "get_video_materials",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stock/local material path must be bypassed")
        ),
    )
    monkeypatch.setattr(
        task.six_clip_render,
        "prepare_six_clip_timeline",
        lambda *args, **kwargs: calls.append("prepare") or prepared,
    )
    monkeypatch.setattr(
        task.six_clip_render,
        "concat_six_clip_timeline",
        lambda *args, **kwargs: calls.append("concat") or str(combined),
    )
    monkeypatch.setattr(
        task,
        "generate_final_videos",
        lambda *args, **kwargs: (
            [str(tmp_path / "final-1.mp4")],
            [str(combined)],
            [],
        ),
    )
    monkeypatch.setattr(
        task.upload_post.upload_post_service,
        "is_configured",
        lambda: False,
    )

    result = task._run_pipeline("task-3", params)

    assert calls == ["prepare", "concat"]
    assert result["materials"] == prepared
