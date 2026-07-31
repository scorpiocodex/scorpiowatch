"""``swatch run`` — assemble the full pipeline and run it in the foreground.

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

from swatch.adapters.filesystem import FilesystemAdapter
from swatch.cli.console import err_console
from swatch.cli.exit_codes import ExitCode
from swatch.cli.log import configure_logging
from swatch.cli.reporter import OutputMode, RunReporter
from swatch.config.loader import ConfigError, load
from swatch.core.config import SwatchConfig
from swatch.core.engine import Engine, EngineStartupError
from swatch.core.scheduler import Run
from swatch.execution.executor import RunState

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
            help="Directory to watch (also where swatch.toml is discovered).",
        ),
    ] = Path("."),
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to swatch.toml (default: PATH/swatch.toml)."),
    ] = None,
    once: Annotated[
        bool,
        typer.Option("--once", help="Process the first matching event batch, then exit."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show full engine records and stream all output."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Only the final tally, plus output of any failure."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Machine output: one JSON event per line, output included."),
    ] = False,
) -> None:
    """Start the engine: watch PATH and run each Trigger's Workflow on a match."""
    mode = _resolve_mode(verbose=verbose, quiet=quiet, as_json=as_json)
    configure_logging(verbose=mode is OutputMode.VERBOSE)
    reporter = RunReporter(mode)
    config_path = config if config is not None else path / "swatch.toml"

    try:
        cfg = load(config_path)
    except ConfigError as error:
        _render_config_error(error, config_path)
        raise typer.Exit(int(ExitCode.CONFIG_ERROR)) from error

    _default_step_cwds(cfg, path.resolve())
    reporter.banner(cfg, path, config_path, once=once)

    try:
        code = asyncio.run(_serve(cfg, path, reporter, once=once))
    except EngineStartupError as error:
        err_console.print(f"  [failure]✗ startup error[/failure]  {error}")
        raise typer.Exit(int(ExitCode.STARTUP_ERROR)) from error

    raise typer.Exit(code)


def _default_step_cwds(cfg: SwatchConfig, root: Path) -> None:
    """Resolve every step's working directory against the watched project ``root``.

    A step's ``cwd`` is where its subprocess runs. Left unset, ``create_subprocess_exec``
    inherits the *invocation* directory — so a freshly-init'd config would run pytest wherever
    ``swatch`` was launched, not in the project it watches (the real-project finding). This
    points an unset cwd at the watched root, and resolves a relative cwd against it, always to
    an absolute path — so even a relative ``run`` PATH lands the subprocess in the right place
    regardless of where the command was invoked. An explicit cwd always wins over the default;
    only its resolution to absolute (against the root) is applied — matching the project-root
    reference of ``EXECUTION_MODEL.md`` §6.

    Mutates ``cfg`` in place: it is a fresh per-invocation config, and where the loader shares
    one Workflow across several expanded Triggers the resolved cwd is identical for each, so
    re-resolving an already-absolute cwd is a harmless no-op.
    """
    for trigger in cfg.triggers:
        for step in trigger.workflow.steps:
            step.cwd = _resolve_cwd(step.cwd, root)


def _resolve_cwd(cwd: Path | None, root: Path) -> Path:
    """The absolute directory a step runs in: the watched ``root`` unless the step set its own."""
    if cwd is None:
        return root
    if cwd.is_absolute():
        return cwd
    return (root / cwd).resolve()


def _resolve_mode(*, verbose: bool, quiet: bool, as_json: bool) -> OutputMode:
    """Pick the output mode from the flags (``--json`` wins; ``--verbose``/``--quiet`` exclusive).

    Raises:
        typer.BadParameter: If both ``--verbose`` and ``--quiet`` are given (a usage error →
            exit 3), since they ask for opposite amounts of output.
    """
    if as_json:
        return OutputMode.JSON
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")
    if verbose:
        return OutputMode.VERBOSE
    if quiet:
        return OutputMode.QUIET
    return OutputMode.DEFAULT


async def _serve(cfg: SwatchConfig, path: Path, reporter: RunReporter, *, once: bool) -> int:
    """Run the Engine to completion under signal handling; return the §7.2 exit code."""
    adapter = FilesystemAdapter(path, debounce_ms=_DEBOUNCE_MS, step_ms=_STEP_MS)
    engine = Engine(cfg, sources=[adapter], reporter=reporter)
    signals = _ShutdownSignals(engine)
    _install_signal_handlers(asyncio.get_running_loop(), signals.on_signal)
    await engine.run(once=once)
    reporter.summary(engine.records)
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


def _render_config_error(error: ConfigError, config_path: Path) -> None:
    """Render a config failure as a clean ``✗ config error`` block, never a traceback."""
    err_console.print()
    err_console.print("  [failure]✗ config error[/failure]")
    err_console.print()
    err_console.print(f"    {error.path or config_path}")
    err_console.print(f"    {error}")
    err_console.print()
    err_console.print("  [muted]fix the config, then re-run.[/muted]")
