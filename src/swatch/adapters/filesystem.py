"""``FilesystemAdapter``: a core-bundled Source Adapter over ``watchfiles``.

Translates filesystem changes under a watched root into ``Event``s. It is async-native
— built on ``watchfiles.awatch``, so there is no bridged thread in Python and no polling
loop (Constitution Articles I and IV). Linux-first for v0.1.0, with macOS and Windows
following (``ROADMAP.md`` v0.1.0 / v0.2.0 / v0.3.0); ``watchfiles`` itself is
cross-platform, so the adapter runs on all three today.

Implements the ``SourceAdapter`` Protocol from ``core/ports.py``: it imports inward from
the core only, and no core module imports it (``ARCHITECTURE.md`` §2; ADR-0010 Option A).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from watchfiles import Change, awatch

from swatch.core.events import Event

if TYPE_CHECKING:
    from swatch.core.ports import SourceAdapter

_log = structlog.get_logger(__name__)

# watchfiles' ``Change`` variants → the ``Event.type`` string this adapter emits.
_CHANGE_TYPES: dict[Change, str] = {
    Change.added: "added",
    Change.modified: "modified",
    Change.deleted: "deleted",
}

# watchfiles' own defaults. This adapter applies no debounce/dedup policy of its own —
# coalescing bursts of the same underlying change is the Scheduler's concern
# (``MODULE_SPECIFICATIONS.md`` §4; ``EXECUTION_MODEL.md`` §5); these knobs are merely
# passed through to watchfiles.
_DEFAULT_DEBOUNCE_MS = 1600
_DEFAULT_STEP_MS = 50


class FilesystemAdapter:
    """A ``SourceAdapter`` that emits one ``Event`` per change under a watched root.

    Attributes:
        name: The adapter identity, used as every emitted ``Event.source``.
    """

    name = "filesystem"

    def __init__(
        self,
        root: Path | str,
        *,
        debounce_ms: int = _DEFAULT_DEBOUNCE_MS,
        step_ms: int = _DEFAULT_STEP_MS,
    ) -> None:
        """Watch ``root`` recursively for filesystem changes.

        Args:
            root: Directory to watch.
            debounce_ms: Passed to ``watchfiles`` — the window over which a burst of
                changes is coalesced before a batch is yielded.
            step_ms: Passed to ``watchfiles`` — how often the watch polls for changes
                and for the stop signal; it bounds how quickly ``stop()`` takes effect.
        """
        self._root = Path(root)
        self._debounce_ms = debounce_ms
        self._step_ms = step_ms
        self._stop_event = asyncio.Event()
        self._watch: AsyncIterator[set[tuple[Change, str]]] | None = None

    async def start(self) -> None:
        """Open the filesystem watch. Idempotent — a second call is a no-op."""
        if self._watch is not None:
            return
        self._stop_event = asyncio.Event()
        self._watch = awatch(
            self._root,
            stop_event=self._stop_event,
            debounce=self._debounce_ms,
            step=self._step_ms,
        )

    async def stop(self) -> None:
        """Stop the watch and release its resources.

        Idempotent and safe to call before ``start()``. Setting the stop event makes
        ``watchfiles`` return from its next step, so the ``events()`` stream ends
        cleanly with no lingering task or thread.
        """
        self._stop_event.set()
        self._watch = None

    def events(self) -> AsyncIterator[Event]:
        """Yield one ``Event`` per filesystem change until the adapter is stopped."""
        return self._stream()

    async def _stream(self) -> AsyncIterator[Event]:
        watch = self._watch
        if watch is None:
            return
        async for changes in watch:
            for event in self._translate(changes):
                yield event

    def _translate(self, changes: set[tuple[Change, str]]) -> list[Event]:
        """Translate a watchfiles change batch into events.

        An unrecognized change is logged and skipped rather than raised
        (``MODULE_SPECIFICATIONS.md`` §1).
        """
        events: list[Event] = []
        for change, path in changes:
            event_type = _CHANGE_TYPES.get(change)
            if event_type is None:
                _log.warning("filesystem.skip_unknown_change", change=repr(change), path=path)
                continue
            events.append(
                Event(
                    id=uuid4(),
                    source=self.name,
                    type=event_type,
                    payload={"path": path},
                    timestamp=datetime.now(UTC),
                )
            )
        return events


if TYPE_CHECKING:
    # Compile-time proof that FilesystemAdapter satisfies the SourceAdapter Protocol.
    _conforms: type[SourceAdapter] = FilesystemAdapter
