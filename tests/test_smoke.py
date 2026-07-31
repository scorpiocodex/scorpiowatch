"""Smoke test: proves the pytest + coverage wiring runs against the package.

This is the one trivial test permitted by task 0.2; real per-module tests arrive
with the modules they cover (CODING_STANDARD.md §4).
"""

import swatch


def test_package_imports() -> None:
    assert swatch.__name__ == "swatch"
