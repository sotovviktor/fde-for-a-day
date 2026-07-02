"""Unit tests for the deterministic triage guardrails (no LLM, no I/O)."""

from typing import Literal

from models import Category
from models import MissingInfo
from models import Reporter
from models import Team
from models import TriageRequest
from models import TriageResponse
from triage.guardrails import apply_guardrails
from triage.guardrails import clamp_text


def _make_request(subject: str = "subject", description: str = "description") -> TriageRequest:
    return TriageRequest(
        ticket_id="SIG-0001",
        subject=subject,
        description=description,
        reporter=Reporter(name="Ada", email="ada@cdss.space", department="Ops"),
        created_at="2026-03-17T09:14:00Z",
        channel="bridge_terminal",
        attachments=[],
    )


def _make_output(
    *,
    category: Category = Category.COMMS,
    priority: Literal["P1", "P2", "P3", "P4"] = "P3",
    assigned_team: Team = Team.COMMS,
    needs_escalation: bool = False,
    missing_information: list[MissingInfo] | None = None,
) -> TriageResponse:
    return TriageResponse(
        ticket_id="SIG-0001",
        category=category,
        priority=priority,
        assigned_team=assigned_team,
        needs_escalation=needs_escalation,
        missing_information=missing_information or [],
        next_best_action="do the thing",
        remediation_steps=["step one"],
    )


def test_not_a_signal_forces_team_none() -> None:
    output = _make_output(category=Category.NOT_SIGNAL, assigned_team=Team.SYSTEMS)
    result = apply_guardrails(output, _make_request())
    assert result.assigned_team == Team.NONE


def test_real_category_with_none_team_gets_default() -> None:
    output = _make_output(category=Category.HULL, assigned_team=Team.NONE)
    result = apply_guardrails(output, _make_request())
    assert result.assigned_team == Team.SYSTEMS


def test_always_escalate_phrase_forces_escalation() -> None:
    output = _make_output(needs_escalation=False)
    request = _make_request(description="We have a hull breach on Deck 4, venting atmosphere.")
    result = apply_guardrails(output, request)
    assert result.needs_escalation is True


def test_always_escalate_matching_is_case_and_separator_tolerant() -> None:
    output = _make_output(needs_escalation=False)
    request = _make_request(description="LIFE-SUPPORT FAILURE reported near the med bay.")
    result = apply_guardrails(output, request)
    assert result.needs_escalation is True


def test_always_escalate_matching_respects_word_boundaries() -> None:
    output = _make_output(needs_escalation=False)
    request = _make_request(description="The unrestricted zone marker was breached during a drill.")
    result = apply_guardrails(output, request)
    assert result.needs_escalation is False


def test_non_escalate_signal_is_left_alone() -> None:
    output = _make_output(needs_escalation=False)
    result = apply_guardrails(output, _make_request(description="A projector flickers occasionally."))
    assert result.needs_escalation is False


def test_missing_information_is_deduped() -> None:
    output = _make_output(missing_information=[MissingInfo.STARDATE, MissingInfo.STARDATE])
    result = apply_guardrails(output, _make_request())
    assert result.missing_information == [MissingInfo.STARDATE]


def test_clamp_text_truncates_and_preserves_short_text() -> None:
    assert clamp_text("abcdef", 3).startswith("abc")
    assert clamp_text("ab", 10) == "ab"
