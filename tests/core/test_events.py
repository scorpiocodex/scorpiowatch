"""Behavioral tests for the ``Event`` domain envelope (MODULE_SPECIFICATIONS §2)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from watchflow.core.events import Event


def make_event(**overrides: Any) -> Event:
    """Build a valid ``Event``, overriding any field by keyword."""
    fields: dict[str, Any] = {
        "id": uuid4(),
        "source": "filesystem",
        "type": "modified",
        "payload": {"path": "src/api.py"},
        "timestamp": datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return Event(**fields)


def test_event_constructs_with_all_fields() -> None:
    event_id = uuid4()
    timestamp = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    event = Event(
        id=event_id,
        source="filesystem",
        type="modified",
        payload={"path": "a.py"},
        timestamp=timestamp,
        metadata={"origin": "human"},
    )
    assert event.id == event_id
    assert event.source == "filesystem"
    assert event.type == "modified"
    assert event.payload == {"path": "a.py"}
    assert event.timestamp == timestamp
    assert event.metadata == {"origin": "human"}


def test_metadata_defaults_to_an_empty_dict() -> None:
    assert make_event().metadata == {}


def test_metadata_default_is_not_shared_between_instances() -> None:
    first = make_event()
    second = make_event()
    first.metadata["only_first"] = 1
    assert second.metadata == {}


def test_json_round_trip_preserves_every_field() -> None:
    event = make_event(metadata={"origin": "test"})
    restored = Event.model_validate_json(event.model_dump_json())
    assert restored == event


def test_id_accepts_a_uuid_string_and_coerces_it() -> None:
    raw = "12345678-1234-5678-1234-567812345678"
    assert make_event(id=raw).id == UUID(raw)


def test_event_is_frozen_attribute_reassignment_raises() -> None:
    event = make_event()
    with pytest.raises(ValidationError):
        event.source = "cron"


def test_declares_exactly_the_specified_fields() -> None:
    assert set(Event.model_fields) == {
        "id",
        "source",
        "type",
        "payload",
        "timestamp",
        "metadata",
    }
