from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from app.services.cloud_agent import download_transport


class CrashedDownload:
    url = "https://signed.example.test/artifact"

    def save_as(self, path):
        Path(path).write_bytes(b"partial")
        raise PlaywrightError(
            "Download.save_as: Target page, context or browser has been closed"
        )


def test_save_download_uses_signed_url_after_browser_save_crashes(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "artifact.zip"
    observed = []

    def direct_download(url, path, *, timeout_seconds):
        observed.append((url, timeout_seconds))
        Path(path).write_bytes(b"complete")

    monkeypatch.setattr(download_transport, "_download_url_to_path", direct_download)

    download_transport.save_download_with_url_fallback(
        CrashedDownload(),
        output,
        timeout_seconds=300.0,
    )

    assert output.read_bytes() == b"complete"
    assert observed == [("https://signed.example.test/artifact", 300.0)]


def test_save_download_does_not_use_network_when_browser_save_succeeds(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "artifact.mp4"

    class SuccessfulDownload:
        url = "https://signed.example.test/artifact"

        def save_as(self, path):
            Path(path).write_bytes(b"browser")

    monkeypatch.setattr(
        download_transport,
        "_download_url_to_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("signed URL fallback must not run")
        ),
    )

    download_transport.save_download_with_url_fallback(
        SuccessfulDownload(),
        output,
        timeout_seconds=30.0,
    )

    assert output.read_bytes() == b"browser"
