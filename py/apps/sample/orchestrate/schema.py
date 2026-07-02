"""Internal structured-output schema for the workflow planner.

The planner returns the next batch of tool calls to execute this round. Call
parameters are a JSON *string* (``parameters_json``) rather than an open
object so the planner can emit arbitrary tool parameters while the shared
``AzureLLMClient.complete`` helper still uses JSON-object mode for every call.
The executor parses the string into a dict before dispatching the call.
"""

from models import WorkflowStatus

from ms.common.models.base import FrozenBaseModel


class PlannedCall(FrozenBaseModel):
    """A single tool invocation the planner wants to execute next."""

    tool: str
    parameters_json: str


class PlanDecision(FrozenBaseModel):
    """The planner's decision for the current round.

    ``calls`` is executed in order (consecutive same-tool calls run in
    parallel). When ``workflow_complete`` is true the executor stops looping and
    reports ``status`` as the final workflow status.

    ``workflow_complete`` and ``status`` default to the "still working" state
    (``False`` / ``"partial"``) so a round where the model emits ``calls`` but
    omits them is treated as "keep going" instead of crashing the whole workflow;
    the round / tool-call caps and cycle detection bound the loop.

    The final-report fields (``constraints_satisfied``, ``emails_skipped``,
    ``skip_reasons``) summarise the whole workflow and are only meaningful on the
    completing round; the executor reads them from the last decision it saw. They
    default to empty so intermediate rounds can omit them.
    """

    calls: list[PlannedCall]
    workflow_complete: bool = False
    status: WorkflowStatus = "partial"
    constraints_satisfied: list[str] = []
    emails_skipped: int = 0
    skip_reasons: dict[str, int] = {}
