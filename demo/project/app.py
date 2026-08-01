"""The toy module the demo's tests exercise — this is the file the recording "edits".

Deliberately trivial: the README clip has to record a *real* ScorpioWatch run, and a real
run is only worth watching if the work it triggers finishes in a fraction of a second.
"""


def add(a: int, b: int) -> int:
    """Return the sum of ``a`` and ``b``."""
    return a + b


def greet(name: str) -> str:
    """Return a greeting for ``name``."""
    return f"hello, {name}"
