import importlib
import importlib.util
import stat
import struct
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from app.services.cloud_agent.errors import (
    FlowArchiveValidationError,
    MediaValidationError,
)
from app.services.cloud_agent.media_probe import MediaProbe
from app.services.cloud_agent.storage import CloudJobStorage


MODULE_NAME = "app.services.cloud_agent.flow_archive"


def _flow_archive_module():
    module_spec = importlib.util.find_spec(MODULE_NAME)
    assert module_spec is not None, "Flow archive materializer is not implemented"
    return importlib.import_module(MODULE_NAME)


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        for name, contents in members:
            archive.writestr(name, contents)


def _valid_members() -> list[tuple[str, bytes]]:
    return [(f"clip {number}.mp4", f"video-{number}".encode()) for number in range(1, 7)]


def _sanitized_vendor_members(numbers=range(1, 7)) -> list[tuple[str, bytes]]:
    return [
        (
            f"Holy_Basil_CLIP_{number}_202609030325.mp4",
            f"video-{number}".encode(),
        )
        for number in numbers
    ]


def _video_probe(path: Path, *, width: int = 1080, height: int = 1920) -> MediaProbe:
    return MediaProbe(
        path=Path(path),
        size_bytes=64,
        duration=10.0,
        has_audio=False,
        has_video=True,
        audio_codec="",
        video_codec="h264",
        width=width,
        height=height,
    )


def _accept_video(monkeypatch, flow_archive) -> None:
    monkeypatch.setattr(
        flow_archive,
        "validate_video",
        lambda path, **_kwargs: _video_probe(Path(path)),
    )


def _set_first_member_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", data, local + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", data, central + 8)[0] | 0x1
    struct.pack_into("<H", data, local + 6, local_flags)
    struct.pack_into("<H", data, central + 8, central_flags)
    path.write_bytes(data)


