"""Integration tests for TriggerEngine.evaluate driving matching off the EventBus."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from watchflow.adapters.filesystem import FilesystemAdapter
from watchflow.core.events import BackpressureStrategy, Event, EventBus
from watchflow.core.triggers import GlobMatch, Trigger, TriggerEngine, TriggerFired
from watchflow.core.workflow import Workflow


def make_event(path: str = "src/api.py", *, source: str = "filesystem") -> Event:
    """Build a filesystem-style Event carrying ``path`` in its payload."""
    return Event(
        id=uuid4(),
        source=source,
        type="modified",
        payload={"path": path},
        timestamp=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    )


def make_trigger(pattern: str, *, name: str = "run-tests", source: str = "filesystem") -> Trigger:
    """Build a glob Trigger with a trivial Workflow."""
    return Trigger(
        name=name,
        source=source,
        match=GlobMatch(pattern=pattern),
        workflow=Workflow(name=f"{name}-wf"),
    )


async def _wait_until(
    predicate: Callable[[], bool], *, tries: int = 200, delay: float = 0.005
) -> None:
    """Poll ``predicate`` until true or fail; keeps bus-timing tests deterministic."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(delay)
    raise AssertionError("condition was not met in time")


async def _run_engine(
    engine: TriggerEngine, bus: EventBus
) -> tuple[list[TriggerFired], asyncio.Task[None]]:
    """Start ``engine.evaluate`` on ``bus`` in a task collecting into a list."""
    fired: list[TriggerFired] = []

    async def sink(event: TriggerFired) -> None:
        fired.append(event)

    task = asyncio.create_task(engine.evaluate(bus, sink))
    await _wait_until(lambda: bus.subscriber_count == 1)  # evaluate has subscribed
    return fired, task


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_matches_emit_triggerfired_and_nonmatches_do_not() -> None:
    bus = EventBus(maxsize=16, backpressure=BackpressureStrategy.BLOCK)
    engine = TriggerEngine()
    trigger = make_trigger("**/*.py")
    engine.register(trigger)
    fired, task = await _run_engine(engine, bus)
    try:
        await bus.publish(make_event("README.md"))  # non-match
        await asyncio.sleep(0.05)
        assert fired == []
        py_event = make_event("src/api.py")  # match
        await bus.publish(py_event)
        await _wait_until(lambda: len(fired) == 1)
        assert fired[0].trigger is trigger
        assert fired[0].event == py_event
    finally:
        await _stop(task)


async def test_multiple_triggers_one_event_in_registration_order() -> None:
    bus = EventBus(maxsize=16, backpressure=BackpressureStrategy.BLOCK)
    engine = TriggerEngine()
    py = make_trigger("**/*.py", name="py")
    everything = make_trigger("**/*", name="all")
    js = make_trigger("**/*.js", name="js")
    for trigger in (py, everything, js):
        engine.register(trigger)
    fired, task = await _run_engine(engine, bus)
    try:
        await bus.publish(make_event("src/api.py"))
        await _wait_until(lambda: len(fired) == 2)
        assert [f.trigger for f in fired] == [py, everything]
    finally:
        await _stop(task)


async def test_source_gating_through_the_bus() -> None:
    bus = EventBus(maxsize=16, backpressure=BackpressureStrategy.BLOCK)
    engine = TriggerEngine()
    engine.register(make_trigger("**/*.py", source="filesystem"))
    fired, task = await _run_engine(engine, bus)
    try:
        # A cron-sourced event reaches the engine off the bus, but no trigger targets it.
        await bus.publish(make_event("src/api.py", source="cron"))
        await asyncio.sleep(0.05)
        assert fired == []
    finally:
        await _stop(task)


async def test_evaluate_cancels_cleanly_and_unsubscribes() -> None:
    bus = EventBus(maxsize=8, backpressure=BackpressureStrategy.BLOCK)
    engine = TriggerEngine()
    engine.register(make_trigger("**/*.py"))
    _, task = await _run_engine(engine, bus)
    assert bus.subscriber_count == 1
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert bus.subscriber_count == 0  # the finally closed the subscription


@pytest.mark.filesystem
async def test_end_to_end_filesystem_to_engine(tmp_path: Path) -> None:
    # One stage further than the 1.1 slice: FilesystemAdapter -> EventBus -> TriggerEngine.
    bus = EventBus(maxsize=128, backpressure=BackpressureStrategy.BLOCK)
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    engine = TriggerEngine()
    engine.register(make_trigger("**/*.py"))
    fired, engine_task = await _run_engine(engine, bus)

    async def pump() -> None:
        async for event in adapter.events():
            await bus.publish(event)

    await adapter.start()
    pump_task = asyncio.create_task(pump())
    try:
        await asyncio.sleep(0.4)  # let the watch establish
        (tmp_path / "api.py").write_text("print('hi')")  # matches **/*.py
        (tmp_path / "notes.md").write_text("hello")  # does not match
        await _wait_until(lambda: any(f.event.type == "added" for f in fired))
        py_fires = [f for f in fired if Path(f.event.payload["path"]).name == "api.py"]
        assert py_fires, "expected a TriggerFired for api.py"
        assert all(Path(f.event.payload["path"]).name != "notes.md" for f in fired)
    finally:
        await adapter.stop()
        await asyncio.wait_for(pump_task, timeout=5)
        await _stop(engine_task)
