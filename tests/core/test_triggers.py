"""Structural checks for the ``Trigger``/``MatchSpec`` models (skeleton, task 0.3)."""

from pydantic import BaseModel

from watchflow.core.triggers import MatchSpec, Trigger


def test_trigger_and_matchspec_are_pydantic_models() -> None:
    assert issubclass(Trigger, BaseModel)
    assert issubclass(MatchSpec, BaseModel)


def test_trigger_declares_the_specified_fields() -> None:
    assert set(Trigger.model_fields) == {
        "name",
        "source",
        "match",
        "threshold",
        "cooldown_ms",
        "workflow",
    }
