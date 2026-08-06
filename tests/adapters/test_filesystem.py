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
from tests.watch_helpers import arm, names, wait_until


async def _collect(
    adapter: FilesystemAdapter, mutate: Callable[[], None], *, expect: str
) -> list[Event]:
    """Run ``mutate`` under a live watch and return every Event delivered.

    Starts the adapter, waits for the watch to arm, applies ``mutate``, then waits for an
    event naming ``expect`` before stopping — so the collector is paced by the platform's
    actual delivery latency rather than by a constant.
    """
    collected: list[Event] = []

    async def pump() -> None:
        async for event in adapter.events():
            collected.append(event)

    await adapter.start()
    task = asyncio.create_task(pump())
    try:
        await arm(adapter._root, collected)
        mutate()
        await wait_until(lambda: expect in names(collected), what=f"an event naming {expect!r}")
    finally:
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
    events = await _collect(adapter, lambda: target.write_text("hello"), expect="new.txt")
    seen = {(e.type, Path(e.payload["path"]).name) for e in events}
    assert ("added", "new.txt") in seen
    assert all(e.source == "filesystem" for e in events)


@pytest.mark.filesystem
async def test_modify_emits_a_change_event_naming_the_path(tmp_path: Path) -> None:
    # The *kind* a write to an existing file surfaces as is the backend's classification,
    # not a decision this adapter makes: `_translate` maps watchfiles' Change 1:1, which
    # `test_translate_maps_each_change_type` above pins exactly, deterministically, with no
    # filesystem involved. The backends genuinely disagree — macOS FSEvents coalesces the
    # write into the create it already had pending and reports `added`, where Linux inotify
    # reports `modified` — so asserting exactly "modified" here tested FSEvents rather than
    # ScorpioWatch. What the adapter does promise, on every platform, is that a change under
    # the watched root surfaces as an Event naming that path, with a kind describing a file
    # that still exists. That is what is asserted.
    target = tmp_path / "existing.txt"
    target.write_text("v1")  # created before the watch starts
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    events = await _collect(adapter, lambda: target.write_text("v2"), expect="existing.txt")
    kinds = {e.type for e in events if Path(e.payload["path"]).name == "existing.txt"}
    assert kinds, "no event named the file that was written to"
    assert kinds <= {"added", "modified"}, f"a write to a live file must not surface as {kinds}"
    assert all(e.source == "filesystem" for e in events)


@pytest.mark.filesystem
async def test_delete_emits_deleted_event(tmp_path: Path) -> None:
    target = tmp_path / "doomed.txt"
    target.write_text("bye")  # created before the watch starts
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    events = await _collect(adapter, target.unlink, expect="doomed.txt")
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
    # Stop a watch that is provably live, not one that may still be starting — stopping
    # a watch that never established would prove nothing about the stream ending cleanly.
    await arm(adapter._root, collected)
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
    # Arming on `received` rather than on the adapter's own output proves the whole chain
    # — adapter, bus, subscriber — is live before the change under test is applied.
    await arm(tmp_path, received)
    await asyncio.to_thread((tmp_path / "slice.txt").write_text, "hi")
    await wait_until(lambda: "slice.txt" in names(received), what="the sliced event")
    await adapter.stop()
    await asyncio.wait_for(pump_task, timeout=5)
    sub_task.cancel()
    with suppress(asyncio.CancelledError):
        await sub_task

    seen = {(e.type, Path(e.payload["path"]).name) for e in received}
    assert ("added", "slice.txt") in seen
