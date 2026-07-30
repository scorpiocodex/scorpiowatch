"""``watchflow init`` — scaffold a starter ``watchflow.toml``.

Writes a minimal, valid config (one filesystem Trigger running one subprocess Step) into the
target directory, refusing to clobber an existing file unless ``--force`` is given.
"""

from pathlib import Path
from typing import Annotated

import typer

from watchflow.cli.console import console, err_console

_CONFIG_FILENAME = "watchflow.toml"

# A minimal, valid v0.1.0 config in the confirmed schema. `kind` defaults to "subprocess" and
# the workflow/step names are supplied by the loader, so neither appears here.
_SCAFFOLD = """\
# watchflow.toml — WatchFlow configuration
# Docs: https://github.com/scorpiocodex/watchflow/blob/main/docs/WATCHFLOW.md

[[trigger]]
name = "run-tests"
source = "filesystem"
# One or more glob patterns. NOTE: until dedupe lands (v0.3.0) a file matching N of these
# patterns fires the workflow N times, so keep patterns non-overlapping for now.
patterns = ["**/*.py"]

  [trigger.workflow]
  # Each step runs as a subprocess (shell=False); `command` is an argv list, never a string.
  # `cwd` is where the step runs: "." is the watched project root (change it to a subdir to
  # scope a step there). Omit `cwd` entirely and the step still runs in the watched root.
  steps = [
    { command = ["python", "-m", "pytest", "-q"], timeout_s = 60, cwd = "." },
  ]
"""


def init(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Directory to scaffold watchflow.toml into.",
        ),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite an existing watchflow.toml."),
    ] = False,
) -> None:
    """Scaffold a starter watchflow.toml in PATH."""
    target = path / _CONFIG_FILENAME
    if target.exists() and not force:
        err_console.print(
            f"  [failure]✗[/failure] {target} already exists — pass [bold]--force[/bold] to replace"
        )
        raise typer.Exit(1)

    target.write_text(_SCAFFOLD, encoding="utf-8")
    console.print(f"  [success]✓[/success] wrote {target}")
    console.print("  edit it, then run:  [bold]watchflow run .[/bold]")
