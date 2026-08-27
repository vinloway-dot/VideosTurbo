import ipaddress
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from app.services.cloud_agent.research.errors import ResearchError


_ALLOWED_PORTS = {"http": 80, "https": 443}
_ALLOWED_ENCODINGS = frozenset({"", "identity", "gzip", "deflate"})
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_SECRET_QUERY_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "credential",
        "expires",
        "key",
        "password",
        "policy",
        "s3token",
        "secret",
        "session_token",
        "sig",
        "signature",
        "token",
        "x-amz-algorithm",
        "x-amz-credential",
        "x-amz-date",
        "x-amz-expires",
        "x-amz-security-token",
        "x-amz-signature",
        "x-goog-algorithm",
        "x-goog-credential",
        "x-goog-date",
        "x-goog-expires",
        "x-goog-signature",
        "x-goog-signedheaders",
    }
)


@dataclass(frozen=True)
class CanonicalURL:
    url: str
    scheme: str
    hostname: str
    port: int
    target: str
    host_header: str


@dataclass(frozen=True)
class DownloadedResource:
    url: str
    final_url: str
    status_code: int
    reason: str
    headers: dict[str, str]
    body: bytes


class PinnedPublicHTTPClient:
    CONNECT_TIMEOUT_SECONDS = 5
    READ_TIMEOUT_SECONDS = 20
    TOTAL_TIMEOUT_SECONDS = 30
    MAX_HEADER_BYTES = 64 * 1024
    MAX_BODY_BYTES = 10 * 1024 * 1024
    MAX_REDIRECTS = 5

    def __init__(
        self,
        *,
        resolver: Callable[..., list[tuple]] | None = None,
        connector: Callable[..., socket.socket] | None = None,
        ssl_context_factory: Callable[[], ssl.SSLContext] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._resolver = resolver or socket.getaddrinfo
        self._connector = connector or socket.create_connection
        self._ssl_context_factory = ssl_context_factory or ssl.create_default_context
        self._monotonic = monotonic or time.monotonic

    def require_one_to_three_public_urls(self, raw_urls: list[str]) -> tuple[str, ...]:
        if not raw_urls:
            raise ResearchError("URL_REQUIRED", "at least one URL is required")
        if len(raw_urls) > 3:
            raise ResearchError("URL_INVALID", "at most three URLs are allowed")

        canonical_urls: list[str] = []
        seen: set[str] = set()
        for raw_url in raw_urls:
            if not str(raw_url or "").strip():
                raise ResearchError("URL_INVALID", "blank URLs are not allowed")
            canonical = self._canonicalize_url(raw_url)
            self._validated_connection_addresses(canonical)
            if canonical.url not in seen:
                canonical_urls.append(canonical.url)
                seen.add(canonical.url)
        if not canonical_urls:
            raise ResearchError("URL_REQUIRED", "at least one URL is required")
        return tuple(canonical_urls)

    def get(self, url: str) -> DownloadedResource:
        current = self._canonicalize_url(url)
        deadline = self._monotonic() + self.TOTAL_TIMEOUT_SECONDS

        for redirect_count in range(self.MAX_REDIRECTS + 1):
            try:
                response = self._download_once(current, deadline)
            except ResearchError as exc:
                if redirect_count > 0 and exc.code in {
                    "URL_INVALID",
                    "URL_TARGET_NOT_PUBLIC",
                }:
                    raise ResearchError(
                        "URL_REDIRECT_REJECTED",
                        exc.detail or "redirect target is not allowed",
                    ) from exc
                raise
            if response.status_code in _REDIRECT_STATUS_CODES:
                if redirect_count >= self.MAX_REDIRECTS:
                    raise ResearchError(
                        "URL_REDIRECT_REJECTED", "redirect limit exceeded"
                    )
                location = response.headers.get("location", "")
                if not location:
                    raise ResearchError(
                        "URL_REDIRECT_REJECTED", "redirect missing location header"
                    )
                redirected = urljoin(current.url, location)
                try:
                    current = self._canonicalize_url(redirected)
                except ResearchError as exc:
                    raise ResearchError(
                        "URL_REDIRECT_REJECTED",
                        exc.detail or "redirect target is not allowed",
                    ) from exc
                continue
            if response.status_code in {401, 402, 403}:
                raise ResearchError(
                    "URL_CONTENT_UNSUPPORTED",
                    f"response status {response.status_code} requires interaction",
                )
            if 200 <= response.status_code < 300:
                return response
            raise ResearchError(
                "URL_FETCH_FAILED",
                f"unexpected response status {response.status_code}",
            )
        raise ResearchError("URL_FETCH_FAILED", "request did not complete")

    def _download_once(
        self, canonical: CanonicalURL, deadline: float
    ) -> DownloadedResource:
        self._check_deadline(deadline)
        addresses = self._validated_connection_addresses(canonical)
        self._check_deadline(deadline)
        ip_address = addresses[0]
        sock = None
        response_file = None
        try:
            sock = self._connect_pinned(
                ip_address,
                canonical.port,
                server_hostname=canonical.hostname,
                deadline=deadline,
            )
            self._apply_timeout(sock, deadline)
            request_bytes = (
                f"GET {canonical.target} HTTP/1.1\r\n"
                f"Host: {canonical.host_header}\r\n"
                "User-Agent: VideosTurboResearch/1.0\r\n"
                "Accept: text/html,application/pdf;q=0.9,*/*;q=0.1\r\n"
                "Accept-Encoding: identity, gzip, deflate\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")
            sock.sendall(request_bytes)

            response_file = sock.makefile("rb")
            status_code, reason, headers = self._read_status_and_headers(
                response_file, sock, deadline
            )
            body = self._read_body(response_file, sock, headers, deadline)
            return DownloadedResource(
                url=canonical.url,
                final_url=canonical.url,
                status_code=status_code,
                reason=reason,
                headers=headers,
                body=body,
            )
        except ResearchError:
            raise
        except (OSError, ssl.SSLError, ValueError, zlib.error) as exc:
            raise ResearchError("URL_FETCH_FAILED", str(exc)) from exc
        finally:
            if response_file is not None:
                response_file.close()
            if sock is not None:
                sock.close()

    def _canonicalize_url(self, value: str) -> CanonicalURL:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 2048:
            raise ResearchError("URL_INVALID", "URL is blank or too long")
        try:
            parsed = urlsplit(normalized)
        except ValueError as exc:
            raise ResearchError("URL_INVALID", str(exc)) from exc

        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_PORTS or not parsed.hostname:
            raise ResearchError("URL_INVALID", "unsupported URL scheme or hostname")
        if parsed.username is not None or parsed.password is not None:
            raise ResearchError("URL_INVALID", "credentials are not allowed")

        try:
            port = parsed.port or _ALLOWED_PORTS[scheme]
        except ValueError as exc:
            raise ResearchError("URL_INVALID", str(exc)) from exc
        if port != _ALLOWED_PORTS[scheme]:
            raise ResearchError("URL_INVALID", "only the standard HTTP(S) ports are allowed")

        for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
            if self._is_secret_query_key(key):
                raise ResearchError(
                    "URL_INVALID", "signed or secret-bearing query parameters are not allowed"
                )

        hostname = parsed.hostname.lower()
        path = parsed.path or "/"
        target = path if not parsed.query else f"{path}?{parsed.query}"
        host_header = hostname
        netloc = self._format_netloc(hostname, None)
        return CanonicalURL(
            url=urlunsplit((scheme, netloc, path, parsed.query, "")),
            scheme=scheme,
            hostname=hostname,
            port=port,
            target=target,
            host_header=host_header,
        )

    def _validated_connection_addresses(self, canonical: CanonicalURL) -> tuple[str, ...]:
        try:
            literal_ip = ipaddress.ip_address(canonical.hostname)
        except ValueError:
            return self._resolve_public_addresses(canonical.hostname, canonical.port)
        if not self._is_public_ip(literal_ip):
            raise ResearchError(
                "URL_TARGET_NOT_PUBLIC", "literal IP target is not globally routable"
            )
        return (canonical.hostname,)

    def _resolve_public_addresses(self, hostname: str, port: int) -> tuple[str, ...]:
        try:
            answers = self._resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ResearchError("URL_FETCH_FAILED", str(exc)) from exc
        addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses:
            raise ResearchError("URL_FETCH_FAILED", "DNS did not return any addresses")
        if any(not self._is_public_ip(ipaddress.ip_address(value)) for value in addresses):
            raise ResearchError(
                "URL_TARGET_NOT_PUBLIC", "DNS returned a prohibited address"
            )
        return addresses

    def _connect_pinned(
        self, ip: str, port: int, *, server_hostname: str, deadline: float
    ) -> socket.socket:
        connect_timeout = self._remaining_timeout(
            deadline, cap_seconds=self.CONNECT_TIMEOUT_SECONDS
        )
        try:
            raw_socket = self._connector((ip, port), timeout=connect_timeout)
        except TypeError:
            raw_socket = self._connector((ip, port), connect_timeout)
        if port == 443:
            self._apply_timeout(raw_socket, deadline)
            wrapped_socket = self._ssl_context_factory().wrap_socket(
                raw_socket, server_hostname=server_hostname
            )
            self._apply_timeout(wrapped_socket, deadline)
            return wrapped_socket
        return raw_socket

    def _read_status_and_headers(
        self, stream, sock: socket.socket, deadline: float
    ) -> tuple[int, str, dict[str, str]]:
        header_bytes = 0
        status_line = self._readline(stream, sock, deadline)
        header_bytes += len(status_line)
        if not status_line.startswith(b"HTTP/"):
            raise ResearchError("URL_FETCH_FAILED", "invalid HTTP status line")
        parts = status_line.decode("iso-8859-1").strip().split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ResearchError("URL_FETCH_FAILED", "invalid HTTP status code")
        status_code = int(parts[1])
        reason = parts[2] if len(parts) > 2 else ""

        headers: dict[str, str] = {}
        while True:
            line = self._readline(stream, sock, deadline)
            header_bytes += len(line)
            if header_bytes > self.MAX_HEADER_BYTES:
                raise ResearchError("URL_FETCH_FAILED", "response headers exceeded limit")
            if line in {b"\r\n", b"\n", b""}:
                break
            decoded = line.decode("iso-8859-1")
            if ":" not in decoded:
                raise ResearchError("URL_FETCH_FAILED", "invalid header line")
            name, value = decoded.split(":", 1)
            header_name = name.strip().lower()
            header_value = value.strip()
            if header_name in headers:
                headers[header_name] = f"{headers[header_name]}, {header_value}"
            else:
                headers[header_name] = header_value
        return status_code, reason, headers

    def _read_body(
        self, stream, sock: socket.socket, headers: dict[str, str], deadline: float
    ) -> bytes:
        encoding = headers.get("content-encoding", "").strip().lower()
        if encoding not in _ALLOWED_ENCODINGS:
            raise ResearchError(
                "URL_CONTENT_UNSUPPORTED",
                f"unsupported content encoding: {encoding}",
            )

        decoder = self._build_decoder(encoding)
        body = bytearray()
        if "chunked" in headers.get("transfer-encoding", "").lower():
            self._read_chunked_body(stream, sock, deadline, decoder, body)
        else:
            while True:
                chunk = self._read(stream, sock, 64 * 1024, deadline)
                if not chunk:
                    break
                self._append_decoded(body, decoder, chunk)
        if decoder is not None:
            self._append_decoded(body, None, decoder.flush())
            if len(body) > self.MAX_BODY_BYTES:
                raise ResearchError("URL_CONTENT_TOO_LARGE", "response body exceeded limit")
        return bytes(body)

    def _read_chunked_body(
        self, stream, sock: socket.socket, deadline: float, decoder, body: bytearray
    ) -> None:
        while True:
            size_line = self._readline(stream, sock, deadline).decode("iso-8859-1").strip()
            chunk_size = int(size_line.split(";", 1)[0], 16)
            if chunk_size == 0:
                while True:
                    trailer = self._readline(stream, sock, deadline)
                    if trailer in {b"\r\n", b"\n", b""}:
                        return
                return
            chunk = self._readexactly(stream, sock, chunk_size, deadline)
            self._append_decoded(body, decoder, chunk)
            ending = self._readexactly(stream, sock, 2, deadline)
            if ending != b"\r\n":
                raise ResearchError("URL_FETCH_FAILED", "invalid chunk terminator")

    def _build_decoder(self, encoding: str):
        if encoding in {"", "identity"}:
            return None
        if encoding == "gzip":
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        return zlib.decompressobj()

    def _append_decoded(self, body: bytearray, decoder, chunk: bytes) -> None:
        if decoder is None:
            body.extend(chunk)
            if len(body) > self.MAX_BODY_BYTES:
                raise ResearchError("URL_CONTENT_TOO_LARGE", "response body exceeded limit")
            return

        pending = chunk
        while pending:
            remaining = self.MAX_BODY_BYTES - len(body)
            if remaining <= 0:
                raise ResearchError("URL_CONTENT_TOO_LARGE", "response body exceeded limit")
            decoded = decoder.decompress(pending, remaining)
            if decoded:
                body.extend(decoded)
            pending = getattr(decoder, "unconsumed_tail", b"")
            if len(body) >= self.MAX_BODY_BYTES and (
                pending or getattr(decoder, "unused_data", b"")
            ):
                raise ResearchError("URL_CONTENT_TOO_LARGE", "response body exceeded limit")
            if not decoded and not pending:
                break

    def _readline(self, stream, sock: socket.socket, deadline: float) -> bytes:
        self._refresh_read_timeout(sock, deadline)
        line = stream.readline(self.MAX_HEADER_BYTES + 1)
        if len(line) > self.MAX_HEADER_BYTES:
            raise ResearchError("URL_FETCH_FAILED", "header line exceeded limit")
        return line

    def _readexactly(
        self, stream, sock: socket.socket, size: int, deadline: float
    ) -> bytes:
        self._refresh_read_timeout(sock, deadline)
        data = stream.read(size)
        if len(data) != size:
            raise ResearchError("URL_FETCH_FAILED", "unexpected end of response body")
        return data

    def _read(self, stream, sock: socket.socket, size: int, deadline: float) -> bytes:
        self._refresh_read_timeout(sock, deadline)
        return stream.read(size)

    def _apply_timeout(self, sock: socket.socket, deadline: float) -> None:
        sock.settimeout(
            self._remaining_timeout(deadline, cap_seconds=self.READ_TIMEOUT_SECONDS)
        )

    def _refresh_read_timeout(self, sock: socket.socket, deadline: float) -> None:
        self._apply_timeout(sock, deadline)

    def _check_deadline(self, deadline: float) -> None:
        if self._monotonic() > deadline:
            raise ResearchError("PROVIDER_TIMEOUT", "request deadline exceeded")

    def _format_netloc(self, hostname: str, port: int | None) -> str:
        if ":" in hostname and not hostname.startswith("["):
            rendered_host = f"[{hostname}]"
        else:
            rendered_host = hostname
        if port is None:
            return rendered_host
        return f"{rendered_host}:{port}"

    def _is_secret_query_key(self, key: str) -> bool:
        normalized = str(key or "").strip().lower()
        if not normalized:
            return False
        return (
            normalized in _SECRET_QUERY_KEYS
            or normalized.startswith("x-amz-")
            or normalized.startswith("x-goog-")
            or normalized.endswith("_token")
            or normalized.endswith("_signature")
            or normalized.endswith("_secret")
            or normalized.endswith("_key")
        )

    def _remaining_timeout(self, deadline: float, *, cap_seconds: float) -> float:
        remaining = min(cap_seconds, deadline - self._monotonic())
        if remaining <= 0:
            raise ResearchError("PROVIDER_TIMEOUT", "request deadline exceeded")
        return remaining

    def _is_public_ip(self, value: ipaddress._BaseAddress) -> bool:
        return value.is_global and not any(
            (
                value.is_multicast,
                value.is_private,
                value.is_loopback,
                value.is_link_local,
                value.is_reserved,
                value.is_unspecified,
                getattr(value, "is_site_local", False),
            )
        )
