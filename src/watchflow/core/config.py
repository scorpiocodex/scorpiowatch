"""Core configuration domain model: ``WatchflowConfig``.

Under ADR-0012 the domain models live in the core layer and ``config/`` is a pure
loader, so the top-level validated configuration the Engine consumes is owned here,
not in ``config/``. See ``MODULE_SPECIFICATIONS.md`` §10/§11.

Filename note: the spec names the type ``WatchflowConfig`` but not a module for it, so
``core/config.py`` is the chosen home — a core-owned domain model, deliberately
distinct from the ``watchflow.config`` loader package. The model is
``watchflow.core.config.WatchflowConfig``; the loader is ``watchflow.config.loader.load``.
"""

from pydantic import BaseModel, Field

from watchflow.core.triggers import Trigger


class WatchflowConfig(BaseModel):
    """The top-level validated configuration parsed from ``watchflow.toml``.

    The domain aggregate the ``Engine`` consumes (``MODULE_SPECIFICATIONS.md`` §11) and
    the config loader returns (§10). Structural skeleton (task 0.3): the scheduler,
    MCP, and daemon sections are added with the loader in task 1.1.

    Attributes:
        triggers: The declared triggers (each carrying its Workflow); empty by default.
    """

    triggers: list[Trigger] = Field(default_factory=list)
