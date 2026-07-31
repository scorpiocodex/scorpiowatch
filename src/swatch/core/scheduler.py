"""The ``Scheduler``: the seam from a matched ``TriggerFired`` to a running Workflow.

See ``MODULE_SPECIFICATIONS.md`` §4. The Scheduler is the ``on_fired`` sink the
``TriggerEngine`` emits to (task 1.2): each ``TriggerFired`` it receives becomes a ``Run``
of the fired Trigger's Workflow, executed by the :class:`~swatch.execution.executor.Executor`
(commit 1). It decouples matching from execution — admitting a run never blocks the engine's
match loop — and owns the ``Run`` lifecycle, including recording a cancelled run as
``CANCELLED`` (``EXECUTION_MODEL.md`` §7.1).

Scope (this commit is the **seam**): §4's admission *controls* — dedupe, cooldown, and
rate limiting — are a ``v0.3.0`` deliverable (``ROADMAP.md``: "``Scheduler`` (rate limit,
dedupe, cooldown)"), and threshold/score-gated admission needs the confidence scoring that
lands in ``v0.2.0``. None of those are implemented here; they are stubbed at their single
enforcement point (:meth:`Scheduler._admission_blocked`) with their target versions, so the
seam runs every fired Trigger's Workflow while the deferred controls have an explicit,
un-silent home. Durable run recording moves to the ``EventStore`` (``storage/``) in
``v0.2.0``; until then terminal runs are recorded in memory.

Layer: Core. ``core.scheduler`` imports ``core`` (``TriggerFired``, ``Workflow``) and
``execution`` (the ``Executor``); both are Core-layer siblings the import-linter contract
joins with ``:`` (they may import each other), so the layering stays intact.
"""

import asyncio
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import structlog

from swatch.core.reporting import RunReporter
from swatch.core.triggers import Trigger, TriggerFired
from swatch.core.workflow import Workflow
from swatch.execution.executor import (
    Executor,
    OutputChunk,
    OutputSink,
    RunContext,
    RunResult,
    RunState,
)

_log = structlog.get_logger(__name__)

# On-by-default leading-edge cooldown window (§4). "Low hundreds of ms": long enough to
# collapse the redundant re-fires one logical change produces, short enough not to hold up a
# genuinely distinct edit. Source-agnostic (it stacks with, rather than replaces, a Source
# Adapter's own debounce), and tunable per trigger via ``Trigger.cooldown_ms``.
_DEFAULT_COOLDOWN_MS = 300

# The cooldown map holds only keys with a live (unexpired) window; expired entries are purged
# lazily on admission. To keep that purge off the hot path, it runs only once the map has grown
# past this many entries — normal operation (a handful of distinct paths per window) never scans.
_COOLDOWN_EVICT_THRESHOLD = 1024

# The cooldown key: which trigger, and which matched path (empty for pathless sources).
_CooldownKey = tuple[str, str]


@dataclass
class Run:
    """One admitted execution of a Workflow and its evolving lifecycle state.

    Returned by :meth:`Scheduler.admit` at admission (state ``PENDING``) and mutated in
    place as it runs, so a caller holding the ``Run`` observes its terminal state and
    result once the Scheduler has drained (``EXECUTION_MODEL.md`` §1).

    Attributes:
        run_id: Stable identifier for this run (also the Executor's ``RunContext.run_id``).
        trigger_name: The name of the Trigger whose firing admitted this run.
        workflow_name: The name of the Workflow being executed.
        state: The run's current lifecycle state.
        result: The Executor's ``RunResult`` once the run reaches a natural terminal state;
            ``None`` while pending/running or if the run was ``CANCELLED``.
    """

    run_id: UUID
    trigger_name: str
    workflow_name: str
    state: RunState = RunState.PENDING
    result: RunResult | None = field(default=None)


def _cooldown_key(fired: TriggerFired) -> _CooldownKey:
    """The cooldown key for ``fired``: its trigger name and the matched event's path.

    The path is taken from the event that *matched* the trigger (``event.payload["path"]``),
    so it is inherently the path the glob matched — not a temp file. An editor's
    write-to-temp-then-rename fires the trigger only on the events whose path matches its glob
    (the temp file's ``.tmp``/``.swp`` name does not), so the burst collapses to the one stable
    post-rename path. Pathless sources (cron, manual) yield an empty path, degrading the key to
    per-trigger.
    """
    path = fired.event.payload.get("path", "")
    return (fired.trigger.name, str(path))


