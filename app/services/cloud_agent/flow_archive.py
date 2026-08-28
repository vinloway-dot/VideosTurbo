from __future__ import annotations

import os
import re
import shutil
import stat
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, NamedTuple
from uuid import uuid4
from zipfile import BadZipFile, LargeZipFile, ZipFile, ZipInfo

from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    MediaValidationError,
)
from app.services.cloud_agent.media_probe import validate_video
from app.services.cloud_agent.storage import CloudJobStorage, JobPaths


_SEMANTIC_CLIP_RE = re.compile(r"^clip\s+([1-6])\.mp4$", re.IGNORECASE)
_VENDOR_SEMANTIC_CLIP_RE = re.compile(r"^clip_([1-6])_.+\.mp4$", re.IGNORECASE)


class FlowArtifactRecovery(NamedTuple):
    paths: tuple[Path, ...]
    source: Literal["canonical", "archive", "staging"]


@dataclass(frozen=True)
class FlowPartialInventory:
    snapshot_path: Path
    semantic_numbers: tuple[int, ...]
    missing_index: int
    staged_files: tuple[Path, ...]
    baseline_digest: str


@dataclass(frozen=True)
class FlowRecoveryMaterialization:
    paths: tuple[Path, ...]
    source: Literal["latest_complete_archive", "merged_replacement_only"]


FlowRecoveryCapture = FlowPartialInventory | FlowRecoveryMaterialization


def validate_flow_source_video(path: Path, *, min_size_bytes: int):
    """Validate a Flow source without weakening the final-video contract."""
    probe = validate_video(path, min_size_bytes=min_size_bytes)
    if probe.width is None or probe.height is None:
        raise MediaValidationError("Flow source video resolution is missing")
    if probe.width < 720 or probe.height < 1280:
        raise MediaValidationError(
            f"Flow source resolution is below 720x1280: {probe.width}x{probe.height}"
        )
    if probe.width * 16 != probe.height * 9:
        raise MediaValidationError(
            f"Flow source resolution is not portrait 9:16: {probe.width}x{probe.height}"
        )
    return probe


def _validate_member_safety(member: ZipInfo) -> None:
    name = member.filename
    posix_path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    unix_mode = member.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    unsafe = (
        not name
        or "\\" in name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in posix_path.parts
        or member.flag_bits & 0x1
        or file_type == stat.S_IFLNK
        or (file_type not in {0, stat.S_IFREG, stat.S_IFDIR})
    )
    if unsafe:
        raise FlowArchiveValidationError("unsafe Flow archive member")


def _collect_semantic_members(archive: ZipFile) -> dict[int, ZipInfo]:
    semantic_members = {}
    for member in archive.infolist():
        _validate_member_safety(member)
        if member.is_dir():
            continue

        basename = unicodedata.normalize("NFKC", PurePosixPath(member.filename).name)
        if Path(basename).suffix.lower() != ".mp4":
            continue
        match = _SEMANTIC_CLIP_RE.fullmatch(basename)
        if match is None:
            match = _VENDOR_SEMANTIC_CLIP_RE.fullmatch(basename)
        if match is None:
            raise FlowArchiveValidationError("ambiguous or unexpected Flow video entry")
        number = int(match.group(1))
        if number in semantic_members:
            raise FlowArchiveValidationError(f"duplicate semantic clip {number}")
        semantic_members[number] = member

    return semantic_members


def _semantic_members(archive: ZipFile) -> dict[int, ZipInfo]:
    semantic_members = _collect_semantic_members(archive)
    if set(semantic_members) != set(range(1, 7)):
        raise FlowArchiveValidationError("archive must contain semantic clips 1 through 6")
    return semantic_members


