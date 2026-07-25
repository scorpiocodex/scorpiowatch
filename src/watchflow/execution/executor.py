"""Strict subprocess execution: run a Workflow's Steps as subprocesses, safely.

Governed by ``EXECUTION_MODEL.md`` (Run/Step lifecycle §1, step execution §2, timeouts and
process groups §6) and ``MODULE_SPECIFICATIONS.md`` §5. Every subprocess is spawned with
``asyncio.create_subprocess_exec`` — ``shell=False``, argv as ``list[str]``, no shell in any
form (Constitution Article III / ADR-0002).

Scope (v0.1.0): the ``Executor`` runs a **linear** Workflow — its Steps in order, stopping
at the first failure (``EXECUTION_MODEL.md`` §3 read for a single branch). §5 houses
whole-Workflow ``run`` on the ``DAGExecutor``; until the DAG lands (v2 band) the Executor
provides the linear ``run`` over the per-Step ``run_step`` primitive, and the DAGExecutor
will reuse ``run_step`` as its per-node executor.

Timeout and cancellation are hard-kill paths (``EXECUTION_MODEL.md`` §6): a step that
exceeds its ``timeout_s``, or an Executor whose task is cancelled, terminates the running
subprocess **and its whole process group** — a new session via ``start_new_session`` on
POSIX (killed with ``os.killpg``) and a kill-on-close Job Object on Windows (§6 names both)
— so a child that spawned grandchildren leaves no orphan.
"""

import asyncio
import os
import signal
import sys
import time
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from watchflow.core.workflow import Step, Workflow

_log = structlog.get_logger(__name__)

# POSIX creates the process group via start_new_session; Windows uses a Job Object instead
# (start_new_session is accepted and ignored there).
_POSIX = sys.platform != "win32"


class RunState(StrEnum):
    """The Run/Step lifecycle states of ``EXECUTION_MODEL.md`` §1.

    A ``StepResult`` uses the terminal subset ``SUCCEEDED | FAILED | TIMED_OUT | SKIPPED``
    (a step is never ``CANCELLED`` in isolation — cancellation propagates as an exception,
    see :meth:`Executor.run_step`); a ``RunResult`` uses ``SUCCEEDED | FAILED | TIMED_OUT``.
    The transient ``PENDING | QUEUED | RUNNING`` states belong to the Scheduler-owned Run
    admission lifecycle, not to the Executor's terminal results.
    """

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class RunContext(BaseModel):
    """Ambient context shared by every Step of one Run.

    Attributes:
        run_id: Stable identifier for this Run, carried into every ``StepResult``'s log
            record and (later) the EventStore. Defaults to a fresh UUID.
    """

    model_config = ConfigDict(frozen=True)

    run_id: UUID = Field(default_factory=uuid4)


class StepResult(BaseModel):
    """The outcome of executing one Step.

    Attributes:
        step_name: The Step's name.
        state: Terminal state — ``SUCCEEDED``, ``FAILED``, ``TIMED_OUT``, or ``SKIPPED``.
        exit_code: The subprocess exit code, or ``None`` for a step that never exited
            cleanly (``TIMED_OUT``) or never ran (``SKIPPED``).
        stdout: Captured standard output, decoded as UTF-8 (errors replaced).
        stderr: Captured standard error, decoded as UTF-8 (errors replaced).
        duration_s: Wall-clock execution time in seconds (0.0 for a skipped step).
    """

    model_config = ConfigDict(frozen=True)

    step_name: str
    state: RunState
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float


class RunResult(BaseModel):
    """The aggregate outcome of executing a Workflow's Steps.

    The aggregate ``state`` is the Executor's contribution to the process exit code that
    ``EXECUTION_MODEL.md`` §7.2 defines; the mapping to a process exit code (including the
    signal-dependent ``130``/``143`` for interruptions) stays with the CLI, which alone
    knows the terminating signal.

    Attributes:
        run_id: The Run's identifier (from its ``RunContext``).
        workflow_name: The executed Workflow's name.
        state: ``SUCCEEDED`` if every step succeeded; otherwise the terminal state of the
            first non-succeeding step (``FAILED`` or ``TIMED_OUT``).
        steps: One ``StepResult`` per Step, in order — including steps marked ``SKIPPED``
            after an earlier failure (never silently omitted, ``MODULE_SPECIFICATIONS.md``
            §5).
        duration_s: Total wall-clock time for the Run in seconds.
    """

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    workflow_name: str
    state: RunState
    steps: list[StepResult]
    duration_s: float


