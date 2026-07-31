"""Behavioral tests for the strict subprocess ``Executor`` (EXECUTION_MODEL §1/§2/§6).

Real subprocesses only — no mocks — driven through the running interpreter
(``sys.executable -c``) so the suite is cross-platform and depends on no shell builtin.
The no-orphan and process-group proofs use a heartbeat file (a live child rewrites it
continuously) rather than PID liveness probes, which are not portable to Windows.
"""

import asyncio
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest

from swatch.core.workflow import Step, Workflow
from swatch.execution.executor import (
    _MAX_RETAINED_CHARS,
    Executor,
    OutputChunk,
    RunContext,
    RunResult,
    RunState,
    StepResult,
    StreamName,
    _BoundedText,
    _pump,
)

# A child that rewrites ``argv[1]`` with the current time forever — a liveness heartbeat.
_HEARTBEAT_SRC = (
    "import sys, time\n"
    "hb = sys.argv[1]\n"
    "while True:\n"
    "    open(hb, 'w').write(str(time.perf_counter()))\n"
    "    time.sleep(0.02)\n"
)

# A child that spawns the heartbeat as a grandchild, then idles — used to prove the whole
# process group (not just the direct child) is terminated.
_SPAWNER_SRC = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]])\n"
    "time.sleep(60)\n"
)


def _py(*code_and_args: str) -> list[str]:
    """Build an argv running the current interpreter with ``-c`` code (+ any extra argv)."""
    return [sys.executable, "-c", *code_and_args]


async def _heartbeat_is_frozen(path: Path, *, settle: float = 0.3) -> bool:
    """Return True if ``path`` stops changing over ``settle`` seconds (writer is dead)."""
    before = await asyncio.to_thread(path.read_text)
    await asyncio.sleep(settle)
    return await asyncio.to_thread(path.read_text) == before


async def _wait_for_file(path: Path, *, tries: int = 300, delay: float = 0.02) -> None:
    """Poll until ``path`` exists (a spawned child has started), or fail."""
    for _ in range(tries):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(delay)
    raise AssertionError(f"{path} never appeared")


# --------------------------------------------------------------------------- #
# Happy path: success, ordering, capture, aggregate state.                     #
# --------------------------------------------------------------------------- #


async def test_single_successful_step() -> None:
    workflow = Workflow(name="wf", steps=[Step(name="ok", command=_py("print('hi')"))])
    result = await Executor().run(workflow)
    assert result.state is RunState.SUCCEEDED
    assert [s.state for s in result.steps] == [RunState.SUCCEEDED]
    assert result.steps[0].exit_code == 0
    assert result.steps[0].stdout.strip() == "hi"


async def test_multi_step_runs_in_order(tmp_path: Path) -> None:
    log = tmp_path / "order.txt"
    append = "import sys; open(sys.argv[1], 'a').write(sys.argv[2])"
    workflow = Workflow(
        name="ordered",
        steps=[
            Step(name="first", command=_py(append, str(log), "1")),
            Step(name="second", command=_py(append, str(log), "2")),
            Step(name="third", command=_py(append, str(log), "3")),
        ],
    )
    result = await Executor().run(workflow)
    assert result.state is RunState.SUCCEEDED
    assert [s.step_name for s in result.steps] == ["first", "second", "third"]
    assert log.read_text() == "123"  # executed strictly in order


async def test_captures_stdout_and_stderr() -> None:
    code = "import sys; print('to-out'); print('to-err', file=sys.stderr)"
    result = await Executor().run(Workflow(name="wf", steps=[Step(name="io", command=_py(code))]))
    step = result.steps[0]
    assert step.stdout.strip() == "to-out"
    assert step.stderr.strip() == "to-err"


async def test_empty_workflow_succeeds_with_no_steps() -> None:
    result = await Executor().run(Workflow(name="nothing"))
    assert result.state is RunState.SUCCEEDED
    assert result.steps == []


async def test_run_id_is_carried_from_context_into_the_result() -> None:
    ctx = RunContext(run_id=uuid4())
    result = await Executor().run(Workflow(name="wf"), ctx)
    assert result.run_id == ctx.run_id
    assert result.workflow_name == "wf"


# --------------------------------------------------------------------------- #
# Failure semantics: non-zero exit stops the linear workflow.                  #
# --------------------------------------------------------------------------- #


async def test_failing_step_stops_workflow_and_skips_the_rest() -> None:
    workflow = Workflow(
        name="stops",
        steps=[
            Step(name="ok", command=_py("print('ran')")),
            Step(name="boom", command=_py("import sys; sys.exit(3)")),
            Step(name="never", command=_py("print('should not run')")),
        ],
    )
    result = await Executor().run(workflow)
    assert result.state is RunState.FAILED
    assert [s.state for s in result.steps] == [
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.SKIPPED,  # present in the result, never silently omitted (§5)
    ]
    assert result.steps[1].exit_code == 3
    assert result.steps[2].exit_code is None  # skipped step never ran