def _baseline_digest(numbers: tuple[int, ...], files: tuple[Path, ...]) -> str:
    if len(numbers) != len(files):
        raise FlowArchiveValidationError("partial inventory is inconsistent")
    digest = sha256()
    for number, path in zip(numbers, files, strict=True):
        if path.is_symlink() or not path.is_file():
            raise FlowArchiveValidationError("partial survivor is unavailable")
        digest.update(f"clip:{number}\0".encode())
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def inspect_partial_flow_archive(
    archive_path: Path,
    paths: JobPaths,
    *,
    min_size_bytes: int,
) -> FlowPartialInventory:
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise FlowArchiveValidationError("partial Flow archive is unavailable")
    staging_dir = paths.flow_staging_dir / f"partial-{uuid4().hex}"
    try:
        with ZipFile(archive_path) as archive:
            semantic_members = _collect_semantic_members(archive)
            numbers = tuple(sorted(semantic_members))
            if len(numbers) != 5 or not set(numbers) < set(range(1, 7)):
                raise FlowArchiveValidationError(
                    "partial archive must contain exactly five unique clips"
                )
            missing = tuple(set(range(1, 7)) - set(numbers))
            if len(missing) != 1:
                raise FlowArchiveValidationError("partial archive has an ambiguous gap")
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_files = []
            for number in numbers:
                staged_path = staging_dir / f"clip {number}.mp4"
                with (
                    archive.open(semantic_members[number]) as source,
                    staged_path.open("wb") as target,
                ):
                    shutil.copyfileobj(source, target)
                validate_flow_source_video(staged_path, min_size_bytes=min_size_bytes)
                staged_files.append(staged_path)
    except FlowArchiveValidationError:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    except (BadZipFile, LargeZipFile, MediaValidationError, OSError, RuntimeError) as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise FlowArchiveValidationError(f"invalid partial Flow archive: {exc}") from exc

    staged_tuple = tuple(staged_files)
    return FlowPartialInventory(
        snapshot_path=archive_path,
        semantic_numbers=numbers,
        missing_index=missing[0],
        staged_files=staged_tuple,
        baseline_digest=_baseline_digest(numbers, staged_tuple),
    )


