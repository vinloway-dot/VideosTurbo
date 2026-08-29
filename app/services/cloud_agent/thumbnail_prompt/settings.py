"""Package-owned configuration and credential storage for Thumbnail Prompt."""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unicodedata
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
import toml

from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError
from app.services.cloud_agent.thumbnail_prompt.models import (
    ThumbnailPromptGenerationSettings,
    ThumbnailPromptProviderMetadata,
    ThumbnailPromptSettings,
    ThumbnailPromptSettingsPayload,
)


_PROVIDERS = {
    "aihubmix": {
        "label": "AIHubMix",
        "default_model": "gpt-5.6-sol",
        "api_key_name": "aihubmix_api_key",
        "base_url_name": "aihubmix_base_url",
        "model_name": "aihubmix_model",
        "custom_model_name": "aihubmix_custom_model",
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "openai/gpt-5.6-sol",
        "api_key_name": "openrouter_api_key",
        "base_url_name": "openrouter_base_url",
        "model_name": "openrouter_model",
        "custom_model_name": "openrouter_custom_model",
    },
}
_DEFAULTS = {
    "master_prompt": "",
    "default_provider": "aihubmix",
    "aihubmix_model": "gpt-5.6-sol",
    "aihubmix_custom_model": "",
    "aihubmix_api_key": "",
    "aihubmix_base_url": "https://aihubmix.com/v1",
    "openrouter_model": "openai/gpt-5.6-sol",
    "openrouter_custom_model": "",
    "openrouter_api_key": "",
    "openrouter_base_url": "https://openrouter.ai/api/v1",
}
_RECOVERY_CONFIG = {
    **_DEFAULTS,
    "default_provider": "",
    "aihubmix_base_url": "",
    "openrouter_base_url": "",
}
_SETTINGS_LOCK = threading.RLock()
_LOCK_FILE_NAME = ".settings.lock"
_MAX_SETTINGS_BYTES = 65_536
_MAX_CORRUPT_BACKUP_BYTES = 1_048_576
_INVALID_DEFAULT_PROVIDER_MESSAGE = (
    "Saved default thumbnail provider is invalid. "
    "Select AIHubMix or OpenRouter and save Thumbnail Prompt Settings."
)
_INVALID_BASE_URL_MESSAGE = (
    "Saved thumbnail provider base URL is invalid. "
    "Enter valid HTTP(S) Base URLs and save Thumbnail Prompt Settings."
)
_CORRUPT_SETTINGS_MESSAGE = (
    "Saved Thumbnail Prompt settings are corrupt. "
    "Save Thumbnail Prompt Settings to repair them."
)


class _PersistedSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    master_prompt: str = Field(max_length=8000)
    default_provider: str = Field(max_length=64)
    aihubmix_model: str = Field(max_length=256)
    aihubmix_custom_model: str = Field(max_length=256)
    aihubmix_api_key: str = Field(max_length=4096)
    aihubmix_base_url: str = Field(max_length=2048)
    openrouter_model: str = Field(max_length=256)
    openrouter_custom_model: str = Field(max_length=256)
    openrouter_api_key: str = Field(max_length=4096)
    openrouter_base_url: str = Field(max_length=2048)


@dataclass(frozen=True)
class _LoadedSettings:
    configured: dict[str, object]
    status: str
    file_identity: tuple[int, int] | None = None

    @property
    def corrupt(self) -> bool:
        return self.status in {"corrupt", "unsafe"}

    @property
    def preservable(self) -> bool:
        return self.status == "corrupt"


@dataclass(frozen=True)
class _DirectoryHandles:
    parent_fd: int
    directory_fd: int
    leaf_name: str
    leaf_identity: tuple[int, int]


@dataclass(frozen=True)
class _PreservedBackup:
    name: str
    identity: tuple[int, int]


class _UnsafeSettingsPath(Exception):
    """The dedicated settings filesystem boundary cannot be trusted."""


