"""The ``Engine``: the single embeddable entry point that assembles the pipeline.

See ``MODULE_SPECIFICATIONS.md`` §11. The Engine wires the core-layer components built in
tasks 1.1-1.3 — the ``EventBus``, the ``TriggerEngine``, the ``Scheduler``, and (through it)
the ``Executor`` — into one running instance, used identically by the CLI, an embedding host
(``from swatch import Engine``), and later the daemon. Source Adapters are **injected**
rather than constructed here: the Engine lives in the Core layer and the ``FilesystemAdapter``
lives in the Adapters layer, and the core imports nothing from ``adapters/`` (ADR-0010
Option A). The Engine therefore depends only on the ``SourceAdapter`` port (``core/ports.py``)
and the caller (the CLI) supplies concrete adapters. The spec's illustrative
``__init__(self, config)`` is preserved as the primary signature; ``sources`` is a
keyword-only extension carrying that injection.

Lifecycle (``EXECUTION_MODEL.md`` §7.1):

- :meth:`run` starts the injected sources, subscribes the ``TriggerEngine`` to the bus, pumps
  every source's events into the bus, and blocks until a shutdown is requested — then drains.
- :meth:`shutdown` requests a **graceful drain**: sources stop admitting, in-flight ``Run``s
  finish to their terminal state, and ``run`` returns. ``shutdown(force=True)`` (the second
  ``SIGINT``) escalates — in-flight ``Run``s are cancelled to ``CANCELLED`` (each terminating
  its subprocess group via the Executor, so no orphan) and ``run`` returns immediately.

A single ``Run``'s failure never crosses into the Engine loop (``EXECUTION_MODEL.md`` §9); it
is contained and recorded by the Scheduler, and matching continues.

Layer: Core. ``core.engine`` imports only core-layer modules (``config``, ``events``,
``ports``, ``scheduler``, ``triggers``) and the Core-sibling ``execution`` transitively via
the Scheduler — the layering (``cli:tui > plugins > adapters > config > core:execution:...``)
stays intact.
"""

import asyncio
from collections.abc import Sequence
from contextlib import suppress

import structlog

from swatch.core.config import SwatchConfig
from swatch.core.events import BackpressureStrategy, EventBus
from swatch.core.ports import SourceAdapter
from swatch.core.reporting import RunReporter
from swatch.core.scheduler import Run, Scheduler
from swatch.core.triggers import TriggerEngine, TriggerFired

_log = structlog.get_logger(__name__)

# How long :meth:`Engine.run` waits for the TriggerEngine to subscribe to the bus before it
# starts pumping, so no early event is published into a bus with no matcher listening. The
# subscription happens on the engine coroutine's first step; this is a generous safety cap.
_SUBSCRIBE_TRIES = 400
_SUBSCRIBE_DELAY_S = 0.005


class EngineStartupError(RuntimeError):
    """A Source Adapter failed to initialize, so the Engine could not start.

    An operational startup failure unrelated to workflow logic — the CLI maps it to exit
    code 4 (``EXECUTION_MODEL.md`` §7.2), distinct from a config error (2) or a workflow
    failure (1).
    """


