import codecs
import re
from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from typing import Callable

from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.network import (
    DownloadedResource,
    PinnedPublicHTTPClient,
)


_BLOCK_TAGS = (
    "article",
    "aside",
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "section",
    "td",
)
_HTML_MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_COOKIE_MARKERS = ("cookie", "consent", "gdpr", "privacy")
_CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "turnstile",
    "bot challenge",
    "cf-chl",
)
_PAYWALL_MARKERS = (
    "subscribe to continue",
    "sign in to continue",
    "log in to continue",
    "members only",
    "premium content",
    "subscription required",
)
_CONTENT_CHARSET_RE = re.compile(
    r"(?:^|;)\s*charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
_CHARSET_ALIASES = {
    "ascii": "cp1252",
    "chinese": "gbk",
    "csiso58gb231280": "gbk",
    "gb_2312-80": "gbk",
    "iso-ir-58": "gbk",
    "iso-8859-1": "cp1252",
    "latin-1": "cp1252",
    "latin1": "cp1252",
    "tis-620": "cp874",
    "us-ascii": "cp1252",
    "x-gbk": "gbk",
    "windows-874": "cp874",
    "windows874": "cp874",
}
_PYTHON_CODEC_ALIASES = {
    "gb2312": "gbk",
    "iso8859-1": "cp1252",
    "utf-16": "utf-16-le",
}
_HTML_TEXT_CODECS = frozenset(
    {
        "big5",
        "big5hkscs",
        "cp866",
        "cp874",
        "cp932",
        "cp949",
        "cp1250",
        "cp1251",
        "cp1252",
        "cp1253",
        "cp1254",
        "cp1255",
        "cp1256",
        "cp1257",
        "cp1258",
        "euc_jp",
        "euc_kr",
        "gb18030",
        "gbk",
        "iso2022_jp",
        "iso8859-2",
        "iso8859-3",
        "iso8859-4",
        "iso8859-5",
        "iso8859-6",
        "iso8859-7",
        "iso8859-8",
        "iso8859-9",
        "iso8859-10",
        "iso8859-11",
        "iso8859-13",
        "iso8859-14",
        "iso8859-15",
        "iso8859-16",
        "koi8-r",
        "koi8-u",
        "mac-roman",
        "shift_jis",
        "utf-16-be",
        "utf-16-le",
        "utf-8",
    }
)
_HIDDEN_CLASS_TOKENS = frozenset(
    {
        "hidden",
        "sr-only",
        "screen-reader",
        "screenreader",
        "visually-hidden",
        "d-none",
        "invisible",
    }
)


class _MetaCharsetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.charset = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._capture_charset(tag, attrs)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._capture_charset(tag, attrs)

    def _capture_charset(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.charset or tag.casefold() != "meta":
            return
        attributes = {
            str(name or "").casefold(): str(value or "") for name, value in attrs
        }
        direct = attributes.get("charset", "").strip()
        if direct:
            self.charset = direct
            return
        if attributes.get("http-equiv", "").strip().casefold() != "content-type":
            return
        match = _CONTENT_CHARSET_RE.search(attributes.get("content", ""))
        if match:
            self.charset = match.group(1)


@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    url: str
    title: str
    content: str
    content_hash: str
    mime_type: str
    final_url: str = ""


@dataclass(frozen=True)
class EvidenceBlock:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidencePacket:
    sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    blocks: tuple[EvidenceBlock, ...] = field(default_factory=tuple)


class ResearchToolRuntime:
    MAX_PDF_PAGES = 30
    MAX_TITLE_LENGTH = 500

    def __init__(
        self,
        *,
        http_client: PinnedPublicHTTPClient | None = None,
        pdf_reader_factory: Callable[..., PdfReader] | None = None,
    ) -> None:
        self.http_client = http_client or PinnedPublicHTTPClient()
        self._pdf_reader_factory = pdf_reader_factory or self._default_pdf_reader_factory

    def preflight_urls(self, raw_urls: list[str]) -> tuple[str, ...]:
        return self.http_client.require_one_to_three_public_urls(raw_urls)

    def execute(self, tool_name: str, supplied_url: str) -> ResearchSource:
        if tool_name not in {"fetch_url", "read_pdf"}:
            raise ResearchError("RESEARCH_RESPONSE_INVALID", "unsupported tool")
        response = self.http_client.get(supplied_url)
        if tool_name == "fetch_url":
            return self._extract_html(response)
        return self._extract_pdf(response)

    def aggregate(self, sources: list[ResearchSource]) -> EvidencePacket:
        blocks_by_text: dict[str, list[str]] = {}
        ordered_blocks: list[str] = []
        for source in sources:
            for block in self._split_blocks(source.content):
                if block not in blocks_by_text:
                    blocks_by_text[block] = [source.source_id]
                    ordered_blocks.append(block)
                    continue
                if source.source_id not in blocks_by_text[block]:
                    blocks_by_text[block].append(source.source_id)
        return EvidencePacket(
            sources=tuple(sources),
            blocks=tuple(
                EvidenceBlock(text=block, source_ids=tuple(blocks_by_text[block]))
                for block in ordered_blocks
            ),
        )

    def _extract_html(self, response: DownloadedResource) -> ResearchSource:
        content_type = response.headers.get("content-type", "").lower()
        mime_type = content_type.split(";", 1)[0].strip()
        if mime_type not in _HTML_MIME_TYPES:
            raise ResearchError("URL_CONTENT_UNSUPPORTED", "HTML content is required")

        try:
            html = response.body.decode(
                self._charset_from_content_type(content_type, response.body),
                "replace",
            )
        except (LookupError, UnicodeError) as exc:
            raise ResearchError(
                "URL_CONTENT_UNSUPPORTED",
                "HTML declared an unsupported charset",
            ) from exc
        soup = BeautifulSoup(html, "html.parser")
        if soup.find("input", attrs={"type": re.compile(r"^password$", re.I)}):
            raise ResearchError("URL_CONTENT_UNSUPPORTED", "password-protected content is unsupported")
        if self._contains_marker(soup, _CAPTCHA_MARKERS):
            raise ResearchError("URL_CONTENT_UNSUPPORTED", "challenge content is unsupported")

        for tag in soup.find_all(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        self._remove_hidden_and_cookie_content(soup)

        text = self._extract_visible_text(soup)
        if not text:
            raise ResearchError("URL_CONTENT_UNSUPPORTED", "page does not expose readable text")
        if self._looks_like_paywall(text):
            raise ResearchError("URL_CONTENT_UNSUPPORTED", "paywall-only content is unsupported")

        title = self._normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        return ResearchSource(
            source_id=self._source_id(response.final_url, text),
            url=response.url,
            final_url=response.final_url,
            title=title[: self.MAX_TITLE_LENGTH],
            content=text,
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
            mime_type=mime_type,
        )

    def _extract_pdf(self, response: DownloadedResource) -> ResearchSource:
        content_type = response.headers.get("content-type", "").lower()
        if content_type.split(";", 1)[0].strip() != "application/pdf":
            raise ResearchError("PDF_INVALID", "PDF MIME type is required")
        if not response.body.startswith(b"%PDF-"):
            raise ResearchError("PDF_INVALID", "PDF signature is required")
        try:
            try:
                reader = self._pdf_reader_factory(
                    BytesIO(response.body), current_url=response.final_url
                )
            except TypeError:
                reader = self._pdf_reader_factory(BytesIO(response.body))
        except Exception as exc:
            raise ResearchError("PDF_INVALID", str(exc)) from exc
        try:
            is_encrypted = bool(getattr(reader, "is_encrypted", False))
            pages = reader.pages
            page_count = len(pages)
        except Exception as exc:
            raise ResearchError("PDF_INVALID", "PDF page tree is malformed") from exc
        if is_encrypted:
            raise ResearchError("PDF_INVALID", "encrypted PDFs are unsupported")
        if page_count > self.MAX_PDF_PAGES:
            raise ResearchError("PDF_TOO_LARGE", "PDF page count exceeded limit")

        try:
            page_texts = [
                self._normalize_text(page.extract_text() or "") for page in pages
            ]
        except Exception as exc:
            raise ResearchError("PDF_INVALID", "PDF text extraction failed") from exc
        text = "\n\n".join(segment for segment in page_texts if segment)
        if not text:
            raise ResearchError("PDF_TEXT_UNAVAILABLE", "PDF does not contain extractable text")

        try:
            metadata = getattr(reader, "metadata", {}) or {}
            raw_title = metadata.get("/Title") if hasattr(metadata, "get") else ""
        except Exception as exc:
            raise ResearchError("PDF_INVALID", "PDF metadata is malformed") from exc
        title = self._normalize_text(raw_title)
        return ResearchSource(
            source_id=self._source_id(response.final_url, text),
            url=response.url,
            final_url=response.final_url,
            title=title[: self.MAX_TITLE_LENGTH],
            content=text,
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
            mime_type="application/pdf",
        )

    def _default_pdf_reader_factory(self, stream, *, current_url: str) -> PdfReader:  # noqa: ARG002
        return PdfReader(stream, strict=True)

    def _charset_from_content_type(
        self,
        content_type: str,
        body: bytes = b"",
    ) -> str:
        for segment in content_type.split(";")[1:]:
            name, separator, value = segment.partition("=")
            if separator and name.strip().lower() == "charset":
                return self._normalize_charset(value) or "utf-8"
        parser = _MetaCharsetParser()
        parser.feed(body[:4096].decode("iso-8859-1"))
        if parser.charset:
            return self._normalize_charset(parser.charset)
        return "utf-8"

    def _normalize_charset(self, value: str) -> str:
        label = str(value or "").strip().strip("'\"").lower()
        label = _CHARSET_ALIASES.get(label, label)
        codec = codecs.lookup(label).name
        codec = _PYTHON_CODEC_ALIASES.get(codec, codec)
        if codec not in _HTML_TEXT_CODECS:
            raise LookupError(f"charset is not supported for HTML: {value}")
        return codec

    def _remove_hidden_and_cookie_content(self, soup: BeautifulSoup) -> None:
        for tag in list(soup.find_all(True)):
            if getattr(tag, "attrs", None) is None:
                continue
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            style = self._normalize_css(tag.get("style", ""))
            marker_text = " ".join([classes, tag_id]).lower()
            class_tokens = {
                token for token in re.split(r"[^a-z0-9-]+", marker_text) if token
            }
            if tag.has_attr("hidden") or tag.get("aria-hidden") == "true":
                tag.decompose()
                continue
            if self._style_hides_content(style):
                tag.decompose()
                continue
            if _HIDDEN_CLASS_TOKENS & class_tokens:
                tag.decompose()
                continue
            if any(marker in marker_text for marker in _COOKIE_MARKERS):
                tag.decompose()

    def _extract_visible_text(self, soup: BeautifulSoup) -> str:
        root = soup.find("main") or soup.body or soup
        blocks: list[str] = []
        for tag in root.find_all(_BLOCK_TAGS):
            if tag.find_parent(_BLOCK_TAGS):
                continue
            text = self._normalize_text(tag.get_text(" ", strip=True))
            if text:
                blocks.append(text)
        if not blocks:
            fallback = self._normalize_text(root.get_text(" ", strip=True))
            return fallback
        return "\n\n".join(blocks)

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _looks_like_paywall(self, text: str) -> bool:
        normalized = text.lower()
        return any(marker in normalized for marker in _PAYWALL_MARKERS)

    def _contains_marker(self, soup: BeautifulSoup, markers: tuple[str, ...]) -> bool:
        haystacks = [soup.get_text(" ", strip=True).lower()]
        for tag in soup.find_all(True):
            values = [
                tag.get("id", ""),
                " ".join(tag.get("class", [])),
                tag.get("src", ""),
                tag.get("title", ""),
            ]
            haystacks.extend(str(value).lower() for value in values if value)
        return any(marker in haystack for haystack in haystacks for marker in markers)

    def _split_blocks(self, content: str) -> tuple[str, ...]:
        parts = [self._normalize_text(part) for part in re.split(r"\n\s*\n", content)]
        return tuple(part for part in parts if part)

    def _source_id(self, url: str, content: str) -> str:
        digest = sha256(f"{url}\n{content}".encode("utf-8")).hexdigest()
        return f"source-{digest[:16]}"

    def _normalize_css(self, value: str) -> str:
        return re.sub(r"\s+", "", str(value or "").lower())

    def _style_hides_content(self, normalized_style: str) -> bool:
        return (
            "display:none" in normalized_style
            or "visibility:hidden" in normalized_style
            or re.search(
                r"(?:^|;)opacity:(?:0(?:\.0+)?|\.0+)(?:!important)?(?:;|$)",
                normalized_style,
            )
            is not None
            or (
                ("position:absolute" in normalized_style or "position:fixed" in normalized_style)
                and re.search(
                    r"(?:left|right|top|bottom):-\d{3,}"
                    r"(?:px|em|rem|%|vw|vh|vmin|vmax)?(?:;|$)",
                    normalized_style,
                )
                is not None
            )
            or re.search(
                r"(?:^|;)text-indent:-\d{3,}(?:px|em|rem|%)?(?:;|$)",
                normalized_style,
            )
            is not None
        )
