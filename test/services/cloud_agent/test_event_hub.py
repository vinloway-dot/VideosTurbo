import asyncio

from app.services.cloud_agent.event_hub import CloudJobEventHub
from app.services.cloud_agent.job_events import CloudJobEvent, CloudJobEventType
from app.models.cloud_agent import CloudJobCheckpoint, CloudJobStatus


def _event(job_id="job-1"):
    return CloudJobEvent(
        event_id=f"event-{job_id}", type=CloudJobEventType.JOB_UPDATED,
        job_id=job_id, status=CloudJobStatus.TTS_GENERATING,
        checkpoint=CloudJobCheckpoint.NONE, current_step="tts_generating",
        progress=15, updated_at="2026-08-28T00:00:00+00:00", completed_at="",
    )


def test_stream_starts_with_sync_and_delivers_event():
    async def scenario():
        hub = CloudJobEventHub()
        stream = hub.stream(heartbeat_seconds=1)
        first = await anext(stream)
        assert "event: sync_required" in first
        await hub.publish(_event())
        frame = await anext(stream)
        assert "event: job.updated" in frame
        assert '"job_id":"job-1"' in frame
        await stream.aclose()

    asyncio.run(scenario())


def test_slow_subscriber_receives_sync_after_overflow():
    async def scenario():
        hub = CloudJobEventHub(subscriber_queue_size=1)
        stream = hub.stream(heartbeat_seconds=1)
        await anext(stream)
        await hub.publish(_event("one"))
        await hub.publish(_event("two"))
        frame = await anext(stream)
        assert "event: sync_required" in frame
        await stream.aclose()

    asyncio.run(scenario())


def test_stream_emits_heartbeat():
    async def scenario():
        hub = CloudJobEventHub()
        stream = hub.stream(heartbeat_seconds=0.01)
        await anext(stream)
        assert await anext(stream) == ": keep-alive\n\n"
        await stream.aclose()

    asyncio.run(scenario())
