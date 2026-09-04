Task 3 report
=============

Summary
-------
- Added a guarded public-web runtime at `app/services/cloud_agent/research/network.py` that canonicalizes URLs, rejects non-public or unsupported targets, pins HTTPS connections to validated addresses, revalidates redirects, and enforces header/body/encoding/time limits without delegating hostname resolution to a higher-level client.
- Added `app/services/cloud_agent/research/runtime.py` as the only HTML/PDF execution boundary for `fetch_url` and `read_pdf`, with readable-text extraction, password/CAPTCHA/JavaScript-shell rejection, PDF page/text guards, normalized title caps, full-content hashing, and exact evidence-block deduplication that retains all contributing source IDs.
- Added `beautifulsoup4==4.15.0` and `pypdf==6.16.2` to `pyproject.toml` and refreshed `uv.lock`.

Files changed
-------------
- `pyproject.toml`
- `uv.lock`
- `app/services/cloud_agent/research/network.py`
- `app/services/cloud_agent/research/runtime.py`
- `test/services/cloud_agent/test_research_network.py`
- `test/services/cloud_agent/test_research_runtime.py`

Implementation notes
--------------------
- `PinnedPublicHTTPClient` accepts only HTTP/HTTPS targets on ports 80/443, rejects credentials and signed query parameters, canonicalizes URLs, and validates public DNS/IP targets both during preflight and again immediately before each pinned connection to close the DNS-rebinding window.
- The HTTP boundary uses explicit socket + TLS handling with the original hostname in `Host` and `server_hostname`, disables auto-redirects, revalidates every redirect target, accepts only `identity`, `gzip`, and `deflate`, and aborts once decoded bytes exceed 10 MiB.
- HTML extraction removes scripts, styles, nav/footer chrome, cookie/hidden elements, and rejects login, CAPTCHA, paywall-only, or JavaScript-shell pages instead of attempting any browser/login/challenge bypass.
- PDF extraction requires both `application/pdf` MIME type and `%PDF-` signature, rejects encrypted/malformed files, rejects documents over 30 pages, extracts text through `PdfReader`, and raises `PDF_TEXT_UNAVAILABLE` for textless scans.
- Evidence content is preserved in full. Only normalized titles are capped at 500 characters. No product/content truncation or summarization was introduced.
- Existing `config.toml.backup-*` and `config.toml.save*` artifacts were preserved untouched. No Playwright, browser, Flow, Canva, or provider API calls were added or executed.

TDD evidence
------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q`
- Output:

```text
==================================== ERRORS ====================================
_____ ERROR collecting test/services/cloud_agent/test_research_network.py ______
ImportError while importing test module '/opt/VideosTurbo/test/services/cloud_agent/test_research_network.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/home/linuxuser/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
test/services/cloud_agent/test_research_network.py:8: in <module>
    from app.services.cloud_agent.research.network import PinnedPublicHTTPClient
E   ModuleNotFoundError: No module named 'app.services.cloud_agent.research.network'
_____ ERROR collecting test/services/cloud_agent/test_research_runtime.py ______
ImportError while importing test module '/opt/VideosTurbo/test/services/cloud_agent/test_research_runtime.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/home/linuxuser/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
test/services/cloud_agent/test_research_runtime.py:7: in <module>
    from app.services.cloud_agent.research.network import DownloadedResource
E   ModuleNotFoundError: No module named 'app.services.cloud_agent.research.network'
=========================== short test summary info ============================
ERROR test/services/cloud_agent/test_research_network.py
ERROR test/services/cloud_agent/test_research_runtime.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
2 errors in 0.51s
```

GREEN:
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q`
- Output:

```text
.....................                                                    [100%]
21 passed in 0.69s
```

