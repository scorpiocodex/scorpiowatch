"""Structural checks for the ``SwatchConfig`` model (skeleton, task 0.3)."""

from pydantic import BaseModel

from swatch.core.config import SwatchConfig


def test_swatch_config_is_a_pydantic_model() -> None:
    assert issubclass(SwatchConfig, BaseModel)


def test_swatch_config_holds_triggers() -> None:
    assert "triggers" in SwatchConfig.model_fields
