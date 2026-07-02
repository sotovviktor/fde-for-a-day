"""Workflow orchestration executor: the plan -> execute -> observe loop.

Repeatedly ask the planner for the next batch of tool calls, execute them
(consecutive same-tool calls run in parallel), feed the results back as
observations, and stop when the planner reports the workflow is complete or a
round / tool-call cap is hit. Records a typed :class:`StepExecuted` per call and
derives the response envelope.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from llm_client import AzureLLMClient
from llm_client import TaskResult
from models import OrchestrateRequest
from models import OrchestrateResponse
from models import StepExecuted
from models import ToolDefinition
from models import WorkflowStatus
from settings import Settings
from text_utils import truncate

from orchestrate.prompt import build_planner_messages
from orchestrate.schema import PlanDecision
from orchestrate.schema import PlannedCall
from orchestrate.tool_client import ToolCallResult
from orchestrate.tool_client import ToolClient

logger = logging.getLogger(__name__)

_OBSERVATION_MAX_CHARS = 1000


@dataclass(frozen=True)
class _PreparedCall:
    """A planner call resolved to a concrete tool + parsed parameters.

    ``tool`` is ``None`` and ``error`` is set when the call cannot be dispatched
    (unknown tool name or malformed ``parameters_json``).
    """

    tool_name: str
    tool: ToolDefinition | None
    parameters: dict[str, Any]
    error: str | None


async def orchestrate(
    req: OrchestrateRequest,
    llm_client: AzureLLMClient,
    tool_client: ToolClient,
    settings: Settings,
) -> TaskResult[OrchestrateResponse]:
    """Plan and execute a workflow end-to-end, returning the response envelope."""
    tools_by_name = {tool.name: tool for tool in req.available_tools}
    steps: list[StepExecuted] = []
    observations: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    planner_status: WorkflowStatus = "partial"
    completed_naturally = False
    latest_decision: PlanDecision | None = None
    seen_call_batches: set[tuple[tuple[str, str], ...]] = set()

    for _round in range(settings.orchestrate_max_rounds):
        messages = build_planner_messages(req, observations, max_chars=settings.max_description_chars)
        result = await llm_client.complete(
            deployment=settings.orchestrate_model,
            messages=messages,
            response_format=PlanDecision,
            max_completion_tokens=settings.orchestrate_max_output_tokens,
        )
        prompt_tokens += result.prompt_tokens
        completion_tokens += result.completion_tokens
        decision = result.parsed
        planner_status = decision.status
        latest_decision = decision

        call_batch = _call_batch_signature(decision.calls)
        if not decision.workflow_complete and call_batch in seen_call_batches:
            logger.warning("planner cycle detected for %s at round %d", req.task_id, _round + 1)
            break
        seen_call_batches.add(call_batch)

        prepared = [_prepare_call(call, tools_by_name) for call in decision.calls]
        remaining_calls = settings.orchestrate_max_tool_calls - len(steps)
        if remaining_calls <= 0:
            break
        hit_tool_cap = len(prepared) > remaining_calls
        if hit_tool_cap:
            logger.warning("tool-call cap reached for %s; truncating planner batch", req.task_id)
            prepared = prepared[:remaining_calls]

        for group in _consecutive_groups(prepared):
            group_results = await asyncio.gather(*(_dispatch(call, tool_client) for call in group))
            for prepared_call, call_result in zip(group, group_results, strict=True):
                steps.append(_record_step(len(steps) + 1, prepared_call, call_result))
                observations.append(_observation(len(steps), prepared_call, call_result))

        if hit_tool_cap:
            break
        if decision.workflow_complete:
            completed_naturally = True
            break
        if len(steps) >= settings.orchestrate_max_tool_calls:
            break

    status = _finalize_status(planner_status, steps, completed_naturally=completed_naturally)
    output = _build_response(req, status, steps, latest_decision)
    return TaskResult(output=output, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def _call_batch_signature(calls: list[PlannedCall]) -> tuple[tuple[str, str], ...]:
    return tuple((call.tool, call.parameters_json.strip()) for call in calls)


def _prepare_call(call: PlannedCall, tools_by_name: dict[str, ToolDefinition]) -> _PreparedCall:
    """Resolve a planner call to a tool + parsed params, capturing dispatch errors."""
    tool = tools_by_name.get(call.tool)
    if tool is None:
        return _PreparedCall(call.tool, None, {}, f"unknown tool: {call.tool}")
    raw = call.parameters_json.strip()
    if not raw:
        return _PreparedCall(call.tool, tool, {}, None)
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _PreparedCall(call.tool, tool, {}, f"invalid parameters_json: {exc}")
    if not isinstance(params, dict):
        return _PreparedCall(call.tool, tool, {}, "parameters_json must be a JSON object")
    return _PreparedCall(call.tool, tool, params, None)


async def _dispatch(call: _PreparedCall, tool_client: ToolClient) -> ToolCallResult:
    """Execute one prepared call, or synthesize a failure if it isn't dispatchable."""
    if call.tool is None or call.error is not None:
        return ToolCallResult(status_code=0, body=None, success=False, error=call.error or "unknown tool")
    return await tool_client.call(call.tool, call.parameters)