Focused verification
--------------------
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q && uv lock --check && uv run ruff check app/services/cloud_agent/research/network.py app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py`
- Output:

```text
.....................                                                    [100%]
21 passed in 0.64s
Resolved 130 packages in 2ms
All checks passed!
```

Notes
-----
- No additional concerns at handoff.

Fix round 1
-----------

Reviewer findings addressed
---------------------------
- HIGH: ipaddress.is_global accepts multicast; runtime now rejects multicast plus other reserved/non-public literal and DNS answers fail-closed through one shared public-address classifier.
- HIGH: hidden HTML style matching now tolerates whitespace in `display: none` / `visibility: hidden`, strips common hidden-class markers, and keeps hidden prompt text out of extracted evidence.
- MEDIUM: `get()` no longer resolves hostnames twice before a successful fetch; hostname DNS resolution happens once per hop immediately before the pinned connection, while preflight remains a separate validation path.
- MEDIUM: the 30-second total deadline is now enforced during blocking reads by refreshing the socket timeout before every line/body read.
- MEDIUM: decompression output is now capped incrementally via bounded `decoder.decompress(..., max_length)` loops so oversized decoded bodies fail before an unchecked append can over-allocate evidence.
- MEDIUM: HTML extraction preserves repeated visible blocks in source content; exact deduplication still happens only in aggregation.

Fix TDD evidence
----------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q`
- Output:

```text
.............FFFF..FF......                                              [100%]
=================================== FAILURES ===================================
________ test_multicast_targets_fail_closed_for_literal_and_dns_answers ________

client = <app.services.cloud_agent.research.network.PinnedPublicHTTPClient object at 0x784c642a7610>
fake_network = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x784c642a7310>

    def test_multicast_targets_fail_closed_for_literal_and_dns_answers(client, fake_network):
        with pytest.raises(ResearchError) as excinfo:
            client.get("http://224.0.0.1/")

>       assert excinfo.value.code == "URL_TARGET_NOT_PUBLIC"
E       AssertionError: assert 'URL_FETCH_FAILED' == 'URL_TARGET_NOT_PUBLIC'
E
E         - URL_TARGET_NOT_PUBLIC
E         + URL_FETCH_FAILED

test/services/cloud_agent/test_research_network.py:310: AssertionError
_____ test_get_resolves_hostname_once_immediately_before_pinned_connection _____

client = <app.services.cloud_agent.research.network.PinnedPublicHTTPClient object at 0x784c65927790>
fake_network = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x784c663422d0>

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

>       assert fake_network.resolve_calls == [
            {"host": "public.example", "port": 443, "type": socket.SOCK_STREAM}
        ]
E       AssertionError: assert [{'host': 'pu...K_STREAM: 1>}] == [{'host': 'pu...K_STREAM: 1>}]
E
E         Left contains one more item: {'host': 'public.example', 'port': 443, 'type': <SocketKind.SOCK_STREAM: 1>}
E         Use -v to get more diff

test/services/cloud_agent/test_research_network.py:332: AssertionError
_________ test_get_refreshes_socket_timeout_before_each_blocking_read __________

fake_network = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x784c642b26d0>

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
        monotonic_values = iter(
            [100.0, 115.0, 118.0, 121.0, 125.0, 126.0, 127.0, 127.5, 128.0, 128.0, 128.0]
        )
        client = PinnedPublicHTTPClient(
            resolver=fake_network.getaddrinfo,
            connector=fake_network.create_connection,
            ssl_context_factory=lambda: _FakeSSLContext(fake_network.connect_calls),
            monotonic=lambda: next(monotonic_values),
        )

        response = client.get("https://public.example/article")

        assert response.body == b"body"
>       assert fake_network.sockets[-1].timeout_history == [15.0, 12.0, 9.0, 5.0, 4.0, 3.0]
E       AssertionError: assert [15.0] == [15.0, 12.0, ...5.0, 4.0, 3.0]
E
E         Right contains 5 more items, first extra item: 12.0
E         Use -v to get more diff

test/services/cloud_agent/test_research_network.py:374: AssertionError
__________ test_decoder_caps_incremental_output_before_body_extension __________

client = <app.services.cloud_agent.research.network.PinnedPublicHTTPClient object at 0x784c642a6f10>
fake_network = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x784c642a6050>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x784c642a48d0>

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
>           client.get("https://public.example/article")

test/services/cloud_agent/test_research_network.py:411:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
app/services/cloud_agent/research/network.py:119: in get
    response = self._download_once(current, deadline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/network.py:181: in _download_once
    body = self._read_body(response_file, headers, deadline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/network.py:331: in _read_body
    self._append_decoded(body, decoder, chunk)
app/services/cloud_agent/research/network.py:360: in _append_decoded
    decoded = chunk if decoder is None else decoder.decompress(chunk)
                                            ^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <test.services.cloud_agent.test_research_network.test_decoder_caps_incremental_output_before_body_extension.<locals>._FakeDecoder object at 0x784c642a4d10>
chunk = b'compressed', max_length = None

    def decompress(self, chunk: bytes, max_length: int | None = None) -> bytes:
        self.calls.append(max_length)
        if max_length is None:
>           raise AssertionError("decoder must receive an incremental max_length cap")
E           AssertionError: decoder must receive an incremental max_length cap

test/services/cloud_agent/test_research_network.py:386: AssertionError
_______ test_hidden_css_content_is_removed_even_with_whitespace_in_style _______

runtime = <app.services.cloud_agent.research.runtime.ResearchToolRuntime object at 0x784c642e9e90>
fake_http = <test.services.cloud_agent.test_research_runtime._FakeHTTPClient object at 0x784c642e8510>

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

>       assert source.content == "Visible fact.\n\nFinal fact."
E       AssertionError: assert 'Visible fact...\nFinal fact.' == 'Visible fact.\n\nFinal fact.'
E
E           Visible fact.
E
E         + Hidden prompt injection
E         +
E         + Hidden sidebar prompt
E         +
E           Final fact.

test/services/cloud_agent/test_research_runtime.py:121: AssertionError
_________ test_visible_repeated_blocks_are_preserved_in_source_content _________

runtime = <app.services.cloud_agent.research.runtime.ResearchToolRuntime object at 0x784c6429b210>
fake_http = <test.services.cloud_agent.test_research_runtime._FakeHTTPClient object at 0x784c6429b190>

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

>       assert source.content == "Echo\n\nEcho\n\nFinal fact."
E       AssertionError: assert 'Echo\n\nFinal fact.' == 'Echo\n\nEcho\n\nFinal fact.'
E
E         - Echo
E         -
E           Echo
E
E           Final fact.

test/services/cloud_agent/test_research_runtime.py:139: AssertionError
=========================== short test summary info ============================
FAILED test/services/cloud_agent/test_research_network.py::test_multicast_targets_fail_closed_for_literal_and_dns_answers
FAILED test/services/cloud_agent/test_research_network.py::test_get_resolves_hostname_once_immediately_before_pinned_connection
FAILED test/services/cloud_agent/test_research_network.py::test_get_refreshes_socket_timeout_before_each_blocking_read
FAILED test/services/cloud_agent/test_research_network.py::test_decoder_caps_incremental_output_before_body_extension
FAILED test/services/cloud_agent/test_research_runtime.py::test_hidden_css_content_is_removed_even_with_whitespace_in_style
FAILED test/services/cloud_agent/test_research_runtime.py::test_visible_repeated_blocks_are_preserved_in_source_content
6 failed, 21 passed in 0.98s
```

