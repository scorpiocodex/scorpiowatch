"""``structlog`` configuration for the foreground CLI process.

The core and adapter layers narrate through ``structlog`` (never ``print`` —
``CODING_STANDARD.md`` §6). That raw engine chatter — UUIDs, argv, ``timeout_s``, float
durations — is **not** the human view (task 1.4 part 2): the ``RunReporter`` renders the
lifecycle instead. So this routes ``structlog`` by verbosity — under ``--verbose`` it renders
the full-fidelity records to **stderr** (the home for everything the default view hides); in
every other mode it drops them below ``CRITICAL`` so nothing engine-voiced reaches the terminal
and the ``RunReporter`` is the sole human/JSON projection. Called once, at the start of a command.
"""

import logging
import sys

import structlog


def configure_logging(*, verbose: bool) -> None:
    """Configure ``structlog``: full engine records to stderr under ``--verbose``, else dropped.

    Args:
        verbose: When true, render ``DEBUG``-and-above engine records to stderr. When false,
            filter at ``CRITICAL`` so the engine's own logging never reaches the terminal (the
            ``RunReporter`` owns the human view in the default / quiet / json modes).
    """
    level = logging.DEBUG if verbose else logging.CRITICAL
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
