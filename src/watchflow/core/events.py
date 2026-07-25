"""Core event types and the in-process ``EventBus``.

Defines the ``Event`` envelope every Source Adapter emits (``MODULE_SPECIFICATIONS.md``
§2) and the bounded, backpressure-aware ``EventBus`` that fans those events out to
subscribers. The bus is the single point every ingested event passes through before
matching; each subscriber has its own bounded queue, and when a queue fills the
configured :class:`BackpressureStrategy` decides what happens. A drop is never
silent — it is counted in :attr:`EventBus.dropped` and logged as ``queue.dropped``
(Constitution Article VIII; ``ENGINEERING_PRINCIPLES.md`` §3).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

_log = structlog.get_logger(__name__)


class Event(BaseModel):
    """A normalized event emitted by a Source Adapter onto the EventBus.

    Attributes:
        id: Stable unique identifier for this event.
        source: Name of the adapter that produced the event (e.g. ``"filesystem"``).
        type: Adapter-defined event type (e.g. ``"modified"``).
        payload: Structured, adapter-specific event data.
        timestamp: When the event was observed.
        metadata: Optional provenance/context annotations; defaults to empty.
    """

    id: UUID
    source: str
    type: str
    payload: dict[str, Any]
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackpressureStrategy(StrEnum):
    """How the ``EventBus`` reacts when a subscriber's bounded queue is full.

    Attributes:
        DROP_OLDEST: Evict the oldest queued event to make room for the new one.
        BLOCK: Await free space, applying backpressure to the publisher.
        REPORT_AND_DROP: Reject the incoming event, accounting it as dropped.
    """

    DROP_OLDEST = "drop_oldest"
    BLOCK = "block"
    REPORT_AND_DROP = "report_and_drop"


class _Channel:
    """One subscriber's private, bounded delivery queue and its topic filter."""

    __slots__ = ("queue", "topic")

    def __init__(self, topic: str | None, maxsize: int) -> None:
        self.topic = topic
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)


class EventBus:
    """A bounded, backpressure-aware, in-process publish/subscribe bus.

    Each subscriber receives its own bounded queue, so one slow subscriber cannot
    stall another. When a subscriber's queue fills, the configured
    :class:`BackpressureStrategy` decides the outcome; any resulting drop is counted
    in :attr:`dropped` and logged as ``queue.dropped`` — never silent.

    Attributes:
        dropped: Total number of events dropped across all subscribers so far.
    """

    def __init__(self, maxsize: int, backpressure: BackpressureStrategy) -> None:
        """Create a bus whose per-subscriber queues each hold at most ``maxsize`` events.

        Args:
            maxsize: Bound on every subscriber's queue; must be positive. An unbounded
                queue is forbidden (``CODING_STANDARD.md`` §6).
            backpressure: Strategy applied when a subscriber's queue is full.

        Raises:
            ValueError: If ``maxsize`` is not positive.
        """
        if maxsize <= 0:
            raise ValueError("EventBus maxsize must be positive; unbounded queues are forbidden")
        self._maxsize = maxsize
        self._backpressure = backpressure
        self._channels: set[_Channel] = set()
        self.dropped = 0

    @property
    def subscriber_count(self) -> int:
        """The number of currently-subscribed consumers."""
        return len(self._channels)

    def subscribe(self, topic: str | None = None) -> AsyncIterator[Event]:
        """Subscribe to events, optionally filtered by ``topic``.

        The subscriber is registered immediately, so every event published after this
        call returns is eligible for delivery. Iterating the returned async iterator
        yields events in the order they were published; ending the iteration
        (``break`` or ``aclose``) unsubscribes.

        Args:
            topic: If given, only events whose ``type`` equals ``topic`` are delivered;
                if ``None``, every event is delivered.

        Returns:
            An async iterator over the events delivered to this subscriber.
        """
        channel = _Channel(topic, self._maxsize)
        self._channels.add(channel)
        return self._consume(channel)

    async def _consume(self, channel: _Channel) -> AsyncIterator[Event]:
        try:
            while True:
                yield await channel.queue.get()
        finally:
            self._channels.discard(channel)

    async def publish(self, event: Event) -> None:
        """Deliver ``event`` to every subscriber whose topic matches it.

        Under :attr:`BackpressureStrategy.BLOCK` the deliveries run concurrently and
        the call awaits until every matching subscriber has accepted the event,
        applying backpressure to the caller without letting one slow subscriber stall
        another. Under the dropping strategies delivery never blocks.

        Args:
            event: The event to fan out.
        """
        targets = [c for c in self._channels if c.topic is None or c.topic == event.type]
        if not targets:
            return
        if self._backpressure is BackpressureStrategy.BLOCK:
            async with asyncio.TaskGroup() as task_group:
                for channel in targets:
                    task_group.create_task(channel.queue.put(event))
            return
        for channel in targets:
            self._put_without_blocking(channel, event)

    def _put_without_blocking(self, channel: _Channel, event: Event) -> None:
        """Enqueue ``event`` on ``channel`` without awaiting, applying a drop strategy."""
        try:
            channel.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        if self._backpressure is BackpressureStrategy.DROP_OLDEST:
            # The queue is full (maxsize > 0), so exactly one slot frees on eviction;
            # nothing awaits between these calls, so the put cannot fail.
            self._record_drop(channel.queue.get_nowait())
            channel.queue.put_nowait(event)
        else:  # REPORT_AND_DROP
            self._record_drop(event)

    def _record_drop(self, event: Event) -> None:
        """Account for and report a single dropped event."""
        self.dropped += 1
        _log.warning(
            "queue.dropped",
            event_id=str(event.id),
            source=event.source,
            type=event.type,
            strategy=self._backpressure.value,
        )
