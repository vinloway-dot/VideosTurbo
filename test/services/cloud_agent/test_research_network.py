import gzip
from itertools import chain, repeat
import socket
import zlib

import pytest

from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.network import PinnedPublicHTTPClient


class _FakeSocket:
    def __init__(self, response_bytes: bytes, *, response_file=None):
        self._response_bytes = response_bytes
        self._response_file = response_file
        self.requests: list[bytes] = []
        self.timeout = None
        self.timeout_history: list[float] = []
        self.closed = False

    def settimeout(self, value):
        self.timeout = value
        self.timeout_history.append(value)

    def sendall(self, data: bytes) -> None:
        self.requests.append(data)

    def makefile(self, mode: str):  # noqa: ARG002
        from io import BytesIO

        if self._response_file is not None:
            return self._response_file
        return BytesIO(self._response_bytes)

    def close(self) -> None:
        self.closed = True


class _FakeSSLContext:
    def __init__(self, connect_calls: list[dict[str, object]]):
        self._connect_calls = connect_calls
        self.wrap_calls: list[dict[str, object]] = []

    def wrap_socket(self, sock, *, server_hostname: str):
        self._connect_calls[-1]["server_hostname"] = server_hostname
        self.wrap_calls.append(
            {
                "server_hostname": server_hostname,
                "socket_timeout": sock.timeout,
            }
        )
        return sock


def _http_response(
    status: int,
    *,
    reason: str = "OK",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    merged_headers = {"Content-Length": str(len(body)), "Connection": "close"}
    if headers:
        merged_headers.update(headers)
    header_blob = "".join(f"{name}: {value}\r\n" for name, value in merged_headers.items())
    return (
        f"HTTP/1.1 {status} {reason}\r\n{header_blob}\r\n".encode("iso-8859-1") + body
    )


class _FakeNetwork:
    def __init__(self):
        self.answers: dict[tuple[str, int], list[str]] = {}
        self.routes: dict[tuple[str, int], list[bytes]] = {}
        self.response_files: dict[tuple[str, int], object] = {}
        self.connect_calls: list[dict[str, object]] = []
        self.sockets: list[_FakeSocket] = []
        self.resolve_calls: list[dict[str, object]] = []

    def resolve(self, hostname: str, addresses: list[str], *, port: int = 443) -> None:
        self.answers[(hostname, port)] = list(addresses)

    def respond(
        self, hostname: str, response_bytes: bytes, *, port: int = 443, append: bool = False
    ) -> None:
        key = (hostname, port)
        if append and key in self.routes:
            self.routes[key].append(response_bytes)
            return
        self.routes[key] = [response_bytes]

    def respond_with_file(self, hostname: str, response_file, *, port: int = 443) -> None:
        self.response_files[(hostname, port)] = response_file

    def getaddrinfo(self, host: str, port: int, *, type: int):  # noqa: A002
        self.resolve_calls.append({"host": host, "port": port, "type": type})
        addresses = self.answers.get((host, port), [])
        return [
            (0, 0, type, "", (address, port))
            for address in addresses
        ]

    def create_connection(self, address: tuple[str, int], timeout: float):
        ip, port = address
        self.connect_calls.append({"ip": ip, "port": port, "timeout": timeout})
        hostname = next(
            (
                host
                for (host, expected_port), values in self.answers.items()
                if expected_port == port and ip in values
            ),
            "",
        )
        payloads = self.routes.get((hostname, port), [])
        if not payloads:
            raise AssertionError(f"no fake response configured for {hostname}:{port}")
        sock = _FakeSocket(
            payloads.pop(0),
            response_file=self.response_files.get((hostname, port)),
        )
        self.sockets.append(sock)
        return sock


class _TrackingResponseFile:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.closed = False

    def readline(self, _limit=-1):
        return self._chunks.pop(0)

    def read(self, size=-1):
        if size == -1:
            if not self._chunks:
                return b""
            data = self._chunks[0]
            self._chunks[0] = b""
            return data
        if not self._chunks:
            return b""
        data = self._chunks[0][:size]
        self._chunks[0] = self._chunks[0][size:]
        if not self._chunks[0]:
            self._chunks.pop(0)
        return data

    def close(self):
        self.closed = True


@pytest.fixture
def fake_network():
    return _FakeNetwork()


@pytest.fixture
def client(fake_network):
    ssl_context = _FakeSSLContext(fake_network.connect_calls)
    return PinnedPublicHTTPClient(
        resolver=fake_network.getaddrinfo,
        connector=fake_network.create_connection,
        ssl_context_factory=lambda: ssl_context,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "http://[::1]/",
        "http://169.254.169.254/",
        "http://user:pass@example.com/",
        "file:///etc/passwd",
        "https://example.com:8080/",
    ],
)
def test_runtime_rejects_non_public_or_unsupported_target(client, url):
    with pytest.raises(ResearchError) as excinfo:
        client.get(url)
    assert excinfo.value.code in {"URL_TARGET_NOT_PUBLIC", "URL_INVALID"}


