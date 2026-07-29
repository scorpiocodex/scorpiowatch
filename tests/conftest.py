"""Shared test fixtures.

The CLI's ``configure_logging`` reconfigures ``structlog`` globally (routing engine logs to
``sys.stderr``). Under the CLI test runner ``sys.stderr`` is a captured stream that is closed
once the invocation ends, so without cleanup a later test's ``structlog`` call would write to a
closed file. Resetting to structlog's defaults after every test keeps that reconfiguration from
leaking across tests.
"""

from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    """Restore structlog's default configuration after each test."""
    yield
    structlog.reset_defaults()
