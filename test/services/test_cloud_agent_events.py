from pathlib import Path

from webui.cloud_agent_events import classify_event


def test_event_listener_uses_component_v2_and_closes_event_source():
    source = Path("webui/cloud_agent_events.py").read_text(encoding="utf-8")
    assert "st.components.v2.component" in source
    assert "new EventSource" in source
    assert "setTriggerValue" in source
    assert "return () =>" in source
    assert ".close()" in source
    assert "components.v1" not in source
    assert "setComponentValue" not in source


def test_classifier_refreshes_selected_job_and_app_on_completion():
    assert classify_event({"event_id": "1", "type": "job.updated", "job_id": "job-1"}, selected_job_id="job-1", last_event_id="") == "refresh_job"
    assert classify_event({"event_id": "2", "type": "job.completed", "job_id": "job-2"}, selected_job_id="job-1", last_event_id="1") == "refresh_app"
    assert classify_event({"event_id": "2", "type": "job.completed", "job_id": "job-2"}, selected_job_id="job-1", last_event_id="2") == "ignore"
    assert classify_event({"event_id": "3", "type": "sync_required"}, selected_job_id="job-1", last_event_id="2") == "sync"
    assert classify_event({"event_id": "4", "type": "job.updated", "job_id": "job-2"}, selected_job_id="job-1", last_event_id="2") == "ignore"
    assert classify_event({}, selected_job_id="job-1", last_event_id="") == "ignore"
