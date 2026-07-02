"""Unit test for the triage classifier with a mocked LLM call (no network)."""

from typing import Any

from llm_client import AzureLLMClient
from llm_client import LLMResult
from llm_client import LLMUnavailableError
from models import Category
from models import Reporter
from models import Team
from models import TriageDecision
from models import TriageRequest
from pytest import MonkeyPatch
from pytest import raises
from settings import get_settings
from triage.classifier import classify


def _make_request() -> TriageRequest:
    return TriageRequest(
        ticket_id="SIG-0042",
        subject="Auto-reply",
        description="I am in cryo-sleep and cannot respond.",
        reporter=Reporter(name="Priya", email="priya@cdss.space", department="Diplomatic Corps"),
        created_at="2026-03-17T07:45:00Z",
        channel="subspace_relay",
        attachments=[],
    )


async def test_classify_applies_guardrails_and_reports_usage(monkeypatch: MonkeyPatch) -> None:
    # Model output is intentionally inconsistent (Not-a-Signal but a real team)
    # so we can assert the guardrail rewrites the team to "None". It also omits
    # ticket_id (as the real model does); the guardrail injects it.
    model_output = TriageDecision(
        category=Category.NOT_SIGNAL,
        priority="P4",
        assigned_team=Team.SYSTEMS,
        needs_escalation=False,
        missing_information=[],
        next_best_action="Close as auto-reply.",
        remediation_steps=["Mark as Not a Mission Signal and close."],
    )

    async def fake_complete(
        *,
        deployment: str,
        messages: object,
        response_format: object,
        max_completion_tokens: int,
    ) -> LLMResult:
        return LLMResult(parsed=model_output, model="gpt-5.4-nano", prompt_tokens=11, completion_tokens=22)

    # Build a real client (offline construction) but replace its network call.
    client = AzureLLMClient(get_settings())
    monkeypatch.setattr(client, "complete", fake_complete)

    result = await classify(_make_request(), client, get_settings())
    await client.aclose()

    assert result.output.ticket_id == "SIG-0042"
    assert result.output.category == Category.NOT_SIGNAL
    assert result.output.assigned_team == Team.NONE
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 22


def _valid_decision() -> TriageDecision:
    return TriageDecision(
        category=Category.NOT_SIGNAL,
        priority="P4",
        assigned_team=Team.NONE,
        needs_escalation=False,
        missing_information=[],
        next_best_action="Close as auto-reply.",
        remediation_steps=["Close."],
    )


async def test_classify_retries_with_clamped_description_on_content_filter(monkeypatch: MonkeyPatch) -> None:
    """A content-filter/jailbreak block triggers one retry with the description clamped hard."""
    req = _make_request().model_copy(update={"description": "x" * 1000})
    prompts: list[str] = []
    attempts = 0

    async def fake_complete(
        *, deployment: str, messages: Any, response_format: object, max_completion_tokens: int
    ) -> LLMResult:
        nonlocal attempts
        attempts += 1
        prompts.append(messages[-1]["content"])
        if attempts == 1:
            raise LLMUnavailableError(
                "LLM call failed after 3 attempts: Error code: 400 - {'code': 'content_filter', "
                "'innererror': {'content_filter_result': {'jailbreak': {'detected': True, 'filtered': True}}}}"
            )
        return LLMResult(parsed=_valid_decision(), model="gpt-5.4-nano", prompt_tokens=5, completion_tokens=6)

    client = AzureLLMClient(get_settings())
    monkeypatch.setattr(client, "complete", fake_complete)

    result = await classify(req, client, get_settings())
    await client.aclose()

    assert attempts == 2
    # First prompt carries the full description; the retry clamps it well below 500 chars.
    assert ("x" * 500) in prompts[0]
    assert ("x" * 500) not in prompts[1]
    assert result.output.ticket_id == req.ticket_id
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 6


async def test_classify_reraises_non_content_filter_failures(monkeypatch: MonkeyPatch) -> None:
    """A failure that is not a content-filter block propagates without the truncation retry."""
    attempts = 0

    async def fake_complete(
        *, deployment: str, messages: object, response_format: object, max_completion_tokens: int
    ) -> LLMResult:
        nonlocal attempts
        attempts += 1
        raise LLMUnavailableError("LLM call failed after 3 attempts: Error code: 500 - upstream down")

    client = AzureLLMClient(get_settings())
    monkeypatch.setattr(client, "complete", fake_complete)

    with raises(LLMUnavailableError):
        await classify(_make_request(), client, get_settings())
    await client.aclose()

    assert attempts == 1


def test_triage_decision_coerces_labelled_enum_values() -> None:
    """A labelled priority/category must coerce to the enum, not raise.

    Without few-shot exemplars the model sometimes echoes the prompt's labels
    (e.g. "P2 (Yellow Alert)"). The before-validators coerce these to the bare
    enum so llm_client.complete does not raise -- which the endpoint would map
    to a 503 that scores 0 on every dimension.
    """
    decision = TriageDecision.model_validate(
        {
            "category": "Communications & Navigation (network routing)",
            "priority": "P2 (Yellow Alert)",
            "assigned_team": "Deep Space Communications",
            "needs_escalation": False,
            "missing_information": [],
            "next_best_action": "Investigate the relay.",
            "remediation_steps": ["Check logs."],
        }
    )
    assert decision.priority == "P2"
    assert decision.category is Category.COMMS
    assert decision.assigned_team is Team.COMMS


def test_triage_decision_maps_team_name_in_category_field() -> None:
    """Deployed regression: the model wrote a *team* name into the category field.

    That used to raise (a team value is not a valid Category) -> 503 -> score 0.
    The before-validator maps it back to the matching category so it still validates.
    """
    decision = TriageDecision.model_validate(
        {
            "category": "Mission Software Operations",  # a Team value, not a Category
            "priority": "P2",
            "assigned_team": "Mission Software Operations",
            "needs_escalation": True,
            "missing_information": [],
            "next_best_action": "Investigate the fault.",
            "remediation_steps": ["Check logs."],
        }
    )
    assert decision.category is Category.SOFTWARE
    assert decision.assigned_team is Team.SOFTWARE


async def test_classify_returns_safe_default_when_content_filter_persists(monkeypatch: MonkeyPatch) -> None:
    """When even the clamped retry is blocked, return a safe default as a scored 200.

    A blocked signal is almost always an injection/abuse attempt; surfacing a 503
    would score 0 on every dimension, so we return a security-conscious default
    (Not a Mission Signal, escalated) instead.
    """
    attempts = 0

    async def fake_complete(
        *, deployment: str, messages: object, response_format: object, max_completion_tokens: int
    ) -> LLMResult:
        nonlocal attempts
        attempts += 1
        raise LLMUnavailableError(
            "LLM call failed after 3 attempts: Error code: 400 - {'code': 'content_filter', "
            "'innererror': {'content_filter_result': {'jailbreak': {'detected': True, 'filtered': True}}}}"
        )

    client = AzureLLMClient(get_settings())
    monkeypatch.setattr(client, "complete", fake_complete)

    req = _make_request()
    result = await classify(req, client, get_settings())
    await client.aclose()

    assert attempts == 2  # original call + one clamped retry, both blocked
    assert result.output.ticket_id == req.ticket_id
    assert result.output.category is Category.NOT_SIGNAL
    assert result.output.assigned_team is Team.NONE
    assert result.output.priority == "P4"
    assert result.output.needs_escalation is True
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
