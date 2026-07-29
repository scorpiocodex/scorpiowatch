"""``structlog`` configuration for the foreground CLI process.

The core and adapter layers log through ``structlog`` (never ``print`` — ``CODING_STANDARD.md``
§6); this routes that output to **stderr** so the human-readable ``rich`` rendering on stdout
stays clean, and sets the level from ``--verbose``. Called once, at the start of a command.
"""

import logging
import sys

import structlog


def configure_logging(*, verbose: bool) -> None:
    """Configure ``structlog`` to render engine logs to stderr at the chosen level.

    Args:
        verbose: Emit ``DEBUG`` and above when true, otherwise ``INFO`` and above.
    """
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        # No first-use caching: a cached logger would bind permanently to the ``sys.stderr``
        # captured here, surviving into any later reconfiguration (and, under a test runner
        # that swaps/closes streams, would write to a closed file).
        cache_logger_on_first_use=False,
    )