async def test_unspawnable_command_is_a_failed_step_not_a_crash(tmp_path: Path) -> None:
    # A program that cannot be launched is a visible step FAILURE, never an uncaught
    # exception that takes down the Run (EXECUTION_MODEL §9).
    missing = str(tmp_path / "no-such-program-xyz")
    workflow = Workflow(
        name="bad",
        steps=[
            Step(name="missing", command=[missing]),
            Step(name="after", command=_py("print('should not run')")),
        ],
    )
    result = await Executor().run(workflow)
    assert result.state is RunState.FAILED
    assert result.steps[0].state is RunState.FAILED
    assert result.steps[0].exit_code is None
    assert result.steps[0].stderr  # carries the OS error text
    assert result.steps[1].state is RunState.SKIPPED


# --------------------------------------------------------------------------- #
# Exec-not-shell: argv is literal, never interpreted by a shell.               #
# --------------------------------------------------------------------------- #


async def test_command_is_literal_argv_not_shell_interpreted(tmp_path: Path) -> None:
    # If a shell were involved these metacharacters would redirect/chain/expand; passed as
    # a single argv element they must reach the program verbatim (Constitution Article III).
    injection = "$HOME && echo pwned > owned.txt; rm -rf / | cat `whoami`"
    sentinel = tmp_path / "owned.txt"
    code = "import sys; print(sys.argv[1])"
    step = Step(name="literal", command=_py(code, injection), cwd=tmp_path)
    result = await Executor().run(Workflow(name="wf", steps=[step]))
    assert result.steps[0].stdout.strip() == injection  # reached the program unchanged
    assert not sentinel.exists()  # no shell ran the redirect


# --------------------------------------------------------------------------- #
# Environment scrubbing (CODING_STANDARD §8).                                  #
# --------------------------------------------------------------------------- #


async def test_env_is_scrubbed_to_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WF_SECRET", "leaked")
    monkeypatch.setenv("WF_ALLOWED", "visible")
    code = "import os; print(os.environ.get('WF_SECRET', 'X'), os.environ.get('WF_ALLOWED', 'X'))"
    result = await Executor().run(
        Workflow(
            name="wf",
            steps=[Step(name="env", command=_py(code), env_allowlist=["WF_ALLOWED"])],
        )
    )
    # Only the allowlisted variable survives; everything else is withheld from the child.
    assert result.steps[0].stdout.strip() == "X visible"


# --------------------------------------------------------------------------- #
# Timeout: the subprocess is killed and the step recorded TIMED_OUT (§6).      #
# --------------------------------------------------------------------------- #


async def test_step_exceeding_timeout_is_terminated_and_recorded() -> None:
    workflow = Workflow(
        name="slow",
        steps=[Step(name="sleeper", command=_py("import time; time.sleep(30)"), timeout_s=0.3)],
    )
    result = await asyncio.wait_for(Executor().run(workflow), timeout=10)
    assert result.state is RunState.TIMED_OUT
    step = result.steps[0]
    assert step.state is RunState.TIMED_OUT
    assert step.exit_code is None
    assert step.duration_s < 10  # returned long before the 30s sleep


async def test_timeout_stops_the_workflow_and_skips_the_rest() -> None:
    workflow = Workflow(
        name="slow-then-more",
        steps=[
            Step(name="sleeper", command=_py("import time; time.sleep(30)"), timeout_s=0.3),
            Step(name="after", command=_py("print('nope')")),
        ],
    )
    result = await asyncio.wait_for(Executor().run(workflow), timeout=10)
    assert result.state is RunState.TIMED_OUT
    assert [s.state for s in result.steps] == [RunState.TIMED_OUT, RunState.SKIPPED]


# --------------------------------------------------------------------------- #
# Cancellation and process-group teardown: no orphaned processes (§4/§6).      #
# --------------------------------------------------------------------------- #


async def test_cancellation_mid_run_terminates_the_subprocess(tmp_path: Path) -> None:
    hb = tmp_path / "hb.txt"
    workflow = Workflow(
        name="heartbeat",
        steps=[Step(name="beat", command=_py(_HEARTBEAT_SRC, str(hb)))],
    )
    task = asyncio.create_task(Executor().run(workflow))
    await _wait_for_file(hb)  # the subprocess is alive and beating
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert await _heartbeat_is_frozen(hb), "subprocess kept running after cancel (orphan)"


