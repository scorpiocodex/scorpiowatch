"""Core workflow domain models: ``Workflow`` and its ``Step``s.

See ``MODULE_SPECIFICATIONS.md`` §5 and ``PROJECT_STRUCTURE.md`` §1. §5 fixes the
runtime ``Executor``/``DAGExecutor`` signatures but not the ``Workflow``/``Step``
model fields, so these are minimal structural skeletons; their full field sets (step
kinds, timeouts, retries, DAG edges) land with the executor in task 1.1.
"""

from pydantic import BaseModel, Field


class Step(BaseModel):
    """A single unit of work within a Workflow.

    Structural skeleton (task 0.3): the ``kind``-specific fields
    (``subprocess``/``plugin``/``http``/``mcp_tool``, timeouts, retries) are defined
    with the Executor in task 1.1. See ``EXECUTION_MODEL.md`` §2.

    Attributes:
        name: Identifier for this step, unique within its Workflow.
    """

    name: str


class Workflow(BaseModel):
    """An ordered graph of Steps admitted and executed as a single Run.

    Structural skeleton (task 0.3): DAG edges, ``max_parallel``, and
    ``continue_on_fail`` propagation are defined with the DAGExecutor in task 1.1.
    See ``EXECUTION_MODEL.md`` §3.

    Attributes:
        name: Identifier for this workflow.
        steps: The workflow's steps; empty by default in this skeleton.
    """

    name: str
    steps: list[Step] = Field(default_factory=list)