def _consecutive_groups(prepared: list[_PreparedCall]) -> Iterator[list[_PreparedCall]]:
    """Group consecutive same-tool calls so each group can run in parallel.

    Preserves cross-tool order (the group boundaries keep e.g. queries before the
    notifications that follow them) while parallelizing fan-outs to one tool.
    """
    for _tool_name, group in groupby(prepared, key=lambda call: call.tool_name):
        yield list(group)


def _record_step(step_no: int, call: _PreparedCall, result: ToolCallResult) -> StepExecuted:
    return StepExecuted(
        step=step_no,
        tool=call.tool_name,
        parameters=call.parameters,
        result_summary=result.summarize(),
        success=result.success,
    )


def _observation(step_no: int, call: _PreparedCall, result: ToolCallResult) -> str:
    params = json.dumps(call.parameters, ensure_ascii=False)
    if result.success:
        body = json.dumps(result.body, ensure_ascii=False, default=str)
    else:
        body = f"ERROR {result.status_code}: {result.error}"
    body = truncate(body, _OBSERVATION_MAX_CHARS)
    return f"[{step_no}] {call.tool_name}({params}) -> {body}"


def _finalize_status(
    planner_status: WorkflowStatus, steps: list[StepExecuted], *, completed_naturally: bool
) -> WorkflowStatus:
    """Decide the final workflow status.

    ``goal_completion`` scores 0 unless status is ``completed``, so we honor the
    planner's judgement when it declared the workflow done, downgrade to
    ``partial`` if we stopped on a round/call cap, and report ``failed`` only when
    nothing executed.
    """
    if not steps:
        return "failed"
    if not completed_naturally:
        return "partial"
    return planner_status


def _build_response(
    req: OrchestrateRequest,
    status: WorkflowStatus,
    steps: list[StepExecuted],
    decision: PlanDecision | None,
) -> OrchestrateResponse:
    """Assemble the response envelope with derived and planner-reported fields."""
    emails_sent = sum(1 for step in steps if step.tool == "email_send" and step.success)
    account_ids = {
        step.parameters["account_id"] for step in steps if isinstance(step.parameters.get("account_id"), str)
    }
    emails_skipped, skip_reasons = _skip_summary(decision)
    return OrchestrateResponse(
        task_id=req.task_id,
        status=status,
        steps_executed=steps,
        accounts_processed=len(account_ids) or None,
        emails_sent=emails_sent or None,
        emails_skipped=emails_skipped,
        skip_reasons=skip_reasons,
        constraints_satisfied=_satisfied_constraints(req.constraints, decision) if steps else [],
    )


def _satisfied_constraints(constraints: list[str], decision: PlanDecision | None) -> list[str]:
    """Return the request constraints the planner reported as satisfied.

    Filtered down to verbatim request constraints (preserving request order,
    de-duplicated) so a hallucinated or paraphrased planner entry never leaks
    into the response.
    """
    if decision is None or not decision.constraints_satisfied:
        return []
    claimed = set(decision.constraints_satisfied)
    satisfied: list[str] = []
    for constraint in constraints:
        if constraint in claimed and constraint not in satisfied:
            satisfied.append(constraint)
    return satisfied


def _skip_summary(decision: PlanDecision | None) -> tuple[int | None, dict[str, int] | None]:
    """Surface the planner's skip counts, collapsing empty/zero reports to None."""
    if decision is None:
        return None, None
    skipped = decision.emails_skipped if decision.emails_skipped > 0 else None
    reasons = {reason: count for reason, count in decision.skip_reasons.items() if count > 0} or None
    return skipped, reasons
