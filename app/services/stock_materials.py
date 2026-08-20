from __future__ import annotations

import random
from pathlib import Path
from typing import Iterable

from app.models.schema import ImageMotion, MaterialType, VideoAspect, VideoConcatMode
from app.services import image_materials, material, stock_images
from app.utils import utils

_PROVIDER_MATERIAL_TYPES = {
    "pexels": {MaterialType.video, MaterialType.image, MaterialType.mixed},
    "pixabay": {MaterialType.video, MaterialType.image, MaterialType.mixed},
    "coverr": {MaterialType.video},
    "loomloom": {MaterialType.video},
    "local": {MaterialType.video, MaterialType.image, MaterialType.mixed},
}


def base_source(source: str) -> str:
    normalized = str(source or "").strip().lower()
    for suffix in ("_image", "_mixed"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def material_type_from_source(source: str) -> MaterialType:
    normalized = str(source or "").strip().lower()
    if normalized.endswith("_image"):
        return MaterialType.image
    if normalized.endswith("_mixed"):
        return MaterialType.mixed
    return MaterialType.video


def supported_material_types(source: str) -> set[MaterialType]:
    return set(_PROVIDER_MATERIAL_TYPES.get(base_source(source), {MaterialType.video}))


def effective_concat_mode(
    material_type: MaterialType | str,
    requested_mode: VideoConcatMode | str,
) -> VideoConcatMode:
    """Preserve the alternating Video → Image timeline in mixed mode.

    Random mixed mode still randomizes candidates inside each media pool before
    interleaving.  The final compositor must then consume that interleaved list in
    sequence; otherwise the legacy random compositor would shuffle all items again
    and destroy the requested media alternation.
    """

    selected_type = MaterialType(material_type)
    requested = VideoConcatMode(requested_mode)
    if selected_type == MaterialType.mixed:
        return VideoConcatMode.sequential
    return requested


def _is_prepared_image_clip(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.parent.name == "stock-image-clips" or candidate.name.startswith(
        "image-clip-"
    )


def build_clip_duration_overrides(
    paths: Iterable[str | Path],
    *,
    material_type: MaterialType | str,
    video_clip_duration: int,
    image_duration: int,
) -> dict[str, float]:
    """Return per-file final playback limits without changing legacy video mode."""

    selected_type = MaterialType(material_type)
    if selected_type == MaterialType.video:
        return {}

    video_seconds = max(0.001, float(video_clip_duration))
    image_seconds = max(0.001, float(image_duration))
    overrides: dict[str, float] = {}
    for raw_path in paths:
        value = str(raw_path)
        if selected_type == MaterialType.image or _is_prepared_image_clip(value):
            overrides[value] = image_seconds
        else:
            overrides[value] = video_seconds
    return overrides


def interleave_material_paths(
    video_paths: Iterable[str], image_paths: Iterable[str]
) -> list[str]:
    videos = list(video_paths)
    images = list(image_paths)
    result: list[str] = []
    count = max(len(videos), len(images))
    for index in range(count):
        if index < len(videos):
            result.append(videos[index])
        if index < len(images):
            result.append(images[index])
    return result


def _prepare_image_materials(
    *,
    task_id: str,
    search_terms: list[str],
    source: str,
    video_aspect: VideoAspect | str,
    audio_duration: float,
    image_duration: int,
    image_motion: ImageMotion | str,
    match_script_order: bool,
) -> list[str]:
    image_paths = stock_images.download_images(
        task_id=task_id,
        search_terms=search_terms,
        source=source,
        video_aspect=VideoAspect(video_aspect),
        audio_duration=audio_duration,
        image_duration=image_duration,
        match_script_order=match_script_order,
    )
    if not image_paths:
        return []
    clip_dir = Path(utils.task_dir(task_id)) / "stock-image-clips"
    return image_materials.prepare_image_clips(
        image_paths,
        output_dir=clip_dir,
        duration=image_duration,
        motion=image_motion,
        aspect=video_aspect,
    )


def download_stock_materials(
    *,
    task_id: str,
    search_terms: list[str],
    source: str,
    material_type: MaterialType | str,
    video_aspect: VideoAspect | str,
    video_concat_mode: VideoConcatMode | str,
    audio_duration: float,
    max_clip_duration: int,
    image_duration: int,
    image_motion: ImageMotion | str,
    match_script_order: bool = False,
) -> list[str]:
    provider = base_source(source)
    selected_type = MaterialType(material_type)
    if selected_type not in supported_material_types(provider):
        raise ValueError(
            f"video source '{provider}' does not support material type "
            f"'{selected_type.value}'"
        )

    requested_concat_mode = VideoConcatMode(video_concat_mode)
    if selected_type == MaterialType.video:
        return material.download_videos(
            task_id=task_id,
            search_terms=search_terms,
            source=provider,
            video_aspect=VideoAspect(video_aspect),
            video_concat_mode=requested_concat_mode,
            audio_duration=audio_duration,
            max_clip_duration=max_clip_duration,
            match_script_order=match_script_order,
        )

    if selected_type == MaterialType.image:
        images = _prepare_image_materials(
            task_id=task_id,
            search_terms=search_terms,
            source=provider,
            video_aspect=video_aspect,
            audio_duration=audio_duration,
            image_duration=image_duration,
            image_motion=image_motion,
            match_script_order=match_script_order,
        )
        if requested_concat_mode == VideoConcatMode.random and not match_script_order:
            random.shuffle(images)
        return images

    half_duration = max(0.0, float(audio_duration)) / 2.0
    videos = material.download_videos(
        task_id=task_id,
        search_terms=search_terms,
        source=provider,
        video_aspect=VideoAspect(video_aspect),
        video_concat_mode=requested_concat_mode,
        audio_duration=half_duration,
        max_clip_duration=max_clip_duration,
        match_script_order=match_script_order,
    )
    images = _prepare_image_materials(
        task_id=task_id,
        search_terms=search_terms,
        source=provider,
        video_aspect=video_aspect,
        audio_duration=half_duration,
        image_duration=image_duration,
        image_motion=image_motion,
        match_script_order=match_script_order,
    )
    if requested_concat_mode == VideoConcatMode.random and not match_script_order:
        random.shuffle(images)
    if not videos:
        return images
    if not images:
        return videos
    return interleave_material_paths(videos, images)
