"""Internal structured-output schema for the workflow planner.

The planner returns the next batch of tool calls to execute this round. Call
parameters are a JSON *string* (``parameters_json``) rather than an open
object so the planner can emit arbitrary tool parameters while the shared
``AzureLLMClient.complete`` helper still uses JSON-object mode for every call.
The executor parses the string into a dict before dispatching the call.
"""

from typing import Any

from models import WorkflowStatus
from pydantic import field_validator

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

    @field_validator("calls", mode="before")
    @classmethod
    def _drop_non_call_entries(cls, value: Any) -> Any:
        """Drop stray prose the model sometimes injects between tool-call objects."""
        if isinstance(value, list):
            return [item for item in value if isinstance(item, (dict, PlannedCall))]
        return value

    @field_validator("constraints_satisfied", mode="before")
    @classmethod
    def _coerce_constraints_satisfied(cls, value: Any) -> Any:
        """The model sometimes emits a bool / null / bare string instead of a list."""
        if value is None or isinstance(value, bool):
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("emails_skipped", mode="before")
    @classmethod
    def _coerce_emails_skipped(cls, value: Any) -> Any:
        """Tolerate a non-integer count (default to 0), but keep an int-coercible string."""
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
        return 0

    @field_validator("skip_reasons", mode="before")
    @classmethod
    def _coerce_skip_reasons(cls, value: Any) -> Any:
        """Expected {reason: count}. The model frequently emits string/list values or a
        non-dict; keep only integer-valued entries and drop the rest so a malformed
        report field never aborts the whole workflow."""
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, int] = {}
        for key, count in value.items():
            if isinstance(count, bool):
                continue
            if isinstance(count, int):
                cleaned[str(key)] = count
            elif isinstance(count, str) and count.strip().lstrip("-").isdigit():
                cleaned[str(key)] = int(count)
        return cleaned
