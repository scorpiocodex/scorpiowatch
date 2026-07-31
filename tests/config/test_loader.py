"""Tests for the ``swatch.toml`` loader: TOML → core-model mapping and clean errors.

The loader maps the ergonomic config (``SCORPIOWATCH.md`` §8) onto the real core models — so the
assertions here are about the produced ``Trigger``/``Workflow``/``Step`` objects and about the
precise ``ConfigError`` a bad config raises, never a partial config or a traceback.
"""

import inspect
from pathlib import Path

import pytest

from swatch.config.loader import ConfigError, load
from swatch.core.config import SwatchConfig
from swatch.core.triggers import GlobMatch


def _write(tmp_path: Path, body: str, *, name: str = "swatch.toml") -> Path:
    """Write ``body`` to a config file under ``tmp_path`` and return its path."""
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# Signature (kept from the 0.3 skeleton).                                      #
# --------------------------------------------------------------------------- #


def test_load_signature_matches_the_spec() -> None:
    sig = inspect.signature(load)
    assert list(sig.parameters) == ["path"]
    assert sig.parameters["path"].annotation is Path
    assert sig.return_annotation is SwatchConfig


# --------------------------------------------------------------------------- #
# Happy-path mapping.                                                          #
# --------------------------------------------------------------------------- #


def test_minimal_trigger_maps_onto_core_models(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "run-tests"
        source = "filesystem"
        patterns = ["**/*.py"]

          [trigger.workflow]
          steps = [
            { command = ["pytest", "-q"], timeout_s = 30 },
          ]
        """,
    )
    config = load(path)
    assert len(config.triggers) == 1
    trigger = config.triggers[0]
    assert trigger.name == "run-tests"
    assert trigger.source == "filesystem"
    assert isinstance(trigger.match, GlobMatch)
    assert trigger.match.pattern == "**/*.py"
    assert trigger.workflow.name == "run-tests"  # defaults to the trigger name
    assert [step.command for step in trigger.workflow.steps] == [["pytest", "-q"]]
    assert trigger.workflow.steps[0].timeout_s == 30
    assert trigger.workflow.steps[0].name == "pytest"  # derived from the program


def test_defaults_are_applied(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    trigger = load(path).triggers[0]
    assert trigger.source == "filesystem"  # default source
    assert trigger.threshold == 0.5
    assert trigger.cooldown_ms is None  # unset → the Scheduler's default window applies
    assert trigger.workflow.steps[0].timeout_s is None  # opt-in timeout


def test_patterns_expand_into_one_trigger_each(tmp_path: Path) -> None:
    # A plural `patterns` fans out to one core Trigger per pattern, sharing name + workflow.
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "lint"
        patterns = ["**/*.py", "**/*.pyi"]
          [trigger.workflow]
          steps = [{ command = ["ruff", "check"] }]
        """,
    )
    config = load(path)
    assert len(config.triggers) == 2
    assert {t.match.pattern for t in config.triggers} == {"**/*.py", "**/*.pyi"}  # type: ignore[union-attr]
    assert all(t.name == "lint" for t in config.triggers)
    assert {t.workflow.name for t in config.triggers} == {"lint"}


def test_explicit_step_and_workflow_names_are_honored(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          name = "custom-wf"
          steps = [{ name = "lint", command = ["ruff", "check"] }]
        """,
    )
    trigger = load(path).triggers[0]
    assert trigger.workflow.name == "custom-wf"
    assert trigger.workflow.steps[0].name == "lint"


def test_cwd_and_env_allowlist_map_through(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["env"], cwd = "sub/dir", env_allowlist = ["PATH", "HOME"] }]
        """,
    )
    step = load(path).triggers[0].workflow.steps[0]
    assert step.cwd == Path("sub/dir")
    assert step.env_allowlist == ["PATH", "HOME"]


