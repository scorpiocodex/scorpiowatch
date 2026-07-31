"""WatchFlow — cross-platform, event-driven workflow orchestration engine.

The single public embeddable entry point is :class:`~swatch.core.engine.Engine`
(``MODULE_SPECIFICATIONS.md`` §11); a host process wires the whole pipeline with::

    from swatch import Engine
"""

from swatch.core.engine import Engine

__all__ = ["Engine"]
