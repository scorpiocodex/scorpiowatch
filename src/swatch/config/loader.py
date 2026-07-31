"""The ``watchflow.toml`` loader.

Under ADR-0012 ``config/`` is a pure loader: it reads and validates configuration and
returns the core-owned :class:`~swatch.core.config.SwatchConfig`. It defines no
domain models of its own — instead it *maps* the ergonomic TOML documented in
``WATCHFLOW.md`` §8 onto the core models (``Trigger`` / ``Workflow`` / ``Step``), which
remain the single validation target. The dependency direction is one-way, ``config ->
core`` (``MODULE_SPECIFICATIONS.md`` §10).

Mapping rules (v0.1.0):

- **``[[trigger]]``** → one or more core ``Trigger``s. A trigger declares ``patterns`` as a
  *list* of globs, but the core ``Trigger`` holds a single ``MatchSpec``; the loader
  therefore expands a trigger with N patterns into **N core ``Trigger``s**, each with one
  ``GlobMatch``, sharing the trigger's name and Workflow. Because dedupe/cooldown are
  deferred to v0.3.0 (``MODULE_SPECIFICATIONS.md`` §4), a single file matching M of a
  trigger's patterns fires its Workflow **M times** until then — keep patterns
  non-overlapping for now.
- **``[trigger.workflow]``** → a core ``Workflow`` whose ``name`` defaults to the trigger's
  name (the TOML omits it).
- **step tables** → core ``Step``s. ``kind`` is optional and defaults to ``"subprocess"``
  (the only kind in v0.1.0; ``plugin``/``http``/``mcp_tool`` land with their adapters).
  ``name`` is optional and defaults to the step's program (else ``step-N``).

Strictness: keys *inside* a ``[[trigger]]`` block (and its workflow/steps) are validated
strictly — an unknown key such as a ``patttern`` typo is a hard error, never silently
dropped. Unknown *top-level* sections (e.g. a future ``[mcp]``) are ignored so a
forward-looking ``watchflow.toml`` still loads. Any failure raises :class:`ConfigError`
with a precise, human-readable message and never a partially-applied config
(``MODULE_SPECIFICATIONS.md`` §10 invariant); the CLI maps that to exit code 2
(``EXECUTION_MODEL.md`` §7.2).
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from swatch.core.config import SwatchConfig
from swatch.core.triggers import GlobMatch, Trigger
from swatch.core.workflow import Step, Workflow

_DEFAULT_SOURCE = "filesystem"
_SUBPROCESS_KIND = "subprocess"

# The keys each block accepts. Anything else inside a trigger/workflow/step is a config
# error (a typo caught early), not a silently-ignored setting.
_TRIGGER_KEYS = frozenset({"name", "source", "patterns", "threshold", "cooldown_ms", "workflow"})
_WORKFLOW_KEYS = frozenset({"name", "steps"})
_STEP_KEYS = frozenset({"name", "kind", "command", "timeout_s", "cwd", "env_allowlist"})


class ConfigError(Exception):
    """A ``watchflow.toml`` could not be read, parsed, or validated.

    Raised for every configuration failure — a missing file, malformed TOML, an unknown
    key, an unsupported step kind, or a field that fails the core model's validation — so
    the CLI has a single exception to catch and render as a clean ``✗ config error``
    (exit code 2, ``EXECUTION_MODEL.md`` §7.2), never a raw traceback.

    Attributes:
        path: The config file the error concerns, if known.
    """

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        """Create a config error.

        Args:
            message: A precise, human-readable description of what is wrong.
            path: The ``watchflow.toml`` the error concerns, if known.
        """
        self.path = path
        super().__init__(message)


def load(path: Path) -> SwatchConfig:
    """Parse and validate a ``watchflow.toml`` into a :class:`SwatchConfig`.

    Reads ``path`` as TOML, maps every ``[[trigger]]`` onto the core ``Trigger`` /
    ``Workflow`` / ``Step`` models (see the module docstring for the mapping), and returns
    the aggregate. Unknown top-level sections are ignored; unknown keys inside a trigger
    are errors.

    Args:
        path: Filesystem path to the ``watchflow.toml`` to load.

    Returns:
        The validated top-level configuration.

    Raises:
        ConfigError: If the file is missing/unreadable, the TOML is malformed, or any
            trigger fails validation. The message is precise and no partial config is
            returned.
    """
    raw = _read_toml(path)
    triggers: list[Trigger] = []
    for index, table in enumerate(_trigger_tables(raw, path)):
        triggers.extend(_build_triggers(table, index, path))
    return SwatchConfig(triggers=triggers)


def _read_toml(path: Path) -> dict[str, Any]:
    """Read ``path`` as a TOML document, mapping every read/parse failure to ConfigError."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"config file not found: {path}", path=path) from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML: {error}", path=path) from error
    except OSError as error:
        raise ConfigError(f"could not read config: {error}", path=path) from error