class Engine:
    """Assemble and run the ScorpioWatch pipeline from a validated configuration.

    The single public embeddable entry point (``from swatch import Engine``): it owns an
    ``EventBus``, a ``TriggerEngine`` registered with ``config.triggers``, and a ``Scheduler``
    driving an ``Executor``, and it consumes injected ``SourceAdapter``s. :meth:`run` blocks
    until :meth:`shutdown` is requested; both are safe to call across the CLI's signal
    handlers.
    """

    def __init__(
        self,
        config: SwatchConfig,
        *,
        sources: Sequence[SourceAdapter] = (),
        max_parallel: int = 4,
        bus_maxsize: int = 256,
        reporter: RunReporter | None = None,
    ) -> None:
        """Create an Engine for ``config`` fed by ``sources``.

        Args:
            config: The validated configuration whose triggers arm the ``TriggerEngine``.
            sources: The Source Adapters to consume events from (injected to preserve the
                core↔adapters layering). Empty yields an inert engine that matches nothing.
            max_parallel: Upper bound on concurrently-executing runs (Scheduler bound).
            bus_maxsize: Per-subscriber bound on the ``EventBus`` queues.
            reporter: Optional :class:`~swatch.core.reporting.RunReporter` the Scheduler
                narrates run lifecycle and subprocess output to (the CLI's output layer).
        """
        self._config = config
        self._sources: tuple[SourceAdapter, ...] = tuple(sources)
        self._bus = EventBus(maxsize=bus_maxsize, backpressure=BackpressureStrategy.BLOCK)
        self._matcher = TriggerEngine()
        for trigger in config.triggers:
            self._matcher.register(trigger)
        self._scheduler = Scheduler(max_parallel=max_parallel, reporter=reporter)
        self._drain = asyncio.Event()
        self._force = asyncio.Event()
        self._once = False

    @property
    def scheduler(self) -> Scheduler:
        """The Scheduler driving this Engine's runs."""
        return self._scheduler

    @property
    def bus(self) -> EventBus:
        """The EventBus every source publishes to and the matcher consumes from."""
        return self._bus

    @property
    def records(self) -> tuple[Run, ...]:
        """Every run that has reached a terminal state, in completion order."""
        return self._scheduler.records

    async def run(self, *, once: bool = False) -> None:
        """Start the pipeline and block until a shutdown is requested, then drain.

        Starts every injected source, subscribes the ``TriggerEngine`` to the bus, and pumps
        source events into it. In continuous mode this returns only after :meth:`shutdown`;
        with ``once=True`` it returns after the first matched event has been admitted and its
        run(s) have drained — the batch mode the CI/`--once` context exits on
        (``EXECUTION_MODEL.md`` §7).

        Args:
            once: Process the first matching event batch, then drain and return.

        Raises:
            EngineStartupError: If a Source Adapter fails to initialize.
        """
        self._once = once
        await self._start_sources()
        match_task = asyncio.create_task(self._matcher.evaluate(self._bus, self._on_fired))
        await self._wait_until_subscribed(match_task)
        pump_tasks = [asyncio.create_task(self._pump(source)) for source in self._sources]
        _log.info(
            "engine.started",
            triggers=len(self._config.triggers),
            sources=[source.name for source in self._sources],
            once=once,
        )
        try:
            await self._drain.wait()
        finally:
            await self._finalize(match_task, pump_tasks)

    async def shutdown(self, *, force: bool = False) -> None:
        """Request shutdown: a graceful drain, or an immediate cancel when ``force``.

        Idempotent and safe to call from a signal handler while :meth:`run` is blocked. The
        first call begins the drain; a ``force=True`` call (the second ``SIGINT``) escalates
        in-flight runs to cancellation (``EXECUTION_MODEL.md`` §7.1).

        Args:
            force: Escalate to immediate cancellation of in-flight runs.
        """
        self._drain.set()
        if force:
            self._force.set()

    async def _start_sources(self) -> None:
        """Start every source, rolling back and surfacing a startup error on any failure."""
        started: list[SourceAdapter] = []
        for source in self._sources:
            try:
                await source.start()
            except Exception as error:
                for done in started:
                    with suppress(Exception):
                        await done.stop()
                raise EngineStartupError(
                    f"source {source.name!r} failed to start: {error}"
                ) from error
            started.append(source)

    async def _on_fired(self, fired: TriggerFired) -> None:
        """The ``TriggerEngine`` sink: admit the fire, and end a ``--once`` run after the first."""
        await self._scheduler.submit(fired)
        if self._once:
            self._drain.set()

    async def _pump(self, source: SourceAdapter) -> None:
        """Forward every event from ``source`` onto the bus until the source's stream ends."""
        async for event in source.events():
            await self._bus.publish(event)

    async def _wait_until_subscribed(self, match_task: asyncio.Task[None]) -> None:
        """Wait for the matcher to subscribe (or die) before pumping, to not miss early events."""
        for _ in range(_SUBSCRIBE_TRIES):
            if self._bus.subscriber_count >= 1 or match_task.done():
                return
            await asyncio.sleep(_SUBSCRIBE_DELAY_S)

    async def _finalize(
        self, match_task: asyncio.Task[None], pump_tasks: list[asyncio.Task[None]]
    ) -> None:
        """Stop sources, drain (or force-cancel) in-flight runs, and tear down the matcher."""
        for source in self._sources:
            with suppress(Exception):
                await source.stop()
        for pump_task in pump_tasks:
            with suppress(asyncio.CancelledError):
                await pump_task
        await self._drain_runs()
        match_task.cancel()
        with suppress(asyncio.CancelledError):
            await match_task
        _log.info("engine.stopped", runs=len(self._scheduler.records))

    async def _drain_runs(self) -> None:
        """Let in-flight runs finish, escalating to cancellation if a force stop is requested."""
        if self._force.is_set():
            await self._scheduler.stop()
            return
        drain = asyncio.create_task(self._await_drain())
        force = asyncio.create_task(self._await_force())
        done, _pending = await asyncio.wait({drain, force}, return_when=asyncio.FIRST_COMPLETED)
        if force in done and drain not in done:
            # A second SIGINT arrived mid-drain: escalate to immediate cancellation (§7.1).
            await self._scheduler.stop()
        force.cancel()
        with suppress(asyncio.CancelledError):
            await force
        await drain

    async def _await_drain(self) -> None:
        """Wait for the Scheduler to drain every in-flight run to a terminal state."""
        await self._scheduler.drain()

    async def _await_force(self) -> None:
        """Complete once an immediate-cancel (force) shutdown has been requested."""
        await self._force.wait()
