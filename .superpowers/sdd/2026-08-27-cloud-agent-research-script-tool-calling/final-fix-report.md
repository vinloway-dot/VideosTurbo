# Cloud Agent Research Script final fix report

Date: 2026-08-27

## Outcome

All 17 findings in `final-findings.md` were addressed in one coherent fix wave. The implementation preserves the existing Standard Script request/worker contract, keeps Research disabled by default behind an independent control, performs no automatic provider/model/Standard fallback, and keeps every verification command non-paid and local.

No OpenRouter or AIHubMix generation, TTS synthesis, Google Flow generation, Canva mutation, browser session, remote-media action, or paid retry was executed during this fix wave.

## Finding disposition

| # | Severity | Resolution | Regression evidence |
|---|---|---|---|
| 1 | Critical | Chunk-size syntax and bounds are validated before reads; accepted chunks are read in 64 KiB pieces; decoder flush output is bounded by the remaining decoded-body allowance. | `test_chunk_sizes_are_validated_before_any_bounded_incremental_read`, `test_decoder_flush_uses_a_bounded_remaining_output_limit` |
| 2 | Important | Standard Start receives a cheap Research-store factory and constructs the store only for a non-empty `research_draft_id`. | `test_standard_start_does_not_construct_research_storage` |
| 3 | Important | Provider metadata now exposes catalog models, default model, configured custom ID, and key state; settings and WebUI use catalog-backed selectors with an explicit `custom` handoff. Unsupported selections still fail closed. | `test_provider_catalog_exposes_selectable_catalog_and_custom_model_handoff`, `test_provider_catalog_exposes_model_default_and_custom_handoff`, `test_research_model_options_come_from_provider_catalog`, `test_success_uses_custom_model_id` |
| 4 | Important | Durable provenance stores tool/round counts, distinct editable/invariant fingerprints, nullable cost, normalized content hashes, and fixed `source_evidence + model_knowledge`. Legacy rows are migrated conservatively. | `test_success_persists_complete_provenance_contract`, store migration regressions |
| 5 | Important | Missing or non-finite cost remains unavailable; attempted provider rounds and tool calls are counted before fallible work; typed failures carry sanitized accounting. | `test_missing_cost_in_any_provider_round_remains_unavailable`, provider/runtime error accounting tests, non-finite adapter/controller tests |
| 6 | Important | The immutable prompt now states the allowlist, untrusted-content, secret, budget, no-retry/fallback, successful-source, model-knowledge, conflict, unstable-fact, citation, and quote rules. Final validation requires successful sources, exact source quotes, claim/source-set agreement, and rejects unrequested narration URLs. | invariant-prompt and final-policy service regressions |
| 7 | Important | Unknown charsets, lazy PDF page/extraction/metadata failures, and non-finite provider metadata map to typed/sanitized behavior. | exact charset/PDF phase parameterizations and non-finite accounting regressions |
| 8 | Important | Research draft requests use a dedicated 300-second WebUI timeout; Standard draft timeout remains 120 seconds. | `test_research_attempt_timeout_covers_the_full_bounded_operation` |
| 9 | Important | DNS resolution runs behind a bounded wait within the source total deadline; preflight shares a total deadline and source deadline failures use `URL_FETCH_FAILED`. | `test_dns_resolution_is_bounded_and_classified_as_url_fetch_failure` and existing timeout regressions |
| 10 | Important | The password-widget value is removed from Streamlit session state before the API request and only non-secret feedback is retained. | `test_research_key_submit_removes_raw_secret_before_api_call` |
| 11 | Important | Exact normalized evidence blocks are emitted once per attempt; later rounds receive stable cross-references. | `test_evidence_block_is_not_repeated_across_provider_rounds` |
| 12 | Medium | `cloud_agent_research_enabled` is an independent, disabled-by-default setting. Disabled Research is omitted from the mode UI and draft generation returns 404 without service construction. Safe settings/key controls remain available to enable/configure it. | exact-default, disabled-route, and mode-option regressions |
| 13 | Medium | HTML/XHTML/PDF MIME matching is exact; opacity-zero and common absolute/fixed offscreen/text-indent hiding are removed. | exact-MIME and hidden-style parameterizations |
| 14 | Medium | Public draft/source models serialize `content_hash`; the old SQLite column name remains an internal migration detail only. | public controller/service/store contract assertions |
| 15 | Medium | WebUI renders one to three individually keyed URL rows with a bounded row-count control. | `test_research_mode_and_url_row_helpers_enforce_explicit_ui_bounds` plus WebUI render regressions |
| 16 | Minor | Tests assert the complete exact Research defaults, error-code set, and Thai public-message mapping. | exact inventory contract regressions |
| 17 | Minor | The unintended tracked root `task-7-report.md` is deleted; the canonical SDD task report remains. | staged-path audit |

## Root-cause notes