async def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    # The step's child spawns a grandchild heartbeat; on timeout the entire group must die,
    # not just the direct child. On Windows this is a kill-on-close Job Object; on POSIX a
    # new-session process group killed with os.killpg (EXECUTION_MODEL §6).
    hb = tmp_path / "group_hb.txt"
    workflow = Workflow(
        name="group",
        steps=[
            Step(
                name="spawner",
                command=_py(_SPAWNER_SRC, _HEARTBEAT_SRC, str(hb)),
                timeout_s=0.5,
                env_allowlist=list(os.environ),  # let the child spawn its own subprocess
            )
        ],
    )
    result = await asyncio.wait_for(Executor().run(workflow), timeout=15)
    assert result.state is RunState.TIMED_OUT
    await _wait_for_file(hb)  # the grandchild did start
    assert await _heartbeat_is_frozen(hb), "grandchild survived group termination (orphan)"


# --------------------------------------------------------------------------- #
# run_step in isolation (the §5 primitive).                                    #
# --------------------------------------------------------------------------- #


async def test_run_step_returns_a_stepresult_for_one_step() -> None:
    ctx = RunContext()
    result = await Executor().run_step(Step(name="one", command=_py("print('x')")), ctx)
    assert isinstance(result, StepResult)
    assert result.state is RunState.SUCCEEDED
    assert result.step_name == "one"


# --------------------------------------------------------------------------- #
# Aggregate-state contract that the CLI's §7.2 exit-code mapping consumes.     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("command", "timeout_s", "expected"),
    [
        (_py("print('ok')"), None, RunState.SUCCEEDED),
        (_py("import sys; sys.exit(1)"), None, RunState.FAILED),
        (_py("import time; time.sleep(30)"), 0.3, RunState.TIMED_OUT),
    ],
)
async def test_aggregate_state_reflects_the_single_step(
    command: list[str], timeout_s: float | None, expected: RunState
) -> None:
    workflow = Workflow(name="agg", steps=[Step(name="s", command=command, timeout_s=timeout_s)])
    result: RunResult = await asyncio.wait_for(Executor().run(workflow), timeout=10)
    assert result.state is expected


# --------------------------------------------------------------------------- #
# Streaming: output reaches the sink AS produced, not buffered until exit.     #
# --------------------------------------------------------------------------- #


class _Recorder:
    """An ``on_output`` sink that records each chunk with its arrival time."""

    def __init__(self) -> None:
        self.chunks: list[tuple[float, OutputChunk]] = []

    async def __call__(self, chunk: OutputChunk) -> None:
        self.chunks.append((time.perf_counter(), chunk))

    def text(self, stream: StreamName) -> str:
        """The concatenated text seen on ``stream``, in arrival order."""
        return "".join(c.text for _, c in self.chunks if c.stream is stream)


# Emits four lines, flushing and sleeping between each, so a streaming reader observes them
# spread over ~0.6s while a buffering reader would see them all at once at exit.
_TICKER_SRC = (
    "import sys, time\n"
    "for i in range(4):\n"
    "    sys.stdout.write(f'tick{i}\\n')\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.2)\n"
)


async def test_output_streams_incrementally_not_buffered_to_exit() -> None:
    recorder = _Recorder()
    step = Step(name="ticker", command=_py(_TICKER_SRC))
    result = await asyncio.wait_for(
        Executor().run_step(step, RunContext(), on_output=recorder), timeout=10
    )
    assert result.state is RunState.SUCCEEDED
    assert len(recorder.chunks) >= 2, "output arrived in a single lump — not streaming"
    span = recorder.chunks[-1][0] - recorder.chunks[0][0]
    # Buffered-to-completion would make every chunk land within a scheduler tick of each other
    # (span ~0); streaming spreads them across the child's ~0.6s of sleeps.
    assert span >= 0.25, f"chunks did not arrive over time (span={span:.3f}s)"
    assert "tick0" in recorder.text(StreamName.STDOUT)
    assert "tick3" in recorder.text(StreamName.STDOUT)


async def test_interleaved_stdout_and_stderr_are_attributed_and_match_the_record() -> None:
    code = (
        "import sys\n"
        "sys.stdout.write('OUT1\\n'); sys.stdout.flush()\n"
        "sys.stderr.write('ERR1\\n'); sys.stderr.flush()\n"
        "sys.stdout.write('OUT2\\n'); sys.stdout.flush()\n"
    )
    recorder = _Recorder()
    result = await asyncio.wait_for(
        Executor().run_step(Step(name="io", command=_py(code)), RunContext(), on_output=recorder),
        timeout=10,
    )
    assert result.state is RunState.SUCCEEDED
    assert "OUT1" in recorder.text(StreamName.STDOUT)
    assert "OUT2" in recorder.text(StreamName.STDOUT)
    assert "ERR1" in recorder.text(StreamName.STDERR)
    # The streamed chunks reassemble exactly into the retained record (small, un-truncated).
    assert recorder.text(StreamName.STDOUT) == result.stdout
    assert recorder.text(StreamName.STDERR) == result.stderr


