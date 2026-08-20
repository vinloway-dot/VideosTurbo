from __future__ import annotations

import random as random_module
from pathlib import Path
from typing import Iterable

import numpy as np
from loguru import logger
from moviepy import CompositeVideoClip, ImageClip
from PIL import Image, ImageOps

from app.models.schema import ImageMotion, VideoAspect
from app.services import video

_RANDOM_MOTIONS = (
    ImageMotion.slow_zoom_in,
    ImageMotion.slow_zoom_out,
    ImageMotion.pan_left_right,
    ImageMotion.pan_right_left,
)
_MOTION_SCALE = 1.08


def normalize_image_motion(value: ImageMotion | str | object) -> ImageMotion:
    try:
        return ImageMotion(value)
    except (TypeError, ValueError):
        logger.warning(f"unsupported image motion {value!r}; using random")
        return ImageMotion.random


def choose_image_motion(
    value: ImageMotion | str | object,
    *,
    rng: random_module.Random | random_module.SystemRandom | None = None,
) -> ImageMotion:
    motion = normalize_image_motion(value)
    if motion != ImageMotion.random:
        return motion
    chooser = rng or random_module
    return chooser.choice(_RANDOM_MOTIONS)


def validate_image_duration(value: int | float | str) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("image_duration must be between 1 and 30 seconds") from exc
    if duration < 1 or duration > 30:
        raise ValueError("image_duration must be between 1 and 30 seconds")
    return duration


def motion_fraction(time_value: float, duration: float) -> float:
    if duration <= 0:
        return 1.0
    return max(0.0, min(1.0, float(time_value) / float(duration)))


def output_filename_for_image(path: str | Path) -> str:
    """Keep the prepared clip identity tied to the downloaded source asset."""

    return f"image-clip-{Path(path).stem}.mp4"


def _fit_image_array(path: Path, width: int, height: int) -> np.ndarray:
    with Image.open(path) as source:
        source.load()
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        fitted = ImageOps.fit(
            normalized,
            (int(width), int(height)),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        return np.asarray(fitted)


def _center_position(target_width: int, target_height: int):
    def position(t, clip):
        return (
            round((target_width - clip.w) / 2),
            round((target_height - clip.h) / 2),
        )

    return position


def _build_motion_clip(
    image_path: Path,
    *,
    duration: int,
    motion: ImageMotion,
    target_width: int,
    target_height: int,
):
    if motion in {ImageMotion.pan_left_right, ImageMotion.pan_right_left}:
        overscan_width = max(target_width + 2, round(target_width * _MOTION_SCALE))
        overscan_height = max(target_height + 2, round(target_height * _MOTION_SCALE))
        image_array = _fit_image_array(image_path, overscan_width, overscan_height)
        moving = ImageClip(image_array).with_duration(duration)
        overflow_x = max(0, moving.w - target_width)
        overflow_y = max(0, moving.h - target_height)

        def pan_position(t):
            fraction = motion_fraction(t, duration)
            if motion == ImageMotion.pan_left_right:
                x = -overflow_x + (overflow_x * fraction)
            else:
                x = -(overflow_x * fraction)
            return (round(x), round(-overflow_y / 2))

        moving = moving.with_position(pan_position)
        composite = CompositeVideoClip(
            [moving],
            size=(target_width, target_height),
        ).with_duration(duration)
        return composite, [moving]

    image_array = _fit_image_array(image_path, target_width, target_height)
    base = ImageClip(image_array).with_duration(duration)
    if motion == ImageMotion.none:
        composite = CompositeVideoClip(
            [base.with_position((0, 0))],
            size=(target_width, target_height),
        ).with_duration(duration)
        return composite, [base]

    def scale_for_time(t):
        fraction = motion_fraction(t, duration)
        if motion == ImageMotion.slow_zoom_out:
            return _MOTION_SCALE - ((_MOTION_SCALE - 1.0) * fraction)
        return 1.0 + ((_MOTION_SCALE - 1.0) * fraction)

    zoomed = base.resized(scale_for_time)

    def centered_position(t):
        # MoviePy evaluates dynamic size and dynamic position independently. Query
        # the same scale function here so every frame stays centered while zooming.
        scale = scale_for_time(t)
        width = target_width * scale
        height = target_height * scale
        return (round((target_width - width) / 2), round((target_height - height) / 2))

    zoomed = zoomed.with_position(centered_position)
    composite = CompositeVideoClip(
        [zoomed],
        size=(target_width, target_height),
    ).with_duration(duration)
    return composite, [base, zoomed]


def prepare_image_clips(
    paths: Iterable[str | Path],
    *,
    output_dir: str | Path,
    duration: int = 8,
    motion: ImageMotion | str = ImageMotion.random,
    aspect: VideoAspect | str = VideoAspect.landscape,
    codec: str | None = None,
) -> list[str]:
    image_duration = validate_image_duration(duration)
    image_motion = normalize_image_motion(motion)
    video_aspect = VideoAspect(aspect)
    target_width, target_height = video_aspect.to_resolution()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    preferred_codec = codec or video._get_configured_video_codec()
    rng = random_module.Random()
    results: list[str] = []

    for raw_path in paths:
        image_path = Path(raw_path)
        if not image_path.is_file():
            logger.warning(f"skip missing stock image: {image_path}")
            continue
        base_filename = output_filename_for_image(image_path)
        output_path = destination / base_filename
        is_stable_stock_image = image_path.name.startswith("stock-image-")
        if (
            is_stable_stock_image
            and output_path.is_file()
            and output_path.stat().st_size > 0
        ):
            logger.info(f"prepared stock image clip already exists: {output_path}")
            results.append(str(output_path))
            continue

        if is_stable_stock_image and output_path.exists():
            output_path.unlink(missing_ok=True)
        elif not is_stable_stock_image:
            output_stem = Path(base_filename).stem
            suffix = 2
            while output_path.exists():
                output_path = destination / f"{output_stem}-{suffix}.mp4"
                suffix += 1

        selected_motion = choose_image_motion(image_motion, rng=rng)

        composite = None
        child_clips = []
        try:
            composite, child_clips = _build_motion_clip(
                image_path,
                duration=image_duration,
                motion=selected_motion,
                target_width=target_width,
                target_height=target_height,
            )
            video._write_videofile_with_codec_fallback(
                composite,
                str(output_path),
                preferred_codec,
                fps=video.fps,
                audio=False,
                logger=None,
                threads=2,
            )
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            logger.warning(
                "failed to prepare stock image clip: "
                f"file={image_path.name}, motion={selected_motion.value}, "
                f"error={type(exc).__name__}, detail={exc}"
            )
            continue
        finally:
            if composite is not None:
                try:
                    composite.close()
                except Exception:
                    pass
            for clip in child_clips:
                try:
                    clip.close()
                except Exception:
                    pass

        if output_path.is_file() and output_path.stat().st_size > 0:
            logger.success(
                f"image material prepared: {image_path.name} -> {output_path.name}, "
                f"motion={selected_motion.value}"
            )
            results.append(str(output_path))
    return results
