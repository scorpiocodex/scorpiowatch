"""Behavioral tests for the ``Engine`` assembly (MODULE_SPECIFICATIONS §11).

Real components throughout — a real ``EventBus`` → ``TriggerEngine`` → ``Scheduler`` →
``Executor`` running real subprocesses (``sys.executable``). The event *source* is a small
in-memory adapter implementing the ``SourceAdapter`` port (the ``FilesystemAdapter`` is proven
separately), which keeps the assembly tests deterministic; one test drives the real
``FilesystemAdapter`` end to end (marked ``filesystem``).
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

import watchflow.core.engine as engine_mod
from watchflow.adapters.filesystem import FilesystemAdapter
from watchflow.core.config import WatchflowConfig
from watchflow.core.engine import Engine, EngineStartupError
from watchflow.core.events import Event, EventBus
from watchflow.core.triggers import GlobMatch, Trigger
from watchflow.core.workflow import Step, Workflow
from watchflow.execution.executor import RunState

_HEARTBEAT_SRC = (
    "import sys, time\n"
    "hb = sys.argv[1]\n"
    "while True:\n"
    "    open(hb, 'w').write(str(time.perf_counter()))\n"
    "    time.sleep(0.02)\n"
)


def _py(*code_and_args: str) -> list[str]:
    """Build an argv running the current interpreter with ``-c`` code (+ any extra argv)."""
    return [sys.executable, "-c", *code_and_args]


def _writer_step(name: str, path: Path, text: str = "done") -> Step:
    """A Step whose subprocess writes ``text`` to ``path``."""
    return Step(
        name=name,
        command=_py("import sys; open(sys.argv[1], 'w').write(sys.argv[2])", str(path), text),
    )


def make_event(path: str = "src/api.py", *, source: str = "filesystem") -> Event:
    """Build a filesystem-style Event carrying ``path``."""
    return Event(
        id=uuid4(),
        source=source,
        type="modified",
        payload={"path": path},
        timestamp=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    )


def make_config(*, pattern: str = "**/*.py", steps: list[Step]) -> WatchflowConfig:
    """Build a one-trigger config binding ``pattern`` to a Workflow of ``steps``."""
    workflow = Workflow(name="wf", steps=steps)
    trigger = Trigger(
        name="t", source="filesystem", match=GlobMatch(pattern=pattern), workflow=workflow
    )
    return WatchflowConfig(triggers=[trigger])


class _ScriptedSource:
    """An in-memory ``SourceAdapter`` that emits preset events, then idles until stopped."""

    name = "filesystem"

    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self._stop = asyncio.Event()
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        self._stop.set()

    async def events(self) -> AsyncIterator[Event]:
        for event in self._events:
            yield event
        await self._stop.wait()  # keep the stream open (continuous) until stopped


class _FailingSource:
    """A ``SourceAdapter`` whose ``start`` fails, to exercise the startup-error path."""

    name = "boom"

    def __init__(self) -> None:
        self.stopped = False

    async def start(self) -> None:
        raise OSError("cannot initialize")

    async def stop(self) -> None:
        self.stopped = True

    async def events(self) -> AsyncIterator[Event]:
        for _ in ():  # never yields — an empty event stream
            yield make_event()


async def _read(path: Path) -> str:
    """Read ``path`` off the event loop (ASYNC-clean)."""
    return await asyncio.to_thread(path.read_text)


async def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` off the event loop (ASYNC-clean)."""
    await asyncio.to_thread(path.write_text, text)


async def _wait_for_file(path: Path, *, tries: int = 400, delay: float = 0.02) -> None:
    """Poll until ``path`` exists, or fail."""
    for _ in range(tries):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(delay)
    raise AssertionError(f"{path} never appeared")


