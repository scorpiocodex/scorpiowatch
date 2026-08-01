"""The tests ScorpioWatch reruns whenever a ``.py`` file in this directory changes.

Kept flat (beside ``app.py``, no ``tests/`` package) so ``import app`` resolves under any
invocation, and kept trivial so the whole suite finishes in well under a second.
"""

from app import add, greet


def test_adds() -> None:
    assert add(2, 2) == 4


def test_adds_negatives() -> None:
    assert add(-1, 1) == 0


def test_greets() -> None:
    assert greet("swatch") == "hello, swatch"
