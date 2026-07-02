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