def _trigger_tables(raw: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    """Return the ``[[trigger]]`` tables, ignoring every other top-level section."""
    triggers = raw.get("trigger", [])
    if not isinstance(triggers, list) or not all(isinstance(item, dict) for item in triggers):
        raise ConfigError("`[[trigger]]` must be an array of tables", path=path)
    return triggers


def _build_triggers(table: dict[str, Any], index: int, path: Path) -> list[Trigger]:
    """Map one ``[[trigger]]`` table to one core ``Trigger`` per declared pattern."""
    label = table.get("name")
    context = f"trigger {label!r}" if isinstance(label, str) else f"[[trigger]] #{index + 1}"
    _reject_unknown_keys(table, _TRIGGER_KEYS, context, path)

    name = _require_str(table, "name", context, path)
    source = table.get("source", _DEFAULT_SOURCE)
    patterns = _require_pattern_list(table, context, path)
    workflow = _build_workflow(table.get("workflow"), name, context, path)

    shared = {
        "name": name,
        "source": source,
        "threshold": table.get("threshold", 0.5),
        # Absent → None so the Scheduler applies its default window; an explicit 0 disables
        # cooldown for the trigger, N > 0 sets an N-ms window (see Trigger.cooldown_ms).
        "cooldown_ms": table.get("cooldown_ms"),
        "workflow": workflow,
    }
    triggers: list[Trigger] = []
    for pattern in patterns:
        # One core Trigger per pattern: the core match spec is a single glob, so a plural
        # `patterns` fans out here. Pre-dedupe (v0.3.0), overlapping patterns therefore fire
        # the shared Workflow once per matching pattern — see the module docstring.
        try:
            triggers.append(Trigger(match=GlobMatch(pattern=pattern), **shared))
        except ValidationError as error:
            raise ConfigError(f"{context}: {_first_error(error)}", path=path) from error
    return triggers


def _build_workflow(workflow_table: Any, default_name: str, context: str, path: Path) -> Workflow:
    """Map a ``[trigger.workflow]`` table to a core ``Workflow`` (name defaults to trigger)."""
    if not isinstance(workflow_table, dict):
        raise ConfigError(f"{context}: missing `[trigger.workflow]` table", path=path)
    _reject_unknown_keys(workflow_table, _WORKFLOW_KEYS, f"{context} workflow", path)

    steps_data = workflow_table.get("steps")
    if not isinstance(steps_data, list) or not steps_data:
        raise ConfigError(
            f"{context}: `[trigger.workflow]` needs a non-empty `steps` array", path=path
        )
    steps = [_build_step(step, i, context, path) for i, step in enumerate(steps_data)]

    try:
        return Workflow(name=workflow_table.get("name", default_name), steps=steps)
    except ValidationError as error:
        raise ConfigError(f"{context} workflow: {_first_error(error)}", path=path) from error


def _build_step(step_data: Any, index: int, context: str, path: Path) -> Step:
    """Map one step table to a core ``Step``; enforce the v0.1.0 ``subprocess`` kind."""
    step_context = f"{context} step #{index + 1}"
    if not isinstance(step_data, dict):
        raise ConfigError(f"{step_context}: each step must be a table", path=path)
    _reject_unknown_keys(step_data, _STEP_KEYS, step_context, path)

    kind = step_data.get("kind", _SUBPROCESS_KIND)
    if kind != _SUBPROCESS_KIND:
        raise ConfigError(
            f"{step_context}: step kind {kind!r} is not supported in v0.1.0 "
            f"(only {_SUBPROCESS_KIND!r}; plugin/http/mcp_tool arrive in later versions)",
            path=path,
        )

    fields = {
        key: step_data[key]
        for key in ("command", "timeout_s", "cwd", "env_allowlist")
        if key in step_data
    }
    name = step_data.get("name") or _default_step_name(step_data.get("command"), index)
    try:
        return Step(name=name, **fields)
    except ValidationError as error:
        raise ConfigError(f"{step_context}: {_first_error(error)}", path=path) from error


def _reject_unknown_keys(
    table: dict[str, Any], allowed: frozenset[str], context: str, path: Path
) -> None:
    """Raise ConfigError if ``table`` carries any key outside ``allowed`` (a likely typo)."""
    unknown = set(table) - allowed
    if unknown:
        offenders = ", ".join(sorted(unknown))
        permitted = ", ".join(sorted(allowed))
        raise ConfigError(f"{context}: unknown key(s) {offenders}; allowed: {permitted}", path=path)


def _require_str(table: dict[str, Any], key: str, context: str, path: Path) -> str:
    """Return ``table[key]`` as a required string, or raise a precise ConfigError."""
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"{context}: `{key}` is required and must be a non-empty string", path=path
        )
    return value


def _require_pattern_list(table: dict[str, Any], context: str, path: Path) -> list[str]:
    """Return the trigger's ``patterns`` as a non-empty list of glob strings."""
    patterns = table.get("patterns")
    if (
        not isinstance(patterns, list)
        or not patterns
        or not all(isinstance(pattern, str) and pattern for pattern in patterns)
    ):
        raise ConfigError(
            f"{context}: `patterns` must be a non-empty list of glob strings", path=path
        )
    return patterns


def _default_step_name(command: Any, index: int) -> str:
    """Derive a step name from its program (else ``step-N``) when the TOML omits one."""
    if isinstance(command, list) and command and isinstance(command[0], str) and command[0]:
        return Path(command[0]).name
    return f"step-{index + 1}"


def _first_error(error: ValidationError) -> str:
    """Reduce a ``pydantic`` ValidationError to a single concise ``field: message`` string."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "value"
    return f"{location}: {first['msg']}"