# --------------------------------------------------------------------------- #
# Platform-specific process-group primitives (EXECUTION_MODEL.md §6).          #
# Only the host platform's branch is reachable, so both are excluded from      #
# coverage; their behavior is verified by the process-group integration test.  #
# --------------------------------------------------------------------------- #
if sys.platform == "win32":  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    class _JobBasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = (
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        )

    class _JobExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _JobBasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _open_group() -> int | None:
        """Create a kill-on-close Job Object; closing or terminating it kills the tree."""
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _JobExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        return int(handle)

    def _join_group(token: int | None, pid: int) -> None:
        """Assign the spawned process to the Job Object so its whole tree is contained."""
        if token is None:
            return
        handle = _kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _kernel32.AssignProcessToJobObject(token, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(handle)

    def _terminate_group(proc: asyncio.subprocess.Process, token: int | None) -> None:
        """Kill every process in the Job Object (the subprocess and its descendants)."""
        if token is not None:
            _kernel32.TerminateJobObject(token, 1)

    def _close_group(token: int | None) -> None:
        """Release the Job Object handle (kill-on-close reaps any straggler)."""
        if token is not None:
            _kernel32.CloseHandle(token)

else:  # pragma: no cover

    def _open_group() -> int | None:
        """POSIX needs no pre-spawn token; the new session is created at spawn time."""
        return None

    def _join_group(token: int | None, pid: int) -> None:
        """POSIX groups the process at spawn (start_new_session); nothing to do here."""
        return None

    def _terminate_group(proc: asyncio.subprocess.Process, token: int | None) -> None:
        """Kill the whole process group led by the subprocess (its new session)."""
        with suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    def _close_group(token: int | None) -> None:
        """POSIX holds no group handle to release."""
        return None


async def _spawn_group(
    command: list[str], cwd: Path | None, env: dict[str, str]
) -> tuple[asyncio.subprocess.Process, int | None]:
    """Spawn ``command`` as a subprocess isolated in its own killable process group.

    Args:
        command: The argv to execute (``shell=False``; never a joined string).
        cwd: Working directory for the child, or ``None`` to inherit the parent's.
        env: The exact environment for the child (already scrubbed to the allowlist).

    Returns:
        The spawned process and its process-group token (a Windows Job Object handle, or
        ``None`` on POSIX where the process group is the child's own session).
    """
    token = _open_group()
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=_POSIX,
        )
    except BaseException:
        _close_group(token)
        raise
    _join_group(token, proc.pid)
    return proc, token


def _decode(data: bytes) -> str:
    """Decode captured subprocess output as UTF-8, replacing undecodable bytes."""
    return data.decode("utf-8", errors="replace")


