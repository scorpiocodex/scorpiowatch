"""``watchflow run`` — assemble the full pipeline and run it in the foreground.

Loads the config, constructs a ``FilesystemAdapter`` over the watched path (the CLI is the
Interface layer, so it may build concrete adapters and inject them into the core ``Engine``),
and runs until interrupted. ``SIGINT``/``SIGTERM`` drive the graceful drain of
``EXECUTION_MODEL.md`` §7.1 — the first signal drains in-flight runs, a second forces
cancellation — and the process exits with the §7.2 code the run earned.
"""

import asyncio
import signal
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer

from watchflow.adapters.filesystem import FilesystemAdapter
from watchflow.cli.console import console, err_console
from watchflow.cli.exit_codes import ExitCode
from watchflow.cli.log import configure_logging
from watchflow.config.loader import ConfigError, load
from watchflow.core.config import WatchflowConfig
from watchflow.core.engine import Engine, EngineStartupError
from watchflow.core.scheduler import Run
from watchflow.execution.executor import RunState

# Snappier than watchfiles' 1600 ms default — a dev tool should react within a fraction of a
# second — while still coalescing the burst of writes a single editor save produces.
_DEBOUNCE_MS = 400
_STEP_MS = 50

_FAILED_STATES = frozenset({RunState.FAILED, RunState.TIMED_OUT})


def run(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Directory to watch (also where watchflow.toml is discovered).",
        ),
    ] = Path("."),
    config: Annotated[
        Path | None,
        typer.Option(
            "--config", "-c", help="Path to watchflow.toml (default: PATH/watchflow.toml)."
        ),
    ] = None,
    once: Annotated[
        bool,
        typer.Option("--once", help="Process the first matching event batch, then exit."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Raise the log level to DEBUG."),
    ] = False,
) -> None:
    """Start the engine: watch PATH and run each Trigger's Workflow on a match."""
    configure_logging(verbose=verbose)
    config_path = config if config is not None else path / "watchflow.toml"

    try:
        cfg = load(config_path)
    except ConfigError as error:
        _render_config_error(error, config_path)
        raise typer.Exit(int(ExitCode.CONFIG_ERROR)) from error

    _print_banner(cfg, path, config_path, once=once)

    try:
        code = asyncio.run(_serve(cfg, path, once=once))
    except EngineStartupError as error:
        err_console.print(f"  [failure]✗ startup error[/failure]  {error}")
        raise typer.Exit(int(ExitCode.STARTUP_ERROR)) from error

    raise typer.Exit(code)


async def _serve(cfg: WatchflowConfig, path: Path, *, once: bool) -> int:
    """Run the Engine to completion under signal handling; return the §7.2 exit code."""
    adapter = FilesystemAdapter(path, debounce_ms=_DEBOUNCE_MS, step_ms=_STEP_MS)
    engine = Engine(cfg, sources=[adapter])
    signals = _ShutdownSignals(engine)
    _install_signal_handlers(asyncio.get_running_loop(), signals.on_signal)
    await engine.run(once=once)
    _print_summary(engine.records)
    return _exit_code(signals.count, engine.records)


class _ShutdownSignals:
    """Track interrupt signals and drive the Engine's two-stage shutdown (§7.1).

    The first signal begins a graceful drain; any subsequent one forces cancellation of the
    in-flight runs.
    """

    def __init__(self, engine: Engine) -> None:
        """Bind to the ``engine`` whose shutdown these signals drive."""
        self._engine = engine
        self.count = 0

    def on_signal(self, _signum: int | None = None) -> None:
        """Handle one SIGINT/SIGTERM: drain on the first, force on any subsequent."""
        self.count += 1
        if self.count == 1:
            err_console.print("  [warning]draining… press Ctrl+C again to force[/warning]")
            asyncio.ensure_future(self._engine.shutdown())  # noqa: RUF006 — fire-and-forget
        else:
            asyncio.ensure_future(self._engine.shutdown(force=True))  # noqa: RUF006


def _exit_code(signal_count: int, records: Sequence[Run]) -> int:
    """The §7.2 process exit code: SIGINT when interrupted, else the batch aggregate."""
    if signal_count > 0:
        return int(ExitCode.SIGINT)
    return int(_aggregate_exit(records))


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, handler: Callable[[int], None]
) -> None:
    """Install ``handler`` for SIGINT/SIGTERM, cross-platform.

    POSIX event loops support ``add_signal_handler``; the Windows ``ProactorEventLoop`` does
    not, so there we fall back to ``signal.signal`` and hop into the loop with
    ``call_soon_threadsafe``.
    """
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, handler, signum)
        except (NotImplementedError, RuntimeError):  # pragma: no cover — Windows ProactorEventLoop
            with suppress(ValueError, OSError):
                signal.signal(signum, lambda sig, _frame: loop.call_soon_threadsafe(handler, sig))


def _aggregate_exit(records: Sequence[Run]) -> ExitCode:
    """Map the batch's terminal run states to an exit code (``EXECUTION_MODEL.md`` §7.2)."""
    if any(record.state in _FAILED_STATES for record in records):
        return ExitCode.WORKFLOW_FAILURE
    return ExitCode.SUCCESS


def _print_banner(cfg: WatchflowConfig, path: Path, config_path: Path, *, once: bool) -> None:
    """Print the engine-starting banner (config summary + what is being watched)."""
    workflows = len({trigger.workflow.name for trigger in cfg.triggers})
    console.print()
    console.print("  [bold]watchflow[/bold]  ·  engine starting")
    console.print()
    console.print(
        f"  [success]✓[/success] config loaded    {config_path}  ·  "
        f"{len(cfg.triggers)} triggers, {workflows} workflows"
    )
    if once:
        console.print(f"  running once over [bold]{path}[/bold]  ·  first match, then exit")
    else:
        console.print(
            f"  watching [bold]{path}[/bold]  ·  {len(cfg.triggers)} triggers armed  ·  ^C to stop"
        )
    console.print()


def _print_summary(records: Sequence[Run]) -> None:
    """Print a one-line tally of the runs this invocation produced."""
    if not records:
        console.print("  [muted]no runs[/muted]")
        return
    succeeded = sum(1 for record in records if record.state is RunState.SUCCEEDED)
    failed = sum(1 for record in records if record.state in _FAILED_STATES)
    cancelled = sum(1 for record in records if record.state is RunState.CANCELLED)
    console.print()
    console.print(
        f"  processed {len(records)} run(s): "
        f"[success]{succeeded} succeeded[/success], "
        f"[failure]{failed} failed[/failure], {cancelled} cancelled"
    )


def _render_config_error(error: ConfigError, config_path: Path) -> None:
    """Render a config failure as a clean ``✗ config error`` block, never a traceback."""
    err_console.print()
    err_console.print("  [failure]✗ config error[/failure]")
    err_console.print()
    err_console.print(f"    {error.path or config_path}")
    err_console.print(f"    {error}")
    err_console.print()
    err_console.print("  [muted]fix the config, then re-run.[/muted]")
