"""Tests for the FilesystemAdapter: unit translation, lifecycle, and real tmp_path I/O.

The real-filesystem tests are marked ``filesystem`` (Linux-first for v0.1.0; advisory on
macOS/Windows CI per CODING_STANDARD §4). They use an actual watch under ``tmp_path`` — no
mock — so the ingestion slice is genuinely proven end to end.
"""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import pytest
from watchfiles import Change

from swatch.adapters.filesystem import FilesystemAdapter
from swatch.core.events import BackpressureStrategy, Event, EventBus

SETTLE_S = 0.4  # proven adequate on Linux inotify and Windows for the debounce below


async def _collect(
    adapter: FilesystemAdapter, mutate: Callable[[], None], *, settle: float = SETTLE_S
) -> list[Event]:
    """Run ``mutate`` against the watched tree and return every emitted Event.

    Starts the adapter, waits for the watch to settle, applies ``mutate``, waits again
    for delivery, then stops cleanly.
    """
    collected: list[Event] = []

    async def pump() -> None:
        async for event in adapter.events():
            collected.append(event)

    await adapter.start()
    task = asyncio.create_task(pump())
    await asyncio.sleep(settle)  # let the watch establish
    mutate()
    await asyncio.sleep(settle)  # let watchfiles detect, debounce, and deliver
    await adapter.stop()
    await asyncio.wait_for(task, timeout=5)
    return collected


# --------------------------------------------------------------------------- #
# Unit tests: translation and lifecycle, no real filesystem required.          #
# --------------------------------------------------------------------------- #


def test_name_identifies_the_adapter() -> None:
    assert FilesystemAdapter("/unused").name == "filesystem"


def test_translate_maps_each_change_type() -> None:
    adapter = FilesystemAdapter("/unused")
    batch = {
        (Change.added, "/w/a.txt"),
        (Change.modified, "/w/b.txt"),
        (Change.deleted, "/w/c.txt"),
    }
    events = adapter._translate(batch)
    assert {(e.type, Path(e.payload["path"]).name) for e in events} == {
        ("added", "a.txt"),
        ("modified", "b.txt"),
        ("deleted", "c.txt"),
    }
    assert all(e.source == "filesystem" for e in events)


def test_translate_logs_and_skips_unknown_change() -> None:
    adapter = FilesystemAdapter("/unused")
    events = adapter._translate({(999, "/w/x.txt"), (Change.added, "/w/a.txt")})  # type: ignore[arg-type]
    assert [e.type for e in events] == ["added"]
    assert Path(events[0].payload["path"]).name == "a.txt"


async def test_events_before_start_is_empty() -> None:
    adapter = FilesystemAdapter("/unused")
    assert [event async for event in adapter.events()] == []


async def test_start_is_idempotent(tmp_path: Path) -> None:
    adapter = FilesystemAdapter(tmp_path)
    await adapter.start()
    first = adapter._watch
    await adapter.start()  # second call is a no-op
    assert adapter._watch is first
    await adapter.stop()


async def test_stop_is_idempotent_and_safe_before_start(tmp_path: Path) -> None:
    adapter = FilesystemAdapter(tmp_path)
    await adapter.stop()  # before start — must not raise
    await adapter.start()
    await adapter.stop()
    await adapter.stop()  # second stop — must not raise


# --------------------------------------------------------------------------- #
# Real-filesystem tests under tmp_path.                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.filesystem
async def test_create_emits_added_event(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    events = await _collect(adapter, lambda: target.write_text("hello"))
    seen = {(e.type, Path(e.payload["path"]).name) for e in events}
    assert ("added", "new.txt") in seen
    assert all(e.source == "filesystem" for e in events)


@pytest.mark.filesystem
async def test_modify_emits_modified_event(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("v1")  # created before the watch starts
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    events = await _collect(adapter, lambda: target.write_text("v2"))
    seen = {(e.type, Path(e.payload["path"]).name) for e in events}
    assert ("modified", "existing.txt") in seen


@pytest.mark.filesystem
async def test_delete_emits_deleted_event(tmp_path: Path) -> None:
    target = tmp_path / "doomed.txt"
    target.write_text("bye")  # created before the watch starts
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    events = await _collect(adapter, target.unlink)
    seen = {(e.type, Path(e.payload["path"]).name) for e in events}
    assert ("deleted", "doomed.txt") in seen


@pytest.mark.filesystem
async def test_stop_ends_the_stream_cleanly(tmp_path: Path) -> None:
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    await adapter.start()
    collected: list[Event] = []

    async def pump() -> None:
        async for event in adapter.events():
            collected.append(event)

    task = asyncio.create_task(pump())
    await asyncio.sleep(0.3)
    await adapter.stop()
    await asyncio.wait_for(task, timeout=5)  # ends on its own — no hang, no cancellation
    assert task.done()
    assert task.cancelled() is False
    assert task.exception() is None


@pytest.mark.filesystem
async def test_slice_adapter_to_bus_to_subscriber(tmp_path: Path) -> None:
    # The whole vertical slice: a filesystem change becomes an Event on the EventBus
    # that a subscriber receives.
    bus = EventBus(maxsize=128, backpressure=BackpressureStrategy.BLOCK)
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    received: list[Event] = []

    async def subscriber() -> None:
        async for event in bus.subscribe():
            received.append(event)

    async def pump() -> None:
        async for event in adapter.events():
            await bus.publish(event)

    sub_task = asyncio.create_task(subscriber())
    await adapter.start()
    pump_task = asyncio.create_task(pump())
    await asyncio.sleep(SETTLE_S)
    (tmp_path / "slice.txt").write_text("hi")
    await asyncio.sleep(SETTLE_S)
    await adapter.stop()
    await asyncio.wait_for(pump_task, timeout=5)
    await asyncio.sleep(0.05)  # let the subscriber drain the last delivered event
    sub_task.cancel()
    with suppress(asyncio.CancelledError):
        await sub_task

    seen = {(e.type, Path(e.payload["path"]).name) for e in received}
    assert ("added", "slice.txt") in seen