def inspect_recovery_flow_archive(
    archive_path: Path,
    paths: JobPaths,
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> FlowRecoveryCapture:
    """Accept a completed six-clip recovery snapshot or inventory an exact five."""
    archive_path = Path(archive_path)
    try:
        with ZipFile(archive_path) as archive:
            numbers = tuple(sorted(_collect_semantic_members(archive)))
    except FlowArchiveValidationError:
        raise
    except (BadZipFile, LargeZipFile, OSError, RuntimeError) as exc:
        raise FlowArchiveValidationError(
            f"invalid Flow recovery archive: {exc}"
        ) from exc

    if numbers == tuple(range(1, 7)):
        return FlowRecoveryMaterialization(
            paths=materialize_flow_archive(
                archive_path,
                paths,
                min_size_bytes=min_size_bytes,
                expected_width=expected_width,
                expected_height=expected_height,
            ),
            source="latest_complete_archive",
        )
    return inspect_partial_flow_archive(
        archive_path,
        paths,
        min_size_bytes=min_size_bytes,
    )


def _materialize_staged_files(
    staged_files: list[Path],
    paths: JobPaths,
) -> tuple[Path, ...]:
    temporary_files = []
    materialization_id = uuid4().hex
    for number, staged_path in enumerate(staged_files, start=1):
        temporary_path = paths.flow_dir / f".clip_{number:02d}.{materialization_id}.tmp"
        shutil.copyfile(staged_path, temporary_path)
        temporary_files.append(temporary_path)
    for temporary_path, canonical_path in zip(
        temporary_files,
        paths.flow_files,
        strict=True,
    ):
        os.replace(temporary_path, canonical_path)
    return paths.flow_files


def materialize_flow_archive(
    archive_path: Path,
    paths: JobPaths,
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> tuple[Path, ...]:
    # These legacy parameters formerly imposed Canva's final-output size on
    # Flow sources. Flow exports may be 720x1280 portrait 9:16 instead.
    del expected_width, expected_height
    archive_path = Path(archive_path)
    try:
        with ZipFile(archive_path) as archive:
            semantic_members = _semantic_members(archive)
            staging_dir = paths.flow_staging_dir / uuid4().hex
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_files = []
            for number in range(1, 7):
                staged_path = staging_dir / f"clip {number}.mp4"
                with (
                    archive.open(semantic_members[number]) as source,
                    staged_path.open("wb") as target,
                ):
                    shutil.copyfileobj(source, target)
                validate_flow_source_video(
                    staged_path,
                    min_size_bytes=min_size_bytes,
                )
                staged_files.append(staged_path)
    except FlowArchiveValidationError:
        raise
    except (BadZipFile, LargeZipFile, MediaValidationError, OSError, RuntimeError) as exc:
        raise FlowArchiveValidationError(f"invalid Flow archive: {exc}") from exc

    return _materialize_staged_files(staged_files, paths)


def materialize_latest_or_merge_recovery(
    archive_path: Path,
    prior_inventory: FlowPartialInventory,
    paths: JobPaths,
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> FlowRecoveryMaterialization:
    validation = {
        "min_size_bytes": min_size_bytes,
        "expected_width": expected_width,
        "expected_height": expected_height,
    }
    try:
        materialized = materialize_flow_archive(archive_path, paths, **validation)
    except FlowArchiveValidationError:
        pass
    else:
        return FlowRecoveryMaterialization(
            paths=materialized,
            source="latest_complete_archive",
        )

    if (
        _baseline_digest(
            prior_inventory.semantic_numbers,
            prior_inventory.staged_files,
        )
        != prior_inventory.baseline_digest
    ):
        raise FlowArchiveValidationError("partial survivor baseline changed")

    replacement_dir = paths.flow_staging_dir / f"replacement-{uuid4().hex}"
    try:
        with ZipFile(archive_path) as archive:
            semantic_members = _collect_semantic_members(archive)
            if set(semantic_members) != {prior_inventory.missing_index}:
                raise FlowArchiveValidationError(
                    "replacement archive must contain only the missing clip"
                )
            replacement_dir.mkdir(parents=True, exist_ok=False)
            replacement_path = (
                replacement_dir / f"clip {prior_inventory.missing_index}.mp4"
            )
            with (
                archive.open(semantic_members[prior_inventory.missing_index]) as source,
                replacement_path.open("wb") as target,
            ):
                shutil.copyfileobj(source, target)
            validate_flow_source_video(
                replacement_path,
                min_size_bytes=min_size_bytes,
            )

        survivors = dict(
            zip(
                prior_inventory.semantic_numbers,
                prior_inventory.staged_files,
                strict=True,
            )
        )
        staged_files = [
            replacement_path if number == prior_inventory.missing_index else survivors[number]
            for number in range(1, 7)
        ]
        if not _validate_video_set(staged_files, **validation):
            raise FlowArchiveValidationError("merged Flow recovery set is invalid")
        materialized = _materialize_staged_files(staged_files, paths)
    except FlowArchiveValidationError:
        shutil.rmtree(replacement_dir, ignore_errors=True)
        raise
    except (BadZipFile, LargeZipFile, MediaValidationError, OSError, RuntimeError) as exc:
        shutil.rmtree(replacement_dir, ignore_errors=True)
        raise FlowArchiveValidationError(f"invalid Flow replacement archive: {exc}") from exc
    return FlowRecoveryMaterialization(
        paths=materialized,
        source="merged_replacement_only",
    )


def _validate_video_set(
    files: tuple[Path, ...] | list[Path],
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> bool:
    del expected_width, expected_height
    if len(files) != 6 or any(path.is_symlink() or not path.is_file() for path in files):
        return False
    try:
        for path in files:
            validate_flow_source_video(
                path,
                min_size_bytes=min_size_bytes,
            )
    except (MediaValidationError, OSError):
        return False
    return True


def _quarantine_path(paths: JobPaths, source: Path) -> Path:
    destination_dir = paths.flow_quarantine_dir / uuid4().hex
    destination_dir.mkdir(parents=False, exist_ok=False)
    destination = destination_dir / source.name
    source.replace(destination)
    return destination


def _staged_semantic_files(directory: Path) -> list[Path] | None:
    semantic_files = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            continue
        normalized = unicodedata.normalize("NFKC", entry.name)
        if Path(normalized).suffix.lower() != ".mp4":
            continue
        match = _SEMANTIC_CLIP_RE.fullmatch(normalized)
        if match is None:
            return None
        number = int(match.group(1))
        if number in semantic_files:
            return None
        semantic_files[number] = entry
    if set(semantic_files) != set(range(1, 7)):
        return None
    return [semantic_files[number] for number in range(1, 7)]


def recover_flow_artifacts(
    storage: CloudJobStorage,
    job_id: str,
    *,
    min_size_bytes: int,
    expected_width: int,
    expected_height: int,
) -> FlowArtifactRecovery | None:
    paths = storage.prepare(job_id)
    validation = {
        "min_size_bytes": min_size_bytes,
        "expected_width": expected_width,
        "expected_height": expected_height,
    }

    if _validate_video_set(paths.flow_files, **validation):
        return FlowArtifactRecovery(paths.flow_files, "canonical")
    storage.quarantine_flow_canonical(job_id)

    if paths.flow_archive_file.is_file() and not paths.flow_archive_file.is_symlink():
        try:
            recovered = materialize_flow_archive(
                paths.flow_archive_file,
                paths,
                **validation,
            )
        except FlowArchiveValidationError:
            _quarantine_path(paths, paths.flow_archive_file)
        else:
            return FlowArtifactRecovery(recovered, "archive")
    elif paths.flow_archive_file.exists() or paths.flow_archive_file.is_symlink():
        _quarantine_path(paths, paths.flow_archive_file)

    candidates = [
        entry
        for entry in paths.flow_staging_dir.iterdir()
        if entry.is_dir() and not entry.is_symlink()
    ]
    for candidate in sorted(candidates):
        staged_files = _staged_semantic_files(candidate)
        if staged_files is not None and _validate_video_set(staged_files, **validation):
            try:
                recovered = _materialize_staged_files(staged_files, paths)
            except OSError:
                _quarantine_path(paths, candidate)
                continue
            return FlowArtifactRecovery(recovered, "staging")
        _quarantine_path(paths, candidate)
    return None