def test_redirect_is_revalidated_before_connection(client, fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            302,
            reason="Found",
            headers={"Location": "http://127.0.0.1/private"},
        ),
    )

    with pytest.raises(ResearchError) as excinfo:
        client.get("https://public.example/a")

    assert excinfo.value.code == "URL_REDIRECT_REJECTED"
    assert fake_network.connect_calls == [
        {
            "ip": "93.184.216.34",
            "port": 443,
            "timeout": client.CONNECT_TIMEOUT_SECONDS,
            "server_hostname": "public.example",
        }
    ]


def test_https_connection_is_pinned_to_validated_address(client, fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body>Article</body></html>",
        ),
    )

    response = client.get("https://public.example/article")

    assert response.body == b"<html><body>Article</body></html>"
    assert fake_network.connect_calls == [
        {
            "ip": "93.184.216.34",
            "port": 443,
            "timeout": client.CONNECT_TIMEOUT_SECONDS,
            "server_hostname": "public.example",
        }
    ]


def test_mixed_public_private_dns_answers_fail_closed(client, fake_network):
    fake_network.resolve("rebinding.example", ["93.184.216.34", "127.0.0.1"])

    with pytest.raises(ResearchError) as excinfo:
        client.get("https://rebinding.example/")

    assert excinfo.value.code == "URL_TARGET_NOT_PUBLIC"


def test_decompressed_body_over_ten_mib_is_rejected(client, fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    large_body = b"A" * ((10 * 1024 * 1024) + 1)
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            body=gzip.compress(large_body),
        ),
    )

    with pytest.raises(ResearchError) as excinfo:
        client.get("https://public.example/large")

    assert excinfo.value.code == "URL_CONTENT_TOO_LARGE"


def test_preflight_urls_deduplicates_canonical_public_targets(client, fake_network):
    fake_network.resolve("example.com", ["93.184.216.34"])
    fake_network.resolve("www.example.com", ["93.184.216.34"])

    urls = client.require_one_to_three_public_urls(
        [
            "https://EXAMPLE.com/article#fragment",
            "https://example.com/article",
            "https://www.example.com/other",
        ]
    )

    assert urls == (
        "https://example.com/article",
        "https://www.example.com/other",
    )


def test_preflight_rejects_more_than_three_urls(client):
    with pytest.raises(ResearchError) as excinfo:
        client.require_one_to_three_public_urls(
            [
                "https://one.example",
                "https://two.example",
                "https://three.example",
                "https://four.example",
            ]
        )

    assert excinfo.value.code == "URL_INVALID"


def test_http_client_rejects_unsupported_content_encoding(client, fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "br",
            },
            body=zlib.compress(b"ignored"),
        ),
    )

    with pytest.raises(ResearchError) as excinfo:
        client.get("https://public.example/article")

    assert excinfo.value.code == "URL_CONTENT_UNSUPPORTED"


def test_multicast_targets_fail_closed_for_literal_and_dns_answers(client, fake_network):
    with pytest.raises(ResearchError) as excinfo:
        client.get("http://224.0.0.1/")

    assert excinfo.value.code == "URL_TARGET_NOT_PUBLIC"

    fake_network.resolve("multicast.example", ["93.184.216.34", "224.0.0.1"])
    with pytest.raises(ResearchError) as excinfo:
        client.get("https://multicast.example/article")

    assert excinfo.value.code == "URL_TARGET_NOT_PUBLIC"


