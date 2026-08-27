Task 4 report
=============

Summary
-------
- Added `app/services/cloud_agent/research/adapters.py` with a shared OpenAI-compatible tool-calling adapter plus concrete `OpenRouterToolCallingAdapter` and `AIHubMixToolCallingAdapter` implementations.
- Restricted generation requests to the repo-owned `fetch_url` and `read_pdf` tools only, with `max_retries=0`, an explicit bounded timeout, and no wrapper retry loop.
- Added focused adapter tests covering owned-tool emission, tool-call parsing, final evidence-envelope validation, custom-model capability lookup, usage/cost parsing, and sanitized timeout/auth failures.

Files changed
-------------
- `app/services/cloud_agent/research/adapters.py`
- `test/services/cloud_agent/test_research_adapters.py`

Implementation notes
--------------------
- `OpenAICompatibleToolCallingAdapter` accepts either a real OpenAI client factory or a fixed fake client, which keeps tests fully local and prevents accidental provider traffic.
- Known catalog models use hardcoded local capability metadata; unknown/custom model IDs go through a non-generative `models.retrieve(...)` preflight that must explicitly prove both tool-calling support and a positive context limit.
- Response parsing is strict by design: tool-call batches must name only `fetch_url` or `read_pdf`, tool arguments must be valid JSON objects with a non-blank `url`, and final synthesis messages must validate as `ProviderFinalPayload`.
- Provider usage is normalized into provider-neutral keys (`input_tokens`, `output_tokens`, `total_tokens`, plus parsed cached/reasoning token details when present). Cost is parsed from either the top-level response or usage block when available.
- Provider exceptions are mapped to typed `ResearchError` codes without echoing raw payload text, auth bodies, or secret values into user-visible messages.

TDD evidence
------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_adapters.py -q`
- Output:

```text
FFFFFFF                                                                  [100%]
=================================== FAILURES ===================================
__________________ test_openrouter_declares_only_owned_tools ___________________

    def test_openrouter_declares_only_owned_tools():
>       from app.services.cloud_agent.research.adapters import (
            FETCH_URL_TOOL,
            READ_PDF_TOOL,
            OpenRouterToolCallingAdapter,
        )
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:128: ModuleNotFoundError
________________ test_aihubmix_parses_tool_call_and_final_usage ________________

    def test_aihubmix_parses_tool_call_and_final_usage():
>       from app.services.cloud_agent.research.adapters import AIHubMixToolCallingAdapter
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:144: ModuleNotFoundError
________________ test_final_message_requires_evidence_envelope _________________

    def test_final_message_requires_evidence_envelope():
>       from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:176: ModuleNotFoundError
_________ test_invalid_final_message_raises_research_response_invalid __________

    def test_invalid_final_message_raises_research_response_invalid():
>       from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:188: ModuleNotFoundError
______________ test_unknown_custom_model_fails_before_completion _______________

    def test_unknown_custom_model_fails_before_completion():
>       from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:200: ModuleNotFoundError
_________ test_generation_client_disables_sdk_retries_and_sets_timeout _________

    def test_generation_client_disables_sdk_retries_and_sets_timeout():
>       from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:215: ModuleNotFoundError
____ test_timeout_and_auth_failures_are_classified_without_leaking_secrets _____

    def test_timeout_and_auth_failures_are_classified_without_leaking_secrets():
>       from app.services.cloud_agent.research.adapters import OpenRouterToolCallingAdapter
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.adapters'

test/services/cloud_agent/test_research_adapters.py:228: ModuleNotFoundError
=========================== short test summary info ============================
FAILED test/services/cloud_agent/test_research_adapters.py::test_openrouter_declares_only_owned_tools
FAILED test/services/cloud_agent/test_research_adapters.py::test_aihubmix_parses_tool_call_and_final_usage
FAILED test/services/cloud_agent/test_research_adapters.py::test_final_message_requires_evidence_envelope
FAILED test/services/cloud_agent/test_research_adapters.py::test_invalid_final_message_raises_research_response_invalid
FAILED test/services/cloud_agent/test_research_adapters.py::test_unknown_custom_model_fails_before_completion
FAILED test/services/cloud_agent/test_research_adapters.py::test_generation_client_disables_sdk_retries_and_sets_timeout
FAILED test/services/cloud_agent/test_research_adapters.py::test_timeout_and_auth_failures_are_classified_without_leaking_secrets
7 failed in 1.47s
```

GREEN:
- Command: `uv run pytest test/services/cloud_agent/test_research_adapters.py -q`
- Output:

```text
.......                                                                  [100%]
7 passed in 1.29s
```

Focused verification
--------------------
- Command: `uv run pytest test/services/cloud_agent/test_research_adapters.py -q && uv run ruff check app/services/cloud_agent/research/adapters.py test/services/cloud_agent/test_research_adapters.py`
- Output:

```text
.......                                                                  [100%]
7 passed in 1.42s
All checks passed!
```

Notes
-----
- Custom-model capability parsing is intentionally strict and fail-closed. If a provider changes its metadata schema, unknown models will surface `PROVIDER_TOOL_CALLING_UNSUPPORTED` or `PROVIDER_MODEL_UNSUPPORTED` until the allowed metadata keys are extended deliberately.
- Existing untracked `config.toml.backup-*` and `config.toml.save*` files were preserved untouched.
