"""Structural checks for the ``SourceAdapter`` port (skeleton, task 0.3)."""

from watchflow.core.ports import SourceAdapter


def test_source_adapter_is_a_protocol() -> None:
    # typing.Protocol subclasses carry this marker; concrete classes do not.
    assert getattr(SourceAdapter, "_is_protocol", False) is True


def test_source_adapter_declares_the_contract_methods() -> None:
    for method in ("start", "stop", "events"):
        assert hasattr(SourceAdapter, method)
