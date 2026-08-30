from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import shutil
import stat
from typing import Iterator
from uuid import uuid4

from app.utils import utils
from app.utils.file_security import resolve_path_within_directory


@dataclass(frozen=True)
class JobPaths:
    job_dir: Path
    input_dir: Path
    audio_dir: Path
    flow_dir: Path
    flow_downloads_dir: Path
    flow_staging_dir: Path
    flow_quarantine_dir: Path
    screenshots_dir: Path
    logs_dir: Path
    final_dir: Path
    script_file: Path
    master_prompt_file: Path
    voice_file: Path
    flow_files: tuple[Path, ...]
    flow_archive_file: Path
    final_file: Path


@dataclass(frozen=True)
class _RetainedPromptEntry:
    parent_fd: int
    file_descriptor: int
    name: str
    identity: tuple[int, int]
    is_directory: bool
    requires_safe_permissions: bool = False


class CloudJobStorage:
    #: Maximum UTF-8 bytes accepted from one saved video master prompt.
    MASTER_PROMPT_MAX_BYTES = 1_048_576
    MASTER_PROMPT_PRIVATE_MODE = 0o600

    def __init__(self, root: Path | None = None):
        if root is None:
            root = Path(utils.storage_dir("jobs", create=True))
        self.root = Path(root)

    @staticmethod
    def _validate_job_id(job_id: str) -> str:
        normalized = str(job_id or "").strip()
        if not normalized or normalized in {".", ".."}:
            raise ValueError("invalid job id")
        if "/" in normalized or "\\" in normalized:
            raise ValueError("job id must not contain path separators")
        if Path(normalized).is_absolute():
            raise ValueError("job id must not be an absolute path")
        return normalized

    def _paths(self, job_id: str) -> JobPaths:
        safe_job_id = self._validate_job_id(job_id)
        root = self.root.resolve()
        job_dir = (root / safe_job_id).resolve()
        if job_dir.parent != root:
            raise ValueError("job path is outside storage root")

        input_dir = job_dir / "input"
        audio_dir = job_dir / "audio"
        flow_dir = job_dir / "flow"
        flow_downloads_dir = flow_dir / "downloads"
        flow_staging_dir = flow_dir / "staging"
        flow_quarantine_dir = flow_dir / "quarantine"
        screenshots_dir = job_dir / "screenshots"
        logs_dir = job_dir / "logs"
        final_dir = job_dir / "final"

        return JobPaths(
            job_dir=job_dir,
            input_dir=input_dir,
            audio_dir=audio_dir,
            flow_dir=flow_dir,
            flow_downloads_dir=flow_downloads_dir,
            flow_staging_dir=flow_staging_dir,
            flow_quarantine_dir=flow_quarantine_dir,
            screenshots_dir=screenshots_dir,
            logs_dir=logs_dir,
            final_dir=final_dir,
            script_file=input_dir / "script.txt",
            master_prompt_file=input_dir / "master_prompt.txt",
            voice_file=audio_dir / "voice.mp3",
            flow_files=tuple(
                flow_dir / f"clip_{index:02d}.mp4" for index in range(1, 7)
            ),
            flow_archive_file=flow_downloads_dir / "product_clips.zip",
            final_file=final_dir / "final.mp4",
        )

    @staticmethod
    def _directory_open_flags() -> int:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    @classmethod
    @contextmanager
    def _open_deleting_directory(
        cls, root: Path, *, create: bool
    ) -> Iterator[tuple[int, int]]:
        root_fd = os.open(root, cls._directory_open_flags())
        deleting_fd = None
        try:
            if create:
                try:
                    os.mkdir(".deleting", dir_fd=root_fd)
                except FileExistsError:
                    pass
            try:
                deleting_fd = os.open(
                    ".deleting", cls._directory_open_flags(), dir_fd=root_fd
                )
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(
                        "deleting directory must be a real directory"
                    ) from exc
                raise
            if not cls._deleting_entry_matches(root_fd, deleting_fd):
                raise ValueError("deleting directory changed during validation")
            yield root_fd, deleting_fd
        finally:
            if deleting_fd is not None:
                os.close(deleting_fd)
            os.close(root_fd)

    @staticmethod
    def _deleting_entry_matches(root_fd: int, deleting_fd: int) -> bool:
        try:
            entry = os.stat(".deleting", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        opened = os.fstat(deleting_fd)
        return (
            stat.S_ISDIR(entry.st_mode)
            and entry.st_dev == opened.st_dev
            and entry.st_ino == opened.st_ino
        )

    @staticmethod
    def _direct_staged_name(root: Path, staged_dir: Path) -> str:
        staged = Path(staged_dir)
        if not staged.is_absolute() or staged.parent != root / ".deleting":
            raise ValueError("staged path is outside storage root")
        if not staged.name or staged.name in {".", ".."}:
            raise ValueError("invalid staged path")
        return staged.name

    def prepare(self, job_id: str) -> JobPaths:
        paths = self._paths(job_id)
        for directory in (
            paths.input_dir,
            paths.audio_dir,
            paths.flow_dir,
            paths.flow_downloads_dir,
            paths.flow_staging_dir,
            paths.flow_quarantine_dir,
            paths.screenshots_dir,
            paths.logs_dir,
            paths.final_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return paths

    def write_inputs(self, job_id: str, script: str, master_prompt: str) -> JobPaths:
        paths = self.prepare(job_id)
        paths.script_file.write_text(script, encoding="utf-8")
        self._write_private_master_prompt(paths.master_prompt_file, master_prompt)
        return paths

    @classmethod
    def _write_private_master_prompt(cls, path: Path, master_prompt: str) -> None:
        if os.name != "posix":
            path.write_text(master_prompt, encoding="utf-8")
            return

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, cls.MASTER_PROMPT_PRIVATE_MODE)
        try:
            os.fchmod(descriptor, cls.MASTER_PROMPT_PRIVATE_MODE)
            data = str(master_prompt).encode("utf-8")
            written = 0
            while written < len(data):
                count = os.write(descriptor, data[written:])
                if count <= 0:
                    raise OSError("failed to write job master prompt")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read_master_prompt(self, job_id: str) -> str:
        """Read at most ``MASTER_PROMPT_MAX_BYTES`` from a verified job prompt."""
        safe_job_id = self._validate_job_id(job_id)
        anchor_fd = None
        retained_entries = []
        try:
            root_path = self.root.absolute()
            anchor_fd = os.open("/", self._directory_open_flags())
            anchor_stat = os.fstat(anchor_fd)
            if not stat.S_ISDIR(anchor_stat.st_mode):
                raise ValueError("job master prompt is unavailable")
            anchor_identity = (anchor_stat.st_dev, anchor_stat.st_ino)
            current_fd = anchor_fd
            directory_names = (*root_path.parts[1:], safe_job_id, "input")
            safe_directory_start = len(directory_names) - 2
            for index, name in enumerate(directory_names):
                directory_fd = os.open(
                    name, self._directory_open_flags(), dir_fd=current_fd
                )
                try:
                    directory_stat = os.fstat(directory_fd)
                    requires_safe_permissions = index >= safe_directory_start
                    if requires_safe_permissions:
                        self._require_safe_master_prompt_directory(directory_stat)
                    elif not stat.S_ISDIR(directory_stat.st_mode):
                        raise ValueError("job master prompt is unavailable")
                except (OSError, ValueError):
                    os.close(directory_fd)
                    raise
                retained_entries.append(
                    _RetainedPromptEntry(
                        parent_fd=current_fd,
                        file_descriptor=directory_fd,
                        name=name,
                        identity=(directory_stat.st_dev, directory_stat.st_ino),
                        is_directory=True,
                        requires_safe_permissions=requires_safe_permissions,
                    )
                )
                current_fd = directory_fd

            prompt_fd = os.open(
                "master_prompt.txt",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            try:
                prompt_stat = os.fstat(prompt_fd)
                self._require_safe_master_prompt(prompt_stat)
            except (OSError, ValueError):
                os.close(prompt_fd)
                raise
            retained_entries.append(
                _RetainedPromptEntry(
                    parent_fd=current_fd,
                    file_descriptor=prompt_fd,
                    name="master_prompt.txt",
                    identity=(prompt_stat.st_dev, prompt_stat.st_ino),
                    is_directory=False,
                )
            )
            content = self._read_master_prompt_bytes(prompt_fd)
            value = content.decode("utf-8").strip()
            self._verify_retained_prompt_entries(
                anchor_fd, anchor_identity, retained_entries
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("job master prompt is unavailable") from exc
        finally:
            descriptors = [
                entry.file_descriptor for entry in reversed(retained_entries)
            ]
            if anchor_fd is not None:
                descriptors.append(anchor_fd)
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if not value:
            raise ValueError("job master prompt is empty")
        return value

    @classmethod
    def _require_safe_master_prompt(cls, prompt_stat: os.stat_result) -> None:
        if (
            not stat.S_ISREG(prompt_stat.st_mode)
            or prompt_stat.st_uid != os.geteuid()
            or prompt_stat.st_nlink != 1
            or stat.S_IMODE(prompt_stat.st_mode) != cls.MASTER_PROMPT_PRIVATE_MODE
            or prompt_stat.st_size > cls.MASTER_PROMPT_MAX_BYTES
        ):
            raise ValueError("job master prompt is unavailable")

    @staticmethod
    def _require_safe_master_prompt_directory(directory_stat: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or directory_stat.st_nlink < 2
            or stat.S_IMODE(directory_stat.st_mode) & 0o022
        ):
            raise ValueError("job master prompt is unavailable")

    @classmethod
    def _read_master_prompt_bytes(cls, prompt_fd: int) -> bytes:
        chunks = []
        remaining = cls.MASTER_PROMPT_MAX_BYTES + 1
        while remaining:
            chunk = os.read(prompt_fd, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > cls.MASTER_PROMPT_MAX_BYTES:
            raise ValueError("job master prompt is unavailable")
        return content

    @classmethod
    def _verify_retained_prompt_entries(
        cls,
        anchor_fd: int,
        anchor_identity: tuple[int, int],
        retained_entries: list[_RetainedPromptEntry],
    ) -> None:
        anchor_stat = os.fstat(anchor_fd)
        if (
            not stat.S_ISDIR(anchor_stat.st_mode)
            or (anchor_stat.st_dev, anchor_stat.st_ino) != anchor_identity
        ):
            raise ValueError("job master prompt is unavailable")
        for entry in retained_entries:
            held = os.fstat(entry.file_descriptor)
            named = os.stat(entry.name, dir_fd=entry.parent_fd, follow_symlinks=False)
            if entry.is_directory:
                if entry.requires_safe_permissions:
                    cls._require_safe_master_prompt_directory(held)
                    cls._require_safe_master_prompt_directory(named)
                elif not stat.S_ISDIR(held.st_mode) or not stat.S_ISDIR(named.st_mode):
                    raise ValueError("job master prompt is unavailable")
            else:
                cls._require_safe_master_prompt(held)
                cls._require_safe_master_prompt(named)
            if (held.st_dev, held.st_ino) != entry.identity or (
                named.st_dev,
                named.st_ino,
            ) != entry.identity:
                raise ValueError("job master prompt is unavailable")

    def cleanup_flow_sources(self, job_id: str) -> None:
        paths = self.prepare(job_id)
        flow_root = paths.flow_dir.resolve()

        for flow_file in paths.flow_files:
            if not flow_file.exists() and not flow_file.is_symlink():
                continue
            resolved = flow_file.resolve()
            if resolved.parent != flow_root:
                raise ValueError("flow source path escapes job flow directory")
            flow_file.unlink()

    def quarantine_flow_canonical(self, job_id: str) -> Path | None:
        paths = self.prepare(job_id)
        flow_root = paths.flow_dir.resolve()
        sources = [
            path for path in paths.flow_files if path.exists() or path.is_symlink()
        ]
        if not sources:
            return None

        for source in sources:
            if source.resolve().parent != flow_root:
                raise ValueError("flow source path escapes job flow directory")

        destination = paths.flow_quarantine_dir / uuid4().hex
        destination.mkdir(parents=False, exist_ok=False)
        for source in sources:
            source.replace(destination / source.name)
        return destination

    def has_valid_final_video(self, job_id: str, recorded_final_video: str) -> bool:
        paths = self._paths(job_id)
        try:
            resolved_recorded = Path(
                resolve_path_within_directory(
                    str(self.root.resolve()), recorded_final_video, require_file=True
                )
            )
        except (TypeError, ValueError):
            return False
        return resolved_recorded == paths.final_file.resolve()

    def stage_job_artifacts(self, job_id: str) -> Path:
        safe_job_id = self._validate_job_id(job_id)
        paths = self._paths(safe_job_id)
        root = self.root.resolve()
        if paths.job_dir.parent != root:
            raise ValueError("job path is outside storage root")
        if paths.job_dir == (root / ".deleting"):
            raise ValueError("reserved storage directory")
        staged_name = f"{safe_job_id}-{uuid4().hex}"
        with self._open_deleting_directory(root, create=True) as (
            root_fd,
            deleting_fd,
        ):
            try:
                job_entry = os.stat(safe_job_id, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise FileNotFoundError(paths.job_dir) from exc
            if not stat.S_ISDIR(job_entry.st_mode):
                raise ValueError("job path must be a directory")
            os.rename(
                safe_job_id,
                staged_name,
                src_dir_fd=root_fd,
                dst_dir_fd=deleting_fd,
            )
            if not self._deleting_entry_matches(root_fd, deleting_fd):
                os.rename(
                    staged_name,
                    safe_job_id,
                    src_dir_fd=deleting_fd,
                    dst_dir_fd=root_fd,
                )
                raise ValueError("deleting directory changed during staging")
        return root / ".deleting" / staged_name

    def restore_staged_job(self, job_id: str, staged_dir: Path) -> None:
        safe_job_id = self._validate_job_id(job_id)
        paths = self._paths(safe_job_id)
        root = self.root.resolve()
        staged_name = self._direct_staged_name(root, staged_dir)
        if paths.job_dir.parent != root:
            raise ValueError("staged path is outside storage root")
        with self._open_deleting_directory(root, create=False) as (
            root_fd,
            deleting_fd,
        ):
            try:
                staged_entry = os.stat(
                    staged_name, dir_fd=deleting_fd, follow_symlinks=False
                )
            except FileNotFoundError as exc:
                raise FileNotFoundError(staged_dir) from exc
            if not stat.S_ISDIR(staged_entry.st_mode):
                raise ValueError("staged path must be a directory")
            try:
                os.stat(safe_job_id, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(paths.job_dir)
            os.rename(
                staged_name,
                safe_job_id,
                src_dir_fd=deleting_fd,
                dst_dir_fd=root_fd,
            )

    def purge_staged_job(self, staged_dir: Path) -> None:
        root = self.root.resolve()
        staged_name = self._direct_staged_name(root, staged_dir)
        try:
            deleting_context = self._open_deleting_directory(root, create=False)
            with deleting_context as (_root_fd, deleting_fd):
                try:
                    staged_entry = os.stat(
                        staged_name, dir_fd=deleting_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    return
                if not stat.S_ISDIR(staged_entry.st_mode):
                    raise ValueError("staged path must be a directory")
                shutil.rmtree(staged_name, dir_fd=deleting_fd)
        except FileNotFoundError:
            return
