"""Core workflow domain models: ``Workflow`` and its ``Step``s.

See ``MODULE_SPECIFICATIONS.md`` §5 and ``PROJECT_STRUCTURE.md`` §1. A ``Workflow`` is an
ordered list of ``Step``s admitted and executed as a single Run. For v0.1.0 a Workflow is
linear (a single-branch chain); the DAG edges, ``max_parallel``, and ``continue_on_fail``
propagation that ``EXECUTION_MODEL.md`` §3 describes arrive with the ``DAGExecutor`` in the
v2 band and are deliberately absent here.

A ``Step`` carries the argv-list command the ``Executor`` runs as a subprocess (never a
shell string — Constitution Article III / ADR-0002), plus the per-step timeout, working
directory, and environment allowlist ``EXECUTION_MODEL.md`` §6 and ``CODING_STANDARD.md``
§8 specify. Only the ``subprocess`` step kind exists in v0.1.0; the ``plugin``/``http``/
``mcp_tool`` kinds in ``EXECUTION_MODEL.md`` §2 land with their respective adapters.
"""

from pathlib import Path

from pydantic import BaseModel, Field


class Step(BaseModel):
    """A single unit of work within a Workflow, run as one subprocess.

    The command is an argv ``list[str]`` executed via ``asyncio.create_subprocess_exec``
    with ``shell=False`` — never a joined shell string (Constitution Article III). The
    environment the child sees is scrubbed to ``env_allowlist`` only; everything else in
    the parent environment is withheld (``CODING_STANDARD.md`` §8).

    Attributes:
        name: Identifier for this step, unique within its Workflow.
        command: The argv to execute, e.g. ``["pytest", "-q"]``. Must be non-empty; its
            first element is the program and the rest are its arguments.
        timeout_s: Hard per-step timeout in seconds (``EXECUTION_MODEL.md`` §6). When set,
            expiry terminates the subprocess and its process group and records the step
            ``TIMED_OUT``. ``None`` (the default) means no timeout — the v0.1.0 default
            per ``ROADMAP.md``; a Step opts into a timeout by setting it.
        cwd: Working directory for the subprocess, or ``None`` to inherit the parent's.
            (``EXECUTION_MODEL.md`` §6's project-root jailing is applied once the Executor
            has a project-root context to validate against — it is not enforced here.)
        env_allowlist: Names of parent-environment variables permitted to pass through to
            the child; every other variable is scrubbed. Empty by default (fully scrubbed
            environment).
    """

    name: str
    command: list[str] = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0)
    cwd: Path | None = None
    env_allowlist: list[str] = Field(default_factory=list)


class Workflow(BaseModel):
    """An ordered sequence of Steps admitted and executed as a single Run.

    For v0.1.0 the sequence is linear: the ``Executor`` runs the steps in order and a
    failed step stops the remainder (``EXECUTION_MODEL.md`` §3, read for a single branch).
    DAG edges and parallel fan-out arrive with the ``DAGExecutor`` (§3, v2 band).

    Attributes:
        name: Identifier for this workflow.
        steps: The workflow's steps, executed in order; empty by default.
    """

    name: str
    steps: list[Step] = Field(default_factory=list)
