"""Core ports (hexagonal): abstract interfaces the core owns and adapters implement.

Home of the ``SourceAdapter`` Protocol (ADR-0010, Option A). The Protocol lives in the
core layer so the dependency direction is unidirectional: concrete adapters import
inward from here, and the core imports nothing from ``adapters/``. See
``MODULE_SPECIFICATIONS.md`` §1 and ``ARCHITECTURE.md`` §2.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from watchflow.core.events import Event


class SourceAdapter(Protocol):
    """The abstract contract every event source implements.

    An adapter normalizes some upstream signal (a filesystem change, a cron tick, a
    manual trigger, an MCP tool call, ...) into ``Event``s the core can consume
    without knowing the concrete source. Implementations live in ``adapters/``
    (core-bundled) or in ``plugins/`` (optional), never in the core itself.

    Attributes:
        name: Stable identifier for this adapter, used as an ``Event.source`` value.
    """

    name: str

    async def start(self) -> None:
        """Begin producing events from the underlying source."""
        ...

    async def stop(self) -> None:
        """Stop producing events and release resources.

        Idempotent and safe to call before ``start()``.
        """
        ...

    def events(self) -> AsyncIterator[Event]:
        """Yield normalized events as they arrive.

        Returns:
            An async iterator over ``Event``s. Per ``MODULE_SPECIFICATIONS.md`` §1 an
            implementation never raises for a single malformed upstream item — it
            logs and skips.
        """
        ...
