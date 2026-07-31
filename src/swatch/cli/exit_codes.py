"""Process exit codes for the WatchFlow CLI (``EXECUTION_MODEL.md`` §7.2).

§7.2 is the authority the task cites; it distinguishes a config error (``2``) from a usage
error (``3``) and a startup/runtime error (``4``) — a finer split than the ``UI_DESIGN.md``
§4.4 table (which folds startup into ``3`` and omits ``4``). That discrepancy is a
documentation erratum to reconcile separately; the codes here follow §7.2.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """The machine-readable summary of a ``swatch`` invocation.

    Attributes:
        SUCCESS: Every run reached ``SUCCEEDED``/``SKIPPED`` (or a clean drain).
        WORKFLOW_FAILURE: At least one run reached ``FAILED``/``TIMED_OUT`` (``--once``/CI).
        CONFIG_ERROR: ``swatch.toml`` failed to read, parse, or validate.
        USAGE_ERROR: Malformed CLI invocation (surfaced by ``typer`` before the Engine starts).
        STARTUP_ERROR: The Engine failed to start for an operational reason (e.g. an adapter
            that could not initialize).
        SIGINT: Terminated by ``SIGINT`` (Ctrl+C) — 128 + 2.
        SIGTERM: Terminated by ``SIGTERM`` — 128 + 15.
    """

    SUCCESS = 0
    WORKFLOW_FAILURE = 1
    CONFIG_ERROR = 2
    USAGE_ERROR = 3
    STARTUP_ERROR = 4
    SIGINT = 130
    SIGTERM = 143
