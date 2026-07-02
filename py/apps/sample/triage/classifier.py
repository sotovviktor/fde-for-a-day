"""Triage classification orchestration: build messages, call the shared LLM
client, parse the structured reply, and apply guardrails.
"""

import logging

from llm_client import AzureLLMClient
from llm_client import LLMError
from llm_client import LLMResult
from llm_client import TaskResult
from models import TriageDecision
from models import TriageRequest
from models import TriageResponse
from settings import Settings

from triage.guardrails import apply_guardrails
from triage.prompt import build_messages

logger = logging.getLogger(__name__)


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
        message = str(exc)
        if "content_filter" not in message and "jailbreak" not in message:
            raise
        logger.warning("triage_content_filter_fallback ticket=%s", req.ticket_id)
        result = await _call_model(req, llm_client, settings, max_chars=settings.triage_content_filter_retry_chars)
    guarded = apply_guardrails(result.parsed, req)
    return TaskResult(
        output=guarded,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
