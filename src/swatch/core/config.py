"""Core configuration domain model: ``SwatchConfig``.

Under ADR-0012 the domain models live in the core layer and ``config/`` is a pure
loader, so the top-level validated configuration the Engine consumes is owned here,
not in ``config/``. See ``MODULE_SPECIFICATIONS.md`` §10/§11.

Filename note: the spec names the type ``SwatchConfig`` but not a module for it, so
``core/config.py`` is the chosen home — a core-owned domain model, deliberately
distinct from the ``swatch.config`` loader package. The model is
``swatch.core.config.SwatchConfig``; the loader is ``swatch.config.loader.load``.
"""

from pydantic import BaseModel, Field

from swatch.core.triggers import Trigger


class SwatchConfig(BaseModel):
    """The top-level validated configuration parsed from ``swatch.toml``.

    The domain aggregate the ``Engine`` consumes (``MODULE_SPECIFICATIONS.md`` §11) and
    the config loader returns (§10). Per ADR-0012 ``config/`` owns no models of its own:
    :func:`swatch.config.loader.load` reads the ergonomic ``swatch.toml`` and *maps*
    it onto the core-owned models (``Trigger``/``Workflow``/``Step``), so this aggregate is
    simply the list of fully-built ``Trigger``s the loader produced — the mapping (plural
    ``patterns`` → per-pattern ``Trigger``s, default step ``kind``, supplied names) lives in
    the loader, not in a second parallel schema here.

    For v0.1.0 the only section modelled is ``[[trigger]]``; the ``scheduler``, ``mcp``, and
    ``daemon`` sections named in ``PROJECT_STRUCTURE.md`` §3 arrive with their features in
    later versions and any such unknown top-level section is ignored by the loader for now.

    Attributes:
        triggers: The declared triggers (each carrying its Workflow); empty by default.
    """

    triggers: list[Trigger] = Field(default_factory=list)
