"""Structural checks for the ``WatchflowConfig`` model (skeleton, task 0.3)."""

from pydantic import BaseModel

from watchflow.core.config import WatchflowConfig


def test_watchflow_config_is_a_pydantic_model() -> None:
    assert issubclass(WatchflowConfig, BaseModel)


def test_watchflow_config_holds_triggers() -> None:
    assert "triggers" in WatchflowConfig.model_fields