async def test_chatty_stdout_with_silent_stderr_does_not_deadlock() -> None:
    # ~100 KiB on stdout overflows the OS pipe buffer; if the readers did not drain both
    # pipes concurrently the child would block writing while we waited on the silent stderr.
    code = "import sys\nfor _ in range(1000): sys.stdout.write('x' * 100 + '\\n')\n"
    result = await asyncio.wait_for(
        Executor().run_step(Step(name="chatty", command=_py(code)), RunContext()), timeout=10
    )
    assert result.state is RunState.SUCCEEDED
    assert result.stdout.count("\n") == 1000
    assert result.stderr == ""  # the silent stream stays empty, no deadlock


async def test_high_volume_output_is_capped_in_the_record() -> None:
    # Produce twice the retention cap; the record keeps only the bounded tail (no ballooning),
    # while the truncation is announced rather than silent.
    produced = _MAX_RETAINED_CHARS * 2
    code = f"import sys\nsys.stdout.write('y' * {produced})\n"
    result = await asyncio.wait_for(
        Executor().run_step(Step(name="flood", command=_py(code)), RunContext()), timeout=15
    )
    assert result.state is RunState.SUCCEEDED
    assert "truncated" in result.stdout  # the drop is announced
    assert len(result.stdout) <= _MAX_RETAINED_CHARS + 80  # notice + capped tail, not 512 KiB
    assert result.stdout.rstrip().endswith("y")  # the retained slice is the tail


async def test_streaming_survives_timeout_with_partial_output() -> None:
    # Output produced before a timeout is both streamed out and retained; the kill path
    # (terminate group, then drain to EOF) does not lose what the child already emitted.
    code = (
        "import sys, time\n"
        "sys.stdout.write('before-timeout\\n'); sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    recorder = _Recorder()
    step = Step(name="slow", command=_py(code), timeout_s=0.5)
    result = await asyncio.wait_for(
        Executor().run_step(step, RunContext(), on_output=recorder), timeout=10
    )
    assert result.state is RunState.TIMED_OUT
    assert any("before-timeout" in c.text for _, c in recorder.chunks)  # streamed before kill
    assert "before-timeout" in result.stdout  # and retained on the record


# --------------------------------------------------------------------------- #
# Retention/decoding units: the bounded buffer and the incremental reader.     #
# --------------------------------------------------------------------------- #


def test_bounded_text_evicts_oldest_and_announces_truncation() -> None:
    buf = _BoundedText(cap=10)
    buf.add("")  # empty add is a no-op, retained nothing
    buf.add("aaaaa")
    buf.add("bbbbb")
    buf.add("ccccc")  # total 15 > cap 10 → oldest "aaaaa" evicted
    rendered = buf.render()
    assert "truncated" in rendered
    assert rendered.endswith("bbbbbccccc")
    assert "aaaaa" not in rendered


def test_bounded_text_trims_a_single_oversized_chunk() -> None:
    buf = _BoundedText(cap=5)
    buf.add("abcdefghij")  # one chunk larger than the whole cap
    rendered = buf.render()
    assert "truncated" in rendered
    assert rendered.endswith("fghij")  # only the last 5 chars survive


def test_bounded_text_without_overflow_has_no_notice() -> None:
    buf = _BoundedText(cap=100)
    buf.add("hello")
    assert buf.render() == "hello"


async def test_pump_flushes_an_incomplete_multibyte_sequence_at_eof() -> None:
    # A read boundary (or EOF) that splits a multi-byte character must not be lost or mojibake:
    # the incremental decoder holds the partial bytes and flushes a replacement char at EOF.
    reader = asyncio.StreamReader()
    reader.feed_data(b"\xe2\x9c")  # first two bytes of ✓ (U+2713), truncated before the third
    reader.feed_eof()
    buf = _BoundedText()
    chunks: list[OutputChunk] = []

    async def sink(chunk: OutputChunk) -> None:
        chunks.append(chunk)

    await _pump(reader, StreamName.STDOUT, sink, buf)
    assert chr(0xFFFD) in buf.render()  # flushed at EOF, not dropped
    assert chunks and chunks[-1].stream is StreamName.STDOUT
    assert chr(0xFFFD) in chunks[-1].text


async def test_pump_flushes_at_eof_even_without_a_sink() -> None:
    # The same EOF flush must still retain the partial character when no sink is attached
    # (the record path is independent of the live stream).
    reader = asyncio.StreamReader()
    reader.feed_data(b"\xe2\x9c")
    reader.feed_eof()
    buf = _BoundedText()
    await _pump(reader, StreamName.STDOUT, None, buf)
    assert chr(0xFFFD) in buf.render()
