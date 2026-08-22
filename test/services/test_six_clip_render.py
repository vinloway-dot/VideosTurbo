from pathlib import Path

import pytest

from app.models.six_clip import SixClipPlan, SixClipSegment
from app.services import six_clip_plan, six_clip_render


def _plan(tmp_path: Path, duration: float = 60.0) -> SixClipPlan:
    segments = []
    ranges = six_clip_plan.build_timeline_ranges(duration)
    for index, (start, end) in enumerate(ranges, start=1):
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
                start_sec=start,
                end_sec=end,
                title=f"Clip {index}",
                narration_context=f"Narration {index}",
                video_prompt=f"Prompt {index}",
                media_kind=media_kind,
                media_path=str(source),
            )
        )
    return SixClipPlan(
        target_words=300 if duration > 60 else 130,
        narration_duration_sec=duration,
        timeline_duration_sec=max(60.0, duration),
        narration_fingerprint="voice-fingerprint" if duration > 60 else "",
        segments=segments,
    )


def test_prepare_dynamic_timeline_keeps_order_and_ten_second_sources(
    monkeypatch, tmp_path
):
    plan = _plan(tmp_path, 63.0)
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

    assert len(prepared) == 7
    assert [Path(value).name for value in prepared] == [
        "six-clip-001.mp4",
        "six-clip-002.mp4",
        "six-clip-003.mp4",
        "six-clip-004.mp4",
        "six-clip-005.mp4",
        "six-clip-006.mp4",
        "six-clip-007.mp4",
    ]
    video_commands = [command for command in commands if "-stream_loop" in command]
    assert len(video_commands) == 6
    assert all(
        command[command.index("-t") + 1] == "10.000" for command in video_commands
    )
    assert all("-an" in command for command in video_commands)


@pytest.mark.parametrize(("duration", "count"), [(63.0, 7), (127.0, 13)])
def test_concat_uses_dynamic_timeline_duration(monkeypatch, tmp_path, duration, count):
    clips = []
    for index in range(1, count + 1):
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
    monkeypatch.setattr(
        six_clip_render,
        "probe_video_duration",
        lambda _: duration,
    )

    output = six_clip_render.concat_six_clip_timeline(
        clips,
        tmp_path / "combined.mp4",
        timeline_duration_sec=duration,
        threads=2,
    )

    assert captured["clip_files"] == clips
    assert captured["max_duration"] == duration
    assert output.endswith("combined.mp4")


def test_concat_rejects_duration_outside_half_second(monkeypatch, tmp_path):
    clips = []
    for index in range(1, 8):
        clip = tmp_path / f"clip-{index}.mp4"
        clip.write_bytes(b"x")
        clips.append(str(clip))

    def fake_concat(**kwargs):
        Path(kwargs["output_file"]).write_bytes(b"combined")

    monkeypatch.setattr(
        six_clip_render.video,
        "concat_video_clips_with_ffmpeg",
        fake_concat,
    )
    monkeypatch.setattr(
        six_clip_render,
        "probe_video_duration",
        lambda _: 61.9,
    )

    with pytest.raises(six_clip_render.SixClipRenderError, match="expected 63.0"):
        six_clip_render.concat_six_clip_timeline(
            clips,
            tmp_path / "combined.mp4",
            timeline_duration_sec=63.0,
        )


def test_probe_video_duration_closes_clip_and_rejects_invalid(monkeypatch):
    class FakeClip:
        duration = 63.25
        closed = False

        def close(self):
            self.closed = True

    clip = FakeClip()
    monkeypatch.setattr(
        six_clip_render.video,
        "_open_video_clip_quietly",
        lambda *args, **kwargs: clip,
    )

    assert six_clip_render.probe_video_duration("combined.mp4") == 63.25
    assert clip.closed