async def _wait_until(
    predicate: Callable[[], bool], *, tries: int = 400, delay: float = 0.02
) -> None:
    """Poll ``predicate`` until true, or fail."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(delay)
    raise AssertionError("condition was not met in time")


async def _heartbeat_is_frozen(path: Path, *, settle: float = 0.3) -> bool:
    """Return True if ``path`` stops changing over ``settle`` seconds (writer is dead)."""
    before = await asyncio.to_thread(path.read_text)
    await asyncio.sleep(settle)
    return await asyncio.to_thread(path.read_text) == before


# --------------------------------------------------------------------------- #
# Construction: the Engine arms its matcher from the config's triggers.        #
# --------------------------------------------------------------------------- #


def test_engine_registers_every_config_trigger() -> None:
    steps = [Step(name="s", command=["true"])]
    config = make_config(steps=steps)
    engine = Engine(config)
    assert len(engine._matcher.triggers) == 1
    assert engine.records == ()
    assert isinstance(engine.bus, EventBus)


async def test_wait_until_subscribed_gives_up_after_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With the retry cap at 0 the wait returns immediately rather than blocking forever if a
    # matcher never subscribes — the defensive exit arc.
    monkeypatch.setattr(engine_mod, "_SUBSCRIBE_TRIES", 0)
    engine = Engine(make_config(steps=[Step(name="s", command=["true"])]))
    dummy: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(0.01))
    await engine._wait_until_subscribed(dummy)
    await dummy


# --------------------------------------------------------------------------- #
# --once: process the first matching batch, then drain and return.            #
# --------------------------------------------------------------------------- #


async def test_run_once_executes_the_matched_workflow(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    config = make_config(pattern="**/*.py", steps=[_writer_step("w", marker)])
    engine = Engine(config, sources=[_ScriptedSource([make_event("src/api.py")])])
    await engine.run(once=True)  # returns once the first fire has drained
    assert await _read(marker) == "done"
    assert [record.state for record in engine.records] == [RunState.SUCCEEDED]


async def test_run_once_ignores_non_matching_events(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    config = make_config(pattern="**/*.py", steps=[_writer_step("w", marker)])
    # A .md event does not match; the .py event does and is the one that ends --once.
    events = [make_event("README.md"), make_event("src/api.py")]
    engine = Engine(config, sources=[_ScriptedSource(events)])
    await engine.run(once=True)
    assert [record.workflow_name for record in engine.records] == ["wf"]


async def test_run_once_records_a_failing_workflow(tmp_path: Path) -> None:
    step = Step(name="boom", command=_py("import sys; sys.exit(3)"))
    config = make_config(pattern="**/*.py", steps=[step])
    engine = Engine(config, sources=[_ScriptedSource([make_event("a.py")])])
    await engine.run(once=True)
    assert [record.state for record in engine.records] == [RunState.FAILED]


# --------------------------------------------------------------------------- #
# Continuous run: shutdown() drains; the pipeline runs matched workflows.      #
# --------------------------------------------------------------------------- #


async def test_continuous_run_drains_on_shutdown(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    config = make_config(pattern="**/*.py", steps=[_writer_step("w", marker)])
    engine = Engine(config, sources=[_ScriptedSource([make_event("src/api.py")])])
    run_task = asyncio.create_task(engine.run())
    try:
        await _wait_for_file(marker)  # the fired trigger drove a run end to end
        assert await _read(marker) == "done"
    finally:
        await engine.shutdown()
        await asyncio.wait_for(run_task, timeout=5)
    assert [record.state for record in engine.records] == [RunState.SUCCEEDED]


async def test_a_run_failure_never_stops_the_engine(tmp_path: Path) -> None:
    # A failing run is contained (§9); the engine keeps matching and a later run still works.
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    failing = Step(name="boom", command=_py("import sys; sys.exit(1)"))
    ok = _writer_step("w", second)
    config = make_config(pattern="**/*", steps=[failing])
    # Two triggers: one failing workflow, one writer workflow, both on every event.
    writer_trigger = Trigger(
        name="w",
        source="filesystem",
        match=GlobMatch(pattern="**/*"),
        workflow=Workflow(name="writer", steps=[ok]),
    )
    config.triggers.append(writer_trigger)
    engine = Engine(config, sources=[_ScriptedSource([make_event("a.py")])])
    await _write(first, "seed")
    run_task = asyncio.create_task(engine.run())
    try:
        await _wait_for_file(second)
        await _wait_until(lambda: len(engine.records) == 2)
    finally:
        await engine.shutdown()
        await asyncio.wait_for(run_task, timeout=5)
    states = {record.workflow_name: record.state for record in engine.records}
    assert states == {"wf": RunState.FAILED, "writer": RunState.SUCCEEDED}


# --------------------------------------------------------------------------- #
# Force shutdown cancels in-flight runs (§7.1) with no orphaned subprocess.    #
# --------------------------------------------------------------------------- #


async def test_force_shutdown_cancels_in_flight_no_orphan(tmp_path: Path) -> None:
    hb = tmp_path / "hb.txt"
    step = Step(name="beat", command=_py(_HEARTBEAT_SRC, str(hb)))
    config = make_config(pattern="**/*.py", steps=[step])
    engine = Engine(config, sources=[_ScriptedSource([make_event("a.py")])])
    run_task = asyncio.create_task(engine.run())
    try:
        await _wait_for_file(hb)  # the heartbeat subprocess is alive
        await engine.shutdown(force=True)
        await asyncio.wait_for(run_task, timeout=5)
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task
    assert [record.state for record in engine.records] == [RunState.CANCELLED]
    assert engine.scheduler.in_flight == 0
    assert await _heartbeat_is_frozen(hb), "subprocess survived force shutdown (orphan)"


async def test_second_shutdown_mid_drain_escalates_to_cancel(tmp_path: Path) -> None:
    # A graceful shutdown starts draining an in-flight run; a second (force) shutdown that
    # arrives *while draining* escalates it to cancellation (§7.1).
    hb = tmp_path / "hb.txt"
    step = Step(name="beat", command=_py(_HEARTBEAT_SRC, str(hb)))
    config = make_config(pattern="**/*.py", steps=[step])
    engine = Engine(config, sources=[_ScriptedSource([make_event("a.py")])])
    run_task = asyncio.create_task(engine.run())
    try:
        await _wait_for_file(hb)  # the heartbeat run is in flight
        await engine.shutdown()  # graceful: drain begins and blocks on the heartbeat
        await asyncio.sleep(0.15)  # let the drain enter its wait (past the force check)
        await engine.shutdown(force=True)  # second signal mid-drain → escalate
        await asyncio.wait_for(run_task, timeout=5)
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task
    assert [record.state for record in engine.records] == [RunState.CANCELLED]
    assert await _heartbeat_is_frozen(hb)


# --------------------------------------------------------------------------- #
# Startup errors surface as EngineStartupError (CLI exit 4).                   #
# --------------------------------------------------------------------------- #


async def test_source_start_failure_raises_startup_error() -> None:
    config = make_config(steps=[Step(name="s", command=["true"])])
    good = _ScriptedSource([])
    engine = Engine(config, sources=[good, _FailingSource()])
    with pytest.raises(EngineStartupError, match="boom"):
        await engine.run()
    assert good.stopped, "an already-started source must be rolled back on startup failure"


# --------------------------------------------------------------------------- #
# The real FilesystemAdapter, end to end through the Engine.                   #
# --------------------------------------------------------------------------- #


@pytest.mark.filesystem
async def test_engine_end_to_end_with_real_filesystem(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    config = make_config(pattern="**/*.py", steps=[_writer_step("w", marker)])
    adapter = FilesystemAdapter(tmp_path, debounce_ms=50, step_ms=10)
    engine = Engine(config, sources=[adapter])
    run_task = asyncio.create_task(engine.run(once=True))
    try:
        await asyncio.sleep(0.4)  # let the watch establish
        await _write(tmp_path / "api.py", "print('hi')")  # matches **/*.py
        await asyncio.wait_for(run_task, timeout=10)
    finally:
        if not run_task.done():
            await engine.shutdown(force=True)
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(run_task, timeout=5)
    assert await _read(marker) == "done"
    assert [record.state for record in engine.records] == [RunState.SUCCEEDED]
