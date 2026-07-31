"""``watchflow init`` — scaffold a starter ``watchflow.toml``.

Writes a minimal, valid config into the target directory, refusing to clobber an existing file
unless ``--force`` is given. The scaffold is deliberately **language-neutral**: its one active
trigger runs a trivial portable command on any file change, and the breadth of the engine — it
runs *any* ``command`` (an argv list) on *any* glob-matched path, in any language or a mix — is
shown through commented, ready-to-uncomment examples (Python, JS/TS, Rust, Go, and a two-language
full-stack config). WatchFlow itself knows nothing about languages; the user supplies commands.
"""

from pathlib import Path
from typing import Annotated

import typer

from swatch.cli.console import console, err_console

_CONFIG_FILENAME = "watchflow.toml"

# A minimal, valid, language-neutral config. Only the first `[[trigger]]` is active; everything
# below it is commented documentation the user uncomments and edits. `kind` defaults to
# "subprocess" and the workflow/step names are supplied by the loader, so neither appears here.
_SCAFFOLD = """\
# watchflow.toml - WatchFlow configuration
# Docs: https://github.com/scorpiocodex/watchflow/blob/main/docs/WATCHFLOW.md
#
# WatchFlow watches files and runs commands when they change. It is language-agnostic: a
# trigger matches paths by glob and runs any `command` (an argv list) as a subprocess - it
# knows nothing about languages or toolchains. Point it at any project, in any language, or a
# mix; you supply the command and, optionally, the `cwd` it runs in.

# --- Active trigger: a ready-to-run demo. Replace `command` with your own. -------------------
[[trigger]]
name = "on-change"
source = "filesystem"
# Glob(s) to watch. `**/*` is every file under the watched root (the watcher already skips
# .git, node_modules, __pycache__, and the like). Per-trigger cooldown is on by default, so the
# burst of events one save produces collapses to a single run.
patterns = ["**/*"]

  [trigger.workflow]
  # `command` is an argv list run as a subprocess (shell=False), never a shell string. This
  # demo uses the Python that ships with WatchFlow as a portable "echo" - it is NOT a statement
  # that your project is Python. Swap in your real command: a test runner, linter, build,
  # deploy, notifier - anything.
  steps = [
    { command = ["python", "-c", "print('watchflow: a file changed')"] },
  ]

# --- Uncomment and adapt one for your stack --------------------------------------------------
# Python - run tests on a source change:
# [[trigger]]
# name = "python-tests"
# patterns = ["**/*.py"]
#   [trigger.workflow]
#   steps = [{ command = ["pytest", "-q"], timeout_s = 120 }]
#
# JavaScript / TypeScript - typecheck then test:
# [[trigger]]
# name = "js-checks"
# patterns = ["**/*.ts", "**/*.tsx", "**/*.js"]
#   [trigger.workflow]
#   steps = [
#     { command = ["npm", "run", "typecheck"], timeout_s = 120 },
#     { command = ["npm", "test"], timeout_s = 300 },
#   ]
#
# Rust - check on a source change:
# [[trigger]]
# name = "rust-check"
# patterns = ["**/*.rs"]
#   [trigger.workflow]
#   steps = [{ command = ["cargo", "check"], timeout_s = 300 }]
#
# Go - build and vet:
# [[trigger]]
# name = "go-build"
# patterns = ["**/*.go"]
#   [trigger.workflow]
#   steps = [{ command = ["go", "build", "./..."], timeout_s = 180 }]

# --- Full-stack / monorepo: two languages in one config, each in its own `cwd` ----------------
# A Node front end and a Python back end, side by side. Today you name each toolchain
# explicitly; per-trigger environment/toolchain resolution is a v1.1 item (see ROADMAP.md).
# [[trigger]]
# name = "frontend"
# patterns = ["frontend/**/*.ts", "frontend/**/*.tsx"]
#   [trigger.workflow]
#   steps = [{ command = ["npm", "test"], cwd = "frontend", timeout_s = 300 }]
#
# [[trigger]]
# name = "backend"
# patterns = ["backend/**/*.py"]
#   [trigger.workflow]
#   steps = [{ command = ["pytest", "-q"], cwd = "backend", timeout_s = 300 }]
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
