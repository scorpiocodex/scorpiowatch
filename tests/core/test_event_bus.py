"""Behavioral and property tests for the bounded, backpressure-aware ``EventBus``."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from watchflow.core.events import BackpressureStrategy, Event, EventBus

DROP_STRATEGIES = [BackpressureStrategy.DROP_OLDEST, BackpressureStrategy.REPORT_AND_DROP]


def make_event(index: int = 0, *, kind: str = "modified") -> Event:
    """Build a valid ``Event`` carrying ``index`` in its payload for identification."""
    return Event(
        id=uuid4(),
        source="filesystem",
        type=kind,
        payload={"i": index},
        timestamp=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    )


# --------------------------------------------------------------------------- #
# Example tests: delivery, filtering, each strategy, backpressure, validation. #
# --------------------------------------------------------------------------- #


async def test_publish_then_receive() -> None:
    bus = EventBus(maxsize=4, backpressure=BackpressureStrategy.REPORT_AND_DROP)
    sub = bus.subscribe()
    await bus.publish(make_event(1))
    received = await anext(sub)
    assert received.payload["i"] == 1
    await sub.aclose()


async def test_topic_filter_delivers_only_matching_types() -> None:
    bus = EventBus(maxsize=4, backpressure=BackpressureStrategy.REPORT_AND_DROP)
    sub = bus.subscribe(topic="modified")
    await bus.publish(make_event(1, kind="created"))  # filtered out
    await bus.publish(make_event(2, kind="modified"))  # delivered
    received = await anext(sub)
    assert received.type == "modified"
    assert received.payload["i"] == 2
    await sub.aclose()


async def test_publish_with_no_subscribers_is_a_noop() -> None:
    bus = EventBus(maxsize=2, backpressure=BackpressureStrategy.REPORT_AND_DROP)
    await bus.publish(make_event(1))
    assert bus.dropped == 0
    assert bus.subscriber_count == 0


async def test_report_and_drop_counts_drops_and_keeps_earliest() -> None:
    bus = EventBus(maxsize=1, backpressure=BackpressureStrategy.REPORT_AND_DROP)
    sub = bus.subscribe()
    for i in range(3):
        await bus.publish(make_event(i))
    assert bus.dropped == 2  # only the first fit; the next two were rejected
    first = await anext(sub)
    assert first.payload["i"] == 0
    await sub.aclose()


async def test_drop_oldest_evicts_oldest_and_keeps_newest() -> None:
    bus = EventBus(maxsize=2, backpressure=BackpressureStrategy.DROP_OLDEST)
    sub = bus.subscribe()
    for i in range(5):
        await bus.publish(make_event(i))
    assert bus.dropped == 3
    got = [await anext(sub), await anext(sub)]
    assert [e.payload["i"] for e in got] == [3, 4]
    await sub.aclose()


async def test_block_applies_backpressure_until_drained() -> None:
    bus = EventBus(maxsize=1, backpressure=BackpressureStrategy.BLOCK)
    sub = bus.subscribe()
    await bus.publish(make_event(0))  # queue is now full
    pending = asyncio.create_task(bus.publish(make_event(1)))
    await asyncio.sleep(0)  # give the second publish a chance to run
    assert not pending.done()  # it is blocked on the full queue
    first = await anext(sub)  # free a slot
    await pending  # the blocked publish now completes
    second = await anext(sub)
    assert [first.payload["i"], second.payload["i"]] == [0, 1]
    assert bus.dropped == 0
    await sub.aclose()


async def test_fanout_delivers_to_every_subscriber() -> None:
    bus = EventBus(maxsize=4, backpressure=BackpressureStrategy.BLOCK)
    subs = [bus.subscribe() for _ in range(3)]
    await bus.publish(make_event(7))
    for sub in subs:
        received = await anext(sub)
        assert received.payload["i"] == 7
    for sub in subs:
        await sub.aclose()


async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus(maxsize=4, backpressure=BackpressureStrategy.REPORT_AND_DROP)
    sub = bus.subscribe()
    await bus.publish(make_event(0))
    assert (await anext(sub)).payload["i"] == 0
    await sub.aclose()  # unsubscribe
    assert bus.subscriber_count == 0
    await bus.publish(make_event(1))  # no subscriber remains to receive it
    assert bus.subscriber_count == 0
    assert bus.dropped == 0  # the absence of a subscriber is not a drop


def test_maxsize_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        EventBus(maxsize=0, backpressure=BackpressureStrategy.BLOCK)
    with pytest.raises(ValueError, match="positive"):
        EventBus(maxsize=-1, backpressure=BackpressureStrategy.BLOCK)


# --------------------------------------------------------------------------- #
# Property tests: the invariants the spec requires, proven across many inputs. #
# --------------------------------------------------------------------------- #


@settings(max_examples=50)
@given(
    maxsize=st.integers(min_value=1, max_value=32),
    count=st.integers(min_value=0, max_value=200),
    strategy=st.sampled_from(DROP_STRATEGIES),
)
def test_prop_bounded_and_no_silent_loss(
    maxsize: int, count: int, strategy: BackpressureStrategy
) -> None:
    # Under any drop strategy: the queue never exceeds maxsize, and every published
    # event is either still queued or explicitly accounted as dropped — never silent.
    async def scenario() -> None:
        bus = EventBus(maxsize=maxsize, backpressure=strategy)
        sub = bus.subscribe()  # registered but intentionally left unconsumed
        try:
            for i in range(count):
                await bus.publish(make_event(i))
            (channel,) = bus._channels
            pending = channel.queue.qsize()
            assert pending <= maxsize
            assert pending + bus.dropped == count
        finally:
            await sub.aclose()

    asyncio.run(scenario())


@settings(max_examples=50)
@given(
    maxsize=st.integers(min_value=1, max_value=16),
    count=st.integers(min_value=0, max_value=64),
)
def test_prop_drop_oldest_retains_the_newest_events(maxsize: int, count: int) -> None:
    # DROP_OLDEST retains exactly the newest `maxsize` events, in order, and the
    # dropped count exactly covers the remainder.
    async def scenario() -> None:
        bus = EventBus(maxsize=maxsize, backpressure=BackpressureStrategy.DROP_OLDEST)
        sub = bus.subscribe()
        try:
            for i in range(count):
                await bus.publish(make_event(i))
            (channel,) = bus._channels
            retained = []
            while not channel.queue.empty():
                retained.append(channel.queue.get_nowait().payload["i"])
            assert retained == list(range(max(0, count - maxsize), count))
            assert bus.dropped == max(0, count - maxsize)
            assert len(retained) + bus.dropped == count
        finally:
            await sub.aclose()

    asyncio.run(scenario())


@settings(max_examples=50)
@given(
    subscribers=st.integers(min_value=1, max_value=5),
    count=st.integers(min_value=0, max_value=40),
)
def test_prop_fanout_delivers_every_event_to_every_subscriber(subscribers: int, count: int) -> None:
    # With capacity for every event, each independent subscriber queue receives every
    # published event and nothing is dropped.
    async def scenario() -> None:
        bus = EventBus(maxsize=max(1, count), backpressure=BackpressureStrategy.REPORT_AND_DROP)
        subs = [bus.subscribe() for _ in range(subscribers)]
        try:
            for i in range(count):
                await bus.publish(make_event(i))
            assert bus.subscriber_count == subscribers
            for channel in bus._channels:
                assert channel.queue.qsize() == count
            assert bus.dropped == 0
        finally:
            for sub in subs:
                await sub.aclose()

    asyncio.run(scenario())