def test_multiple_steps_default_to_step_n_when_unnamed(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [
            { command = ["mypy"] },
            { command = ["pytest"] },
          ]
        """,
    )
    steps = load(path).triggers[0].workflow.steps
    assert [s.name for s in steps] == ["mypy", "pytest"]


def test_empty_config_is_valid_with_no_triggers(tmp_path: Path) -> None:
    config = load(_write(tmp_path, "# nothing here\n"))
    assert config.triggers == []


def test_unknown_top_level_section_is_ignored(tmp_path: Path) -> None:
    # §8's canonical example carries [mcp.server]; v0.1.0 ignores unknown top-level sections
    # so that config still loads.
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["true"] }]

        [mcp.server]
        enabled = true
        expose = ["trigger_workflow"]
        """,
    )
    config = load(path)
    assert len(config.triggers) == 1


# --------------------------------------------------------------------------- #
# Strictness inside a [[trigger]] block — typos are errors, not silent drops.  #
# --------------------------------------------------------------------------- #


def test_unknown_trigger_key_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patttern = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match=r"unknown key.*patttern"):
        load(path)


def test_unknown_step_key_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["true"], timout_s = 5 }]
        """,
    )
    with pytest.raises(ConfigError, match=r"unknown key.*timout_s"):
        load(path)


def test_unknown_workflow_key_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          on_fail = "notify"
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match=r"unknown key.*on_fail"):
        load(path)


# --------------------------------------------------------------------------- #
# Structural / value errors → clean ConfigError with the file on it.          #
# --------------------------------------------------------------------------- #


def test_missing_name_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match="`name` is required"):
        load(path)


def test_missing_patterns_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match="`patterns` must be a non-empty list"):
        load(path)


def test_empty_patterns_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = []
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match="`patterns` must be a non-empty list"):
        load(path)


def test_non_string_pattern_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = [123]
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match="`patterns` must be a non-empty list"):
        load(path)


def test_missing_workflow_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
        """,
    )
    with pytest.raises(ConfigError, match=r"missing .*trigger\.workflow"):
        load(path)


def test_empty_steps_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = []
        """,
    )
    with pytest.raises(ConfigError, match="non-empty `steps`"):
        load(path)


def test_step_must_be_a_table(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = ["pytest -q"]
        """,
    )
    with pytest.raises(ConfigError, match="each step must be a table"):
        load(path)


def test_unsupported_step_kind_is_a_config_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ kind = "mcp_tool", command = ["x"] }]
        """,
    )
    with pytest.raises(ConfigError, match="kind 'mcp_tool' is not supported"):
        load(path)


def test_missing_command_reports_the_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ timeout_s = 5 }]
        """,
    )
    with pytest.raises(ConfigError, match="step #1: command"):
        load(path)


def test_bad_timeout_reports_the_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          steps = [{ command = ["true"], timeout_s = 0 }]
        """,
    )
    with pytest.raises(ConfigError, match="step #1: timeout_s"):
        load(path)


def test_bad_trigger_field_is_wrapped(tmp_path: Path) -> None:
    # threshold must be a float; a bad value surfaces as a field-level ConfigError.
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
        threshold = "high"
          [trigger.workflow]
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match="threshold"):
        load(path)


def test_bad_workflow_name_is_wrapped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [[trigger]]
        name = "t"
        patterns = ["*.py"]
          [trigger.workflow]
          name = 123
          steps = [{ command = ["true"] }]
        """,
    )
    with pytest.raises(ConfigError, match="workflow: name"):
        load(path)


def test_trigger_must_be_an_array_of_tables(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [trigger]
        name = "t"
        """,
    )
    with pytest.raises(ConfigError, match="array of tables"):
        load(path)


# --------------------------------------------------------------------------- #
# Read/parse failures.                                                         #
# --------------------------------------------------------------------------- #


def test_malformed_toml_is_a_clean_config_error(tmp_path: Path) -> None:
    path = _write(tmp_path, '[[trigger]]\nname = "run-tests\n')  # unterminated string
    with pytest.raises(ConfigError, match="invalid TOML") as info:
        load(path)
    assert info.value.path == path


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(ConfigError, match="not found") as info:
        load(missing)
    assert info.value.path == missing


def test_unreadable_path_is_a_config_error(tmp_path: Path) -> None:
    # A directory is not a readable TOML file → the generic OS-error branch.
    with pytest.raises(ConfigError, match="could not read config"):
        load(tmp_path)
