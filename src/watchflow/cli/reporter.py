"""The ``run`` output layer: one projection of the engine's two voices, four ways.

This is the single :class:`~watchflow.core.reporting.RunReporter` implementation the Scheduler
narrates to. It separates the **engine voice** (lifecycle: a run started, a run finished with
its state + duration) from the **subprocess voice** (the watched program's own stdout/stderr,
streamed from the Executor via task-1.4 part 1), and renders both per the selected
:class:`OutputMode` — one code path, four renderings (``UI_DESIGN.md`` §1.5 / ADR-0008), never
four that can disagree.

Default policy (the approved design): the engine's own chatter — UUIDs, 14-decimal durations,
argv, ``timeout_s`` — is **never** in the default human view; it lives in ``--verbose`` and
``--json``. The default view shows compact ``UI_DESIGN.md`` §4.3 lifecycle lines, a transient
single-line liveness spinner while a step runs (TTY only, revealed after a short delay so fast
steps don't flash), and — only on failure or timeout — the failing step's retained output tail
(task-1.4 part 1's bounded 256 KiB tail: the pytest summary / traceback, not the setup banner).

``print`` / ``rich`` live here by sanction (``CODING_STANDARD.md`` §6: CLI layer only).

New surface relative to ``UI_DESIGN.md`` (on the doc-sync list, not fixed here): the ``--quiet``
flag, any rendering of subprocess output in ``run``, the CLI liveness spinner, and the
``--json`` ``step.output`` events.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from rich.console import Console
from rich.markup import escape
from rich.status import Status

from watchflow.cli.console import console as _stdout_console
from watchflow.cli.console import err_console as _stderr_console
from watchflow.core.config import WatchflowConfig
from watchflow.core.scheduler import Run
from watchflow.execution.executor import OutputChunk, RunState, StepResult, StreamName

# States a step/run can end in that count as failure for the failing-tail policy.
_FAILED_STATES = frozenset({RunState.FAILED, RunState.TIMED_OUT})

# How long a step must run before its liveness spinner appears, so a sub-second run does not
# flash a spinner on and off (approved default: "only after ~1s").
_LIVENESS_DELAY_S = 1.0

# The rolling last-output line shown in the spinner is trimmed to this many characters so the
# liveness stays a single terminal row.
_LIVENESS_TAIL_CHARS = 80


class OutputMode(StrEnum):
    """How ``watchflow run`` renders its two voices (see the module docstring)."""

    DEFAULT = "default"
    VERBOSE = "verbose"
    QUIET = "quiet"
    JSON = "json"


@dataclass
class _RunView:
    """Per-run presentation state: when it started and its most recent output line."""

    run: Run
    started: float
    last_line: str = ""


def _short_id(run_id: UUID) -> str:
    """Render a run's UUID as the compact ``r_xxxx`` id of ``UI_DESIGN.md`` §4.3."""
    return f"r_{run_id.hex[:4]}"


def _fmt_duration(seconds: float) -> str:
    """Format a duration the §4.3 way: ``1.8s`` under a minute, ``3m58s`` above."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _failing_step(run: Run) -> StepResult | None:
    """The first non-succeeding step of ``run`` (whose output explains the failure), if any."""
    if run.result is None:
        return None
    for step in run.result.steps:
        if step.state in _FAILED_STATES:
            return step
    return None


class _Liveness:
    """The transient single-line spinner shown while steps run — TTY only, delayed reveal.

    Headless (a pipe, CI, or a test — ``console.is_terminal`` is false) it is inert, so no
    control codes leak into a redirected log and durable lines are all that appear. On a TTY it
    reveals a ``rich`` spinner after :data:`_LIVENESS_DELAY_S`, updates its label as output
    arrives, and is erased when the last run finishes.
    """

    def __init__(self, console: Console) -> None:
        """Bind the liveness to ``console`` (its ``is_terminal`` gates all rendering)."""
        self._console = console
        self._status: Status | None = None
        self._pending: asyncio.TimerHandle | None = None
        self._label = ""

    def ensure(self, label: str) -> None:
        """Schedule the spinner's delayed reveal if it is not already pending/shown (TTY only)."""
        self._label = label
        if not self._console.is_terminal:
            return
        if self._status is not None or self._pending is not None:
            return
        self._pending = asyncio.get_running_loop().call_later(_LIVENESS_DELAY_S, self._reveal)

    def _reveal(self) -> None:  # pragma: no cover — TTY-only rich Live glue
        """Start the spinner once the delay elapses (fires only on a real terminal)."""
        self._pending = None
        self._status = self._console.status(self._label or "running…", spinner="dots")
        self._status.start()

    def update(self, label: str) -> None:
        """Update the spinner's label to ``label`` (a no-op until it has been revealed)."""
        self._label = label
        if self._status is not None:  # pragma: no cover — TTY-only rich Live glue
            self._status.update(label)

    def stop(self) -> None:
        """Cancel a pending reveal and erase the spinner if shown (idempotent)."""
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        if self._status is not None:  # pragma: no cover — TTY-only rich Live glue
            self._status.stop()
            self._status = None


