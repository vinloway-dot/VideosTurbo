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
