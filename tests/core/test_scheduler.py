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

import swatch.core.scheduler as scheduler_mod
from swatch.core.events import BackpressureStrategy, Event, EventBus
from swatch.core.scheduler import Run, Scheduler
from swatch.core.triggers import GlobMatch, Trigger, TriggerEngine, TriggerFired
from swatch.core.workflow import Step, Workflow
from swatch.execution.executor import RunContext, RunResult, RunState

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
    # Distinct trigger names → distinct cooldown keys, so all five admit (cooldown collapses
    # re-fires of the *same* trigger+path, not distinct triggers).
    for i, out in enumerate(outs):
        wf = Workflow(name="wf", steps=[_writer_step("w", out)])
        await scheduler.submit(make_fired(wf, name=f"t{i}"))
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
    for i, (start, end) in enumerate(spans):
        step = Step(name="w", command=_py(span_code, str(start), str(end)))
        # Distinct trigger names so both runs admit (cooldown suppresses same-key re-fires).
        await scheduler.submit(make_fired(Workflow(name="wf", steps=[step]), name=f"t{i}"))
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
# Cooldown disabled (cooldown_ms=0) admits every fire; the other §4 controls stay deferred.  #
# --------------------------------------------------------------------------- #


async def test_cooldown_disabled_admits_every_fire(tmp_path: Path) -> None:
    markers = tmp_path / "markers"
    markers.mkdir()
    # Each run's subprocess drops its OWN uniquely-named marker (a fresh UUID minted inside the
    # child), so "both admitted fires executed" is proven by counting two distinct markers —
    # never by how two concurrent writes to one shared file interleave (a Windows append race).
    code = (
        "import os, sys, uuid; "
        "open(os.path.join(sys.argv[1], uuid.uuid4().hex + '.txt'), 'w').write('ran')"
    )
    wf = Workflow(name="wf", steps=[Step(name="w", command=_py(code, str(markers)))])
    scheduler = Scheduler(default_cooldown_ms=0)  # cooldown off → no suppression at all
    assert scheduler.default_cooldown_ms == 0
    fired = make_fired(wf)
    assert scheduler._admission_blocked(fired) is False
    # Two identical fires that WOULD be collapsed by cooldown both admit and run when it's off.
    r1 = await scheduler.admit(fired)
    r2 = await scheduler.admit(fired)
    await scheduler.drain()
    assert r1 is not None
    assert r2 is not None
    assert len(list(markers.iterdir())) == 2  # both distinct runs executed, order-independent
    assert len(scheduler.records) == 2
    assert all(run.state is RunState.SUCCEEDED for run in scheduler.records)


# --------------------------------------------------------------------------- #
# Leading-edge cooldown (§4): suppress redundant re-fires of one logical change.
# --------------------------------------------------------------------------- #


class _CooldownRecorder:
    """A minimal RunReporter that records cooldown suppressions (the observability seam)."""

    def __init__(self) -> None:
        self.suppressed: list[tuple[str, str, int]] = []

    def run_started(self, run: Run) -> None:
        pass

    async def on_output(self, run: Run, chunk: object) -> None:
        pass

    def run_finished(self, run: Run) -> None:
        pass

    def admission_suppressed(self, *, trigger_name: str, path: str, remaining_ms: int) -> None:
        self.suppressed.append((trigger_name, path, remaining_ms))


def _fire(wf: Workflow, *, name: str = "t", path: str = "src/api.py") -> TriggerFired:
    """A TriggerFired for ``wf`` carrying a specific trigger name and matched path."""
    return TriggerFired(trigger=make_trigger(wf, name=name), event=make_event(path=path))


async def test_cooldown_suppresses_a_burst_of_same_key_fires(tmp_path: Path) -> None:
    recorder = _CooldownRecorder()
    scheduler = Scheduler(default_cooldown_ms=5000, reporter=recorder)  # type: ignore[arg-type]
    wf = Workflow(name="wf", steps=[_writer_step("w", tmp_path / "ran.txt")])
    # Four fires of the SAME (trigger, path) within the window — one logical change re-notified.
    results = [await scheduler.admit(_fire(wf)) for _ in range(4)]
    await scheduler.drain()
    assert sum(r is not None for r in results) == 1  # only the first (leading edge) admitted
    assert sum(r is None for r in results) == 3  # the redundant re-fires suppressed
    assert len(scheduler.records) == 1
    assert [(t, p) for t, p, _ in recorder.suppressed] == [("t", "src/api.py")] * 3
    assert all(remaining > 0 for _, _, remaining in recorder.suppressed)  # window still live


