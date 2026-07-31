"""Structural and validation checks for the ``Workflow``/``Step`` models (§5)."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from swatch.core.workflow import Step, Workflow


def test_step_and_workflow_are_pydantic_models() -> None:
    assert issubclass(Step, BaseModel)
    assert issubclass(Workflow, BaseModel)


def test_workflow_composes_named_steps() -> None:
    assert set(Workflow.model_fields) == {"name", "steps"}
    assert set(Step.model_fields) == {"name", "command", "timeout_s", "cwd", "env_allowlist"}


def test_step_defaults_are_no_timeout_scrubbed_env_inherited_cwd() -> None:
    step = Step(name="build", command=["make"])
    assert step.command == ["make"]
    assert step.timeout_s is None  # no timeout by default (ROADMAP v0.1.0)
    assert step.cwd is None
    assert step.env_allowlist == []


def test_step_accepts_full_field_set() -> None:
    step = Step(
        name="test",
        command=["pytest", "-q"],
        timeout_s=30.0,
        cwd=Path("/proj"),
        env_allowlist=["PATH", "HOME"],
    )
    assert step.command == ["pytest", "-q"]
    assert step.timeout_s == 30.0
    assert step.cwd == Path("/proj")
    assert step.env_allowlist == ["PATH", "HOME"]


def test_step_requires_a_command() -> None:
    with pytest.raises(ValidationError):
        Step(name="no-command")  # type: ignore[call-arg]


def test_step_rejects_an_empty_command() -> None:
    # An empty argv has no program to exec — never a valid subprocess step.
    with pytest.raises(ValidationError):
        Step(name="empty", command=[])


def test_step_rejects_a_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        Step(name="zero", command=["make"], timeout_s=0)


def test_workflow_defaults_to_no_steps() -> None:
    assert Workflow(name="empty").steps == []
