"""Integration tests for TriggerEngine.evaluate driving matching off the EventBus."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from swatch.adapters.filesystem import FilesystemAdapter
from swatch.core.events import BackpressureStrategy, Event, EventBus
from swatch.core.triggers import GlobMatch, Trigger, TriggerEngine, TriggerFired
from swatch.core.workflow import Workflow
from tests.watch_helpers import arm, wait_until


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


async def _run_engine(
    engine: TriggerEngine, bus: EventBus
) -> tuple[list[TriggerFired], asyncio.Task[None]]:
    """Start ``engine.evaluate`` on ``bus`` in a task collecting into a list."""
    fired: list[TriggerFired] = []

    async def sink(event: TriggerFired) -> None:
        fired.append(event)

    task = asyncio.create_task(engine.evaluate(bus, sink))
    await wait_until(lambda: bus.subscriber_count == 1, what="evaluate to subscribe")
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
        await wait_until(lambda: len(fired) == 1, what="the single expected fire")
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
        await wait_until(lambda: len(fired) == 2, what="both expected fires")
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

    seen: list[Event] = []

    async def pump() -> None:
        async for event in adapter.events():
            seen.append(event)  # tee, so `arm` can observe delivery without changing it
            await bus.publish(event)

    await adapter.start()
    pump_task = asyncio.create_task(pump())
    try:
        # Arming's sentinel is a `.tmp`, so it matches none of the registered `**/*.py`
        # triggers and can never appear in `fired` — it only establishes that the watch is
        # live, so neither write below can land in the pre-establishment gap and vanish.
        await arm(tmp_path, seen)
        await asyncio.to_thread((tmp_path / "api.py").write_text, "print('hi')")  # matches
        await asyncio.to_thread((tmp_path / "notes.md").write_text, "hello")  # does not match
        await wait_until(
            lambda: any(f.event.type == "added" for f in fired), what="an added fire to arrive"
        )
        py_fires = [f for f in fired if Path(f.event.payload["path"]).name == "api.py"]
        assert py_fires, "expected a TriggerFired for api.py"
        assert all(Path(f.event.payload["path"]).name != "notes.md" for f in fired)
    finally:
        await adapter.stop()
        await asyncio.wait_for(pump_task, timeout=5)
        await _stop(engine_task)
