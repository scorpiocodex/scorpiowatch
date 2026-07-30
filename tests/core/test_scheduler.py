"""Behavioral tests for the v0.1.0 ``Scheduler`` seam (MODULE_SPECIFICATIONS §4).

Real components throughout — a real ``Scheduler`` driving a real ``Executor`` running real
subprocesses (``sys.executable``) — with only a tiny stand-in Executor used to exercise the
failure-isolation path. Liveness is proven with a heartbeat file, never a PID probe.
"""

import asyncio
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from watchflow.core.events import BackpressureStrategy, Event, EventBus
from watchflow.core.scheduler import Run, Scheduler
from watchflow.core.triggers import GlobMatch, Trigger, TriggerEngine, TriggerFired
from watchflow.core.workflow import Step, Workflow
from watchflow.execution.executor import RunContext, RunResult, RunState

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


def make_trigger(workflow: Workflow, *, name: str = "t", pattern: str = "**/*") -> Trigger:
    """Build a glob Trigger bound to ``workflow``."""
    return Trigger(
        name=name, source="filesystem", match=GlobMatch(pattern=pattern), workflow=workflow
    )


def make_fired(workflow: Workflow, *, name: str = "t") -> TriggerFired:
    """Build a TriggerFired for ``workflow``."""
    return TriggerFired(trigger=make_trigger(workflow, name=name), event=make_event())


async def _read(path: Path) -> str:
    """Read ``path`` off the event loop (ASYNC240-clean)."""
    return await asyncio.to_thread(path.read_text)


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
# The seam: a TriggerFired runs its Workflow on the Executor.                  #
# --------------------------------------------------------------------------- #


async def test_trigger_fired_runs_the_workflow(tmp_path: Path) -> None:
    out = tmp_path / "ran.txt"
    scheduler = Scheduler()
    run = await scheduler.admit(make_fired(Workflow(name="wf", steps=[_writer_step("w", out)])))
    assert run is not None
    await scheduler.drain()
    assert await _read(out) == "done"
    assert run.state is RunState.SUCCEEDED
    assert [r.state for r in scheduler.records] == [RunState.SUCCEEDED]


async def test_admit_returns_a_run_handle(tmp_path: Path) -> None:
    fired = make_fired(Workflow(name="wf", steps=[_writer_step("w", tmp_path / "x")]), name="my-t")
    scheduler = Scheduler()
    run = await scheduler.admit(fired)
    assert isinstance(run, Run)
    assert run.trigger_name == "my-t"
    assert run.workflow_name == "wf"
    await scheduler.drain()


async def test_submit_runs_workflow_end_to_end_through_the_bus(tmp_path: Path) -> None:
    # TriggerEngine -> EventBus -> Scheduler.submit (the on_fired sink) -> Executor.
    out = tmp_path / "e2e.txt"
    bus = EventBus(maxsize=16, backpressure=BackpressureStrategy.BLOCK)
    engine = TriggerEngine()
    engine.register(
        make_trigger(Workflow(name="wf", steps=[_writer_step("w", out)]), pattern="**/*.py")
    )
    scheduler = Scheduler()
    engine_task = asyncio.create_task(engine.evaluate(bus, scheduler.submit))
    try:
        await _wait_until(lambda: bus.subscriber_count == 1)  # engine has subscribed
        await bus.publish(make_event("src/api.py"))
        await _wait_for_file(out)  # the fired trigger drove a run end to end
        assert await _read(out) == "done"
    finally:
        engine_task.cancel()
        with suppress(asyncio.CancelledError):
            await engine_task
        await scheduler.stop()


async def test_failing_workflow_is_recorded_failed(tmp_path: Path) -> None:
    wf = Workflow(name="wf", steps=[Step(name="boom", command=_py("import sys; sys.exit(2)"))])
    scheduler = Scheduler()
    run = await scheduler.admit(make_fired(wf))
    await scheduler.drain()
    assert run is not None
    assert run.state is RunState.FAILED
    assert scheduler.records[0].state is RunState.FAILED


# --------------------------------------------------------------------------- #
# Concurrency model: bounded-concurrent, max_parallel runs at once.           #
# --------------------------------------------------------------------------- #


