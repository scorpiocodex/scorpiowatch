"""The ``RunReporter`` port: the seam the Scheduler narrates a Run's lifecycle through.

The Scheduler owns *when* a Run starts, streams output, and finishes; *how* that is shown
to a human (or emitted as JSON) is an Interface-layer concern. This Protocol is the boundary
between them: the Scheduler calls it, the CLI's ``rich`` renderer implements it, and — exactly
as with :class:`~watchflow.core.ports.SourceAdapter` (ADR-0010 Option A) — ``core`` depends
only on the Protocol and never imports ``cli``.

It carries the two voices the output layer separates (``UI_DESIGN.md`` §4.3): the **engine
voice** (:meth:`run_started` / :meth:`run_finished` — lifecycle, names, states, timing) and
the **subprocess voice** (:meth:`on_output` — the watched program's own stdout/stderr, fed
from the Executor's streaming sink built in task 1.4 part 1).
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from watchflow.core.scheduler import Run
    from watchflow.execution.executor import OutputChunk


class RunReporter(Protocol):
    """A sink the Scheduler narrates each Run's lifecycle and output to.

    All three methods are best-effort presentation and must never raise back into the
    Scheduler (a reporting failure must not fail a Run — ``EXECUTION_MODEL.md`` §9). A Run
    that never reaches ``RUNNING`` (cancelled while queued) yields a :meth:`run_finished`
    with no preceding :meth:`run_started`; implementations tolerate that.
    """

    def run_started(self, run: "Run") -> None:
        """Called when ``run`` transitions to ``RUNNING`` (before its first Step spawns)."""
        ...

    async def on_output(self, run: "Run", chunk: "OutputChunk") -> None:
        """Called for each subprocess output chunk of ``run`` as it is produced.

        Awaited, so a slow renderer back-pressures the Executor's reader (the task-1.4
        part-1 discipline) rather than growing an unbounded buffer.
        """
        ...

    def run_finished(self, run: "Run") -> None:
        """Called once ``run`` has reached a terminal state (result available unless cancelled)."""
        ...

    def admission_suppressed(self, *, trigger_name: str, path: str, remaining_ms: int) -> None:
        """Called when a fire is suppressed by cooldown (§4) instead of becoming a Run.

        A suppression is an intentional, observable admission decision, not a silent drop
        (Article VIII); this is how it reaches ``--json`` (and a coalesced count for the human
        summary), separate from the ``admission.suppressed`` structlog record that ``--verbose``
        renders. ``remaining_ms`` is how long the trigger+path's cooldown window still has to run.
        """
        ...
