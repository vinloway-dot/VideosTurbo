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
      try { component.setTriggerValue(JSON.parse(event.data)); } catch (_) {}
    };
    source.addEventListener('job.updated', emit);
    source.addEventListener('job.completed', emit);
    source.addEventListener('sync_required', emit);
    parent.__cloudAgentEventSource = {source, emit};
  }
  return () => {
    const state = parent.__cloudAgentEventSource;
    if (state) { state.source.close(); delete parent.__cloudAgentEventSource; }
  };
}
"""


def render_cloud_job_event_listener(stream_url: str, *, key: str) -> Mapping[str, object] | None:
    components = getattr(st, "components", None)
    if components is None or not hasattr(components, "v2"):
        return None
    renderer = components.v2.component(
        "cloud-agent-events",
        html="<span aria-hidden='true'></span>",
        js=_EVENT_RENDERER,
    )
    result = renderer(
        data={"streamUrl": stream_url},
        key=key,
        on_trigger=lambda value: value,
    )
    return result if isinstance(result, Mapping) else None


def classify_event(event, *, selected_job_id: str, last_event_id: str) -> Literal[
    "ignore", "refresh_job", "refresh_app", "sync"
]:
    if not isinstance(event, Mapping):
        return "ignore"
    event_id = str(event.get("event_id") or "")
    if event_id and event_id == last_event_id:
        return "ignore"
    event_type = event.get("type")
    if event_type == "sync_required":
        return "sync"
    if event_type == "job.completed":
        return "refresh_job" if event.get("job_id") == selected_job_id else "refresh_app"
    if event_type == "job.updated" and event.get("job_id") == selected_job_id:
        return "refresh_job"
    return "ignore"
