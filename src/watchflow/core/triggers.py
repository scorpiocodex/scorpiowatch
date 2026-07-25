"""Core trigger domain model: ``Trigger`` and its ``MatchSpec``.

See ``MODULE_SPECIFICATIONS.md`` §3 and ``PROJECT_STRUCTURE.md`` §1. This module holds
the ``Trigger`` *model* skeleton only; the ``TriggerEngine`` that evaluates it is a
later addition (task 1.1) and is intentionally absent here.
"""

from pydantic import BaseModel

from watchflow.core.workflow import Workflow


class MatchSpec(BaseModel):
    """How a Trigger decides that an Event is relevant.

    §3 types ``Trigger.match`` as a ``MatchSpec`` but does not enumerate its variants;
    the comment there lists ``glob | cron_expr | predicate | mcp_tool_name``. This is a
    placeholder skeleton carrying no fields yet — the concrete discriminated union is
    defined in task 1.1.
    """


class Trigger(BaseModel):
    """A declared rule binding matched Events to a Workflow.

    See ``MODULE_SPECIFICATIONS.md`` §3.

    Attributes:
        name: Identifier for this trigger.
        source: Which adapter's events this trigger considers.
        match: How events are matched (see ``MatchSpec``).
        threshold: Minimum confidence score, in ``[0, 1]``, required to fire.
        cooldown_ms: Minimum spacing between fires, in milliseconds.
        workflow: The Workflow admitted when this trigger fires.
    """

    name: str
    source: str
    match: MatchSpec
    threshold: float = 0.5
    cooldown_ms: int = 0
    workflow: Workflow
