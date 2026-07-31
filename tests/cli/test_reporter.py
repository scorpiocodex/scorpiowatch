"""Tests for the ``run`` output layer (``cli/reporter.py``): the four renderings.

The reporter is driven directly with real ``Run`` / ``StepResult`` / ``OutputChunk`` values and
an injected ``rich`` Console writing to a buffer, so each mode's rendering is asserted on the
captured text without a live terminal. The engine-voice/subprocess-voice separation, the
default quiet-on-success / loud-on-failure policy, the ``--verbose`` passthrough, the ``--quiet``
verdict-only view, and the incremental ``--json`` stream are each pinned here.
"""

import io
import json
from pathlib import Path
from uuid import uuid4

from rich.console import Console

from swatch.cli.reporter import (
    OutputMode,
    RunReporter,
    _failing_step,
    _fmt_duration,
    _Liveness,
    _short_id,
)
from swatch.core.config import SwatchConfig
from swatch.core.scheduler import Run
from swatch.execution.executor import OutputChunk, RunResult, RunState, StepResult, StreamName


def _buffer_console() -> tuple[Console, io.StringIO]:
    """A non-terminal rich Console writing to an in-memory buffer (deterministic, no ANSI)."""
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=200, no_color=True), buf


def _reporter(mode: OutputMode) -> tuple[RunReporter, io.StringIO, io.StringIO]:
    """A reporter in ``mode`` with buffer-backed stdout and stderr consoles."""
    out_console, out_buf = _buffer_console()
    err_console, err_buf = _buffer_console()
    return RunReporter(mode, console=out_console, err_console=err_console), out_buf, err_buf


def _step(
    name: str = "s",
    *,
    state: RunState = RunState.SUCCEEDED,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
) -> StepResult:
    return StepResult(
        step_name=name,
        state=state,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=0.5,
    )


def _run(
    *,
    trigger: str = "run-tests",
    state: RunState = RunState.SUCCEEDED,
    steps: list[StepResult] | None = None,
    duration_s: float = 1.8,
) -> Run:
    run = Run(run_id=uuid4(), trigger_name=trigger, workflow_name="wf", state=state)
    if state is not RunState.CANCELLED:
        run.result = RunResult(
            run_id=run.run_id,
            workflow_name="wf",
            state=state,
            steps=steps if steps is not None else [_step(state=state, exit_code=0)],
            duration_s=duration_s,
        )
    return run


# --------------------------------------------------------------------------- #
# Format helpers.                                                              #
# --------------------------------------------------------------------------- #


def test_short_id_is_r_plus_four_hex() -> None:
    run_id = uuid4()
    assert _short_id(run_id) == f"r_{run_id.hex[:4]}"


def test_fmt_duration_seconds_and_minutes() -> None:
    assert _fmt_duration(1.84) == "1.8s"
    assert _fmt_duration(238.4) == "3m58s"
    assert _fmt_duration(60.0) == "1m00s"


# --------------------------------------------------------------------------- #
# DEFAULT mode: quiet on success, failing tail on failure.                     #
# --------------------------------------------------------------------------- #


def test_default_success_shows_verdict_but_not_program_output() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    run = _run(state=RunState.SUCCEEDED, steps=[_step(stdout="tons of noisy passing output\n")])
    reporter.run_started(run)
    reporter.run_finished(run)
    text = out.getvalue()
    assert "→ started" in text
    assert "✓ succeeded" in text
    assert "1.8s" in text
    assert "noisy passing output" not in text  # program voice hidden on success


async def test_default_failure_shows_the_retained_tail() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    failing = _step(name="pytest", state=RunState.FAILED, exit_code=1, stdout="=== 1 failed ===\n")
    run = _run(state=RunState.FAILED, steps=[failing])
    reporter.run_started(run)
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDOUT, text="live line\n"))
    reporter.run_finished(run)
    text = out.getvalue()
    assert "✗ failed" in text
    assert "exit 1" in text
    assert "program output" in text  # framed as the program's voice
    assert "1 failed" in text  # the retained tail is shown on failure


def test_default_timeout_renders_timed_out_with_tail() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    step = _step(name="slow", state=RunState.TIMED_OUT, exit_code=None, stderr="stuck\n")
    run = _run(state=RunState.TIMED_OUT, steps=[step], duration_s=0.5)
    reporter.run_started(run)
    reporter.run_finished(run)
    text = out.getvalue()
    assert "✗ timed out" in text
    assert "stuck" in text  # stderr tail shown


def test_default_program_output_with_markup_is_not_interpreted() -> None:
    # A watched program that prints rich-looking brackets must appear verbatim, not be parsed.
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    step = _step(name="p", state=RunState.FAILED, exit_code=2, stdout="[bold]not markup[/bold]\n")
    run = _run(state=RunState.FAILED, steps=[step])
    reporter.run_started(run)
    reporter.run_finished(run)
    assert "[bold]not markup[/bold]" in out.getvalue()