- Network bounds had been applied only after a chunk was accepted/read, while synchronous resolution sat outside an enforceable deadline. Bounds now surround allocation/read decisions and DNS waiting.
- Research dependencies were injected eagerly at the shared Start boundary. A factory dependency keeps the normal Standard path storage-independent.
- Catalog data, UI selection, settings validation, and effective-model resolution previously described different contracts. They now share the provider-owned catalog plus explicit custom-ID path.
- Provenance and accounting models used numeric placeholders and older ambiguous field names. The public/durable models now distinguish unavailable data, preserve exact counts, and expose purpose-specific fingerprints.
- Several policy statements existed only implicitly. The immutable prompt and deterministic final-envelope checks now enforce the machine-checkable parts, while capability, allowlist, SSRF, context, and budget checks remain fail closed in code.
- Streamlit's keyed password widget retained submitted input. The submit callback now removes it before dispatch and stores only a result category/message.

## Spec rulings

No finding conflicted with the approved design, so no item was skipped or waived. Three boundary interpretations were made explicitly:

- "Disabled Research should not expose active generation routes/UI" is implemented as a fail-closed 404 on draft generation plus removal of Research mode from the UI. Non-generating provider/settings/key routes remain available because the approved design makes them the server-side control used to configure and enable Research, and they make no provider request.
- `enabled` is optional on the existing settings update payload so older callers preserve the current value; new UI saves send it explicitly. This preserves the route/API while adding the independent control.
- `content_hash` is the sole public model/JSON name. The legacy SQLite `source_hash` column remains internal so existing databases migrate without destructive schema replacement.

## RED evidence

The new regressions were first run against the pre-fix implementation:

```text
$ uv run pytest test/services/cloud_agent/test_research_contracts.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_webui.py -q
43 failed, 113 passed, 11 warnings in 8.09s
exit code: 1
```

The failures covered the intended gaps: missing enable/catalog/default inventories; missing provenance counts/fingerprints; placeholder zero cost; unbounded chunk/decompress/DNS work; wrong source timeout class; permissive MIME and hidden content; raw charset/lazy-PDF/non-finite failures; eager Start storage; incomplete policy/accounting; repeated evidence; missing UI bounds/timeout/secret cleanup.

Two bounds/migration regressions were isolated during root-cause work:

```text
$ uv run pytest test/services/cloud_agent/test_research_store.py::test_legacy_placeholder_zero_cost_migrates_as_unavailable -q
1 failed in 2.47s
exit code: 1

$ uv run pytest test/services/cloud_agent/test_research_network.py::test_decoder_flush_uses_a_bounded_remaining_output_limit -q
1 failed in 0.39s
exit code: 1
```

The first exposed legacy `0.0` being misreported as supplied cost; the second observed an unbounded `decompressobj.flush()` call.

## GREEN evidence

Post-fix focused Research/controller/WebUI run made all new regressions green:

```text
$ uv run pytest test/services/cloud_agent/test_research_contracts.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_webui.py -q
159 passed, 11 warnings in 25.16s
exit code: 0
```

The final Task 8 release commands and their fresh outputs are recorded below after the final report-only edit.

## Safety and repository audit

- Tests use fixtures, fakes, local TestClient calls, temporary SQLite databases, and bounded fake transports only.
- No provider, browser, TTS, Flow, Canva, or remote-media operation was invoked.
- `git diff --check` and Python bytecode compilation passed before final matrix execution.
- The following protected user files remained untracked, unmodified, and excluded from staging:
  - `config.toml.backup-20260825T181857Z`
  - `config.toml.backup-20260826T055642Z`
  - `config.toml.backup-20260826T160959Z-before-aihubmix`
  - `config.toml.backup-20260826T165339Z-before-flow-locale`
  - `config.toml.backup-20260827T015730Z-before-canva-design-replace`
  - `config.toml.save`
  - `config.toml.save.1`
  - `config.toml.save.2`

## Final verification results

Fresh Task 8 verification immediately before staging/commit:

```text
$ uv run pytest test/services/cloud_agent/test_research_controller.py::test_research_route_inventory_uses_only_cloud_agent_prefix -q
1 passed, 11 warnings in 3.21s
exit code: 0
```

```text
$ uv run pytest test/services/cloud_agent/test_research_contracts.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py -q
185 passed, 11 warnings in 29.38s
exit code: 0
```

```text
$ uv run pytest test/services/cloud_agent test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py test/services/test_llm.py test/services/test_six_clip_plan.py -q
656 passed, 1 skipped, 11 warnings in 39.05s
exit code: 0
```

```text
$ uv run ruff check app webui test
All checks passed!
exit code: 0

$ uv lock --check
Resolved 130 packages in 2ms
exit code: 0
```

The 11 warnings are existing Starlette TestClient and Pydantic v2 migration deprecations. The single skip is the existing non-paid matrix skip; there were no failures or errors.
