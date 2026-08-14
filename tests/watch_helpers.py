"""Shared waiting helpers for the tests that drive a real filesystem watch.

Every wait here is conditional — poll until the thing we need has actually happened. A fixed
sleep would have to be simultaneously long enough for the slowest loaded CI runner and short
enough not to pad the suite, and no constant is both: inotify, FSEvents, and
ReadDirectoryChangesW differ by an order of magnitude in delivery latency, and a networked or
9p-backed mount stretches that further still (a 0.4s wait that is ample on a local Linux disk
misses the event entirely under WSL's ``/mnt/c``). The ceilings below are therefore set
generously, because they never pace a healthy run — they only bound a hang.

The module is deliberately not named ``test_*``, so pytest imports it without collecting it.
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from swatch.core.events import Event

POLL_S = 0.02
WAIT_S = 20.0

# The sentinel rewritten until the watch delivers an event for it (see :func:`arm`). Two
# properties matter. It is clear of watchfiles' ``DefaultFilter`` (no leading dot, no
# ``~``/``.pyc``-style suffix), so it can never be silently filtered out — which would look
# exactly like a watch that never armed. And its ``.tmp`` suffix matches none of the
# ``**/*.py`` patterns these tests trigger on, so arming can never fire a workflow of its own
# and perturb what a test is asserting.
ARM_NAME = "_swatch_arm.tmp"


def names(events: list[Event]) -> set[str]:
    """The basenames of the paths carried by ``events``."""
    return {Path(event.payload["path"]).name for event in events}


def pairs(events: list[Event]) -> set[tuple[str, str]]:
    """The ``(kind, basename)`` pairs carried by ``events``.

    Waiting on a pair rather than on a bare basename is what keeps a wait honest across
    backends: macOS replays the entries that already existed when a watch opened as fresh
    ``added`` events, so a name-only wait for a file that predates the watch is satisfied by
    the replay instead of by the change under test.
    """
    return {(event.type, Path(event.payload["path"]).name) for event in events}


async def wait_until(predicate: Callable[[], bool], *, what: str) -> None:
    """Poll ``predicate`` until it is true, or fail naming ``what`` we were waiting for.

    Args:
        predicate: Checked every :data:`POLL_S` until true.
        what: Named in the failure message, so a timeout says what never happened.

    Raises:
        AssertionError: If ``predicate`` is still false after :data:`WAIT_S`.
    """
    deadline = time.monotonic() + WAIT_S
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {WAIT_S}s waiting for {what}")
        await asyncio.sleep(POLL_S)


async def arm(root: Path, collected: list[Event]) -> None:
    """Block until the watch over ``root`` is provably live, by making it prove it.

    ``watchfiles`` exposes no "the watch is established" signal, and a change applied before
    it is established is lost silently — the classic source of a flaky watcher test, because
    the mutation lands in the gap and no event ever arrives. Rewriting a sentinel until an
    event for it comes back replaces that guess with evidence: delivery *is* the proof.

    Args:
        root: The watched directory to write the sentinel into.
        collected: The list the test is accumulating delivered events into; polled for the
            sentinel's arrival.

    Raises:
        AssertionError: If no event for the sentinel arrives within :data:`WAIT_S`.
    """
    sentinel = root / ARM_NAME
    deadline = time.monotonic() + WAIT_S
    while ARM_NAME not in names(collected):
        if time.monotonic() > deadline:
            raise AssertionError(f"the watch never armed: no event for {ARM_NAME} in {WAIT_S}s")
        await asyncio.to_thread(sentinel.write_text, str(time.monotonic()))
        await asyncio.sleep(POLL_S)
