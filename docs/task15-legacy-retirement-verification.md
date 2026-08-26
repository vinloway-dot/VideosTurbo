# Task 15 legacy-retirement verification

## Scope

This change retires the public classic local-video product while preserving the
two retained products: **Cloud Agent** and **Music Batch**. Shared renderer and
stock-material code remains because Music Batch has verified direct callers.

## Static retirement checks

- The classic `/videos` creation endpoint and both legacy material endpoints
  return the explicit `LEGACY_VIDEO_GENERATION_RETIRED` response.
- `webui/Main.py` is now a Cloud Agent-only entry point; classic generation,
  stock-media, local upload and six-clip controls are absent.
- The retired local six-clip media, renderer and timeline modules are absent.
- The classic pipeline refuses the retired video stage before it can invoke a
  local renderer.

## Cloud Agent

Focused Cloud Agent regression completed without a paid provider call. The
Cloud Agent API and its Streamlit FastAPI client remain present.

## Music Batch

Music Batch regression completed. Its independent Streamlit page and its
shared material/renderer dependencies remain intact.

## API

The non-paid API health check returned enabled, storage-writable and an online
worker. The check was performed locally and does not record credentials or
private browser data.

## WebUI

The WebUI startup regression passed, including execution from an external
directory. The separate Music Batch page remains registered.

## Worker

The production worker service was active during the non-paid smoke check and
reported an online heartbeat. No job, TTS synthesis or Flow generation was
started.

## Sessions

The non-paid session check reported `READY` for Google Flow and Canva. No
interactive authentication, remote media mutation, or paid generation was
performed.

## Verification commands

- `uv run pytest test/services/cloud_agent test/services/music_batch test/services/test_controller_video.py test/services/test_cloud_agent_webui.py test/test_music_batch_webui.py -q`
- `uv run pytest test/services/test_legacy_retirement.py test/services/test_task.py test/services/test_controller_video.py test/services/test_video.py test/services/test_cloud_agent_webui.py test/services/test_webui_startup.py test/services/test_webui_material_type.py test/test_music_batch_webui.py test/services/test_six_clip_plan.py test/services/test_six_clip_ai.py -q`
- `uv run ruff check app webui test`
- `uv lock --check`
- local API health and session-check requests