class ThumbnailPromptSettingsService:
    """Own Thumbnail Prompt settings without sharing application configuration."""

    DEFAULT_PROVIDER_ID = "aihubmix"
    KEY_NAMES = {
        provider_id: str(metadata["api_key_name"])
        for provider_id, metadata in _PROVIDERS.items()
    }

    def __init__(self, *, settings_path: Path) -> None:
        self._settings_path = Path(settings_path)
        self._settings_name = self._settings_path.name
        shared_temp_root = Path(tempfile.gettempdir())
        dedicated_root = self._settings_path.parent.parent
        if (
            not self._settings_path.is_absolute()
            or ".." in self._settings_path.parts
            or self._settings_name != "settings.toml"
            or self._settings_path.parent.name != "thumbnail_prompt"
            or dedicated_root in {Path("/"), Path.cwd(), shared_temp_root}
        ):
            raise ValueError(
                "expected an absolute dedicated Thumbnail Prompt settings path"
            )

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def list_providers(self) -> list[ThumbnailPromptProviderMetadata]:
        loaded = self._read_loaded_settings()
        return [
            self._provider_from_config(
                provider_id, loaded.configured, corrupt=loaded.corrupt
            )
            for provider_id in _PROVIDERS
        ]

    def get_provider(self, provider_id: str) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        loaded = self._read_loaded_settings()
        return self._provider_from_config(
            normalized, loaded.configured, corrupt=loaded.corrupt
        )

    def get_settings(self) -> ThumbnailPromptSettings:
        loaded = self._read_loaded_settings()
        return self._settings_from_config(loaded.configured, corrupt=loaded.corrupt)

    def get_configured_provider_id(self) -> str:
        loaded = self._read_loaded_settings()
        self._require_usable(loaded)
        configured = self._configured_text(loaded.configured, "default_provider")
        return self._require_provider(configured)

    def update_settings(
        self, payload: ThumbnailPromptSettingsPayload
    ) -> ThumbnailPromptSettings:
        self._require_provider(payload.default_provider)
        self._validate_model(
            "aihubmix", payload.aihubmix_model, payload.aihubmix_custom_model_id
        )
        self._validate_model(
            "openrouter", payload.openrouter_model, payload.openrouter_custom_model_id
        )
        aihubmix_base_url = self._validate_base_url(
            "aihubmix", payload.aihubmix_base_url
        )
        openrouter_base_url = self._validate_base_url(
            "openrouter", payload.openrouter_base_url
        )

        with _SETTINGS_LOCK:
            try:
                with self._locked_directory() as (handles, lock_fd):
                    loaded = self._load_locked(handles.directory_fd)
                    configured = self._configuration_for_write(loaded)
                    configured.update(
                        {
                            "master_prompt": payload.master_prompt,
                            "default_provider": payload.default_provider,
                            _PROVIDERS["aihubmix"][
                                "model_name"
                            ]: payload.aihubmix_model,
                            _PROVIDERS["aihubmix"][
                                "custom_model_name"
                            ]: payload.aihubmix_custom_model_id,
                            _PROVIDERS["aihubmix"]["base_url_name"]: aihubmix_base_url,
                            _PROVIDERS["openrouter"][
                                "model_name"
                            ]: payload.openrouter_model,
                            _PROVIDERS["openrouter"][
                                "custom_model_name"
                            ]: payload.openrouter_custom_model_id,
                            _PROVIDERS["openrouter"][
                                "base_url_name"
                            ]: openrouter_base_url,
                        }
                    )
                    self._save_locked(handles, lock_fd, configured, loaded)
            except _UnsafeSettingsPath as exc:
                raise self._unsafe_settings_error() from exc
        return self._settings_from_config(configured, corrupt=False)

    def set_api_key(
        self, provider_id: str, value: str
    ) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        cleaned_value = str(value or "").strip()
        if not cleaned_value:
            return self.get_provider(normalized)
        if len(cleaned_value) > 4096:
            raise ThumbnailPromptError(
                "THUMBNAIL_PROMPT_REQUEST_INVALID", "provider key is too long"
            )

        with _SETTINGS_LOCK:
            try:
                with self._locked_directory() as (handles, lock_fd):
                    loaded = self._load_locked(handles.directory_fd)
                    configured = self._configuration_for_write(loaded)
                    configured[self.KEY_NAMES[normalized]] = cleaned_value
                    self._save_locked(handles, lock_fd, configured, loaded)
            except _UnsafeSettingsPath as exc:
                raise self._unsafe_settings_error() from exc
        return self._provider_from_config(normalized, configured)

    def remove_api_key(
        self, provider_id: str, confirmed: bool
    ) -> ThumbnailPromptProviderMetadata:
        normalized = self._require_provider(provider_id)
        if confirmed is not True:
            raise ThumbnailPromptError(
                "THUMBNAIL_PROMPT_REQUEST_INVALID", "key removal not confirmed"
            )
        with _SETTINGS_LOCK:
            try:
                with self._locked_directory() as (handles, lock_fd):
                    loaded = self._load_locked(handles.directory_fd)
                    configured = self._configuration_for_write(loaded)
                    configured[self.KEY_NAMES[normalized]] = ""
                    self._save_locked(handles, lock_fd, configured, loaded)
            except _UnsafeSettingsPath as exc:
                raise self._unsafe_settings_error() from exc
        return self._provider_from_config(normalized, configured)

    def get_api_key_for_generation(self, provider_id: str) -> SecretStr:
        normalized = self._require_provider(provider_id)
        loaded = self._read_loaded_settings()
        self._require_usable(loaded)
        value = self._configured_text(loaded.configured, self.KEY_NAMES[normalized])
        if not value:
            raise ThumbnailPromptError(
                "PROVIDER_API_KEY_MISSING",
                f"{normalized} provider key is not configured",
            )
        return SecretStr(value)

    def resolve_model(self, provider_id: str) -> str:
        normalized = self._require_provider(provider_id)
        metadata = _PROVIDERS[normalized]
        loaded = self._read_loaded_settings()
        self._require_usable(loaded)
        choice = self._configured_text(loaded.configured, metadata["model_name"])
        custom_model = self._configured_text(
            loaded.configured, metadata["custom_model_name"]
        )
        self._validate_model(normalized, choice, custom_model)
        return custom_model if choice == "custom" else choice

    def get_base_url_for_generation(self, provider_id: str) -> str:
        normalized = self._require_provider(provider_id)
        loaded = self._read_loaded_settings()
        self._require_usable(loaded)
        value = self._configured_text(
            loaded.configured, _PROVIDERS[normalized]["base_url_name"]
        )
        return self._validate_base_url(normalized, value)

    def get_generation_snapshot(self) -> ThumbnailPromptGenerationSettings:
        with _SETTINGS_LOCK:
            try:
                with self._locked_directory() as (handles, _lock_fd):
                    loaded = self._load_locked(handles.directory_fd)
                    self._require_usable(loaded)
                    configured = loaded.configured
                    provider_id = self._require_provider(
                        self._configured_text(configured, "default_provider")
                    )
                    metadata = _PROVIDERS[provider_id]
                    api_key_value = self._configured_text(
                        configured, metadata["api_key_name"]
                    )
                    if not api_key_value:
                        raise ThumbnailPromptError(
                            "PROVIDER_API_KEY_MISSING",
                            f"{provider_id} provider key is not configured",
                        )
                    model_choice = self._configured_text(
                        configured, metadata["model_name"]
                    )
                    custom_model = self._configured_text(
                        configured, metadata["custom_model_name"]
                    )
                    self._validate_model(provider_id, model_choice, custom_model)
                    model_id = (
                        custom_model if model_choice == "custom" else model_choice
                    )
                    base_url = self._validate_base_url(
                        provider_id,
                        self._configured_text(configured, metadata["base_url_name"]),
                    )
                    master_prompt = self._configured_text(configured, "master_prompt")
                    if not master_prompt:
                        raise ThumbnailPromptError(
                            "THUMBNAIL_MASTER_PROMPT_MISSING",
                            "ยังไม่ได้ตั้งค่า Thumbnail Master Prompt",
                        )
                    return ThumbnailPromptGenerationSettings(
                        provider_id=provider_id,
                        api_key=SecretStr(api_key_value),
                        model_id=model_id,
                        base_url=base_url,
                        master_prompt=master_prompt,
                    )
            except _UnsafeSettingsPath as exc:
                raise self._unsafe_settings_error() from exc

    def _read_loaded_settings(self) -> _LoadedSettings:
        with _SETTINGS_LOCK:
            try:
                with self._locked_directory() as (handles, _lock_fd):
                    return self._load_locked(handles.directory_fd)
            except _UnsafeSettingsPath:
                return _LoadedSettings(dict(_RECOVERY_CONFIG), "unsafe")

    @contextmanager
    def _locked_directory(self):
        handles = None
        lock_fd = None
        lock_acquired = False
        try:
            handles = self._open_directory_handles()
            lock_fd = self._open_regular_file_locked(
                handles.directory_fd,
                _LOCK_FILE_NAME,
                os.O_RDWR | os.O_CREAT,
                mode=0o600,
                require_single_link=True,
            )
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            lock_acquired = True
            self._verify_leaf_identity_locked(handles)
            self._verify_lock_identity_locked(handles.directory_fd, lock_fd)
            yield handles, lock_fd
            self._verify_leaf_identity_locked(handles)
        finally:
            active_exception = sys.exc_info()[0] is not None
            cleanup_error = None
            if lock_fd is not None:
                if lock_acquired:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except Exception as exc:
                        cleanup_error = exc
                try:
                    os.close(lock_fd)
                except Exception as exc:
                    cleanup_error = cleanup_error or exc
            if handles is not None:
                for file_descriptor in (handles.directory_fd, handles.parent_fd):
                    try:
                        os.close(file_descriptor)
                    except Exception as exc:
                        cleanup_error = cleanup_error or exc
            if cleanup_error is not None and not active_exception:
                raise _UnsafeSettingsPath("settings descriptor cleanup failed") from (
                    cleanup_error
                )

    def _open_directory_handles(self) -> _DirectoryHandles:
        parent = self._settings_path.parent
        current_fd = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        components = parent.parts[1:]
        try:
            for index, component in enumerate(components):
                if component in {"", "."}:
                    continue
                if component == "..":
                    raise _UnsafeSettingsPath("parent traversal is not allowed")
                is_leaf = index == len(components) - 1
                next_fd = self._open_or_create_directory_locked(
                    current_fd, component, allow_create=is_leaf
                )
                if is_leaf:
                    try:
                        leaf_stat = os.fstat(next_fd)
                        self._require_safe_directory(leaf_stat)
                    except Exception:
                        try:
                            os.close(next_fd)
                        except OSError:
                            pass
                        raise
                    return _DirectoryHandles(
                        parent_fd=current_fd,
                        directory_fd=next_fd,
                        leaf_name=component,
                        leaf_identity=(leaf_stat.st_dev, leaf_stat.st_ino),
                    )
                closing_fd = current_fd
                current_fd = None
                try:
                    os.close(closing_fd)
                except Exception:
                    try:
                        os.close(next_fd)
                    except OSError:
                        pass
                    raise
                current_fd = next_fd
            raise _UnsafeSettingsPath("dedicated settings leaf is missing")
        except Exception:
            if current_fd is not None:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
            raise

    @staticmethod
    def _require_safe_directory(directory_stat: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or stat.S_IMODE(directory_stat.st_mode) & 0o022
            or directory_stat.st_nlink < 2
        ):
            raise _UnsafeSettingsPath("unsafe dedicated settings directory")

    def _verify_leaf_identity_locked(self, handles: _DirectoryHandles) -> None:
        try:
            opened = os.fstat(handles.directory_fd)
            named = os.stat(
                handles.leaf_name,
                dir_fd=handles.parent_fd,
                follow_symlinks=False,
            )
            self._require_safe_directory(opened)
            self._require_safe_directory(named)
        except OSError as exc:
            raise _UnsafeSettingsPath("settings directory identity changed") from exc
        if (opened.st_dev, opened.st_ino) != handles.leaf_identity or (
            named.st_dev,
            named.st_ino,
        ) != handles.leaf_identity:
            raise _UnsafeSettingsPath("settings directory identity changed")

    @staticmethod
    def _open_or_create_directory_locked(
        parent_fd: int, component: str, *, allow_create: bool
    ) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            return os.open(component, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            if not allow_create:
                raise _UnsafeSettingsPath("settings ancestor does not exist")
            try:
                os.mkdir(component, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            try:
                directory_fd = os.open(component, flags, dir_fd=parent_fd)
            except OSError as exc:
                raise _UnsafeSettingsPath("unsafe settings directory") from exc
            return directory_fd
        except OSError as exc:
            raise _UnsafeSettingsPath("unsafe settings directory") from exc

    @staticmethod
    def _verify_lock_identity_locked(directory_fd: int, lock_fd: int) -> None:
        try:
            held = os.fstat(lock_fd)
            named = os.stat(
                _LOCK_FILE_NAME,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise _UnsafeSettingsPath("settings lock identity changed") from exc
        ThumbnailPromptSettingsService._require_private_regular_file(held)
        ThumbnailPromptSettingsService._require_private_regular_file(named)
        if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            raise _UnsafeSettingsPath("settings lock identity changed")

    @staticmethod
    def _open_regular_file_locked(
        directory_fd: int,
        name: str,
        flags: int,
        *,
        mode: int = 0o600,
        require_single_link: bool,
    ) -> int:
        try:
            file_descriptor = os.open(
                name,
                flags | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise _UnsafeSettingsPath("unsafe settings file") from exc
        try:
            file_stat = os.fstat(file_descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or (
                require_single_link and file_stat.st_nlink != 1
            ):
                raise _UnsafeSettingsPath("settings file is not a private regular file")
        except Exception as exc:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            if isinstance(exc, _UnsafeSettingsPath):
                raise
            raise _UnsafeSettingsPath("could not inspect settings file") from exc
        return file_descriptor

    @staticmethod
    def _require_private_regular_file(file_stat: os.stat_result) -> None:
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.geteuid()
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_nlink != 1
        ):
            raise _UnsafeSettingsPath("settings file is not private")

    @classmethod
    def _private_regular_file_stat(cls, file_descriptor: int) -> os.stat_result:
        try:
            file_stat = os.fstat(file_descriptor)
        except OSError as exc:
            raise _UnsafeSettingsPath("could not inspect settings file") from exc
        cls._require_private_regular_file(file_stat)
        return file_stat

    def _load_locked(self, directory_fd: int) -> _LoadedSettings:
        try:
            file_descriptor = os.open(
                self._settings_name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return _LoadedSettings(dict(_DEFAULTS), "missing")
        except OSError as exc:
            try:
                file_stat = os.stat(
                    self._settings_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return _LoadedSettings(dict(_DEFAULTS), "missing")
            except OSError as stat_error:
                raise _UnsafeSettingsPath("unsafe settings file") from stat_error
            if (
                stat.S_ISLNK(file_stat.st_mode)
                or not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.geteuid()
                or stat.S_IMODE(file_stat.st_mode) != 0o600
                or file_stat.st_nlink != 1
            ):
                raise _UnsafeSettingsPath("unsafe settings file") from exc
            return _LoadedSettings(
                dict(_RECOVERY_CONFIG),
                "corrupt",
                (file_stat.st_dev, file_stat.st_ino),
            )

        try:
            file_stat = self._private_regular_file_stat(file_descriptor)
        except Exception:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            raise
        file_identity = (file_stat.st_dev, file_stat.st_ino)

        try:
            persisted_bytes = self._read_bounded(file_descriptor)
        except OSError:
            self._verify_named_private_file_locked(
                directory_fd, self._settings_name, file_identity
            )
            return _LoadedSettings(dict(_RECOVERY_CONFIG), "corrupt", file_identity)
        finally:
            try:
                os.close(file_descriptor)
            except OSError as exc:
                raise _UnsafeSettingsPath("settings file cleanup failed") from exc
        self._verify_named_private_file_locked(
            directory_fd, self._settings_name, file_identity
        )
        try:
            persisted_text = persisted_bytes.decode("utf-8")
            persisted = toml.loads(persisted_text)
            configured = dict(_DEFAULTS)
            if not isinstance(persisted, dict):
                return _LoadedSettings(dict(_RECOVERY_CONFIG), "corrupt", file_identity)
            configured.update(
                {key: persisted[key] for key in _DEFAULTS if key in persisted}
            )
            validated = _PersistedSettings.model_validate(configured)
        except (UnicodeDecodeError, toml.TomlDecodeError, ValidationError, TypeError):
            return _LoadedSettings(dict(_RECOVERY_CONFIG), "corrupt", file_identity)
        return _LoadedSettings(validated.model_dump(), "valid", file_identity)

    @staticmethod
    def _read_bounded(file_descriptor: int) -> bytes:
        chunks = []
        remaining = _MAX_SETTINGS_BYTES + 1
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > _MAX_SETTINGS_BYTES:
            raise OSError(errno.EFBIG, "settings file exceeds size limit")
        return content

    @staticmethod
    def _configuration_for_write(loaded: _LoadedSettings) -> dict[str, object]:
        if loaded.status == "unsafe":
            raise _UnsafeSettingsPath("unsafe settings state")
        if loaded.preservable:
            return dict(_DEFAULTS)
        return dict(loaded.configured)

    def _save_locked(
        self,
        handles: _DirectoryHandles,
        lock_fd: int,
        configured: dict[str, object],
        loaded: _LoadedSettings,
    ) -> None:
        directory_fd = handles.directory_fd
        validated = _PersistedSettings.model_validate(configured).model_dump()
        serialized = toml.dumps(validated).encode("utf-8")
        temporary_name = f".{self._settings_name}.{uuid4().hex}.tmp"
        temporary_fd = None
        temporary_identity = None
        preserved_backup = None
        try:
            temporary_fd = self._open_regular_file_locked(
                directory_fd,
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                require_single_link=True,
            )
            os.fchmod(temporary_fd, 0o600)
            temporary_stat = self._private_regular_file_stat(temporary_fd)
            temporary_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
            self._verify_temporary_file_locked(
                directory_fd,
                temporary_name,
                temporary_fd,
                temporary_identity,
            )
            self._write_all(temporary_fd, serialized)
            os.fsync(temporary_fd)
            self._verify_canonical_identity_locked(directory_fd, loaded.file_identity)
            if loaded.preservable:
                preserved_backup = self._copy_corrupt_backup_locked(
                    directory_fd, loaded.file_identity
                )
            self._verify_canonical_identity_locked(directory_fd, loaded.file_identity)
            self._verify_leaf_identity_locked(handles)
            self._verify_lock_identity_locked(directory_fd, lock_fd)
            self._verify_leaf_identity_locked(handles)
            self._verify_temporary_file_locked(
                directory_fd,
                temporary_name,
                temporary_fd,
                temporary_identity,
            )
            os.replace(
                temporary_name,
                self._settings_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            self._verify_named_private_file_locked(
                directory_fd, self._settings_name, temporary_identity
            )
            self._verify_leaf_identity_locked(handles)
            os.fsync(directory_fd)
            self._verify_named_private_file_locked(
                directory_fd, self._settings_name, temporary_identity
            )
            self._verify_leaf_identity_locked(handles)
        except Exception:
            if temporary_identity is not None:
                self._unlink_matching_file_locked(
                    directory_fd, temporary_name, temporary_identity
                )
            if preserved_backup is not None and loaded.file_identity is not None:
                self._rollback_corrupt_backup_locked(
                    directory_fd, preserved_backup, loaded.file_identity
                )
            raise
        finally:
            if temporary_fd is not None:
                active_exception = sys.exc_info()[0] is not None
                try:
                    os.close(temporary_fd)
                except OSError as exc:
                    if not active_exception:
                        raise _UnsafeSettingsPath(
                            "temporary settings cleanup failed"
                        ) from exc

    def _copy_corrupt_backup_locked(
        self, directory_fd: int, expected_identity: tuple[int, int] | None
    ) -> _PreservedBackup:
        if expected_identity is None:
            raise _UnsafeSettingsPath("corrupt settings identity is missing")
        try:
            source_fd = os.open(
                self._settings_name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise _UnsafeSettingsPath("could not preserve corrupt settings") from exc
        backup_name = f".corrupt-{uuid4().hex}"
        backup_fd = None
        backup_identity = None
        backup_complete = False
        try:
            source_stat = self._private_regular_file_stat(source_fd)
            if (source_stat.st_dev, source_stat.st_ino) != expected_identity:
                raise _UnsafeSettingsPath("corrupt settings identity changed")
            if source_stat.st_size > _MAX_CORRUPT_BACKUP_BYTES:
                raise _UnsafeSettingsPath("corrupt settings exceeds backup limit")
            backup_fd = self._open_regular_file_locked(
                directory_fd,
                backup_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                mode=0o600,
                require_single_link=True,
            )
            os.fchmod(backup_fd, 0o600)
            backup_stat = self._private_regular_file_stat(backup_fd)
            backup_identity = (backup_stat.st_dev, backup_stat.st_ino)
            copied_bytes = 0
            while True:
                chunk = os.read(source_fd, 8192)
                if not chunk:
                    break
                copied_bytes += len(chunk)
                if copied_bytes > _MAX_CORRUPT_BACKUP_BYTES:
                    raise _UnsafeSettingsPath("corrupt settings exceeds backup limit")
                self._write_all(backup_fd, chunk)
            os.fsync(backup_fd)
            closing_fd = backup_fd
            backup_fd = None
            os.close(closing_fd)
            os.fsync(directory_fd)
            closing_fd = source_fd
            source_fd = None
            try:
                os.close(closing_fd)
            except Exception as exc:
                raise _UnsafeSettingsPath("settings backup cleanup failed") from exc
            backup_complete = True
        finally:
            active_exception = sys.exc_info()[0] is not None
            cleanup_error = None
            if source_fd is not None:
                closing_fd = source_fd
                source_fd = None
                try:
                    os.close(closing_fd)
                except Exception as exc:
                    cleanup_error = exc
            if backup_fd is not None:
                closing_fd = backup_fd
                backup_fd = None
                try:
                    os.close(closing_fd)
                except Exception as exc:
                    cleanup_error = cleanup_error or exc
            if not backup_complete and backup_identity is not None:
                try:
                    if self._unlink_matching_file_locked(
                        directory_fd, backup_name, backup_identity
                    ):
                        os.fsync(directory_fd)
                except Exception as exc:
                    cleanup_error = cleanup_error or exc
            if cleanup_error is not None and not active_exception:
                raise _UnsafeSettingsPath("settings backup cleanup failed") from (
                    cleanup_error
                )
        return _PreservedBackup(backup_name, backup_identity)

    def _rollback_corrupt_backup_locked(
        self,
        directory_fd: int,
        preserved_backup: _PreservedBackup,
        expected_canonical_identity: tuple[int, int],
    ) -> None:
        try:
            self._verify_canonical_identity_locked(
                directory_fd, expected_canonical_identity
            )
        except (OSError, _UnsafeSettingsPath):
            return
        if self._unlink_matching_file_locked(
            directory_fd, preserved_backup.name, preserved_backup.identity
        ):
            try:
                os.fsync(directory_fd)
            except OSError:
                pass

    def _verify_named_private_file_locked(
        self, directory_fd: int, name: str, expected_identity: tuple[int, int]
    ) -> None:
        try:
            file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            self._require_private_regular_file(file_stat)
        except OSError as exc:
            raise _UnsafeSettingsPath("settings file identity changed") from exc
        if (file_stat.st_dev, file_stat.st_ino) != expected_identity:
            raise _UnsafeSettingsPath("settings file identity changed")

    def _verify_temporary_file_locked(
        self,
        directory_fd: int,
        name: str,
        file_descriptor: int,
        expected_identity: tuple[int, int],
    ) -> None:
        held = self._private_regular_file_stat(file_descriptor)
        if (held.st_dev, held.st_ino) != expected_identity:
            raise _UnsafeSettingsPath("temporary settings identity changed")
        self._verify_named_private_file_locked(directory_fd, name, expected_identity)

    def _unlink_matching_file_locked(
        self, directory_fd: int, name: str, expected_identity: tuple[int, int]
    ) -> bool:
        try:
            file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            self._require_private_regular_file(file_stat)
            if (file_stat.st_dev, file_stat.st_ino) != expected_identity:
                return False
            os.unlink(name, dir_fd=directory_fd)
            return True
        except (OSError, _UnsafeSettingsPath):
            return False

    def _verify_canonical_identity_locked(
        self, directory_fd: int, expected_identity: tuple[int, int] | None
    ) -> None:
        try:
            file_stat = os.stat(
                self._settings_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if expected_identity is None:
                return
            raise _UnsafeSettingsPath("settings identity changed") from None
        except OSError as exc:
            raise _UnsafeSettingsPath("settings identity changed") from exc
        self._require_private_regular_file(file_stat)
        if (
            expected_identity is None
            or (
                file_stat.st_dev,
                file_stat.st_ino,
            )
            != expected_identity
        ):
            raise _UnsafeSettingsPath("settings identity changed")

    @staticmethod
    def _write_all(file_descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(file_descriptor, content[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "settings write made no progress")
            offset += written

    def _provider_from_config(
        self,
        provider_id: str,
        configured: dict[str, object],
        *,
        corrupt: bool = False,
    ) -> ThumbnailPromptProviderMetadata:
        metadata = _PROVIDERS[provider_id]
        readable_base_url, _ = self._readable_base_url(provider_id, configured)
        return ThumbnailPromptProviderMetadata(
            id=provider_id,
            label=str(metadata["label"]),
            models=[str(metadata["default_model"]), "custom"],
            default_model=str(metadata["default_model"]),
            custom_model_id=self._configured_text(
                configured, metadata["custom_model_name"]
            ),
            base_url=readable_base_url,
            api_key_configured=bool(
                self._configured_text(configured, metadata["api_key_name"])
            ),
            configuration_error=_CORRUPT_SETTINGS_MESSAGE if corrupt else None,
        )

    def _settings_from_config(
        self, configured: dict[str, object], *, corrupt: bool
    ) -> ThumbnailPromptSettings:
        if corrupt:
            return ThumbnailPromptSettings(
                master_prompt="",
                default_provider=None,
                configuration_error=_CORRUPT_SETTINGS_MESSAGE,
                aihubmix_model=str(_DEFAULTS["aihubmix_model"]),
                aihubmix_custom_model_id="",
                aihubmix_base_url="",
                openrouter_model=str(_DEFAULTS["openrouter_model"]),
                openrouter_custom_model_id="",
                openrouter_base_url="",
            )
        configured_provider = self._configured_text(configured, "default_provider")
        readable_provider = (
            configured_provider if configured_provider in _PROVIDERS else None
        )
        aihubmix_base_url, aihubmix_base_url_valid = self._readable_base_url(
            "aihubmix", configured
        )
        openrouter_base_url, openrouter_base_url_valid = self._readable_base_url(
            "openrouter", configured
        )
        configuration_errors = []
        if not readable_provider:
            configuration_errors.append(_INVALID_DEFAULT_PROVIDER_MESSAGE)
        if not (aihubmix_base_url_valid and openrouter_base_url_valid):
            configuration_errors.append(_INVALID_BASE_URL_MESSAGE)
        try:
            return ThumbnailPromptSettings(
                master_prompt=self._configured_text(configured, "master_prompt"),
                default_provider=readable_provider,
                configuration_error=" ".join(configuration_errors) or None,
                aihubmix_model=self._configured_text(
                    configured, _PROVIDERS["aihubmix"]["model_name"]
                ),
                aihubmix_custom_model_id=self._configured_text(
                    configured, _PROVIDERS["aihubmix"]["custom_model_name"]
                ),
                aihubmix_base_url=aihubmix_base_url,
                openrouter_model=self._configured_text(
                    configured, _PROVIDERS["openrouter"]["model_name"]
                ),
                openrouter_custom_model_id=self._configured_text(
                    configured, _PROVIDERS["openrouter"]["custom_model_name"]
                ),
                openrouter_base_url=openrouter_base_url,
            )
        except ValidationError:
            return self._settings_from_config(dict(_DEFAULTS), corrupt=True)

    def _validate_model(
        self, provider_id: str, model_choice: str, custom_model_id: str
    ) -> None:
        normalized = self._require_provider(provider_id)
        choice = str(model_choice or "").strip()
        allowed_models = [str(_PROVIDERS[normalized]["default_model"]), "custom"]
        if choice not in allowed_models:
            raise ThumbnailPromptError(
                "PROVIDER_MODEL_UNSUPPORTED",
                f"unsupported catalog model choice for {normalized}",
            )
        if choice == "custom" and not str(custom_model_id or "").strip():
            raise ThumbnailPromptError(
                "PROVIDER_CUSTOM_MODEL_REQUIRED",
                f"custom model id is required for {normalized}",
            )

    def _require_provider(self, provider_id: str) -> str:
        normalized = str(provider_id or "").strip()
        if normalized not in _PROVIDERS:
            raise ThumbnailPromptError(
                "PROVIDER_UNSUPPORTED",
                f"unsupported thumbnail prompt provider: {normalized or '<blank>'}",
            )
        return normalized

    @staticmethod
    def _validate_base_url(provider_id: str, value: str) -> str:
        normalized = str(value or "").strip()
        try:
            parsed = urlsplit(normalized)
            hostname = parsed.hostname
            parsed.port
        except ValueError as exc:
            raise ThumbnailPromptError(
                "PROVIDER_BASE_URL_INVALID",
                f"invalid base URL for {provider_id}",
            ) from exc
        if (
            not normalized
            or len(normalized) > 2048
            or any(
                character == "\\" or unicodedata.category(character) == "Cc"
                for character in normalized
            )
            or any(character.isspace() for character in normalized)
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            raise ThumbnailPromptError(
                "PROVIDER_BASE_URL_INVALID",
                f"invalid base URL for {provider_id}",
            )
        return normalized.rstrip("/")

    def _readable_base_url(
        self, provider_id: str, configured: dict[str, object]
    ) -> tuple[str, bool]:
        metadata = _PROVIDERS[provider_id]
        value = self._configured_text(configured, metadata["base_url_name"])
        try:
            return self._validate_base_url(provider_id, value), True
        except ThumbnailPromptError:
            return "", False

    @staticmethod
    def _configured_text(configured: dict[str, object], key: object) -> str:
        return str(configured.get(str(key), "") or "").strip()

    @staticmethod
    def _require_usable(loaded: _LoadedSettings) -> None:
        if loaded.status == "unsafe":
            raise ThumbnailPromptSettingsService._unsafe_settings_error()
        if loaded.status == "corrupt":
            raise ThumbnailPromptError(
                "THUMBNAIL_PROMPT_SETTINGS_CORRUPT", _CORRUPT_SETTINGS_MESSAGE
            )

    @staticmethod
    def _unsafe_settings_error() -> ThumbnailPromptError:
        return ThumbnailPromptError(
            "THUMBNAIL_PROMPT_SETTINGS_UNSAFE",
            "Thumbnail Prompt settings storage is unsafe.",
        )
