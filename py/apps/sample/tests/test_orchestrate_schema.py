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


def test_plan_decision_coerces_non_list_constraints_satisfied() -> None:
    # Deployed regression: the model emits a bool instead of the list of satisfied
    # constraints, which used to raise -> 503 -> score 0 for the whole workflow.
    decision = PlanDecision.model_validate({"calls": [], "constraints_satisfied": False})
    assert decision.constraints_satisfied == []


def test_plan_decision_wraps_bare_string_constraints_satisfied() -> None:
    decision = PlanDecision.model_validate({"calls": [], "constraints_satisfied": "all satisfied"})
    assert decision.constraints_satisfied == ["all satisfied"]


def test_plan_decision_drops_non_integer_skip_reasons() -> None:
    # Expected {reason: count}; the model frequently emits string or list values.
    decision = PlanDecision.model_validate(
        {"calls": [], "skip_reasons": {"already_notified": 2, "opted_out": "many", "vip": ["a", "b"]}}
    )
    assert decision.skip_reasons == {"already_notified": 2}


def test_plan_decision_coerces_non_dict_skip_reasons() -> None:
    decision = PlanDecision.model_validate({"calls": [], "skip_reasons": "none"})
    assert decision.skip_reasons == {}


def test_plan_decision_drops_stray_strings_in_calls() -> None:
    # The model sometimes narrates inside the calls array; keep only real call objects.
    decision = PlanDecision.model_validate(
        {"calls": ["let me think about this", {"tool": "audit_log", "parameters_json": "{}"}]}
    )
    assert [call.tool for call in decision.calls] == ["audit_log"]


def test_plan_decision_coerces_non_integer_emails_skipped() -> None:
    assert PlanDecision.model_validate({"calls": [], "emails_skipped": True}).emails_skipped == 0
    assert PlanDecision.model_validate({"calls": [], "emails_skipped": ["x"]}).emails_skipped == 0
    assert PlanDecision.model_validate({"calls": [], "emails_skipped": "3"}).emails_skipped == 3