# --------------------------------------------------------------------------- #
# VERBOSE mode: full passthrough, including on success.                        #
# --------------------------------------------------------------------------- #


async def test_verbose_streams_all_output_including_success() -> None:
    reporter, out, err = _reporter(OutputMode.VERBOSE)
    run = _run(state=RunState.SUCCEEDED)
    reporter.run_started(run)
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDOUT, text="hello-out"))
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDERR, text="hello-err"))
    reporter.run_finished(run)
    assert "hello-out" in out.getvalue()  # stdout passthrough
    assert "hello-err" in err.getvalue()  # stderr preserved on its own stream
    # No compact verdict line in verbose — structlog narrates the engine voice.
    assert "✓ succeeded" not in out.getvalue()


# --------------------------------------------------------------------------- #
# QUIET mode: verdict only + failing tail, no per-run lines.                   #
# --------------------------------------------------------------------------- #


def test_quiet_suppresses_success_lines_but_shows_failing_tail() -> None:
    reporter, out, _ = _reporter(OutputMode.QUIET)
    ok = _run(state=RunState.SUCCEEDED)
    bad = _run(
        state=RunState.FAILED, steps=[_step(state=RunState.FAILED, exit_code=1, stdout="boom\n")]
    )
    for run in (ok, bad):
        reporter.run_started(run)
        reporter.run_finished(run)
    text = out.getvalue()
    assert "→ started" not in text  # no per-event lines
    assert "✓ succeeded" not in text
    assert "boom" in text  # the failing tail still surfaces


def test_quiet_banner_is_suppressed_but_summary_prints() -> None:
    from swatch.core.config import SwatchConfig

    reporter, out, _ = _reporter(OutputMode.QUIET)
    reporter.banner(SwatchConfig(triggers=[]), object(), object(), once=True)  # type: ignore[arg-type]
    assert out.getvalue() == ""  # no banner in quiet
    reporter.summary((_run(state=RunState.SUCCEEDED),))
    assert "1 succeeded" in out.getvalue()


# --------------------------------------------------------------------------- #
# JSON mode: one object per line, streaming, uncapped.                         #
# --------------------------------------------------------------------------- #


async def test_json_emits_one_object_per_lifecycle_and_output_event() -> None:
    reporter, out, _ = _reporter(OutputMode.JSON)
    run = _run(state=RunState.SUCCEEDED)
    reporter.run_started(run)
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDOUT, text="chunk-1"))
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDERR, text="chunk-2"))
    reporter.run_finished(run)
    reporter.summary((run,))
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    events = [obj["event"] for obj in lines]
    assert events == ["run.started", "step.output", "step.output", "run.completed", "run.summary"]
    output_events = [o for o in lines if o["event"] == "step.output"]
    assert output_events[0] == {
        "event": "step.output",
        "run_id": str(run.run_id),
        "stream": "stdout",
        "text": "chunk-1",
    }
    assert output_events[1]["stream"] == "stderr"
    assert lines[3]["status"] == "succeeded"
    assert lines[4]["succeeded"] == 1


async def test_json_output_is_not_truncated_to_the_retention_cap() -> None:
    # A machine consumer gets everything: a chunk far larger than the 256 KiB human cap streams
    # through the JSON event verbatim (the ruling: truncating a machine stream is worse).
    reporter, out, _ = _reporter(OutputMode.JSON)
    run = _run(state=RunState.SUCCEEDED)
    big = "y" * (600 * 1024)
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDOUT, text=big))
    obj = next(
        json.loads(line)
        for line in out.getvalue().splitlines()
        if line.strip() and json.loads(line)["event"] == "step.output"
    )
    assert len(obj["text"]) == len(big)  # nothing dropped


def test_json_banner_suppressed() -> None:
    from swatch.core.config import SwatchConfig

    reporter, out, _ = _reporter(OutputMode.JSON)
    reporter.banner(SwatchConfig(triggers=[]), object(), object(), once=False)  # type: ignore[arg-type]
    assert out.getvalue() == ""


# --------------------------------------------------------------------------- #
# Cancellation and the empty summary.                                         #
# --------------------------------------------------------------------------- #


def test_default_cancelled_run_renders_without_a_result() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    run = _run(state=RunState.CANCELLED)  # no result attached
    reporter.run_started(run)
    reporter.run_finished(run)
    assert "✗ cancelled" in out.getvalue()


def test_default_summary_with_no_runs() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    reporter.summary(())
    assert "no runs" in out.getvalue()


def test_run_finished_without_run_started_is_tolerated() -> None:
    # A run cancelled while still queued never gets run_started; run_finished must not raise.
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    reporter.run_finished(_run(state=RunState.CANCELLED))
    assert "✗ cancelled" in out.getvalue()


