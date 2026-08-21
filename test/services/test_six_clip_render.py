from pathlib import Path

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services import six_clip_render


def _plan(tmp_path: Path) -> SixClipPlan:
    segments = []
    for index in range(1, 7):
        source = tmp_path / (
            f"source-{index}.jpg" if index == 2 else f"source-{index}.mp4"
        )
        if source.suffix == ".jpg":
            source.write_bytes(b"\xff\xd8\xff" + b"x" * 32)
            media_kind = "image"
        else:
            source.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 32)
            media_kind = "video"
        segments.append(
            SixClipSegment(
                index=index,
                start_sec=(index - 1) * 10,
                end_sec=index * 10,
                title=f"Clip {index}",
                narration_context=f"Narration {index}",
                video_prompt=f"Prompt {index}",
                media_kind=media_kind,
                media_path=str(source),
            )
        )
    return SixClipPlan(target_words=130, segments=segments)


def test_prepare_timeline_keeps_fixed_order_and_uses_ten_second_video_loop(
    monkeypatch, tmp_path
):
    plan = _plan(tmp_path)
    commands = []

    def fake_run(command, capture_output, text, check):
        commands.append(command)
        Path(command[-1]).write_bytes(b"video")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    def fake_prepare_images(paths, **kwargs):
        output = Path(kwargs["output_dir"]) / "prepared-image.mp4"
        output.write_bytes(b"image-video")
        assert kwargs["duration"] == 10
        return [str(output)]

    monkeypatch.setattr(six_clip_render.subprocess, "run", fake_run)
    monkeypatch.setattr(
        six_clip_render.image_materials,
        "prepare_image_clips",
        fake_prepare_images,
    )
    monkeypatch.setattr(
        six_clip_render.video,
        "_get_configured_video_codec",
        lambda: "libx264",
    )

    prepared = six_clip_render.prepare_six_clip_timeline(
        "task-1",
        plan,
        video_aspect="9:16",
        image_motion="random",
        threads=2,
        output_dir=tmp_path / "prepared",
    )

    assert len(prepared) == 6
    assert [Path(value).name for value in prepared] == [
        "six-clip-01.mp4",
        "six-clip-02.mp4",
        "six-clip-03.mp4",
        "six-clip-04.mp4",
        "six-clip-05.mp4",
        "six-clip-06.mp4",
    ]
    video_commands = [command for command in commands if "-stream_loop" in command]
    assert len(video_commands) == 5
    assert all(
        command[command.index("-t") + 1] == "10.000" for command in video_commands
    )
    assert all("-an" in command for command in video_commands)


def test_concat_timeline_passes_all_six_clips_in_order_and_caps_at_sixty(
    monkeypatch, tmp_path
):
    clips = []
    for index in range(1, 7):
        clip = tmp_path / f"clip-{index}.mp4"
        clip.write_bytes(b"x")
        clips.append(str(clip))
    captured = {}

    def fake_concat(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output_file"]).write_bytes(b"combined")
        return "libx264"

    monkeypatch.setattr(
        six_clip_render.video,
        "concat_video_clips_with_ffmpeg",
        fake_concat,
    )

    output = six_clip_render.concat_six_clip_timeline(
        clips,
        tmp_path / "combined.mp4",
        threads=2,
    )

    assert captured["clip_files"] == clips
    assert captured["max_duration"] == 60.0
    assert output.endswith("combined.mp4")
