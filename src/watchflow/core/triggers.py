"""Core trigger domain model and matching: ``Trigger``, ``MatchSpec``, ``TriggerEngine``.

See ``MODULE_SPECIFICATIONS.md`` §3. ``MatchSpec`` is a discriminated union over the four
variants §3 names (``glob | cron_expr | predicate | mcp_tool_name``). For v0.1.0 only the
``glob`` variant is implemented — matching is boolean (a glob hit), with **no confidence
scoring** (``ROADMAP.md`` v0.1.0: "TriggerEngine with glob patterns, no scoring yet";
scoring arrives in v0.2.0). The other three variants are present in the union but raise
``NotImplementedError`` until their roadmap version.

The ``TriggerEngine`` also drives matching off the ``EventBus``: :meth:`TriggerEngine.evaluate`
subscribes to the bus, runs the pure :meth:`TriggerEngine.matching_triggers` on each event,
and emits a :class:`TriggerFired` per match to a sink. Nothing is scheduled or executed here
(that is task 1.3+); a match yields a ``TriggerFired`` and nothing more.
"""

import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from functools import lru_cache
from typing import Annotated, Literal, cast

from pydantic import BaseModel, Field

from watchflow.core.events import Event, EventBus
from watchflow.core.workflow import Workflow


@lru_cache(maxsize=512)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a glob pattern into a compiled, path-aware regex.

    Semantics: ``*`` matches any run of characters within a single path segment, ``?``
    matches one non-separator character, and ``**`` spans zero or more segments (so
    ``**/*.py`` matches both ``api.py`` and ``src/api.py``). Matching is anchored to the
    whole path via ``fullmatch``.

    Args:
        pattern: A forward-slash glob pattern.

    Returns:
        The compiled regular expression equivalent to ``pattern``.
    """
    parts: list[str] = []
    index, length = 0, len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern[index + 1 : index + 2] == "*":
                index += 2
                if pattern[index : index + 1] == "/":
                    index += 1
                    parts.append("(?:.*/)?")  # '**/' — zero or more directories
                else:
                    parts.append(".*")  # '**' — anything, path separators included
            else:
                parts.append("[^/]*")  # '*' — within a single segment
                index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("".join(parts))


class GlobMatch(BaseModel):
    """Match events whose ``payload["path"]`` matches a glob ``pattern``.

    Attributes:
        kind: Discriminator; always ``"glob"``.
        pattern: A forward-slash glob (e.g. ``"**/*.py"``).
    """

    kind: Literal["glob"] = "glob"
    pattern: str

    def matches(self, event: Event) -> bool:
        """Return whether ``event``'s path matches this glob.

        The path is read from ``event.payload["path"]`` and normalized to forward
        slashes before matching, so a Trigger authored on one OS matches identically on
        another (Constitution Article II). An event without a string ``path`` never
        matches (and never raises).

        Args:
            event: The event to test.

        Returns:
            ``True`` if the event carries a path that matches ``pattern``.
        """
        path = event.payload.get("path")
        if not isinstance(path, str):
            return False
        return _compile_glob(self.pattern).fullmatch(path.replace("\\", "/")) is not None


class CronExprMatch(BaseModel):
    """Match cron-tick events against a cron expression. Deferred to v0.2.0.

    Attributes:
        kind: Discriminator; always ``"cron_expr"``.
        expr: The cron expression (unused until implemented).
    """

    kind: Literal["cron_expr"] = "cron_expr"
    expr: str

    def matches(self, event: Event) -> bool:
        """Not implemented in v0.1.0.

        Raises:
            NotImplementedError: Always — cron matching lands with the ``CronAdapter``.
        """
        raise NotImplementedError(
            "cron_expr matching arrives with the CronAdapter in v0.2.0 (ROADMAP.md)"
        )


class PredicateMatch(BaseModel):
    """Match via a named predicate function. Deferred to v2.1.0.

    Attributes:
        kind: Discriminator; always ``"predicate"``.
        predicate: The predicate's registered name (unused until implemented).
    """

    kind: Literal["predicate"] = "predicate"
    predicate: str

    def matches(self, event: Event) -> bool:
        """Not implemented in v0.1.0.

        Raises:
            NotImplementedError: Always — predicate ``match`` specs land in v2.1.0.
        """
        raise NotImplementedError(
            "predicate matching arrives in v2.1.0 (ROADMAP.md: regex/predicate match specs)"
        )


class McpToolNameMatch(BaseModel):
    """Match MCP tool-call events by tool name. Deferred to v0.3.0.

    Attributes:
        kind: Discriminator; always ``"mcp_tool_name"``.
        tool_name: The MCP tool name to match (unused until implemented).
    """

    kind: Literal["mcp_tool_name"] = "mcp_tool_name"
    tool_name: str

    def matches(self, event: Event) -> bool:
        """Not implemented in v0.1.0.

        Raises:
            NotImplementedError: Always — MCP-tool matching lands with the adapter.
        """
        raise NotImplementedError(
            "mcp_tool_name matching arrives with the MCPTriggerAdapter in v0.3.0 (ROADMAP.md)"
        )