class Executor:
    """Runs a linear Workflow's Steps as subprocesses, safely (``EXECUTION_MODEL.md`` §2).

    :meth:`run_step` is the safety-critical primitive — one Step, one subprocess, with the
    per-step timeout and process-group termination of §6. :meth:`run` drives a linear
    Workflow over it: steps in order, a failure stopping the remainder. No shell is used in
    any form (Constitution Article III); no scheduling, DAG, or CLI concerns live here.
    """

    async def run(self, workflow: Workflow, ctx: RunContext | None = None) -> RunResult:
        """Execute ``workflow``'s Steps in order and return the aggregate result.

        Linear semantics (``EXECUTION_MODEL.md`` §3, single branch): steps run one after
        another; the first step that does not succeed stops execution and every remaining
        step is recorded ``SKIPPED`` (never omitted). The Run's ``state`` is ``SUCCEEDED``
        if all steps succeeded, else the terminal state of the step that stopped it.

        Args:
            workflow: The Workflow to run.
            ctx: The Run context; a fresh one (new ``run_id``) is created if omitted.

        Returns:
            A ``RunResult`` carrying every ``StepResult`` and the aggregate state.
        """
        context = ctx if ctx is not None else RunContext()
        started = time.monotonic()
        results: list[StepResult] = []
        aggregate = RunState.SUCCEEDED
        stopped = False
        for step in workflow.steps:
            if stopped:
                results.append(
                    StepResult(
                        step_name=step.name,
                        state=RunState.SKIPPED,
                        exit_code=None,
                        stdout="",
                        stderr="",
                        duration_s=0.0,
                    )
                )
                continue
            result = await self.run_step(step, context)
            results.append(result)
            if result.state is not RunState.SUCCEEDED:
                aggregate = result.state
                stopped = True
        return RunResult(
            run_id=context.run_id,
            workflow_name=workflow.name,
            state=aggregate,
            steps=results,
            duration_s=time.monotonic() - started,
        )

    async def run_step(self, step: Step, ctx: RunContext) -> StepResult:
        """Execute one Step as a subprocess and return its result.

        The child runs under a scrubbed environment (``step.env_allowlist`` only) in its
        own process group. If ``step.timeout_s`` is set and elapses, the subprocess and its
        whole process group are terminated and the step is recorded ``TIMED_OUT``. If this
        coroutine's task is cancelled, the subprocess and its group are likewise terminated
        before the ``CancelledError`` propagates — a cancelled Executor leaves no orphaned
        process (``EXECUTION_MODEL.md`` §4/§6).

        Args:
            step: The Step to execute.
            ctx: The ambient Run context (supplies the ``run_id`` for log records).

        Returns:
            A ``StepResult`` with the step's state, exit code, captured output, and timing.
        """
        env = {name: os.environ[name] for name in step.env_allowlist if name in os.environ}
        started = time.monotonic()
        _log.info(
            "step.started",
            run_id=str(ctx.run_id),
            step=step.name,
            command=step.command,
            timeout_s=step.timeout_s,
        )
        try:
            proc, token = await _spawn_group(step.command, step.cwd, env)
        except OSError as error:
            # The program could not be launched at all (not found, not executable, ...). A
            # step failure must stay visible and never crash the Run (EXECUTION_MODEL §9).
            _log.warning(
                "step.spawn_failed", run_id=str(ctx.run_id), step=step.name, error=str(error)
            )
            return StepResult(
                step_name=step.name,
                state=RunState.FAILED,
                exit_code=None,
                stdout="",
                stderr=str(error),
                duration_s=time.monotonic() - started,
            )
        communicate = asyncio.create_task(proc.communicate())
        stdout, stderr = b"", b""
        timed_out = False
        try:
            try:
                if step.timeout_s is None:
                    stdout, stderr = await asyncio.shield(communicate)
                else:
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.shield(communicate), step.timeout_s
                    )
            except TimeoutError:
                timed_out = True
                _terminate_group(proc, token)
                stdout, stderr = await communicate
        except asyncio.CancelledError:
            _terminate_group(proc, token)
            communicate.cancel()
            with suppress(asyncio.CancelledError):
                await communicate
            _log.warning("step.cancelled", run_id=str(ctx.run_id), step=step.name)
            raise
        finally:
            _close_group(token)

        duration = time.monotonic() - started
        if timed_out:
            _log.warning(
                "step.timed_out", run_id=str(ctx.run_id), step=step.name, duration_s=duration
            )
            return StepResult(
                step_name=step.name,
                state=RunState.TIMED_OUT,
                exit_code=None,
                stdout=_decode(stdout),
                stderr=_decode(stderr),
                duration_s=duration,
            )
        exit_code = proc.returncode
        state = RunState.SUCCEEDED if exit_code == 0 else RunState.FAILED
        _log.info(
            "step.finished",
            run_id=str(ctx.run_id),
            step=step.name,
            state=state.value,
            exit_code=exit_code,
            duration_s=duration,
        )
        return StepResult(
            step_name=step.name,
            state=state,
            exit_code=exit_code,
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            duration_s=duration,
        )