GREEN / verification:
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q && uv lock --check && uv run ruff check app/services/cloud_agent/research/network.py app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py`
- Output:

```text
...........................                                              [100%]
27 passed in 0.74s
Resolved 130 packages in 2ms
All checks passed!
```

Fix round 2
-----------

Reviewer findings addressed
---------------------------
- MEDIUM: `_download_once()` now checks the hard 30-second total deadline before connect, after DNS validation, and passes the remaining total budget into both the TCP connect timeout and the TLS handshake timeout path.
- MEDIUM: hidden-content pruning now skips already decomposed BeautifulSoup tags, so nested hidden parents can be removed without crashing when their detached descendants appear later in the traversal snapshot.

Fix TDD evidence
----------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q`
- Output:

```text
.......FF.....F......                                           [100%]
=================================== FAILURES ===================================
______________ test_connect_and_tls_use_remaining_total_deadline _______________

fake_network = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x742e15f675d0>

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
        monotonic_values = iter([100.0, 128.0, 128.5, 129.0, 129.2, 129.4, 129.5, 129.6])
        client = PinnedPublicHTTPClient(
            resolver=fake_network.getaddrinfo,
            connector=fake_network.create_connection,
            ssl_context_factory=lambda: ssl_context,
            monotonic=lambda: next(monotonic_values),
        )

>       response = client.get("https://public.example/article")
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

test/services/cloud_agent/test_research_network.py:405:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
app/services/cloud_agent/research/network.py:119: in get
    response = self._download_once(current, deadline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/network.py:190: in _download_once
    body = self._read_body(response_file, sock, headers, deadline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/network.py:340: in _read_body
    chunk = self._read(stream, sock, 64 * 1024, deadline)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/network.py:415: in _read
    self._refresh_read_timeout(sock, deadline)
app/services/cloud_agent/research/network.py:425: in _refresh_read_timeout
    self._apply_timeout(sock, deadline)
app/services/cloud_agent/research/network.py:419: in _apply_timeout
    remaining = min(self.READ_TIMEOUT_SECONDS, deadline - self._monotonic())
                                                          ^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

>       monotonic=lambda: next(monotonic_values),
                          ^^^^^^^^^^^^^^^^^^^^^^
    )
E   StopIteration

test/services/cloud_agent/test_research_network.py:402: StopIteration
________ test_connect_rejects_when_total_deadline_is_already_exhausted _________

fake_network = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x742e15f657d0>

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
>           client.get("https://public.example/article")

test/services/cloud_agent/test_research_network.py:432:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
app/services/cloud_agent/research/network.py:119: in get
    response = self._download_once(current, deadline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/network.py:170: in _download_once
    sock = self._connect_pinned(
app/services/cloud_agent/research/network.py:280: in _connect_pinned
    raw_socket = self._connector((ip, port), timeout=self.CONNECT_TIMEOUT_SECONDS)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <test.services.cloud_agent.test_research_network._FakeNetwork object at 0x742e15f657d0>
address = ('93.184.216.34', 443), timeout = 5

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
>           raise AssertionError(f"no fake response configured for {hostname}:{port}")
E           AssertionError: no fake response configured for public.example:443

test/services/cloud_agent/test_research_network.py:115: AssertionError
_____ test_nested_hidden_parent_does_not_crash_and_removes_descendant_text _____

runtime = <app.services.cloud_agent.research.runtime.ResearchToolRuntime object at 0x742e15eb3890>
fake_http = <test.services.cloud_agent.test_research_runtime._FakeHTTPClient object at 0x742e15eb1f10>

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

>       source = runtime.execute("fetch_url", "https://public.example/nested-hidden")
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

test/services/cloud_agent/test_research_runtime.py:158:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
app/services/cloud_agent/research/runtime.py:109: in execute
    return self._extract_html(response)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
app/services/cloud_agent/research/runtime.py:145: in _extract_html
    self._remove_hidden_and_cookie_content(soup)
app/services/cloud_agent/research/runtime.py:216: in _remove_hidden_and_cookie_content
    classes = " ".join(tag.get("class", []))
                       ^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <></>, key = 'class', default = []

    def get(
        self, key: str, default: Optional[_AttributeValue] = None
    ) -> Optional[_AttributeValue]:
        """Returns the value of the 'key' attribute for the tag, or
        the value given for 'default' if it doesn't have that
        attribute.

        :param key: The attribute to look for.
        :param default: Use this value if the attribute is not present
            on this `Tag`.
        """
>       return self.attrs.get(key, default)
               ^^^^^^^^^^^^^^
E       AttributeError: 'NoneType' object has no attribute 'get'

.venv/lib/python3.11/site-packages/bs4/element.py:2449: AttributeError
=========================== short test summary info ============================
FAILED test/services/cloud_agent/test_research_network.py::test_connect_and_tls_use_remaining_total_deadline
FAILED test/services/cloud_agent/test_research_network.py::test_connect_rejects_when_total_deadline_is_already_exhausted
FAILED test/services/cloud_agent/test_research_runtime.py::test_nested_hidden_parent_does_not_crash_and_removes_descendant_text
3 failed, 27 passed in 1.06s
```

GREEN / verification:
- Command: `uv run pytest test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py -q && uv lock --check && uv run ruff check app/services/cloud_agent/research/network.py app/services/cloud_agent/research/runtime.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py`
- Output:

```text
..............................                                           [100%]
30 passed in 0.75s
Resolved 130 packages in 4ms
All checks passed!
```