async def test_multiple_fired_triggers_all_run(tmp_path: Path) -> None:
    scheduler = Scheduler(max_parallel=3)
    outs = [tmp_path / f"r{i}.txt" for i in range(5)]
    for out in outs:
        await scheduler.submit(make_fired(Workflow(name="wf", steps=[_writer_step("w", out)])))
    await scheduler.drain()
    for out in outs:
        assert await _read(out) == "done"
    assert len(scheduler.records) == 5
    assert all(r.state is RunState.SUCCEEDED for r in scheduler.records)


async def test_max_parallel_one_serializes_runs(tmp_path: Path) -> None:
    # With max_parallel=1 the two runs must not overlap in time (wall-clock spans disjoint).
    scheduler = Scheduler(max_parallel=1)
    assert scheduler.max_parallel == 1
    span_code = (
        "import sys, time\n"
        "open(sys.argv[1], 'w').write(str(time.time()))\n"
        "time.sleep(0.3)\n"
        "open(sys.argv[2], 'w').write(str(time.time()))\n"
    )
    spans = [(tmp_path / f"s{i}.txt", tmp_path / f"e{i}.txt") for i in range(2)]
    for start, end in spans:
        step = Step(name="w", command=_py(span_code, str(start), str(end)))
        await scheduler.submit(make_fired(Workflow(name="wf", steps=[step])))
    await scheduler.drain()
    intervals = sorted([(float(await _read(s)), float(await _read(e))) for s, e in spans])
    assert intervals[0][1] <= intervals[1][0]  # first run ended before the second started


# --------------------------------------------------------------------------- #
# Cancellation on stop: recorded CANCELLED, no orphaned subprocess (§7.1/§9).  #
# --------------------------------------------------------------------------- #


async def test_stop_cancels_in_flight_records_cancelled_no_orphan(tmp_path: Path) -> None:
    hb = tmp_path / "hb.txt"
    wf = Workflow(name="wf", steps=[Step(name="beat", command=_py(_HEARTBEAT_SRC, str(hb)))])
    scheduler = Scheduler()
    run = await scheduler.admit(make_fired(wf))
    assert run is not None
    await _wait_for_file(hb)  # the subprocess is alive
    await scheduler.stop()
    assert run.state is RunState.CANCELLED
    assert scheduler.records[0].state is RunState.CANCELLED
    assert scheduler.in_flight == 0
    assert await _heartbeat_is_frozen(hb), "subprocess survived scheduler stop (orphan)"


async def test_unexpected_executor_error_is_contained_as_failed(tmp_path: Path) -> None:
    # A run failure never crashes the Scheduler; it is recorded FAILED (§9).
    class _BoomExecutor:
        async def run(self, workflow: Workflow, ctx: RunContext | None = None) -> RunResult:
            raise RuntimeError("kaboom")

    scheduler = Scheduler(executor=_BoomExecutor())  # type: ignore[arg-type]
    run = await scheduler.admit(make_fired(Workflow(name="wf")))
    await scheduler.drain()
    assert run is not None
    assert run.state is RunState.FAILED
    assert scheduler.records[0].state is RunState.FAILED


# --------------------------------------------------------------------------- #
# Deferred §4 controls are stubbed with versions, not silently omitted.        #
# --------------------------------------------------------------------------- #


async def test_admission_controls_are_deferred_every_fire_runs(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    append = "import sys; open(sys.argv[1], 'a').write('x')"
    wf = Workflow(name="wf", steps=[Step(name="w", command=_py(append, str(counter)))])
    scheduler = Scheduler(default_cooldown_ms=5000)
    # The cooldown window is stored per §4, but no control is active yet in v0.1.0.
    assert scheduler.default_cooldown_ms == 5000
    fired = make_fired(wf)
    assert scheduler._admission_blocked(fired) is False
    # Two identical fires within the cooldown window both admit and run (no dedupe/cooldown).
    r1 = await scheduler.admit(fired)
    r2 = await scheduler.admit(fired)
    await scheduler.drain()
    assert r1 is not None
    assert r2 is not None
    assert await _read(counter) == "xx"
    assert len(scheduler.records) == 2


def test_max_parallel_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_parallel"):
        Scheduler(max_parallel=0)
