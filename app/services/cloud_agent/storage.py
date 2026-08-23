from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.utils import utils


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


class CloudJobStorage:
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
            flow_files=tuple(flow_dir / f"clip_{index:02d}.mp4" for index in range(1, 7)),
            flow_archive_file=flow_downloads_dir / "product_clips.zip",
            final_file=final_dir / "final.mp4",
        )

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
        paths.master_prompt_file.write_text(master_prompt, encoding="utf-8")
        return paths

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
            path
            for path in paths.flow_files
            if path.exists() or path.is_symlink()
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