# --------------------------------------------------------------------------- #
# Liveness spinner: headless is inert; the TTY schedule/cancel path is driven. #
# --------------------------------------------------------------------------- #


def test_liveness_is_inert_without_a_terminal() -> None:
    console, _ = _buffer_console()  # force_terminal=False → not a TTY
    live = _Liveness(console)
    live.ensure("label")  # no reveal scheduled off a TTY
    assert live._pending is None
    live.stop()  # idempotent, nothing to cancel


async def test_liveness_schedules_and_cancels_reveal_on_a_terminal() -> None:
    console = Console(file=io.StringIO(), force_terminal=True)
    live = _Liveness(console)
    live.ensure("first")  # schedules a delayed reveal
    assert live._pending is not None
    live.ensure("second")  # already pending → no second timer
    live.stop()  # cancels the pending reveal before it fires
    assert live._pending is None


# --------------------------------------------------------------------------- #
# Edge branches: banner variants, missing results, concurrency, blank output.  #
# --------------------------------------------------------------------------- #


def test_banner_default_shows_watching_when_continuous() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    reporter.banner(SwatchConfig(triggers=[]), Path("proj"), Path("proj/swatch.toml"), once=False)
    text = out.getvalue()
    assert "engine starting" in text
    assert "watching" in text  # the continuous (non-once) banner branch


def test_failing_step_is_none_without_result_or_without_a_failure() -> None:
    assert _failing_step(_run(state=RunState.CANCELLED)) is None  # no result attached
    all_ok = _run(state=RunState.FAILED, steps=[_step(state=RunState.SUCCEEDED)])
    assert _failing_step(all_ok) is None  # a result, but no failing step in it


def test_default_failed_run_without_result_renders_verdict_and_no_tail() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    run = Run(
        run_id=uuid4(), trigger_name="t", workflow_name="wf", state=RunState.FAILED
    )  # no result
    reporter.run_started(run)
    reporter.run_finished(run)
    text = out.getvalue()
    assert "✗ failed" in text  # verdict still renders (no exit clause, no tail)
    assert "program output" not in text


async def test_default_output_for_an_unstarted_run_is_ignored() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    run = _run()  # never run_started → no view
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDOUT, text="orphan"))
    assert out.getvalue() == ""


async def test_default_blank_output_leaves_the_last_line_unset() -> None:
    reporter, _, _ = _reporter(OutputMode.DEFAULT)
    run = _run()
    reporter.run_started(run)
    await reporter.on_output(run, OutputChunk(stream=StreamName.STDOUT, text="   \n\t\n"))
    assert reporter._views[run.run_id].last_line == ""  # no non-blank line to remember


def test_default_liveness_persists_until_the_last_of_two_runs_finishes() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    first, second = _run(trigger="a"), _run(trigger="b")
    reporter.run_started(first)
    reporter.run_started(second)
    reporter.run_finished(first)  # 'second' still in flight → liveness not yet torn down
    reporter.run_finished(second)
    text = out.getvalue()
    assert "a" in text and "b" in text


def test_active_label_is_a_placeholder_with_no_runs() -> None:
    reporter, _, _ = _reporter(OutputMode.DEFAULT)
    assert reporter._active_label() == "running…"


# --------------------------------------------------------------------------- #
# Cooldown suppression: observable in --json and coalesced into the summary.    #
# --------------------------------------------------------------------------- #


def test_admission_suppressed_emits_a_json_event() -> None:
    reporter, out, _ = _reporter(OutputMode.JSON)
    reporter.admission_suppressed(trigger_name="run-tests", path="src/api.py", remaining_ms=250)
    assert json.loads(out.getvalue().strip()) == {
        "event": "admission.suppressed",
        "reason": "cooldown",
        "trigger": "run-tests",
        "path": "src/api.py",
        "remaining_ms": 250,
    }


def test_admission_suppressed_count_coalesces_into_the_default_summary() -> None:
    reporter, out, _ = _reporter(OutputMode.DEFAULT)
    reporter.admission_suppressed(trigger_name="t", path="a.py", remaining_ms=250)
    reporter.admission_suppressed(trigger_name="t", path="a.py", remaining_ms=100)
    reporter.summary((_run(state=RunState.SUCCEEDED),))
    assert "2 suppressed" in out.getvalue()  # silence isn't mistaken for a missed change


def test_admission_suppressed_count_in_the_json_summary() -> None:
    reporter, out, _ = _reporter(OutputMode.JSON)
    reporter.admission_suppressed(trigger_name="t", path="a.py", remaining_ms=250)
    reporter.summary((_run(state=RunState.SUCCEEDED),))
    summary = next(
        json.loads(line)
        for line in out.getvalue().splitlines()
        if line.strip() and json.loads(line)["event"] == "run.summary"
    )
    assert summary["suppressed"] == 1
