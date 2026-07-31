"""The WatchFlow CLI application and its process entry point.

Builds the ``typer`` app (``run`` + ``init`` — the v0.1.0 command surface per ``ROADMAP.md``;
``check``/``doctor``/``list``/``mcp``/… in ``UI_DESIGN.md`` §4.2 arrive in later versions) and
maps its outcome to the ``EXECUTION_MODEL.md`` §7.2 exit codes. ``main`` runs the app in
``click``'s non-standalone mode so it owns the codes: in particular it remaps the default
usage-error code (``2``) to ``3``, keeping ``2`` reserved for config errors as §7.2 requires.
"""

import sys
from contextlib import suppress

import typer

from swatch.cli.commands.init import init
from swatch.cli.commands.run import run
from swatch.cli.exit_codes import ExitCode

# typer (0.27+) vendors click into ``typer._click`` and raises the vendored exceptions, not
# the top-level ``click`` package's. ``typer.Abort`` is re-exported publicly (used below); the
# usage-error class is not, so resolve it from the vendored module, falling back to real click
# for older, un-vendored typer builds.
try:
    from typer._click.exceptions import UsageError as _UsageError
except ImportError:  # pragma: no cover — older typer using the un-vendored click
    from click.exceptions import UsageError as _UsageError  # type: ignore[assignment]

app = typer.Typer(
    name="swatch",
    help="Cross-platform, event-driven workflow orchestration.",
    no_args_is_help=True,
    add_completion=False,
)
app.command()(run)
app.command()(init)


def _reconfigure_utf8(stream: object) -> None:
    """Force ``stream`` to UTF-8 (errors replaced) when it supports reconfiguration.

    The CLI's status chrome uses Unicode marks (``✓`` / ``✗``). On a legacy Windows console
    the process starts with a cp1252-backed stream that cannot encode them, so ``rich`` raises
    ``UnicodeEncodeError``; UTF-8 stdio makes the output portable (Constitution Article II).
    Streams without ``reconfigure`` (already-wrapped or redirected) are left untouched.

    Args:
        stream: The standard stream to reconfigure, if it is a reconfigurable text stream.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    with suppress(Exception):
        reconfigure(encoding="utf-8", errors="replace")


def _configure_stdio() -> None:
    """Make ``stdout``/``stderr`` able to encode the CLI's Unicode chrome on any platform."""
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)


def main() -> None:
    """Console-script entry point: run the CLI, mapping outcomes to §7.2 exit codes."""
    _configure_stdio()
    try:
        result = app(standalone_mode=False)
    except _UsageError as error:
        # click exits usage errors with 2; §7.2 reserves 2 for config errors and maps usage
        # errors to 3, so remap here.
        error.show()
        sys.exit(int(ExitCode.USAGE_ERROR))
    except typer.Abort:
        sys.exit(int(ExitCode.SIGINT))
    # In non-standalone mode the app returns the command's value (``None`` on success) or the
    # code carried by a ``typer.Exit``; a non-zero code becomes the process exit status.
    if isinstance(result, int) and result != 0:
        sys.exit(result)


if __name__ == "__main__":  # pragma: no cover — module executed as a script
    main()
