"""Load the orchestration planner prompt + few-shot from ``prompt.yaml`` and
build the chat messages for one planning round."""

from pathlib import Path

from models import OrchestrateRequest
from models import ToolDefinition
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam
from prompt_loading import few_shot_messages
from prompt_loading import load_prompt

_PROMPT_PATH = Path(__file__).parent / "prompt.yaml"
_SYSTEM_PROMPT, _FEW_SHOTS = load_prompt(_PROMPT_PATH)


def _format_tool(tool: ToolDefinition) -> str:
    params = tool.parameters
    if isinstance(params, list):
        param_str = ", ".join(f"{p.name}:{p.type}{'' if p.required else '?'}" for p in params)
    else:
        param_str = ", ".join(f"{name}:{ptype}" for name, ptype in params.items())
    return f"- {tool.name}({param_str}) — {tool.description}"


def _format_request(req: OrchestrateRequest, max_chars: int) -> str:
    tools = "\n".join(_format_tool(tool) for tool in req.available_tools)
    constraints = "\n".join(f"- {c}" for c in req.constraints) if req.constraints else "(none)"
    return f"GOAL: {req.goal[:max_chars]}\n\nAVAILABLE TOOLS:\n{tools}\n\nCONSTRAINTS:\n{constraints}"


def build_planner_messages(
    req: OrchestrateRequest,
    observations: list[str],
    *,
    max_chars: int,
) -> list[ChatCompletionMessageParam]:
    """Assemble the system prompt, few-shot, the workflow, and observations so far."""
    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content=_SYSTEM_PROMPT),
        *few_shot_messages(_FEW_SHOTS),
    ]

    user_content = _format_request(req, max_chars)
    if observations:
        joined = "\n".join(observations)
        user_content += (
            f"\n\nOBSERVATIONS SO FAR (results of executed calls):\n{joined}"
            "\n\nPlan the remaining calls using the observed IDs/values. If the goal is now "
            "fully satisfied, return no further calls and set workflow_complete=true. "
            "Respond with the PlanDecision json object."
        )
    else:
        user_content += (
            "\n\nPlan the WHOLE workflow now. If every parameter is already known from the "
            "GOAL and CONSTRAINTS, emit the complete ordered plan in one batch and set "
            "workflow_complete=true. Only when a later step needs an ID/value you can read "
            "solely from a tool result (a search/lookup) do you emit just the calls up to and "
            "including that lookup with workflow_complete=false. Respond with the PlanDecision "
            "json object."
        )
    messages.append(ChatCompletionUserMessageParam(role="user", content=user_content))
    return messages