class Scheduler:
    """Admit ``TriggerFired`` records and run their Workflows on the ``Executor``.

    The Scheduler is the async ``on_fired`` sink of the ``TriggerEngine`` (task 1.2):
    :meth:`submit` (fire-and-forget) and :meth:`admit` (returning the ``Run`` handle) both
    launch a background run bounded by ``max_parallel`` and return without waiting for
    execution — so a long Workflow never stalls event matching. Stopping the Scheduler
    cancels in-flight runs, which the Executor tears down to no orphaned processes, and
    records each as ``CANCELLED``.
    """

    def __init__(
        self,
        executor: Executor | None = None,
        *,
        max_parallel: int = 4,
        default_cooldown_ms: int = _DEFAULT_COOLDOWN_MS,
        reporter: RunReporter | None = None,
    ) -> None:
        """Create a Scheduler driving ``executor`` with at most ``max_parallel`` runs.

        Args:
            executor: The Executor that runs each admitted Workflow; a fresh one is created
                if omitted.
            max_parallel: Upper bound on concurrently-executing runs; must be positive.
            default_cooldown_ms: The §4 cooldown window applied to a trigger that does not set
                its own ``cooldown_ms``. Leading-edge, on by default (see
                :meth:`_admission_blocked`); pass ``0`` to disable cooldown globally.
            reporter: Optional :class:`~swatch.core.reporting.RunReporter` narrated as each
                run starts, streams output, and finishes (the CLI's output layer). ``None``
                runs silently.

        Raises:
            ValueError: If ``max_parallel`` is not positive.
        """
        if max_parallel <= 0:
            raise ValueError("Scheduler max_parallel must be positive")
        self._executor = executor if executor is not None else Executor()
        self._max_parallel = max_parallel
        self._default_cooldown_ms = default_cooldown_ms
        self._reporter = reporter
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._records: list[Run] = []
        # Cooldown state: matched key → monotonic deadline (first-fire time + window). A fire
        # is suppressed while now < deadline (leading edge, fixed window from the first fire).
        self._cooldowns: dict[_CooldownKey, float] = {}

    @property
    def max_parallel(self) -> int:
        """The configured bound on concurrently-executing runs."""
        return self._max_parallel

    @property
    def default_cooldown_ms(self) -> int:
        """The configured default cooldown window (stored; unenforced until v0.3.0)."""
        return self._default_cooldown_ms

    @property
    def in_flight(self) -> int:
        """The number of runs currently admitted and not yet terminal."""
        return len(self._tasks)

    @property
    def records(self) -> tuple[Run, ...]:
        """Every run that has reached a terminal state, in completion order."""
        return tuple(self._records)

    def _admission_blocked(self, fired: TriggerFired) -> bool:
        """Whether §4's admission controls reject ``fired``. Enforces **cooldown** in v0.1.0.

        Cooldown is a leading-edge throttle keyed on ``(trigger.name, matched_path)``: the
        first fire for a key is admitted and opens a fixed window (``window`` ms from that
        fire); a subsequent fire for the same key while the window is live is **suppressed** —
        an intentional, observable admission decision (Article VIII), logged as
        ``admission.suppressed`` and surfaced to the reporter, never a silent drop. When the
        window elapses the next fire is admitted and opens a fresh window. This is the single
        enforcement point; the still-deferred §4 controls share it:

        - **dedupe** — ``sha256(trigger.name + sorted(event.payload keys))`` (v0.3.0).
        - **rate limiting** — separate MCP vs. human/cron counters
          (``SECURITY_MODEL.md``; v0.3.0).
        - **threshold** — score-gated admission, once confidence scoring exists (v0.2.0).

        Args:
            fired: The fired Trigger under consideration.

        Returns:
            ``True`` if ``fired`` is suppressed by cooldown; otherwise ``False`` (and the
            key's window is opened/refreshed as a side effect of admission).
        """
        window_ms = self._cooldown_window_ms(fired.trigger)
        if window_ms <= 0:
            return False  # cooldown disabled for this trigger (cooldown_ms=0 or default 0)
        now = time.monotonic()
        self._evict_expired(now)
        key = _cooldown_key(fired)
        deadline = self._cooldowns.get(key)
        if deadline is not None and now < deadline:
            self._report_suppressed(fired, key, remaining_ms=round((deadline - now) * 1000))
            return True
        self._cooldowns[key] = now + window_ms / 1000.0
        return False

    def _cooldown_window_ms(self, trigger: Trigger) -> int:
        """The effective cooldown window for ``trigger``: its own, else the default."""
        return trigger.cooldown_ms if trigger.cooldown_ms is not None else self._default_cooldown_ms

    def _evict_expired(self, now: float) -> None:
        """Purge cooldown keys whose window has fully elapsed (lazy, off the hot path).

        Only rebuilds once the map is large: a live window's key must be retained to suppress
        its re-fires, so the map is naturally bounded by the distinct keys fired within one
        window — this just reclaims keys after their windows expire, under a long-running daemon.
        """
        if len(self._cooldowns) <= _COOLDOWN_EVICT_THRESHOLD:
            return
        self._cooldowns = {key: dl for key, dl in self._cooldowns.items() if dl > now}

    def _report_suppressed(
        self, fired: TriggerFired, key: _CooldownKey, *, remaining_ms: int
    ) -> None:
        """Record a cooldown suppression: a structured log line, and the reporter (for --json)."""
        _log.info(
            "admission.suppressed",
            reason="cooldown",
            trigger=fired.trigger.name,
            path=key[1],
            remaining_ms=remaining_ms,
        )
        if self._reporter is not None:
            self._reporter.admission_suppressed(
                trigger_name=fired.trigger.name, path=key[1], remaining_ms=remaining_ms
            )

    async def admit(self, fired: TriggerFired) -> Run | None:
        """Admit ``fired`` and launch its Workflow as a background run (``§4``).

        In v0.1.0 admission always succeeds (the deferred controls in
        :meth:`_admission_blocked` never reject); the run is launched bounded by
        ``max_parallel`` and this returns immediately with the ``Run`` handle, without
        waiting for execution.

        Args:
            fired: The ``TriggerFired`` to admit.

        Returns:
            The admitted ``Run``, or ``None`` once a future admission control rejects it
            (never ``None`` in v0.1.0).
        """
        if self._admission_blocked(fired):
            return (
                None  # suppressed by cooldown (§4) — an observed decision, see _report_suppressed
            )
        run = Run(
            run_id=uuid4(),
            trigger_name=fired.trigger.name,
            workflow_name=fired.trigger.workflow.name,
        )
        task = asyncio.create_task(self._execute(run, fired.trigger.workflow))
        self._tasks[run.run_id] = task
        return run

    async def submit(self, fired: TriggerFired) -> None:
        """Handle a ``TriggerFired`` as the ``TriggerEngine``'s fire-and-forget sink.

        The ``on_fired`` callback seam from task 1.2: it admits ``fired`` and discards the
        ``Run`` handle, so it can be wired directly as ``engine.evaluate(bus, scheduler.submit)``.

        Args:
            fired: The ``TriggerFired`` emitted by the engine.
        """
        await self.admit(fired)

    async def _execute(self, run: Run, workflow: Workflow) -> None:
        """Run ``workflow`` on the Executor, bounded by ``max_parallel``, recording ``run``.

        A cancellation (from :meth:`stop`) records the run ``CANCELLED`` and re-raises; any
        other execution error is contained and recorded ``FAILED`` — a run's failure never
        crashes the Scheduler (``EXECUTION_MODEL.md`` §9). Either way the run is recorded
        exactly once and removed from the in-flight set.
        """
        try:
            async with self._semaphore:
                run.state = RunState.RUNNING
                if self._reporter is not None:
                    self._reporter.run_started(run)
                result = await self._executor.run(
                    workflow,
                    RunContext(run_id=run.run_id),
                    on_output=self._output_sink(run),
                )
            run.state = result.state
            run.result = result
        except asyncio.CancelledError:
            run.state = RunState.CANCELLED
            _log.warning("run.cancelled", run_id=str(run.run_id), trigger=run.trigger_name)
            raise
        except Exception as error:
            run.state = RunState.FAILED
            _log.error(
                "run.errored", run_id=str(run.run_id), trigger=run.trigger_name, error=str(error)
            )
        finally:
            self._tasks.pop(run.run_id, None)
            self._records.append(run)
            if self._reporter is not None:
                self._reporter.run_finished(run)
            _log.info(
                "run.recorded",
                run_id=str(run.run_id),
                trigger=run.trigger_name,
                state=run.state.value,
            )

    def _output_sink(self, run: Run) -> OutputSink | None:
        """Bind the reporter's output sink to ``run`` so each chunk carries its run identity.

        Returns ``None`` when no reporter is attached, so the Executor skips the live hand-off
        entirely (still draining and retaining as ever).
        """
        reporter = self._reporter
        if reporter is None:
            return None

        async def sink(chunk: OutputChunk) -> None:
            await reporter.on_output(run, chunk)

        return sink

    async def drain(self) -> None:
        """Wait for every in-flight run to reach a terminal state (no cancellation)."""
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def stop(self) -> None:
        """Cancel every in-flight run and wait for its teardown.

        Each cancelled run terminates its subprocess and process group via the Executor
        (no orphans, commit 1) and is recorded ``CANCELLED`` (``EXECUTION_MODEL.md`` §7.1).
        Idempotent and safe to call with nothing in flight.
        """
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