def test_get_resolves_hostname_once_immediately_before_pinned_connection(client, fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body>Article</body></html>",
        ),
    )

    client.get("https://public.example/article")

    assert fake_network.resolve_calls == [
        {"host": "public.example", "port": 443, "type": socket.SOCK_STREAM}
    ]


def test_get_refreshes_socket_timeout_before_each_blocking_read(fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"body",
        ),
    )
    fake_network.respond_with_file(
        "public.example",
        _TrackingResponseFile(
            [
                b"HTTP/1.1 200 OK\r\n",
                b"Content-Type: text/html; charset=utf-8\r\n",
                b"Content-Length: 4\r\n",
                b"Connection: close\r\n",
                b"\r\n",
                b"body",
                b"",
            ]
        ),
    )
    monotonic_values = chain(
        [100.0, 115.0, 118.0, 121.0, 125.0, 126.0, 127.0, 127.5, 128.0],
        repeat(128.0),
    )
    client = PinnedPublicHTTPClient(
        resolver=fake_network.getaddrinfo,
        connector=fake_network.create_connection,
        ssl_context_factory=lambda: _FakeSSLContext(fake_network.connect_calls),
        monotonic=lambda: next(monotonic_values),
    )

    response = client.get("https://public.example/article")

    assert response.body == b"body"
    timeout_history = fake_network.sockets[-1].timeout_history
    assert len(timeout_history) >= 6
    assert timeout_history[:3] == [5.0, 4.0, 3.0]
    assert timeout_history[-1] < timeout_history[0]
    assert timeout_history == sorted(timeout_history, reverse=True)


def test_connect_and_tls_use_remaining_total_deadline(fake_network):
    ssl_context = _FakeSSLContext(fake_network.connect_calls)
    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"ok",
        ),
    )
    monotonic_values = chain(
        [100.0, 128.0, 128.5, 129.0, 129.2, 129.4, 129.5, 129.6],
        repeat(129.6),
    )
    client = PinnedPublicHTTPClient(
        resolver=fake_network.getaddrinfo,
        connector=fake_network.create_connection,
        ssl_context_factory=lambda: ssl_context,
        monotonic=lambda: next(monotonic_values),
    )

    response = client.get("https://public.example/article")

    assert response.body == b"ok"
    assert fake_network.connect_calls == [
        {
            "ip": "93.184.216.34",
            "port": 443,
            "timeout": 1.0,
            "server_hostname": "public.example",
        }
    ]
    assert ssl_context.wrap_calls == [
        {
            "server_hostname": "public.example",
            "socket_timeout": pytest.approx(0.8),
        }
    ]


def test_connect_rejects_when_total_deadline_is_already_exhausted(fake_network):
    fake_network.resolve("public.example", ["93.184.216.34"])
    monotonic_values = iter([100.0, 130.1])
    client = PinnedPublicHTTPClient(
        resolver=fake_network.getaddrinfo,
        connector=fake_network.create_connection,
        ssl_context_factory=lambda: _FakeSSLContext(fake_network.connect_calls),
        monotonic=lambda: next(monotonic_values),
    )

    with pytest.raises(ResearchError) as excinfo:
        client.get("https://public.example/article")

    assert excinfo.value.code == "PROVIDER_TIMEOUT"
    assert fake_network.connect_calls == []


def test_decoder_caps_incremental_output_before_body_extension(client, fake_network, monkeypatch):
    class _FakeDecoder:
        def __init__(self):
            self.calls: list[int | None] = []
            self.unconsumed_tail = b"x"

        def decompress(self, chunk: bytes, max_length: int | None = None) -> bytes:
            self.calls.append(max_length)
            if max_length is None:
                raise AssertionError("decoder must receive an incremental max_length cap")
            if len(self.calls) == 1:
                return b"A" * max_length
            self.unconsumed_tail = b""
            return b"B"

        def flush(self) -> bytes:
            return b""

    fake_network.resolve("public.example", ["93.184.216.34"])
    fake_network.respond(
        "public.example",
        _http_response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            body=b"compressed",
        ),
    )
    decoder = _FakeDecoder()
    monkeypatch.setattr(client, "_build_decoder", lambda encoding: decoder)

    with pytest.raises(ResearchError) as excinfo:
        client.get("https://public.example/article")

    assert excinfo.value.code == "URL_CONTENT_TOO_LARGE"
    assert decoder.calls[0] == client.MAX_BODY_BYTES
