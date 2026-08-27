Task 5 report
=============

Summary
-------
- Added `app/services/cloud_agent/research/service.py` with `ResearchScriptService`, a bounded three-tool/three-provider-round orchestration layer for research-backed script drafts.
- Wired `build_research_script_service()` in `app/services/cloud_agent/factory.py` with the guarded runtime, research settings/store, and the OpenRouter/AIHubMix tool-calling adapters.
- Added mock-only state-machine coverage in `test/services/cloud_agent/test_research_service.py` for preflight ordering, raw URL limits, custom-model validation, allowlist enforcement, tool-budget atomicity, per-attempt source caching, source-context guardrails, final evidence validation, safe continuation after recoverable source failures, success provenance, and factory composition.

Files changed
-------------
- `app/services/cloud_agent/factory.py`
- `app/services/cloud_agent/research/service.py`
- `test/services/cloud_agent/test_research_service.py`

Implementation notes
--------------------
- `ResearchScriptService.create_draft(...)` validates one to three raw URL entries before provider capability/completion calls, resolves custom model IDs from trimmed `custom_model_id`, preflights URLs through the guarded runtime, and then runs at most three provider completion rounds.
- Tool execution is capped at three total requested tool calls. A tool batch that exceeds the remaining budget is rejected before any call in that batch executes.
- Tool URLs are canonicalized and checked against the preflighted allowlist. Out-of-allowlist and malformed tool arguments stop the attempt as provider protocol failures.
- Successful tool results are cached by `(tool_name, canonical_url)` within one create attempt only. Repeated model requests reuse the verified source body but still increment the tool-call accounting.
- Recoverable source-read errors are returned to the model as sanitized tool messages only when another source in the attempt has succeeded; no-source attempts fail with `SOURCE_EVIDENCE_EMPTY`.
- After tool execution, the service aggregates all successful sources and sends the complete evidence packet back to the provider. It does not chunk, summarize, or truncate source text; instead, deterministic UTF-8 byte accounting plus 2,048 protocol and 2,048 output reserves enforce `SOURCE_CONTEXT_TOO_LARGE`.
- Final envelopes are validated at request time: source IDs must refer to successful sources, at least one successful source must be used, each evidence quote must occur in that source's normalized content, and unstable claims must have verified evidence.
- Standard Script generation, CloudJob creation/queueing, and editor state mutation are not invoked by this service. Provenance is saved only after final evidence validation and six-clip/master-prompt construction succeed.

TDD evidence
------------
RED:
- Command: `uv run pytest test/services/cloud_agent/test_research_service.py -q`
- Output:

```text
EEEEEEEEEEEEEEEEF                                                        [100%]
...
E       ModuleNotFoundError: No module named 'app.services.cloud_agent.research.service'
...
FAILED test/services/cloud_agent/test_research_service.py::test_factory_builds_research_script_service
ERROR test/services/cloud_agent/test_research_service.py::test_success_returns_existing_draft_shape_and_provenance
ERROR test/services/cloud_agent/test_research_service.py::test_accounting_sums_provider_round_usage_and_cost
ERROR test/services/cloud_agent/test_research_service.py::test_fourth_tool_is_rejected_without_partial_batch_execution
ERROR test/services/cloud_agent/test_research_service.py::test_context_overflow_is_error_not_silent_truncation
ERROR test/services/cloud_agent/test_research_service.py::test_empty_urls_are_typed_before_provider_call
ERROR test/services/cloud_agent/test_research_service.py::test_private_dns_target_is_rejected_before_provider_sees_url
ERROR test/services/cloud_agent/test_research_service.py::test_more_than_three_supplied_urls_fail_before_provider_call
ERROR test/services/cloud_agent/test_research_service.py::test_custom_choice_requires_non_blank_custom_model_before_provider_call
ERROR test/services/cloud_agent/test_research_service.py::test_canonical_duplicate_urls_collapse_before_tool_call
ERROR test/services/cloud_agent/test_research_service.py::test_repeated_model_tool_call_reuses_source_but_consumes_budget
ERROR test/services/cloud_agent/test_research_service.py::test_evidence_claim_quote_must_exist_in_successful_source
ERROR test/services/cloud_agent/test_research_service.py::test_model_only_final_is_rejected_even_after_source_read
ERROR test/services/cloud_agent/test_research_service.py::test_tool_urls_must_match_supplied_allowlist
ERROR test/services/cloud_agent/test_research_service.py::test_failed_tool_result_can_continue_when_another_source_succeeds
ERROR test/services/cloud_agent/test_research_service.py::test_errors_do_not_persist_research_draft
ERROR test/services/cloud_agent/test_research_service.py::test_success_uses_custom_model_id
1 failed, 16 errors in 3.31s
```

GREEN:
- Command: `uv run pytest test/services/cloud_agent/test_research_service.py -q`
- Output:

```text
.................                                                        [100%]
17 passed in 2.75s
```

Focused verification
--------------------
- Command: `uv run pytest test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py -q && uv run ruff check app/services/cloud_agent/factory.py app/services/cloud_agent/research/service.py test/services/cloud_agent/test_research_service.py`
- Output:

```text
.........................................                                [100%]
41 passed in 2.90s
All checks passed!
```

Notes
-----
- No paid provider calls, browser calls, network calls, Standard Script calls, or CloudJob mutations were used in the Task 5 tests.
- The repository has a broad `.gitignore` rule for `test_*.py`; `test/services/cloud_agent/test_research_service.py` must be force-added for the commit.
- Existing untracked `config.toml.backup-*` and `config.toml.save*` files were preserved untouched.
