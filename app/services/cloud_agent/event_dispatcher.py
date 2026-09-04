import queue
import threading
from collections.abc import Callable
from urllib.parse import urlparse

import requests
from loguru import logger

from app.services.cloud_agent.job_events import CloudAgentEvent, JobEventSink


class RequestsJobEventTransport:
    def __init__(self, url: str, timeout_seconds: float):
        parsed = urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.path != "/api/v1/cloud-agent/internal/events"
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("event intake URL must be an exact loopback HTTP endpoint")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.url = url
        self.timeout_seconds = float(timeout_seconds)

    def send(self, event: CloudAgentEvent) -> None:
        response = requests.post(
            self.url,
            json=event.model_dump(mode="json"),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()


class CloudJobEventDispatcher(JobEventSink):
    def __init__(self, transport: Callable[[CloudAgentEvent], None], queue_size: int):
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._transport = transport
        self._queue: queue.Queue[CloudAgentEvent | object] = queue.Queue(maxsize=queue_size)
        self._sentinel = object()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._deliver, daemon=True, name="cloud-agent-events")
        self._thread.start()

    def publish_nowait(self, event: CloudAgentEvent) -> bool:
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            return False
        return True

    def _deliver(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._sentinel:
                    return
                event = item
                try:
                    self._transport(event)
                except Exception as exc:
                    logger.warning(
                        "cloud job event delivery failed type={} item_id={} error_type={}",
                        getattr(event.type, "value", event.type),
                        getattr(event, "job_id", getattr(event, "former_job_id", "")),
                        type(exc).__name__,
                    )
            finally:
                self._queue.task_done()

    def close(self, timeout_seconds: float = 1.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        if self._closed.is_set():
            self._thread.join(timeout_seconds)
            return
        self._closed.set()
        try:
            self._queue.put_nowait(self._sentinel)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(self._sentinel)
            except queue.Full:
                pass
        self._thread.join(timeout_seconds)
