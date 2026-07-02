"""Unit tests for the planner decision schema (PlanDecision)."""

from orchestrate.schema import PlanDecision
from pydantic import ValidationError
from pytest import raises


def test_plan_decision_defaults_to_still_working_when_flags_omitted() -> None:
    # Regression (TASK-0460): the model emitted a batch of calls but omitted
    # workflow_complete and status. That must parse as "keep going", not crash.
    decision = PlanDecision.model_validate(
        {"calls": [{"tool": "audit_log", "parameters_json": '{"action": "renewal_initiated"}'}]}
    )

    assert decision.workflow_complete is False
    assert decision.status == "partial"
    assert [call.tool for call in decision.calls] == ["audit_log"]


def test_plan_decision_respects_provided_completion_flags() -> None:
    decision = PlanDecision.model_validate({"calls": [], "workflow_complete": True, "status": "completed"})

    assert decision.workflow_complete is True
    assert decision.status == "completed"


def test_plan_decision_still_requires_calls() -> None:
    with raises(ValidationError):
        PlanDecision.model_validate({"workflow_complete": True, "status": "completed"})