class RunReporter:
    """Render the Scheduler's run lifecycle and subprocess output per an :class:`OutputMode`.

    One instance per ``watchflow run`` invocation. Implements the
    :class:`~watchflow.core.reporting.RunReporter` port structurally; the Scheduler calls it and
    never learns which mode is active.
    """

    def __init__(
        self,
        mode: OutputMode,
        *,
        console: Console | None = None,
        err_console: Console | None = None,
    ) -> None:
        """Create a reporter rendering in ``mode``.

        Args:
            mode: Which of the four renderings to produce.
            console: Human/stdout console (defaults to the shared themed one; injectable for
                tests).
            err_console: Diagnostics/stderr console (defaults to the shared themed one).
        """
        self._mode = mode
        self._console = console if console is not None else _stdout_console
        self._err = err_console if err_console is not None else _stderr_console
        self._views: dict[UUID, _RunView] = {}
        self._liveness = _Liveness(self._console)
        self._suppressed = 0  # fires dropped by cooldown this session (surfaced in the summary)

    # -- Banner / summary (invoked directly by the run command) --------------- #

    def banner(self, cfg: WatchflowConfig, path: Path, config_path: Path, *, once: bool) -> None:
        """Print the engine-starting banner, unless the mode is machine/verdict-only."""
        if self._mode in (OutputMode.JSON, OutputMode.QUIET):
            return
        workflows = len({trigger.workflow.name for trigger in cfg.triggers})
        self._console.print()
        self._console.print("  [bold]watchflow[/bold]  ·  engine starting")
        self._console.print()
        self._console.print(
            f"  [success]✓[/success] config loaded    {config_path}  ·  "
            f"{len(cfg.triggers)} triggers, {workflows} workflows"
        )
        if once:
            self._console.print(
                f"  running once over [bold]{path}[/bold]  ·  first match, then exit"
            )
        else:
            armed = f"{len(cfg.triggers)} triggers armed"
            self._console.print(f"  watching [bold]{path}[/bold]  ·  {armed}  ·  ^C to stop")
        self._console.print()

    def summary(self, records: tuple[Run, ...]) -> None:
        """Print the final run tally (or emit it as a JSON event in ``--json``)."""
        succeeded = sum(1 for r in records if r.state is RunState.SUCCEEDED)
        failed = sum(1 for r in records if r.state in _FAILED_STATES)
        cancelled = sum(1 for r in records if r.state is RunState.CANCELLED)
        if self._mode is OutputMode.JSON:
            self._emit_json(
                {
                    "event": "run.summary",
                    "processed": len(records),
                    "succeeded": succeeded,
                    "failed": failed,
                    "cancelled": cancelled,
                    "suppressed": self._suppressed,
                }
            )
            return
        if not records:
            self._console.print("  [muted]no runs[/muted]")
            return
        # A coalesced suppressed count so a quieter run's silence is never mistaken for a
        # missed change; the per-event detail lives in --verbose / --json.
        suppressed = (
            f"  ·  [muted]{self._suppressed} suppressed[/muted]" if self._suppressed else ""
        )
        self._console.print()
        self._console.print(
            f"  processed {len(records)} run(s): "
            f"[success]{succeeded} succeeded[/success], "
            f"[failure]{failed} failed[/failure], {cancelled} cancelled{suppressed}"
        )

    # -- RunReporter port ----------------------------------------------------- #

    def run_started(self, run: Run) -> None:
        """Record the run and, in the default view, announce it and arm the liveness spinner."""
        self._views[run.run_id] = _RunView(run=run, started=time.monotonic())
        if self._mode is OutputMode.DEFAULT:
            self._console.print(
                f"  [muted]{self._now()}[/muted]  ·  {escape(run.trigger_name)}  "
                f"→ started  [run_id]{_short_id(run.run_id)}[/run_id]"
            )
            self._liveness.ensure(self._active_label())
        elif self._mode is OutputMode.JSON:
            self._emit_json(
                {
                    "event": "run.started",
                    "run_id": str(run.run_id),
                    "trigger": run.trigger_name,
                    "workflow": run.workflow_name,
                    "ts": self._iso(),
                }
            )

    async def on_output(self, run: Run, chunk: OutputChunk) -> None:
        """Route one subprocess output chunk per the active mode."""
        if self._mode is OutputMode.DEFAULT:
            view = self._views.get(run.run_id)
            if view is not None:
                self._remember_last_line(view, chunk.text)
                self._liveness.update(self._active_label())
        elif self._mode is OutputMode.VERBOSE:
            sink = self._console if chunk.stream is StreamName.STDOUT else self._err
            sink.out(chunk.text, end="", highlight=False)
        elif self._mode is OutputMode.JSON:
            self._emit_json(
                {
                    "event": "step.output",
                    "run_id": str(run.run_id),
                    "stream": chunk.stream.value,
                    "text": chunk.text,
                }
            )
        # QUIET drops output entirely; the failing-tail on failure comes from the record.

    def run_finished(self, run: Run) -> None:
        """Announce the terminal state, showing the failing output tail where the policy asks."""
        view = self._views.pop(run.run_id, None)
        if not self._views:
            self._liveness.stop()
        duration = self._duration(run, view)
        if self._mode is OutputMode.DEFAULT:
            self._console.print(
                f"  [muted]{self._now()}[/muted]  ·  {self._verdict(run, duration)}"
            )
            self._render_failing_tail(run)
        elif self._mode is OutputMode.QUIET:
            self._render_failing_tail(run)
        elif self._mode is OutputMode.JSON:
            self._emit_json(
                {
                    "event": "run.completed",
                    "run_id": str(run.run_id),
                    "status": run.state.value,
                    "duration_s": duration,
                }
            )

    def admission_suppressed(self, *, trigger_name: str, path: str, remaining_ms: int) -> None:
        """Count a cooldown suppression; emit it as a JSON event so the machine stream is complete.

        The default/quiet human views stay quiet-on-success — the count is coalesced into the
        summary; ``--verbose`` already sees the ``admission.suppressed`` structlog line, so the
        reporter adds nothing there.
        """
        self._suppressed += 1
        if self._mode is OutputMode.JSON:
            self._emit_json(
                {
                    "event": "admission.suppressed",
                    "reason": "cooldown",
                    "trigger": trigger_name,
                    "path": path,
                    "remaining_ms": remaining_ms,
                }
            )

    # -- Rendering helpers ---------------------------------------------------- #

    def _verdict(self, run: Run, duration: float) -> str:
        """The compact ``✓ succeeded`` / ``✗ failed … exit N`` verdict clause for a run."""
        name = escape(run.trigger_name)
        if run.state is RunState.SUCCEEDED:
            return f"{name}  [success]✓ succeeded[/success]  {_fmt_duration(duration)}"
        if run.state is RunState.TIMED_OUT:
            return f"{name}  [failure]✗ timed out[/failure]  {_fmt_duration(duration)}"
        if run.state is RunState.CANCELLED:
            return f"{name}  [warning]✗ cancelled[/warning]"
        step = _failing_step(run)
        exit_clause = (
            f"  exit {step.exit_code}" if step is not None and step.exit_code is not None else ""
        )
        return f"{name}  [failure]✗ failed[/failure]  {_fmt_duration(duration)}{exit_clause}"

    def _render_failing_tail(self, run: Run) -> None:
        """Print the failing step's retained output tail, framed as the program's voice."""
        if run.state not in _FAILED_STATES:
            return
        step = _failing_step(run)
        if step is None:
            return
        header = f"{run.trigger_name} · {step.step_name}"
        self._console.print(f"    [muted]── {escape(header)} — program output (tail) ──[/muted]")
        for label, body in (("stdout", step.stdout), ("stderr", step.stderr)):
            if body.strip():
                self._console.print(f"    [muted]{label}:[/muted]")
                self._console.out(self._indent(body), end="", highlight=False)
        self._console.print("    [muted]────────────────────────────────────────[/muted]")

    def _active_label(self) -> str:
        """Build the liveness label from the oldest in-flight run (name · elapsed · last line)."""
        views = list(self._views.values())
        if not views:
            return "running…"
        oldest = min(views, key=lambda v: v.started)
        elapsed = _fmt_duration(time.monotonic() - oldest.started)
        name = oldest.run.trigger_name if len(views) == 1 else f"{len(views)} runs"
        label = f"{escape(name)} · {elapsed}"
        if oldest.last_line:
            label += f" · {escape(oldest.last_line[-_LIVENESS_TAIL_CHARS:])}"
        return label

    def _duration(self, run: Run, view: _RunView | None) -> float:
        """The run's duration: the recorded value, or wall-clock since start for a cancel."""
        if run.result is not None:
            return run.result.duration_s
        return time.monotonic() - view.started if view is not None else 0.0

    def _emit_json(self, obj: dict[str, object]) -> None:
        """Write one compact JSON object as a single line (never a buffered array)."""
        self._console.print(
            json.dumps(obj, separators=(",", ":")), markup=False, highlight=False, soft_wrap=True
        )

    @staticmethod
    def _remember_last_line(view: _RunView, text: str) -> None:
        """Keep the most recent non-blank line of ``text`` for the liveness label."""
        for line in reversed(text.splitlines()):
            if line.strip():
                view.last_line = line.strip()
                return

    @staticmethod
    def _indent(body: str) -> str:
        """Indent a program-output block so it reads as nested under its header."""
        return "".join(f"      {line}\n" for line in body.splitlines())

    @staticmethod
    def _now() -> str:
        """The wall-clock time for a lifecycle line (``HH:MM:SS``)."""
        return datetime.now().strftime("%H:%M:%S")

    @staticmethod
    def _iso() -> str:
        """An ISO-8601 timestamp for a JSON lifecycle event."""
        return datetime.now().astimezone().isoformat()