async def test_cooldown_admits_distinct_paths_within_the_window(tmp_path: Path) -> None:
    scheduler = Scheduler(default_cooldown_ms=5000)
    for path in ("a.py", "b.py"):
        wf = Workflow(name="wf", steps=[_writer_step("w", tmp_path / f"{path}.done")])
        assert await scheduler.admit(_fire(wf, path=path)) is not None  # distinct key → admits
    await scheduler.drain()
    assert len(scheduler.records) == 2  # different files are different changes — both run


async def test_cooldown_admits_again_after_the_window_elapses(tmp_path: Path) -> None:
    scheduler = Scheduler(default_cooldown_ms=50)  # short window, real monotonic clock
    wf = Workflow(name="wf", steps=[_writer_step("w", tmp_path / "ran.txt")])
    assert await scheduler.admit(_fire(wf)) is not None  # first opens the window
    assert await scheduler.admit(_fire(wf)) is None  # within the window → suppressed
    await asyncio.sleep(0.15)  # the 50 ms window fully elapses
    assert await scheduler.admit(_fire(wf)) is not None  # window expired → admits again
    await scheduler.drain()
    assert len(scheduler.records) == 2  # first and third ran; the middle was suppressed


async def test_per_trigger_cooldown_ms_zero_disables_cooldown(tmp_path: Path) -> None:
    scheduler = Scheduler(default_cooldown_ms=5000)  # default on…
    wf = Workflow(name="wf", steps=[_writer_step("w", tmp_path / "ran.txt")])
    # …but this trigger sets cooldown_ms=0, opting out entirely.
    trigger = Trigger(
        name="t", source="filesystem", match=GlobMatch(pattern="**/*"), cooldown_ms=0, workflow=wf
    )
    fired = TriggerFired(trigger=trigger, event=make_event())
    assert await scheduler.admit(fired) is not None
    assert await scheduler.admit(fired) is not None  # no suppression: cooldown disabled
    await scheduler.drain()
    assert len(scheduler.records) == 2


async def test_cooldown_never_suppresses_the_first_fire(tmp_path: Path) -> None:
    # The property that makes cooldown inert under `--once`: the first (and, in --once, only)
    # fire is always outside any window, so it is admitted even with cooldown on.
    scheduler = Scheduler(default_cooldown_ms=5000)
    wf = Workflow(name="wf", steps=[_writer_step("w", tmp_path / "ran.txt")])
    assert await scheduler.admit(_fire(wf)) is not None
    await scheduler.drain()
    assert len(scheduler.records) == 1


async def test_suppressed_fire_emits_the_structlog_event(tmp_path: Path) -> None:
    from structlog.testing import capture_logs

    scheduler = Scheduler(default_cooldown_ms=5000)
    wf = Workflow(name="wf", steps=[_writer_step("w", tmp_path / "ran.txt")])
    await scheduler.admit(_fire(wf))  # first admits, opens the window
    with capture_logs() as logs:
        await scheduler.admit(_fire(wf))  # suppressed → observable, never silent (Article VIII)
    await scheduler.drain()
    suppressions = [record for record in logs if record["event"] == "admission.suppressed"]
    assert len(suppressions) == 1
    assert suppressions[0]["reason"] == "cooldown"
    assert suppressions[0]["trigger"] == "t"
    assert suppressions[0]["path"] == "src/api.py"


def test_evict_expired_prunes_expired_keys_over_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_mod, "_COOLDOWN_EVICT_THRESHOLD", 2)
    scheduler = Scheduler()
    now = 100.0
    scheduler._cooldowns = {("t", "a"): now - 1, ("t", "b"): now + 10, ("t", "c"): now - 5}
    scheduler._evict_expired(now)  # len 3 > threshold 2 → rebuild, dropping expired a and c
    assert set(scheduler._cooldowns) == {("t", "b")}


def test_evict_expired_is_a_noop_under_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_mod, "_COOLDOWN_EVICT_THRESHOLD", 2)
    scheduler = Scheduler()
    now = 100.0
    scheduler._cooldowns = {("t", "a"): now - 1, ("t", "b"): now + 10}  # len 2 == threshold
    scheduler._evict_expired(now)  # under threshold → no scan, expired 'a' retained for now
    assert set(scheduler._cooldowns) == {("t", "a"), ("t", "b")}


def test_max_parallel_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_parallel"):
        Scheduler(max_parallel=0)
