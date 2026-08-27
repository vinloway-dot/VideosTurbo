Task 7 Report

Scope
- Added explicit `Standard Script` and `Research Script` modes to the Streamlit Cloud Agent thin client.
- Preserved the existing Standard Script generation and Start request contract when no research draft is attached.
- Kept the Script Editor as the shared handoff point for both modes.

What Changed
- Added FastAPI-only research helpers in `webui/cloud_agent.py` for:
  - loading provider metadata and saved research settings
  - saving research settings with exact readback verification
  - saving/removing provider API keys
  - creating research drafts
  - extracting safe research error payloads
  - merging optional `research_draft_id` into Start requests only when present
- Added research-mode UI controls for:
  - mode selection
  - research provider
  - provider-specific saved model ids and custom model ids
  - research custom system prompt
  - provider API key management
  - source URL entry
  - research draft generation with a spinner but no fake progress
- Added research accounting and source rendering after successful research responses and safe accounting rendering on API errors.
- Moved `Refresh Draft` to the shared post-editor flow and added `_store_refreshed_draft()` so:
  - unchanged refreshed research scripts retain `cloud_agent_research_draft_id`
  - edited scripts that refresh to a different draft clear stale research provenance
- Updated `_store_draft()` so standard draft generation clears stale research provenance before storing a new draft.
- Extended `_start_job()` so `research_draft_id` is included only when non-blank.

RED -> GREEN
- Added failing WebUI coverage first in `test/services/test_cloud_agent_webui.py` for:
  - research-mode control visibility
  - safe research error parsing
  - settings readback verification
  - blank key omission
  - stale provenance clearing
  - unchanged refresh provenance retention
  - optional `research_draft_id` Start payload merge
  - failure path preserving the editor without storing a draft
- Verified RED with:
  - `uv run pytest /opt/VideosTurbo/test/services/test_cloud_agent_webui.py -q`
- Implemented the minimum production changes in `webui/cloud_agent.py`
- Verified GREEN and focused regression with:
  - `uv run pytest /opt/VideosTurbo/test/services/test_cloud_agent_webui.py /opt/VideosTurbo/test/services/cloud_agent/test_research_controller.py -q && uv run ruff check /opt/VideosTurbo/webui/cloud_agent.py /opt/VideosTurbo/test/services/test_cloud_agent_webui.py`

Verification Result
- Focused regression passed: `44 passed`
- Ruff passed cleanly.

Constraints Honored
- No sqlite access from the WebUI.
- No browser manager or provider-direct generation calls from the WebUI.
- No provider work triggered on page load or while saving research settings.
- Standard mode request body stays unchanged unless a valid `research_draft_id` exists.

Residual Notes
- The focused regression emits pre-existing deprecation warnings from FastAPI/Starlette and some Pydantic models. They are unrelated to Task 7 and were not modified here.
