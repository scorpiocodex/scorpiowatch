"""WatchFlow — cross-platform, event-driven workflow orchestration engine.

The single public embeddable entry point is :class:`~watchflow.core.engine.Engine`
(``MODULE_SPECIFICATIONS.md`` §11); a host process wires the whole pipeline with::

    from watchflow import Engine
"""

from watchflow.core.engine import Engine

__all__ = ["Engine"]
