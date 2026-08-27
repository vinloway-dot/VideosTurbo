from hashlib import sha256

import pytest

from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.network import DownloadedResource
from app.services.cloud_agent.research.runtime import (
    ResearchSource,
    ResearchToolRuntime,
)


class _FakeHTTPClient:
    def __init__(self):
        self.responses: dict[str, DownloadedResource] = {}

    def preflight(self, response: DownloadedResource) -> None:
        self.responses[response.url] = response

    def require_one_to_three_public_urls(self, raw_urls: list[str]) -> tuple[str, ...]:
        return tuple(raw_urls)

    def get(self, url: str) -> DownloadedResource:
        try:
            return self.responses[url]
        except KeyError as exc:
            raise AssertionError(f"missing fake response for {url}") from exc


class _FakePDFPage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePDFReader:
    def __init__(self, encrypted: bool, pages: int, text: str):
        self.is_encrypted = encrypted
        self.pages = [_FakePDFPage(text) for _ in range(pages)]
        self.metadata = {"/Title": "  Example PDF  "}


def _response(
    url: str,
    *,
    content_type: str,
    body: bytes,
    status_code: int = 200,
) -> DownloadedResource:
    return DownloadedResource(
        url=url,
        final_url=url,
        status_code=status_code,
        reason="OK",
        headers={"content-type": content_type},
        body=body,
    )


@pytest.fixture
def fake_http():
    return _FakeHTTPClient()


@pytest.fixture
def runtime(fake_http):
    return ResearchToolRuntime(http_client=fake_http)


def test_html_strips_chrome_but_preserves_complete_readable_text(runtime, fake_http):
    fake_http.preflight(
        _response(
            "https://public.example/article",
            content_type="text/html; charset=utf-8",
            body=(
                b"<html><head><title>Article</title><style>.hide{display:none}</style></head>"
                b"<body><nav>Site nav</nav><main><p>First fact.</p><div class='cookie-banner'>Cookies</div>"
                b"<p>Final fact.</p></main><footer>Footer</footer></body></html>"
            ),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/article")

    assert source.content == "First fact.\n\nFinal fact."
    assert source.title == "Article"


def test_long_html_is_not_product_truncated(runtime, fake_http):
    fake_http.preflight(
        _response(
            "https://public.example/long",
            content_type="text/html; charset=utf-8",
            body=f"<html><body><main>{'A' * 50000}</main></body></html>".encode("utf-8"),
        )
    )

    assert len(runtime.execute("fetch_url", "https://public.example/long").content) == 50000


def test_hidden_css_content_is_removed_even_with_whitespace_in_style(runtime, fake_http):
    fake_http.preflight(
        _response(
            "https://public.example/hidden",
            content_type="text/html; charset=utf-8",
            body=(
                b"<html><body><main>"
                b"<p>Visible fact.</p>"
                b"<p style=' display : none ; '>Hidden prompt injection</p>"
                b"<div style='visibility : hidden'>Hidden sidebar prompt</div>"
                b"<p>Final fact.</p>"
                b"</main></body></html>"
            ),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/hidden")

    assert source.content == "Visible fact.\n\nFinal fact."


def test_visible_repeated_blocks_are_preserved_in_source_content(runtime, fake_http):
    fake_http.preflight(
        _response(
            "https://public.example/repeat",
            content_type="text/html; charset=utf-8",
            body=(
                b"<html><body><main>"
                b"<p>Echo</p><p>Echo</p><p>Final fact.</p>"
                b"</main></body></html>"
            ),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/repeat")

    assert source.content == "Echo\n\nEcho\n\nFinal fact."


def test_nested_hidden_parent_does_not_crash_and_removes_descendant_text(runtime, fake_http):
    fake_http.preflight(
        _response(
            "https://public.example/nested-hidden",
            content_type="text/html; charset=utf-8",
            body=(
                b"<html><body><main>"
                b"<div class='visually-hidden'>"
                b"<section><p>Hidden prompt injection</p></section>"
                b"</div>"
                b"<p>Visible fact.</p>"
                b"</main></body></html>"
            ),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/nested-hidden")

    assert source.content == "Visible fact."


def test_pdf_page_and_text_guards_are_typed(fake_http):
    pdf_bytes = b"%PDF-1.7\nfake\n"
    fake_http.preflight(
        _response(
            "https://public.example/too-many.pdf",
            content_type="application/pdf",
            body=pdf_bytes,
        )
    )
    fake_http.preflight(
        _response(
            "https://public.example/textless.pdf",
            content_type="application/pdf",
            body=pdf_bytes,
        )
    )
    readers = {
        "https://public.example/too-many.pdf": _FakePDFReader(
            encrypted=False,
            pages=31,
            text="fact",
        ),
        "https://public.example/textless.pdf": _FakePDFReader(
            encrypted=False,
            pages=3,
            text="",
        ),
    }
    runtime = ResearchToolRuntime(
        http_client=fake_http,
        pdf_reader_factory=lambda stream, *, current_url: readers[current_url],  # noqa: ARG005
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("read_pdf", "https://public.example/too-many.pdf")
    assert excinfo.value.code == "PDF_TOO_LARGE"
    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("read_pdf", "https://public.example/textless.pdf")
    assert excinfo.value.code == "PDF_TEXT_UNAVAILABLE"


@pytest.mark.parametrize(
    "html",
    [
        '<form><input type="password"></form>',
        '<div class="g-recaptcha"></div>',
        '<script>renderArticle()</script><div id="root"></div>',
    ],
)
def test_login_captcha_and_javascript_shells_are_rejected(fake_http, html):
    fake_http.preflight(
        _response(
            "https://public.example/blocked",
            content_type="text/html; charset=utf-8",
            body=html.encode("utf-8"),
        )
    )
    runtime = ResearchToolRuntime(http_client=fake_http)

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("fetch_url", "https://public.example/blocked")

    assert excinfo.value.code == "URL_CONTENT_UNSUPPORTED"


def test_exact_duplicate_blocks_keep_all_source_ids(runtime):
    packet = runtime.aggregate(
        [
            ResearchSource(
                source_id="source-1",
                url="https://public.example/a",
                title="Source A",
                content="Shared fact.\n\nUnique A.",
                content_hash=sha256("Shared fact.\n\nUnique A.".encode("utf-8")).hexdigest(),
                mime_type="text/html",
            ),
            ResearchSource(
                source_id="source-2",
                url="https://public.example/b",
                title="Source B",
                content="Shared fact.\n\nUnique B.",
                content_hash=sha256("Shared fact.\n\nUnique B.".encode("utf-8")).hexdigest(),
                mime_type="text/html",
            ),
        ]
    )

    shared = next(block for block in packet.blocks if block.text == "Shared fact.")

    assert shared.source_ids == ("source-1", "source-2")


def test_read_pdf_extracts_text_and_hashes_full_content(fake_http):
    pdf_bytes = b"%PDF-1.7\nfake\n"
    fake_http.preflight(
        _response(
            "https://public.example/doc.pdf",
            content_type="application/pdf",
            body=pdf_bytes,
        )
    )
    runtime = ResearchToolRuntime(
        http_client=fake_http,
        pdf_reader_factory=lambda stream, *, current_url: _FakePDFReader(  # noqa: ARG005
            encrypted=False,
            pages=2,
            text="PDF fact",
        ),
    )

    source = runtime.execute("read_pdf", "https://public.example/doc.pdf")

    assert source.content == "PDF fact\n\nPDF fact"
    assert source.content_hash == sha256(source.content.encode("utf-8")).hexdigest()