def test_shuffled_archive_members_materialize_in_semantic_order(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(
        paths.flow_archive_file,
        [
            ("exports/clip 4.mp4", b"four"),
            ("exports/clip 1.mp4", b"one"),
            ("exports/clip 6.mp4", b"six"),
            ("exports/clip 2.mp4", b"two"),
            ("exports/clip 5.mp4", b"five"),
            ("exports/clip 3.mp4", b"three"),
        ],
    )
    validated = []
    monkeypatch.setattr(
        flow_archive,
        "validate_video",
        lambda path, **kwargs: (
            validated.append((Path(path), kwargs)),
            _video_probe(Path(path)),
        )[1],
    )

    result = flow_archive.materialize_flow_archive(
        paths.flow_archive_file,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result == paths.flow_files
    assert [path.read_bytes() for path in result] == [
        b"one",
        b"two",
        b"three",
        b"four",
        b"five",
        b"six",
    ]
    assert [path.name for path, _ in validated] == [
        "clip 1.mp4",
        "clip 2.mp4",
        "clip 3.mp4",
        "clip 4.mp4",
        "clip 5.mp4",
        "clip 6.mp4",
    ]
    assert all(
        kwargs
        == {"min_size_bytes": 1}
        for _, kwargs in validated
    )


def test_vendor_export_names_materialize_in_semantic_order(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(
        paths.flow_archive_file,
        [
            ("CLIP_6_order_timestamp.mp4", b"six"),
            ("clip_1_polar_timestamp.mp4", b"one"),
            ("CLIP_4_polygon_timestamp.mp4", b"four"),
            ("cLiP_2_storm_timestamp.mp4", b"two"),
            ("CLIP_5_seasons_timestamp.mp4", b"five"),
            ("CLIP_3_waves_timestamp.mp4", b"three"),
        ],
    )
    _accept_video(monkeypatch, flow_archive)

    result = flow_archive.materialize_flow_archive(
        paths.flow_archive_file,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result == paths.flow_files
    assert [path.read_bytes() for path in result] == [
        b"one",
        b"two",
        b"three",
        b"four",
        b"five",
        b"six",
    ]


def test_sanitized_vendor_recovery_archive_materializes_unordered_six(
    monkeypatch,
    tmp_path,
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    snapshot = paths.flow_snapshots_dir / "partial-0.zip"
    _write_archive(
        snapshot,
        _sanitized_vendor_members((4, 1, 6, 2, 5, 3)),
    )
    _accept_video(monkeypatch, flow_archive)

    result = flow_archive.inspect_recovery_flow_archive(
        snapshot,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result.source == "latest_complete_archive"
    assert result.paths == paths.flow_files
    assert [path.read_bytes() for path in result.paths] == [
        f"video-{number}".encode() for number in range(1, 7)
    ]


def test_sanitized_vendor_recovery_archive_exact_five_identifies_gap(
    monkeypatch,
    tmp_path,
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    snapshot = paths.flow_snapshots_dir / "partial-0.zip"
    _write_archive(snapshot, _sanitized_vendor_members((1, 2, 3, 5, 6)))
    _accept_video(monkeypatch, flow_archive)

    result = flow_archive.inspect_recovery_flow_archive(
        snapshot,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result.missing_index == 4
    assert result.semantic_numbers == (1, 2, 3, 5, 6)
    assert [path.read_bytes() for path in result.staged_files] == [
        b"video-1",
        b"video-2",
        b"video-3",
        b"video-5",
        b"video-6",
    ]


@pytest.mark.parametrize(
    "members",
    [
        [
            *_sanitized_vendor_members(),
            ("Sacred_Basil_CLIP_1_202609030326.mp4", b"duplicate"),
        ],
        [
            *_sanitized_vendor_members(range(1, 6)),
            ("Holy_Basil_CLIP_7_202609030325.mp4", b"out-of-range"),
        ],
        [
            (
                "Holy_Basil_CLIP_1_CLIP_2_202609030325.mp4",
                b"ambiguous",
            ),
            *_sanitized_vendor_members(range(2, 7)),
        ],
        [*_sanitized_vendor_members(), ("README.txt", b"unexpected")],
        _sanitized_vendor_members(range(1, 6)),
    ],
    ids=[
        "duplicate-index",
        "out-of-range-index",
        "multiple-clip-numbers",
        "unexpected-non-video",
        "missing-clip",
    ],
)
def test_sanitized_vendor_archive_remains_fail_closed(
    monkeypatch,
    tmp_path,
    members,
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(paths.flow_archive_file, members)
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )


def test_vendor_export_name_with_unicode_em_dash_is_accepted(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(
        paths.flow_archive_file,
        [
            (f"CLIP_{number}_—_title_202608250702.mp4", str(number).encode())
            for number in range(1, 7)
        ],
    )
    _accept_video(monkeypatch, flow_archive)

    result = flow_archive.materialize_flow_archive(
        paths.flow_archive_file,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert [path.read_bytes() for path in result] == [
        str(number).encode() for number in range(1, 7)
    ]


def test_vendor_export_rejects_missing_semantic_index(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    members = [
        (f"CLIP_{number}_title_timestamp.mp4", b"video")
        for number in range(1, 6)
    ]
    _write_archive(paths.flow_archive_file, members)
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )


def test_vendor_export_rejects_duplicate_semantic_index(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    members = [
        (f"CLIP_{number}_title_timestamp.mp4", b"video")
        for number in range(1, 7)
    ] + [("CLIP_1_duplicate_timestamp.mp4", b"duplicate")]
    _write_archive(paths.flow_archive_file, members)
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )


@pytest.mark.parametrize("name", ["CLIP_10_title_timestamp.mp4", "something_else.mp4"])
def test_vendor_export_rejects_nonsemantic_video_names(monkeypatch, tmp_path, name):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(
        paths.flow_archive_file,
        [
            (f"CLIP_{number}_title_timestamp.mp4", b"video")
            for number in range(1, 7)
        ]
        + [(name, b"unexpected")],
    )
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )


@pytest.mark.parametrize("width,height", [(720, 1280), (1080, 1920)])
def test_flow_source_validation_accepts_portrait_nine_by_sixteen(
    monkeypatch, tmp_path, width, height
):
    flow_archive = _flow_archive_module()
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(
        flow_archive,
        "validate_video",
        lambda path, **_kwargs: _video_probe(Path(path), width=width, height=height),
    )

    validate_source = getattr(flow_archive, "validate_flow_source_video", None)
    assert validate_source is not None, "Flow-specific source validation is not implemented"
    assert validate_source(media_path, min_size_bytes=1).width == width


@pytest.mark.parametrize(
    "width,height",
    [(540, 960), (360, 640), (720, 720), (1280, 720), (720, 1200)],
)
def test_flow_source_validation_rejects_below_minimum_or_wrong_aspect(
    monkeypatch, tmp_path, width, height
):
    flow_archive = _flow_archive_module()
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"video")
    monkeypatch.setattr(
        flow_archive,
        "validate_video",
        lambda path, **_kwargs: _video_probe(Path(path), width=width, height=height),
    )

    validate_source = getattr(flow_archive, "validate_flow_source_video", None)
    assert validate_source is not None, "Flow-specific source validation is not implemented"
    with pytest.raises(MediaValidationError):
        validate_source(media_path, min_size_bytes=1)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "/absolute/clip 1.mp4",
        "../clip 1.mp4",
        "nested/../../clip 1.mp4",
        "..\\clip 1.mp4",
        "C:\\clip 1.mp4",
        "\\\\server\\share\\clip 1.mp4",
    ],
)
def test_archive_rejects_unsafe_member_paths(monkeypatch, tmp_path, unsafe_name):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    members = _valid_members()
    members[0] = (unsafe_name, b"unsafe")
    _write_archive(paths.flow_archive_file, members)
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError, match="unsafe"):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )

    assert all(not path.exists() for path in paths.flow_files)


def test_archive_rejects_symlink_entry(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(paths.flow_archive_file, _valid_members()[1:])
    symlink = ZipInfo("clip 1.mp4")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(paths.flow_archive_file, "a") as archive:
        archive.writestr(symlink, "target.mp4")
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError, match="unsafe"):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )


def test_archive_rejects_encrypted_entry(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(paths.flow_archive_file, _valid_members())
    _set_first_member_encrypted(paths.flow_archive_file)
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError, match="unsafe"):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )


@pytest.mark.parametrize(
    "members",
    [
        _valid_members()[:-1],
        [*_valid_members(), ("duplicate/clip 1.mp4", b"duplicate")],
        [*_valid_members(), ("clip 1 copy.mp4", b"ambiguous")],
        [*_valid_members(), ("clip 7.mp4", b"extra")],
    ],
)
def test_archive_rejects_incomplete_duplicate_or_extra_video_sets(
    monkeypatch, tmp_path, members
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(paths.flow_archive_file, members)
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )

    assert all(not path.exists() for path in paths.flow_files)


def test_archive_media_failure_is_typed_and_exposes_no_canonical_set(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    _write_archive(paths.flow_archive_file, _valid_members())

    def reject_third(path, **_kwargs):
        if Path(path).name == "clip 3.mp4":
            raise MediaValidationError("invalid media")
        return _video_probe(Path(path))

    monkeypatch.setattr(flow_archive, "validate_video", reject_third)

    with pytest.raises(FlowArchiveValidationError, match="invalid media"):
        flow_archive.materialize_flow_archive(
            paths.flow_archive_file,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )

    assert all(not path.exists() for path in paths.flow_files)


def test_five_semantic_clips_produce_one_missing_index(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    snapshot = paths.flow_snapshots_dir / "partial-0.zip"
    _write_archive(
        snapshot,
        [
            (f"clip {number}.mp4", f"video-{number}".encode())
            for number in (1, 3, 4, 5, 6)
        ],
    )
    _accept_video(monkeypatch, flow_archive)

    result = flow_archive.inspect_partial_flow_archive(
        snapshot,
        paths,
        min_size_bytes=1,
    )

    assert result.missing_index == 2
    assert result.semantic_numbers == (1, 3, 4, 5, 6)
    assert len(result.baseline_digest) == 64
    assert [path.read_bytes() for path in result.staged_files] == [
        b"video-1",
        b"video-3",
        b"video-4",
        b"video-5",
        b"video-6",
    ]


def test_recovery_archive_with_all_six_clips_materializes_without_replacement(
    monkeypatch, tmp_path
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    snapshot = paths.flow_snapshots_dir / "partial-0.zip"
    _write_archive(
        snapshot,
        [
            (f"CLIP_{number}_title_timestamp.mp4", f"video-{number}".encode())
            for number in range(1, 7)
        ],
    )
    _accept_video(monkeypatch, flow_archive)

    result = flow_archive.inspect_recovery_flow_archive(
        snapshot,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result.source == "latest_complete_archive"
    assert result.paths == paths.flow_files
    assert [path.read_bytes() for path in result.paths] == [
        f"video-{number}".encode() for number in range(1, 7)
    ]


@pytest.mark.parametrize(
    "numbers",
    [(1, 2, 3, 4), (1, 2, 2, 4, 5), (1, 2, 3, 4, 7)],
)
def test_partial_inventory_rejects_non_exact_safe_five(
    monkeypatch, tmp_path, numbers
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    snapshot = paths.flow_snapshots_dir / "partial-0.zip"
    seen = set()
    members = []
    for position, number in enumerate(numbers):
        name = (
            f"CLIP_{number}_duplicate.mp4"
            if number in seen
            else f"clip {number}.mp4"
        )
        members.append((name, f"video-{position}".encode()))
        seen.add(number)
    _write_archive(
        snapshot,
        members,
    )
    _accept_video(monkeypatch, flow_archive)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.inspect_partial_flow_archive(
            snapshot,
            paths,
            min_size_bytes=1,
        )


def test_complete_second_archive_wins_without_merge(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    partial = paths.flow_snapshots_dir / "partial-0.zip"
    latest = paths.flow_snapshots_dir / "replacement-1.zip"
    _write_archive(
        partial,
        [(f"clip {n}.mp4", f"old-{n}".encode()) for n in (1, 3, 4, 5, 6)],
    )
    _write_archive(
        latest,
        [(f"clip {n}.mp4", f"new-{n}".encode()) for n in range(1, 7)],
    )
    _accept_video(monkeypatch, flow_archive)
    inventory = flow_archive.inspect_partial_flow_archive(
        partial, paths, min_size_bytes=1
    )
    for path in inventory.staged_files:
        path.unlink()

    result = flow_archive.materialize_latest_or_merge_recovery(
        latest,
        inventory,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result.source == "latest_complete_archive"
    assert [path.read_bytes() for path in paths.flow_files] == [
        f"new-{n}".encode() for n in range(1, 7)
    ]


def test_replacement_only_archive_merges_with_immutable_survivors(
    monkeypatch, tmp_path
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    partial = paths.flow_snapshots_dir / "partial-0.zip"
    replacement = paths.flow_snapshots_dir / "replacement-1.zip"
    _write_archive(
        partial,
        [(f"clip {n}.mp4", f"old-{n}".encode()) for n in (1, 3, 4, 5, 6)],
    )
    _write_archive(replacement, [("clip 2.mp4", b"new-2")])
    _accept_video(monkeypatch, flow_archive)
    inventory = flow_archive.inspect_partial_flow_archive(
        partial, paths, min_size_bytes=1
    )

    result = flow_archive.materialize_latest_or_merge_recovery(
        replacement,
        inventory,
        paths,
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert result.source == "merged_replacement_only"
    assert [path.read_bytes() for path in paths.flow_files] == [
        b"old-1",
        b"new-2",
        b"old-3",
        b"old-4",
        b"old-5",
        b"old-6",
    ]


def test_replacement_validation_failure_keeps_existing_canonical_files(
    monkeypatch, tmp_path
):
    flow_archive = _flow_archive_module()
    paths = CloudJobStorage(tmp_path / "jobs").prepare("job-123")
    partial = paths.flow_snapshots_dir / "partial-0.zip"
    replacement = paths.flow_snapshots_dir / "replacement-1.zip"
    _write_archive(
        partial,
        [(f"clip {n}.mp4", f"old-{n}".encode()) for n in (1, 3, 4, 5, 6)],
    )
    _write_archive(replacement, [("clip 2.mp4", b"invalid")])
    _accept_video(monkeypatch, flow_archive)
    inventory = flow_archive.inspect_partial_flow_archive(
        partial, paths, min_size_bytes=1
    )
    for path in paths.flow_files:
        path.write_bytes(b"canonical-original")

    def reject_replacement(path, **_kwargs):
        if Path(path).read_bytes() == b"invalid":
            raise MediaValidationError("invalid replacement")
        return _video_probe(Path(path))

    monkeypatch.setattr(flow_archive, "validate_video", reject_replacement)

    with pytest.raises(FlowArchiveValidationError):
        flow_archive.materialize_latest_or_merge_recovery(
            replacement,
            inventory,
            paths,
            min_size_bytes=1,
            expected_width=1080,
            expected_height=1920,
        )

    assert all(path.read_bytes() == b"canonical-original" for path in paths.flow_files)


def test_recovery_prefers_complete_valid_canonical_set(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    for number, path in enumerate(paths.flow_files, start=1):
        path.write_bytes(f"canonical-{number}".encode())
    paths.flow_archive_file.write_bytes(b"not-a-zip")
    validations = []
    monkeypatch.setattr(
        flow_archive,
        "validate_video",
        lambda path, **kwargs: (
            validations.append((Path(path), kwargs)),
            _video_probe(Path(path)),
        )[1],
    )

    recover = getattr(flow_archive, "recover_flow_artifacts", None)
    assert recover is not None, "Flow artifact recovery is not implemented"
    recovered = recover(
        storage,
        "job-123",
        min_size_bytes=11,
        expected_width=1080,
        expected_height=1920,
    )

    assert recovered.source == "canonical"
    assert recovered.paths == paths.flow_files
    assert [path for path, _ in validations] == list(paths.flow_files)
    assert all(kwargs["min_size_bytes"] == 11 for _, kwargs in validations)
    assert paths.flow_archive_file.read_bytes() == b"not-a-zip"


def test_recovery_quarantines_partial_canonical_then_salvages_zip(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    paths.flow_files[0].write_bytes(b"partial-one")
    paths.flow_files[1].write_bytes(b"partial-two")
    _write_archive(paths.flow_archive_file, _valid_members())
    _accept_video(monkeypatch, flow_archive)

    recovered = flow_archive.recover_flow_artifacts(
        storage,
        "job-123",
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert recovered.source == "archive"
    assert recovered.paths == paths.flow_files
    assert [path.read_bytes() for path in paths.flow_files] == [
        f"video-{number}".encode() for number in range(1, 7)
    ]
    quarantined = list(paths.flow_quarantine_dir.rglob("clip_01.mp4"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"partial-one"


def test_recovery_salvages_complete_staging_after_partial_canonical_materialization(
    monkeypatch, tmp_path
):
    flow_archive = _flow_archive_module()
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    paths.flow_files[0].write_bytes(b"partial-canonical")
    staged_dir = paths.flow_staging_dir / "validated-before-crash"
    staged_dir.mkdir()
    for number in range(1, 7):
        (staged_dir / f"clip {number}.mp4").write_bytes(
            f"staged-{number}".encode()
        )
    _accept_video(monkeypatch, flow_archive)

    recovered = flow_archive.recover_flow_artifacts(
        storage,
        "job-123",
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert recovered.source == "staging"
    assert recovered.paths == paths.flow_files
    assert [path.read_bytes() for path in paths.flow_files] == [
        f"staged-{number}".encode() for number in range(1, 7)
    ]
    assert len(list(paths.flow_quarantine_dir.rglob("clip_01.mp4"))) == 1
    assert all((staged_dir / f"clip {number}.mp4").is_file() for number in range(1, 7))


def test_recovery_quarantines_invalid_archive_then_uses_complete_staging(
    monkeypatch, tmp_path
):
    flow_archive = _flow_archive_module()
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    paths.flow_archive_file.write_bytes(b"invalid-archive")
    staged_dir = paths.flow_staging_dir / "complete"
    staged_dir.mkdir()
    for number in range(1, 7):
        (staged_dir / f"clip {number}.mp4").write_bytes(b"valid")
    _accept_video(monkeypatch, flow_archive)

    recovered = flow_archive.recover_flow_artifacts(
        storage,
        "job-123",
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert recovered.source == "staging"
    assert len(list(paths.flow_quarantine_dir.rglob("product_clips.zip"))) == 1


def test_recovery_returns_none_for_partial_invalid_local_state(monkeypatch, tmp_path):
    flow_archive = _flow_archive_module()
    storage = CloudJobStorage(tmp_path / "jobs")
    paths = storage.prepare("job-123")
    paths.flow_files[0].write_bytes(b"partial")
    staged_dir = paths.flow_staging_dir / "partial"
    staged_dir.mkdir()
    (staged_dir / "clip 1.mp4").write_bytes(b"only-one")
    _accept_video(monkeypatch, flow_archive)

    recovered = flow_archive.recover_flow_artifacts(
        storage,
        "job-123",
        min_size_bytes=1,
        expected_width=1080,
        expected_height=1920,
    )

    assert recovered is None
    assert all(not path.exists() for path in paths.flow_files)
    assert not staged_dir.exists()
    assert list(paths.flow_quarantine_dir.rglob("clip_01.mp4"))
    assert list(paths.flow_quarantine_dir.rglob("clip 1.mp4"))
