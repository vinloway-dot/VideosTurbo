from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def _download_url_to_path(
    url: str,
    path: Path,
    *,
    timeout_seconds: float,
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("download URL must use HTTP or HTTPS")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout_seconds) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    if not path.is_file() or path.stat().st_size <= 0:
        raise OSError("direct download produced an empty artifact")


def save_download_with_url_fallback(
    download: Any,
    destination: Path,
    *,
    timeout_seconds: float,
) -> None:
    """Save through Playwright first, then recover via its signed URL if it crashes."""
    destination = Path(destination)
    signed_url = ""
    try:
        signed_url = str(download.url or "").strip()
    except Exception:
        # Some browser revisions do not expose a URL. The Playwright save path
        # remains usable and its original exception is preserved on failure.
        pass

    try:
        download.save_as(str(destination))
        return
    except Exception as browser_error:
        destination.unlink(missing_ok=True)
        if not signed_url:
            raise

        direct_temporary = destination.parent / f".{destination.name}.direct.tmp"
        direct_temporary.unlink(missing_ok=True)
        try:
            _download_url_to_path(
                signed_url,
                direct_temporary,
                timeout_seconds=timeout_seconds,
            )
            direct_temporary.replace(destination)
        except Exception as direct_error:
            direct_temporary.unlink(missing_ok=True)
            raise browser_error from direct_error

