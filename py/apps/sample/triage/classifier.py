"""Triage classification orchestration: build messages, call the shared LLM
client, parse the structured reply, and apply guardrails.
"""

import logging

from llm_client import AzureLLMClient
from llm_client import LLMError
from llm_client import LLMResult
from llm_client import TaskResult
from models import Category
from models import Team
from models import TriageDecision
from models import TriageRequest
from models import TriageResponse
from settings import Settings

from triage.guardrails import apply_guardrails
from triage.prompt import build_messages

logger = logging.getLogger(__name__)

# When Azure's content filter blocks a signal even after the clamped retry, fall back to a
# valid, security-conscious default. A blocked signal is almost always an injection/abuse attempt, which the rubric
# treats as "Not a Mission Signal" that must still be escalated for manual security review.
_CONTENT_FILTER_DEFAULT = TriageDecision(
    category=Category.NOT_SIGNAL,
    priority="P4",
    assigned_team=Team.NONE,
    needs_escalation=True,
    missing_information=[],
    next_best_action="Escalate to Threat Response Command for manual security review.",
    remediation_steps=["Hold the signal for manual triage; do not auto-route blocked content."],
)


def _is_content_filter_error(exc: LLMError) -> bool:
    """True when the failure is an Azure content-filter / jailbreak block."""
    message = str(exc)
    return "content_filter" in message or "jailbreak" in message


def _content_filter_default(req: TriageRequest) -> TaskResult[TriageResponse]:
    """Apply guardrails to the safe default (stamps ticket_id) and report zero token usage."""
    guarded = apply_guardrails(_CONTENT_FILTER_DEFAULT, req)
    return TaskResult(output=guarded, prompt_tokens=0, completion_tokens=0)


async def _call_model(
    req: TriageRequest, llm_client: AzureLLMClient, settings: Settings, *, max_chars: int
) -> LLMResult:
    """Build the triage messages (clamping the description to ``max_chars``) and call the LLM."""
    messages = build_messages(req, max_chars=max_chars)
    return await llm_client.complete(
        deployment=settings.triage_model,
        messages=messages,
        response_format=TriageDecision,
        max_completion_tokens=settings.triage_max_output_tokens,
    )


async def classify(req: TriageRequest, llm_client: AzureLLMClient, settings: Settings) -> TaskResult[TriageResponse]:
    """Classify a single signal end-to-end (LLM call + deterministic guardrails)."""
    try:
        result = await _call_model(req, llm_client, settings, max_chars=settings.max_description_chars)
    except LLMError as exc:
        # Retry once with the description clamped hard: this keeps the legitimate operational
        # content while dropping the tail that trips the filter.
        if not _is_content_filter_error(exc):
            raise
        logger.warning("triage_content_filter_fallback ticket=%s", req.ticket_id)
        try:
            result = await _call_model(req, llm_client, settings, max_chars=settings.triage_content_filter_retry_chars)
        except LLMError as retry_exc:
            # Still blocked after clamping: return the safe default.
            if not _is_content_filter_error(retry_exc):
                raise
            logger.warning("triage_content_filter_default ticket=%s", req.ticket_id)
            return _content_filter_default(req)
    guarded = apply_guardrails(result.parsed, req)
    return TaskResult(
        output=guarded,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
