from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlsplit

import requests
from loguru import logger

from app.config import config
from app.models.schema import MaterialInfo, VideoAspect
from app.services import material
from app.services.material import get_api_key
from app.utils import utils

_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _matches_image_aspect(width: object, height: object, video_aspect: VideoAspect) -> bool:
    aspect = VideoAspect(video_aspect)
    # The existing local-image pipeline crops to the requested output ratio. Stock
    # sites rarely provide exact square originals, so square searches intentionally
    # accept crop-friendly landscape/portrait photos just like the current square
    # video material path does.
    if aspect == VideoAspect.square:
        try:
            return int(float(width)) > 0 and int(float(height)) > 0
        except (TypeError, ValueError):
            return False
    return material._matches_video_aspect(width, height, aspect)


def search_images_pexels(
    search_term: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> list[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    api_key = get_api_key("pexels_api_keys")
    headers = {
        "Authorization": api_key,
        "User-Agent": "MoneyPrinterTurbo/stock-images",
    }
    params = {
        "query": str(search_term or "").strip(),
        "per_page": 20,
        "orientation": aspect.name,
    }
    url = f"https://api.pexels.com/v1/search?{urlencode(params)}"
    logger.info(f"searching images on pexels: term={search_term!r}")

    try:
        response = requests.get(
            url,
            headers=headers,
            proxies=config.proxy,
            verify=material._get_tls_verify(),
            timeout=(30, 60),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.error(
            "pexels image search failed: "
            f"error={type(exc).__name__}, "
            f"detail={material._redact_request_error(exc, api_key)}"
        )
        return []

    results: list[MaterialInfo] = []
    for photo in payload.get("photos", []) if isinstance(payload, dict) else []:
        if not isinstance(photo, dict):
            continue
        width = photo.get("width")
        height = photo.get("height")
        if not _matches_image_aspect(width, height, aspect):
            continue
        src = photo.get("src") if isinstance(photo.get("src"), dict) else {}
        image_url = str(src.get("original") or src.get("large2x") or "").strip()
        if not image_url:
            continue
        item = MaterialInfo()
        item.provider = "pexels"
        item.url = image_url
        item.duration = 0
        item.source_info = {
            "provider": "pexels",
            "search_term": str(search_term or "").strip(),
            "asset_id": str(photo.get("id")) if photo.get("id") is not None else None,
            "source_page": material._safe_public_url(photo.get("url")),
            "creator": material._creator_info(
                {
                    "name": photo.get("photographer"),
                    "url": photo.get("photographer_url"),
                }
            ),
            "rendition": {
                "id": str(photo.get("id")) if photo.get("id") is not None else None,
                "width": int(width or 0),
                "height": int(height or 0),
            },
        }
        results.append(item)
    return results


def search_images_pixabay(
    search_term: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
) -> list[MaterialInfo]:
    aspect = VideoAspect(video_aspect)
    api_key = get_api_key("pixabay_api_keys")
    orientation = {
        VideoAspect.landscape: "horizontal",
        VideoAspect.portrait: "vertical",
        VideoAspect.square: "all",
    }[aspect]
    params = {
        "key": api_key,
        "q": str(search_term or "").strip(),
        "per_page": 20,
        "orientation": orientation,
        "image_type": "photo",
        "safesearch": "true",
    }
    url = f"https://pixabay.com/api/?{urlencode(params)}"
    logger.info(f"searching images on pixabay: term={search_term!r}")

    try:
        response = requests.get(
            url,
            proxies=config.proxy,
            verify=material._get_tls_verify(),
            timeout=(30, 60),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.error(
            "pixabay image search failed: "
            f"error={type(exc).__name__}, "
            f"detail={material._redact_request_error(exc, api_key)}"
        )
        return []

    results: list[MaterialInfo] = []
    for hit in payload.get("hits", []) if isinstance(payload, dict) else []:
        if not isinstance(hit, dict):
            continue
        width = hit.get("imageWidth") or hit.get("webformatWidth")
        height = hit.get("imageHeight") or hit.get("webformatHeight")
        if not _matches_image_aspect(width, height, aspect):
            continue
        image_url = str(
            hit.get("largeImageURL") or hit.get("fullHDURL") or hit.get("webformatURL") or ""
        ).strip()
        if not image_url:
            continue
        user = str(hit.get("user") or "").strip()
        user_id = hit.get("user_id")
        profile_url = (
            f"https://pixabay.com/users/{user}-{user_id}/"
            if user and user_id not in (None, "")
            else None
        )
        item = MaterialInfo()
        item.provider = "pixabay"
        item.url = image_url
        item.duration = 0
        item.source_info = {
            "provider": "pixabay",
            "search_term": str(search_term or "").strip(),
            "asset_id": str(hit.get("id")) if hit.get("id") is not None else None,
            "source_page": material._safe_public_url(hit.get("pageURL")),
            "creator": material._creator_info(
                {"id": user_id, "name": user, "url": profile_url}
            ),
            "rendition": {
                "id": str(hit.get("id")) if hit.get("id") is not None else None,
                "width": int(width or 0),
                "height": int(height or 0),
            },
        }
        results.append(item)
    return results


def _extension_for_image(item: MaterialInfo, response: requests.Response) -> str:
    content_type = str((response.headers or {}).get("content-type", "")).split(";", 1)[0].lower()
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    suffix = Path(urlsplit(str(item.url)).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".jpg"


def _download_image(item: MaterialInfo, directory: Path, index: int) -> Path:
    response = requests.get(
        item.url,
        proxies=config.proxy,
        verify=material._get_tls_verify(),
        timeout=(30, 120),
    )
    response.raise_for_status()
    content_type = str((response.headers or {}).get("content-type", "")).split(";", 1)[0].lower()
    if content_type and content_type not in _IMAGE_CONTENT_TYPES:
        raise ValueError(f"unexpected image content type: {content_type}")
    if not response.content:
        raise ValueError("downloaded image is empty")

    source = item.source_info if isinstance(item.source_info, dict) else {}
    asset_id = _SAFE_FILENAME.sub("-", str(source.get("asset_id") or index)).strip("-_")
    extension = _extension_for_image(item, response)
    target = directory / f"stock-image-{index:03d}-{asset_id or index}{extension}"
    suffix = 2
    while target.exists():
        target = directory / f"stock-image-{index:03d}-{asset_id or index}-{suffix}{extension}"
        suffix += 1
    target.write_bytes(response.content)
    return target


def download_images(
    *,
    task_id: str,
    search_terms: list[str],
    source: str,
    video_aspect: VideoAspect,
    audio_duration: float,
    image_duration: int,
    match_script_order: bool = False,
    persist_sources: bool = True,
) -> list[Path]:
    normalized_source = str(source or "").strip().lower()
    search_fn: Callable[[str, VideoAspect], list[MaterialInfo]]
    if normalized_source == "pexels":
        search_fn = search_images_pexels
    elif normalized_source == "pixabay":
        search_fn = search_images_pixabay
    else:
        raise ValueError(f"image search is not supported for source '{source}'")

    duration = int(image_duration)
    if duration < 1 or duration > 30:
        raise ValueError("image_duration must be between 1 and 30 seconds")
    terms = [str(term).strip() for term in search_terms if str(term).strip()]
    if not terms:
        return []

    material_directory = Path(utils.task_dir(task_id)) / "stock-images"
    material_directory.mkdir(parents=True, exist_ok=True)
    needed = max(1, int(math.ceil(max(0.0, float(audio_duration)) / duration)))
    downloaded: list[Path] = []
    source_records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for term in terms:
        items = search_fn(term, VideoAspect(video_aspect))
        logger.info(f"found {len(items)} images for '{term}'")
        for item in items:
            info = item.source_info if isinstance(item.source_info, dict) else {}
            dedupe_key = (item.provider, str(info.get("asset_id") or item.url))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            try:
                local_path = _download_image(item, material_directory, len(downloaded) + 1)
            except Exception as exc:
                logger.warning(
                    "failed to download stock image: "
                    f"provider={item.provider}, error={type(exc).__name__}, detail={exc}"
                )
                continue
            downloaded.append(local_path)
            try:
                source_records.append(material._material_source_record(item, str(local_path)))
            except Exception as exc:
                logger.warning(
                    "failed to prepare image source record: "
                    f"provider={item.provider}, error={type(exc).__name__}, detail={exc}"
                )
            if len(downloaded) >= needed:
                break
        if len(downloaded) >= needed:
            break

    if persist_sources and source_records:
        material._persist_material_sources(task_id, source_records)
    return downloaded
