"""The ScorpioWatch CLI application and its process entry point.

Builds the ``typer`` app (``run`` + ``init`` — the v0.1.0 command surface per ``ROADMAP.md``;
``check``/``doctor``/``list``/``mcp``/… in ``UI_DESIGN.md`` §4.2 arrive in later versions) and
maps its outcome to the ``EXECUTION_MODEL.md`` §7.2 exit codes. ``main`` runs the app in
``click``'s non-standalone mode so it owns the codes: in particular it remaps the default
usage-error code (``2``) to ``3``, keeping ``2`` reserved for config errors as §7.2 requires.

The app also carries the one root-level flag, ``--version``. Besides being the affordance every
installed CLI is expected to answer, it is the scaffold's demo command: ``swatch init`` writes a
step that runs ``swatch --version``, because the ``swatch`` executable is the one program a user
who just ran ``swatch init`` is guaranteed to have on their PATH (see ``commands/init.py``).
"""

import sys
from contextlib import suppress
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from typing import Annotated

import typer

from swatch.cli.commands.init import init
from swatch.cli.commands.run import run
from swatch.cli.console import console
from swatch.cli.exit_codes import ExitCode

# The PyPI distribution name (the import package is ``swatch``; the project is ``scorpiowatch``).
_DISTRIBUTION = "scorpiowatch"

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


def _installed_version() -> str:
    """The installed distribution's version string.

    Returns:
        The version recorded in the installed distribution's metadata, or ``"unknown"`` when
        ScorpioWatch is being run straight from a source tree that was never installed (no
        ``.dist-info`` exists to read a version from).
    """
    try:
        return _distribution_version(_DISTRIBUTION)
    except PackageNotFoundError:
        return "unknown"


def _version_callback(value: bool) -> None:
    """Print ``swatch <version>`` and exit, when ``--version`` was given.

    Eager and terminal, the conventional shape for a version flag: it answers before the app
    insists on a subcommand, so ``swatch --version`` is a complete invocation.

    Args:
        value: Whether ``--version`` was passed.

    Raises:
        typer.Exit: When ``value`` is true, ending the invocation with success.
    """
    if not value:
        return
    console.print(f"swatch {_installed_version()}")
    raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed ScorpioWatch version and exit.",
        ),
    ] = False,
) -> None:
    """Cross-platform, event-driven workflow orchestration."""


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
