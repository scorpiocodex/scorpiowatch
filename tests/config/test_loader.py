"""Structural checks for the config loader signature (skeleton, task 0.3)."""

import inspect
from pathlib import Path

from watchflow.config.loader import load
from watchflow.core.config import WatchflowConfig


def test_load_signature_matches_the_spec() -> None:
    sig = inspect.signature(load)
    assert list(sig.parameters) == ["path"]
    assert sig.parameters["path"].annotation is Path
    assert sig.return_annotation is WatchflowConfig