MatchSpec = Annotated[
    GlobMatch | CronExprMatch | PredicateMatch | McpToolNameMatch,
    Field(discriminator="kind"),
]
"""How a Trigger decides an Event is relevant — a discriminated union on ``kind``."""


class Trigger(BaseModel):
    """A declared rule binding matched Events to a Workflow.

    See ``MODULE_SPECIFICATIONS.md`` §3.

    Attributes:
        name: Identifier for this trigger.
        source: Which adapter's events this trigger considers (matched against
            ``Event.source``).
        match: How events are matched (see ``MatchSpec``).
        threshold: Minimum confidence score, in ``[0, 1]``, required to fire. Unused in
            v0.1.0 boolean matching; consumed by confidence scoring in v0.2.0.
        cooldown_ms: Minimum spacing between this trigger's fires for one matched path, in
            milliseconds — the Scheduler's leading-edge cooldown window (§4). ``None`` (the
            default) uses the Scheduler's default window; ``0`` explicitly disables cooldown
            for this trigger; ``N > 0`` sets an N-millisecond window. Consumed by the
            Scheduler, not by matching.
        workflow: The Workflow admitted when this trigger fires.
    """

    name: str
    source: str
    match: MatchSpec
    threshold: float = 0.5
    cooldown_ms: int | None = None
    workflow: Workflow


class TriggerFired(BaseModel):
    """The result of a Trigger matching an Event, emitted for the Scheduler to admit.

    ``MODULE_SPECIFICATIONS.md`` §3 names ``TriggerFired`` as ``evaluate``'s output but
    does not enumerate its fields; these are the minimum the downstream Scheduler needs
    — §4's ``admit``/dedupe keys on ``trigger.name`` and ``event.payload``, so a fired
    record carries the Trigger that matched and the Event that caused it. A confidence
    ``score`` joins these when scoring lands in v0.2.0 (``ROADMAP.md``); v0.1.0 matching
    is boolean, so a ``TriggerFired`` existing *is* the match.

    Attributes:
        trigger: The Trigger that matched.
        event: The Event that caused the match.
    """

    trigger: Trigger
    event: Event


class TriggerEngine:
    """Hold the Triggers, report which match an Event, and drive matching off the bus.

    v0.1.0 scope (``ROADMAP.md``): boolean glob matching only — **no confidence
    scoring**. A Trigger matches an Event when its ``source`` equals ``event.source`` and
    its ``MatchSpec`` matches. :meth:`matching_triggers` is a pure function of the
    registry and an event; :meth:`evaluate` is the async driver that subscribes to the
    bus and emits a ``TriggerFired`` per match to a sink. Confidence scoring
    (``pattern_match * recency * history_weight``, ``MODULE_SPECIFICATIONS.md`` §3), and
    the scheduling/execution a ``TriggerFired`` eventually feeds, arrive in later tasks.
    """

    def __init__(self) -> None:
        """Create an empty engine with no registered Triggers."""
        self._triggers: list[Trigger] = []

    def register(self, trigger: Trigger) -> None:
        """Add ``trigger`` to the registry.

        Args:
            trigger: The Trigger to register.
        """
        self._triggers.append(trigger)

    @property
    def triggers(self) -> tuple[Trigger, ...]:
        """The registered Triggers, in registration order."""
        return tuple(self._triggers)

    def matching_triggers(self, event: Event) -> list[Trigger]:
        """Return every registered Trigger that matches ``event``.

        Pure: it reads the registry and returns matches with no scheduling, execution,
        I/O, or mutation. A Trigger matches when its ``source`` equals ``event.source``
        and its ``MatchSpec`` matches the event.

        Args:
            event: The event to match against the registry.

        Returns:
            The matching Triggers, in registration order (empty if none match).
        """
        return [
            trigger
            for trigger in self._triggers
            if trigger.source == event.source and trigger.match.matches(event)
        ]

    async def evaluate(
        self, bus: EventBus, on_fired: Callable[[TriggerFired], Awaitable[None]]
    ) -> None:
        """Consume events from ``bus`` and emit a ``TriggerFired`` per matching Trigger.

        This is the async, bus-driven form of ``MODULE_SPECIFICATIONS.md`` §3's
        ``evaluate`` (whose illustrative signature is per-event and returns a single
        ``TriggerFired | None``): it subscribes to the bus with ``topic=None`` — the
        engine gates on ``event.source`` in :meth:`matching_triggers`, not on the bus's
        ``event.type`` topic — and for each event awaits ``on_fired`` once per matching
        Trigger, in registration order. It runs until the awaiting task is cancelled,
        at which point the subscription is closed and the bus subscriber is removed. No
        scheduling or execution happens here; a match yields a ``TriggerFired`` only.

        Args:
            bus: The EventBus to consume events from.
            on_fired: An async sink awaited once per ``TriggerFired``.
        """
        subscription = cast(AsyncGenerator[Event, None], bus.subscribe())
        try:
            async for event in subscription:
                for trigger in self.matching_triggers(event):
                    await on_fired(TriggerFired(trigger=trigger, event=event))
        finally:
            await subscription.aclose()
