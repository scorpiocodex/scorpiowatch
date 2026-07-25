"""Structural checks for the ``Workflow``/``Step`` models (skeleton, task 0.3)."""

from pydantic import BaseModel

from watchflow.core.workflow import Step, Workflow


def test_step_and_workflow_are_pydantic_models() -> None:
    assert issubclass(Step, BaseModel)
    assert issubclass(Workflow, BaseModel)


def test_workflow_composes_named_steps() -> None:
    assert set(Workflow.model_fields) == {"name", "steps"}
    assert set(Step.model_fields) == {"name"}
