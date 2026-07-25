"""Structural checks for the ``Event`` domain model (skeleton, task 0.3)."""

from pydantic import BaseModel

from watchflow.core.events import Event


def test_event_is_a_pydantic_model() -> None:
    assert issubclass(Event, BaseModel)


def test_event_declares_the_specified_fields() -> None:
    assert set(Event.model_fields) == {
        "id",
        "source",
        "type",
        "payload",
        "timestamp",
        "metadata",
    }
