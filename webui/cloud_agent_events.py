"""Worker-driven Cloud Agent event listener (Streamlit Components v2)."""
# The renderer is created through the Streamlit ``st.components.v2.component`` API.

from typing import Literal, Mapping

import streamlit as st


_EVENT_RENDERER = """
export default function(component) {
  const parent = component.parentElement;
  if (!parent.__cloudAgentEventSource) {
    const source = new EventSource(component.data.streamUrl);
    const emit = (event) => {
      try { component.setTriggerValue('event', JSON.parse(event.data)); } catch (_) {}
    };
    source.addEventListener('job.updated', emit);
    source.addEventListener('job.completed', emit);
    source.addEventListener('job.incident', emit);
    source.addEventListener('sync_required', emit);
    parent.__cloudAgentEventSource = {source, emit, pollTimer: null};
  }
  const state = parent.__cloudAgentEventSource;
  if (component.data.pollingEnabled && !state.pollTimer) {
    state.pollTimer = setInterval(() => {
      component.setTriggerValue('event', {
        event_id: `status-probe-${Date.now()}`,
        type: 'status_probe',
      });
    }, component.data.pollIntervalMs);
  } else if (!component.data.pollingEnabled && state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  return () => {
    const current = parent.__cloudAgentEventSource;
    if (current) {
      if (current.pollTimer) clearInterval(current.pollTimer);
      current.source.close();
      delete parent.__cloudAgentEventSource;
    }
  };
}
"""


def render_cloud_job_event_listener(
    stream_url: str,
    *,
    key: str,
    polling_enabled: bool = False,
    poll_interval_seconds: int = 15,
) -> Mapping[str, object] | None:
    components = getattr(st, "components", None)
    if components is None or not hasattr(components, "v2"):
        return None
    renderer = components.v2.component(
        "cloud-agent-events",
        html="<span aria-hidden='true'></span>",
        js=_EVENT_RENDERER,
    )
    result = renderer(
        data={
            "streamUrl": stream_url,
            "pollingEnabled": bool(polling_enabled),
            "pollIntervalMs": max(1, int(poll_interval_seconds)) * 1000,
        },
        key=key,
        on_event_change=lambda: None,
    )
    if not isinstance(result, Mapping):
        return None
    event = result.get("event")
    return event if isinstance(event, Mapping) else None


def classify_event(event, *, selected_job_id: str, last_event_id: str) -> Literal[
    "ignore", "refresh_job", "refresh_app", "refresh_incidents", "sync"
]:
    if not isinstance(event, Mapping):
        return "ignore"
    event_id = str(event.get("event_id") or "")
    if event_id and event_id == last_event_id:
        return "ignore"
    event_type = event.get("type")
    if event_type == "sync_required":
        return "sync"
    if event_type == "status_probe" and selected_job_id:
        return "refresh_job"
    if event_type == "job.completed":
        return "refresh_job" if event.get("job_id") == selected_job_id else "refresh_app"
    if event_type == "job.incident":
        return "refresh_incidents"
    if event_type == "job.updated" and event.get("job_id") == selected_job_id:
        return "refresh_job"
    return "ignore"
