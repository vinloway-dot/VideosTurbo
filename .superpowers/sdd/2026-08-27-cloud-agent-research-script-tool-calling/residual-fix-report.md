# Cloud Agent Research Script residual fix report

Date: 2026-08-27

## Outcome

All four residual findings in `residual-fix-brief.md` were addressed with focused
test-first changes. Existing Research and Standard Script behavior remains isolated,
all failures continue through the typed Research error envelope, and verification was
local and non-paid.

No OpenRouter or AIHubMix generation, TTS synthesis, Google Flow generation, Canva
mutation, browser session, remote-media action, or paid retry was executed.

## Finding disposition

1. Legacy migrations now mark an existing positive finite
   `estimated_cost_usd` as available only when adding `cost_available` to an old
   table. The stored value is not rewritten. Placeholder zero remains unavailable.
2. Final narration validation recognizes internal source IDs, numeric bracket
   citations, HTTP(S) URLs, and bare `www` URLs. Citation authorization now requires
   an affirmative editable-prompt request; negated instructions do not authorize
   citations, and ordinary prose using the word `source` remains allowed.
3. Both PDF reader factory signatures are inside the `PDF_INVALID` exception
   boundary. The compatibility retry for factories that do not accept `current_url`
   remains in place, and lazy page, extraction, and metadata failures remain typed.
4. Hidden-style detection now recognizes zero opacity with `!important`, leading-dot
   zero opacity, and viewport-unit offscreen positioning while retaining visible
   evidence.

## RED evidence

Each regression was added and run against the pre-fix production implementation.

### Legacy nonzero cost

```text
$ uv run pytest test/services/cloud_agent/test_research_store.py::test_legacy_nonzero_cost_migrates_as_available -q
1 failed in 2.69s
```

The expected failure loaded the legacy `1.25` value as `None`.

### Citation policy bypasses

```text
$ uv run pytest test/services/cloud_agent/test_research_service.py::test_unrequested_or_negated_citation_forms_are_rejected test/services/cloud_agent/test_research_service.py::test_affirmative_citation_request_allows_citations test/services/cloud_agent/test_research_service.py::test_ordinary_non_citation_use_of_source_is_allowed -q
4 failed, 3 passed in 2.13s
```

The four intended failures were `source-1`, `[1]`, a bare `www.example.com` URL,
and an HTTP(S) URL paired with `Do not cite sources or include URLs.` The existing
HTTP(S)-without-request case already failed closed; the affirmative and ordinary-prose
controls already passed.

A follow-up control within the same finding verified that a non-citation instruction
using the word `sources` is not authorization:

```text
$ uv run pytest test/services/cloud_agent/test_research_service.py::test_unrequested_or_negated_citation_forms_are_rejected -q
1 failed, 5 passed in 1.86s
```

The expected failure accepted `[1]` when the editable prompt said only `Use sources
only to verify factual accuracy.`

### PDF reader fallback

```text
$ uv run pytest test/services/cloud_agent/test_research_runtime.py::test_pdf_factory_type_error_from_both_signatures_maps_to_pdf_invalid -q
1 failed in 0.63s
```

The fallback factory call raised a raw `TypeError` instead of `ResearchError` with
`PDF_INVALID`.

### Hidden CSS variants

```text
$ uv run pytest test/services/cloud_agent/test_research_runtime.py::test_common_visually_hidden_offscreen_content_is_removed -q
3 failed, 3 passed in 0.64s
```

The expected failures leaked content hidden by `opacity:0!important`, `opacity:.0`,
and `position:absolute;left:-100vw`; the three existing hidden forms still passed.

## Focused GREEN evidence

```text
$ uv run pytest test/services/cloud_agent/test_research_store.py::test_legacy_nonzero_cost_migrates_as_available test/services/cloud_agent/test_research_store.py::test_legacy_placeholder_zero_cost_migrates_as_unavailable -q
2 passed in 2.53s

$ uv run pytest test/services/cloud_agent/test_research_service.py::test_unrequested_or_negated_citation_forms_are_rejected test/services/cloud_agent/test_research_service.py::test_affirmative_citation_request_allows_citations test/services/cloud_agent/test_research_service.py::test_ordinary_non_citation_use_of_source_is_allowed -q
8 passed in 1.71s

$ uv run pytest test/services/cloud_agent/test_research_runtime.py::test_pdf_factory_type_error_from_both_signatures_maps_to_pdf_invalid test/services/cloud_agent/test_research_runtime.py::test_lazy_pdf_failures_map_to_pdf_invalid test/services/cloud_agent/test_research_runtime.py::test_read_pdf_extracts_text_and_hashes_full_content -q
5 passed in 0.54s

$ uv run pytest test/services/cloud_agent/test_research_runtime.py::test_common_visually_hidden_offscreen_content_is_removed test/services/cloud_agent/test_research_runtime.py::test_html_strips_chrome_but_preserves_complete_readable_text test/services/cloud_agent/test_research_runtime.py::test_visible_repeated_blocks_are_preserved_in_source_content -q
8 passed in 0.60s
```

Complete changed-component regression:

```text
$ uv run pytest test/services/cloud_agent/test_research_store.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_service.py -q
70 passed in 3.56s
```

## Required non-paid verification

Task 8 targeted matrix:

```text
$ uv run pytest test/services/cloud_agent/test_research_contracts.py test/services/cloud_agent/test_research_settings.py test/services/cloud_agent/test_research_store.py test/services/cloud_agent/test_research_network.py test/services/cloud_agent/test_research_runtime.py test/services/cloud_agent/test_research_adapters.py test/services/cloud_agent/test_research_service.py test/services/cloud_agent/test_research_controller.py test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py -q
198 passed, 11 warnings in 25.53s
```

Task 8 broader Cloud Agent matrix:

```text
$ uv run pytest test/services/cloud_agent test/services/test_cloud_agent_controller.py test/services/test_cloud_agent_webui.py test/services/test_llm.py test/services/test_six_clip_plan.py -q
669 passed, 1 skipped, 11 warnings in 41.12s
```

Static and lock checks:

```text
$ uv run ruff check app webui test
All checks passed!

$ uv lock --check
Resolved 130 packages in 2ms
```

The warnings are the existing Starlette TestClient and Pydantic v2 migration
deprecations. The single skip is the existing non-paid matrix skip; there were no
failures or errors.

## Safety and repository audit

- Tests used fakes, fixtures, local clients, and temporary SQLite databases only.
- No production provider, browser, TTS, Flow, Canva, or remote-media operation ran.
- No configuration file or dependency lock changed.
- The protected untracked `config.toml.backup-*` and `config.toml.save*` files were
  left unmodified and excluded from staging.
