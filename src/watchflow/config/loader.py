"""The ``watchflow.toml`` loader.

Under ADR-0012 ``config/`` is a pure loader: it reads and validates configuration and
returns the core-owned ``WatchflowConfig`` (``watchflow.core.config``). It defines no
domain models of its own, and the dependency direction is one-way, ``config -> core``.
See ``MODULE_SPECIFICATIONS.md`` §10.
"""

from pathlib import Path

from watchflow.core.config import WatchflowConfig


def load(path: Path) -> WatchflowConfig:
    """Parse and validate a ``watchflow.toml`` into a ``WatchflowConfig``.

    Structural skeleton (task 0.3): the TOML read, the environment-variable overlay,
    and ``pydantic`` validation are implemented in task 1.1. See
    ``PROJECT_STRUCTURE.md`` §3 for the precedence rules this loader will apply.

    Args:
        path: Filesystem path to the ``watchflow.toml`` to load.

    Returns:
        The validated top-level configuration.
    """
    raise NotImplementedError
