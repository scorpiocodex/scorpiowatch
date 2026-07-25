"""Core event types: the ``Event`` envelope every Source Adapter emits.

See ``MODULE_SPECIFICATIONS.md`` §2. This module defines the domain ``Event``; the
bounded ``EventBus`` that carries it is implemented alongside this envelope in task 1.1.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Event(BaseModel):
    """A normalized event emitted by a Source Adapter onto the EventBus.

    Attributes:
        id: Stable unique identifier for this event.
        source: Name of the adapter that produced the event (e.g. ``"filesystem"``).
        type: Adapter-defined event type (e.g. ``"modified"``).
        payload: Structured, adapter-specific event data.
        timestamp: When the event was observed.
        metadata: Optional provenance/context annotations; defaults to empty.
    """

    id: UUID
    source: str
    type: str
    payload: dict[str, Any]
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
