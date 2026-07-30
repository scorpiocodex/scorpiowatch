"""Behavioral tests for the strict subprocess ``Executor`` (EXECUTION_MODEL §1/§2/§6).

Real subprocesses only — no mocks — driven through the running interpreter
(``sys.executable -c``) so the suite is cross-platform and depends on no shell builtin.
The no-orphan and process-group proofs use a heartbeat file (a live child rewrites it
continuously) rather than PID liveness probes, which are not portable to Windows.
"""

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest

from watchflow.core.workflow import Step, Workflow
from watchflow.execution.executor import Executor, RunContext, RunResult, RunState, StepResult

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
