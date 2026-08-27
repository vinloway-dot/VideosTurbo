import gzip
import zlib

import pytest

from app.services.cloud_agent.research.errors import ResearchError
from app.services.cloud_agent.research.network import PinnedPublicHTTPClient


class _FakeSocket:
    def __init__(self, response_bytes: bytes):
        self._response_bytes = response_bytes
        self.requests: list[bytes] = []
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.requests.append(data)

    def makefile(self, mode: str):  # noqa: ARG002
        from io import BytesIO

        return BytesIO(self._response_bytes)

    def close(self) -> None:
        self.closed = True


class _FakeSSLContext:
    def __init__(self, connect_calls: list[dict[str, object]]):
        self._connect_calls = connect_calls

    def wrap_socket(self, sock, *, server_hostname: str):
        self._connect_calls[-1]["server_hostname"] = server_hostname
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
        self.connect_calls: list[dict[str, object]] = []

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

    def getaddrinfo(self, host: str, port: int, *, type: int):  # noqa: A002
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
        return _FakeSocket(payloads.pop(0))


@pytest.fixture
def fake_network():
    return _FakeNetwork()


@pytest.fixture
def client(fake_network):
    return PinnedPublicHTTPClient(
        resolver=fake_network.getaddrinfo,
        connector=fake_network.create_connection,
        ssl_context_factory=lambda: _FakeSSLContext(fake_network.connect_calls),
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
