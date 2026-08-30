"""Additional ancestor-permission guard for the POSIX Thumbnail Prompt backend."""

from __future__ import annotations

from contextlib import contextmanager
import os
import stat

from app.services.cloud_agent.thumbnail_prompt._settings_posix import (
    ThumbnailPromptSettingsService as _PosixThumbnailPromptSettingsService,
    _UnsafeSettingsPath,
    fcntl,
)


class ThumbnailPromptSettingsService(_PosixThumbnailPromptSettingsService):
    """Reject writable non-leaf ancestors around the descriptor-safe backend."""

    @contextmanager
    def _locked_directory(self):
        self._require_safe_nonleaf_ancestors()
        with super()._locked_directory() as locked:
            self._require_safe_nonleaf_ancestors()
            yield locked
            self._require_safe_nonleaf_ancestors()

    def _require_safe_nonleaf_ancestors(self) -> None:
        parent = self._settings_path.parent
        components = parent.parts[1:-1]
        current_fd = None
        try:
            current_fd = os.open(
                "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            self._require_safe_ancestor(os.fstat(current_fd))
            for component in components:
                if component in {"", ".", ".."}:
                    raise _UnsafeSettingsPath("unsafe settings ancestor")
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                try:
                    self._require_safe_ancestor(os.fstat(next_fd))
                except Exception:
                    os.close(next_fd)
                    raise
                os.close(current_fd)
                current_fd = next_fd
        except OSError as exc:
            raise _UnsafeSettingsPath("unsafe settings ancestor") from exc
        finally:
            if current_fd is not None:
                try:
                    os.close(current_fd)
                except OSError:
                    pass

    @staticmethod
    def _require_safe_ancestor(directory_stat: os.stat_result) -> None:
        mode = stat.S_IMODE(directory_stat.st_mode)
        writable_by_others = bool(mode & 0o022)
        sticky_shared_directory = bool(mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_nlink < 2
            or (writable_by_others and not sticky_shared_directory)
        ):
            raise _UnsafeSettingsPath("unsafe settings ancestor")


__all__ = ["ThumbnailPromptSettingsService", "fcntl"]
