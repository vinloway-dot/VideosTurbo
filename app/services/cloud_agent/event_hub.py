import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

from app.services.cloud_agent.job_events import CloudAgentEvent


class CloudJobEventHub:
    def __init__(self, subscriber_queue_size: int = 128):
        if subscriber_queue_size <= 0:
            raise ValueError("subscriber_queue_size must be positive")
        self.subscriber_queue_size = subscriber_queue_size
        self._subscribers: set[asyncio.Queue[CloudAgentEvent | None]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: CloudAgentEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                # Drop stale updates; the consumer will reconcile via sync_required.
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def stream(self, heartbeat_seconds: float = 25) -> AsyncIterator[str]:
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        queue: asyncio.Queue[CloudAgentEvent | None] = asyncio.Queue(
            maxsize=self.subscriber_queue_size
        )
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield self._frame("sync_required", {"event_id": uuid4().hex})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    yield self._frame("sync_required", {"event_id": uuid4().hex})
                    continue
                event_type = getattr(event.type, "value", event.type)
                yield self._frame(event_type, event.model_dump(mode="json"), event.event_id)
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @staticmethod
    def _frame(event_name: str, payload: dict, event_id: str | None = None) -> str:
        identifier = event_id or payload.get("event_id") or uuid4().hex
        return f"id: {identifier}\nevent: {event_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


event_hub = CloudJobEventHub()
