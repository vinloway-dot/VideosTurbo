import json
import subprocess

from webui import cloud_agent_events
from webui.cloud_agent_events import classify_event


def test_event_renderer_emits_named_event_trigger():
    renderer = cloud_agent_events._EVENT_RENDERER.replace(
        "export default function(component)", "const render = function(component)"
    )
    harness = """
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    globalThis.eventSource = this;
  }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  close() {}
  emit(name, payload) {
    this.listeners[name]({data: JSON.stringify(payload)});
  }
}
globalThis.EventSource = FakeEventSource;
const triggers = [];
const component = {
  parentElement: {},
  data: {streamUrl: '/api/v1/cloud-agent/events/stream'},
  setTriggerValue: (...args) => triggers.push(args),
};
render(component);
eventSource.emit('sync_required', {event_id: 'sync-1', type: 'sync_required'});
eventSource.emit('job.updated', {event_id: 'event-2', type: 'job.updated', job_id: 'job-1'});
eventSource.emit('job.incident', {event_id: 'event-3', type: 'job.incident', incident_id: 'incident-1'});
process.stdout.write(JSON.stringify(triggers));
"""

    completed = subprocess.run(
        ["node", "-e", f"{renderer}\n{harness}"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == [
        ["event", {"event_id": "sync-1", "type": "sync_required"}],
        [
            "event",
            {"event_id": "event-2", "type": "job.updated", "job_id": "job-1"},
        ],
        [
            "event",
            {"event_id": "event-3", "type": "job.incident", "incident_id": "incident-1"},
        ],
    ]


def test_event_listener_returns_named_event_payload(monkeypatch):
    payload = {"event_id": "event-1", "type": "job.updated", "job_id": "job-1"}
    mounted = {}

    class FakeV2:
        @staticmethod
        def component(*_args, **_kwargs):
            def renderer(**kwargs):
                mounted.update(kwargs)
                return {"event": payload}

            return renderer

    class FakeComponents:
        v2 = FakeV2()

    monkeypatch.setattr(cloud_agent_events.st, "components", FakeComponents())

    event = cloud_agent_events.render_cloud_job_event_listener(
        "/api/v1/cloud-agent/events/stream", key="cloud-agent-events"
    )

    assert event == payload
    assert "on_event_change" in mounted
    assert "on_trigger" not in mounted


def test_classifier_refreshes_selected_job_and_app_on_completion():
    assert classify_event({"event_id": "1", "type": "job.updated", "job_id": "job-1"}, selected_job_id="job-1", last_event_id="") == "refresh_job"
    assert classify_event({"event_id": "2", "type": "job.completed", "job_id": "job-2"}, selected_job_id="job-1", last_event_id="1") == "refresh_app"
    assert classify_event({"event_id": "2", "type": "job.completed", "job_id": "job-2"}, selected_job_id="job-1", last_event_id="2") == "ignore"
    assert classify_event({"event_id": "3", "type": "sync_required"}, selected_job_id="job-1", last_event_id="2") == "sync"
    assert classify_event({"event_id": "4", "type": "job.updated", "job_id": "job-2"}, selected_job_id="job-1", last_event_id="2") == "ignore"
    assert classify_event({}, selected_job_id="job-1", last_event_id="") == "ignore"
    assert classify_event({"event_id": "5", "type": "job.incident", "former_job_id": "job-1"}, selected_job_id="job-1", last_event_id="4") == "refresh_incidents"
