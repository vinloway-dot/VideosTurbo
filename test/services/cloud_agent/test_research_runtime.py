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


def test_html_uses_meta_charset_when_header_omits_charset(runtime, fake_http):
    html = (
        '<html><head><meta charset="windows-874">'
        "<title>สมุนไพร คำฝอย</title></head>"
        "<body><main><p>ดอกคำฝอยใช้เป็นสมุนไพร</p></main></body></html>"
    )
    fake_http.preflight(
        _response(
            "https://public.example/thai-herb",
            content_type="text/html",
            body=html.encode("cp874"),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/thai-herb")

    assert source.title == "สมุนไพร คำฝอย"
    assert source.content == "ดอกคำฝอยใช้เป็นสมุนไพร"


def test_html_uses_legacy_http_equiv_meta_charset(runtime, fake_http):
    html = (
        '<html><head><meta http-equiv="Content-Type" '
        'content="text/html; charset=windows-874">'
        "<title>สรรพคุณสมุนไพร 200 ชนิด</title></head>"
        "<body><main><p>ดอกคำฝอยใช้เป็นสมุนไพร</p></main></body></html>"
    )
    fake_http.preflight(
        _response(
            "https://public.example/legacy-thai-herb",
            content_type="text/html",
            body=html.encode("cp874"),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/legacy-thai-herb")

    assert source.title == "สรรพคุณสมุนไพร 200 ชนิด"
    assert source.content == "ดอกคำฝอยใช้เป็นสมุนไพร"


def test_http_charset_takes_precedence_over_meta_charset(runtime, fake_http):
    html = (
        '<html><head><meta charset="windows-874">'
        "<title>สมุนไพร คำฝอย</title></head>"
        "<body><main><p>ดอกคำฝอยใช้เป็นสมุนไพร</p></main></body></html>"
    )
    fake_http.preflight(
        _response(
            "https://public.example/http-charset",
            content_type="text/html; charset=utf-8",
            body=html.encode("utf-8"),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/http-charset")

    assert source.title == "สมุนไพร คำฝอย"
    assert source.content == "ดอกคำฝอยใช้เป็นสมุนไพร"


@pytest.mark.parametrize(
    ("declared_charset", "encoding", "title"),
    [
        ("gb2312", "gbk", "中药资料"),
        ("utf-16", "utf-16-le", "Herbal facts"),
    ],
)
def test_html_supports_common_http_charset_aliases(
    runtime,
    fake_http,
    declared_charset,
    encoding,
    title,
):
    html = (
        f"<html><head><title>{title}</title></head>"
        "<body><main><p>Readable fact</p></main></body></html>"
    )
    fake_http.preflight(
        _response(
            "https://public.example/http-charset-alias",
            content_type=f"text/html; charset={declared_charset}",
            body=html.encode(encoding),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/http-charset-alias")

    assert source.title == title
    assert source.content == "Readable fact"


def test_meta_charset_ignores_comments_and_unrelated_attributes(runtime, fake_http):
    html = (
        '<html><head><!-- <meta charset="utf-8"> -->'
        '<meta data-charset="utf-8"><meta charset="windows-874">'
        "<title>สมุนไพร คำฝอย</title></head>"
        "<body><main><p>ดอกคำฝอยใช้เป็นสมุนไพร</p></main></body></html>"
    )
    fake_http.preflight(
        _response(
            "https://public.example/meta-lookalikes",
            content_type="text/html",
            body=html.encode("cp874"),
        )
    )

    source = runtime.execute("fetch_url", "https://public.example/meta-lookalikes")

    assert source.title == "สมุนไพร คำฝอย"
    assert source.content == "ดอกคำฝอยใช้เป็นสมุนไพร"


@pytest.mark.parametrize("charset", ["idna", "unicode_escape", "utf-7"])
def test_unsupported_meta_charset_maps_to_typed_source_error(
    runtime,
    fake_http,
    charset,
):
    fake_http.preflight(
        _response(
            "https://public.example/unsupported-meta-charset",
            content_type="text/html",
            body=(
                f'<html><head><meta charset="{charset}"></head>'
                "<body><main><p>Fact</p></main></body></html>"
            ).encode("ascii"),
        )
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("fetch_url", "https://public.example/unsupported-meta-charset")

    assert excinfo.value.code == "URL_CONTENT_UNSUPPORTED"


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


@pytest.mark.parametrize(
    "style",
    [
        "opacity: 0",
        "opacity:0!important",
        "opacity:.0",
        "position:absolute;left:-10000px",
        "position:absolute;left:-100vw",
        "position:fixed;top:-9999px",
    ],
)
def test_common_visually_hidden_offscreen_content_is_removed(runtime, fake_http, style):
    fake_http.preflight(
        _response(
            "https://public.example/hidden",
            content_type="text/html; charset=utf-8",
            body=(
                "<html><body><main><p>Visible fact.</p>"
                f"<p style='{style}'>Hidden prompt injection</p>"
                "<p>Final fact.</p></main></body></html>"
            ).encode(),
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


@pytest.mark.parametrize("content_type", ["text/html-extra", "application/xhtml+xml-extra"])
def test_html_requires_an_exact_supported_mime(runtime, fake_http, content_type):
    fake_http.preflight(
        _response(
            "https://public.example/article",
            content_type=content_type,
            body=b"<html><body><main>Fact</main></body></html>",
        )
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("fetch_url", "https://public.example/article")

    assert excinfo.value.code == "URL_CONTENT_UNSUPPORTED"


def test_pdf_requires_exact_mime(fake_http):
    url = "https://public.example/doc.pdf"
    fake_http.preflight(
        _response(url, content_type="application/pdf-extra", body=b"%PDF-1.7\n")
    )
    runtime = ResearchToolRuntime(
        http_client=fake_http,
        pdf_reader_factory=lambda *_args, **_kwargs: _FakePDFReader(False, 1, "Fact"),
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("read_pdf", url)

    assert excinfo.value.code == "PDF_INVALID"


def test_unknown_html_charset_maps_to_typed_source_error(runtime, fake_http):
    fake_http.preflight(
        _response(
            "https://public.example/article",
            content_type="text/html; charset=attacker-unknown-charset",
            body=b"<html><body><main>Fact</main></body></html>",
        )
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("fetch_url", "https://public.example/article")

    assert excinfo.value.code == "URL_CONTENT_UNSUPPORTED"


@pytest.mark.parametrize("failure_phase", ["pages", "extract", "metadata"])
def test_lazy_pdf_failures_map_to_pdf_invalid(fake_http, failure_phase):
    class FailingPages:
        def __len__(self):
            if failure_phase == "pages":
                raise ValueError("malformed page tree")
            return 1

        def __iter__(self):
            class Page:
                def extract_text(self):
                    if failure_phase == "extract":
                        raise ValueError("malformed content stream")
                    return "Fact"

            return iter([Page()])

    class Reader:
        is_encrypted = False
        pages = FailingPages()

        @property
        def metadata(self):
            if failure_phase == "metadata":
                raise ValueError("malformed metadata")
            return {}

    url = "https://public.example/doc.pdf"
    fake_http.preflight(_response(url, content_type="application/pdf", body=b"%PDF-1.7\n"))
    runtime = ResearchToolRuntime(
        http_client=fake_http,
        pdf_reader_factory=lambda *_args, **_kwargs: Reader(),
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("read_pdf", url)

    assert excinfo.value.code == "PDF_INVALID"


def test_pdf_factory_type_error_from_both_signatures_maps_to_pdf_invalid(fake_http):
    url = "https://public.example/doc.pdf"
    fake_http.preflight(
        _response(url, content_type="application/pdf", body=b"%PDF-1.7\n")
    )

    def invalid_factory(_stream, **kwargs):
        if "current_url" in kwargs:
            raise TypeError("current_url is unsupported")
        raise TypeError("PDF parser rejected the stream")

    runtime = ResearchToolRuntime(
        http_client=fake_http,
        pdf_reader_factory=invalid_factory,
    )

    with pytest.raises(ResearchError) as excinfo:
        runtime.execute("read_pdf", url)

    assert excinfo.value.code == "PDF_INVALID"
