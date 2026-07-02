"""Load the triage system prompt + few-shots from ``prompt.yaml`` and build the
chat messages for a single signal."""

from pathlib import Path

from models import TriageRequest
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam
from prompt_loading import few_shot_messages
from prompt_loading import load_prompt

from triage.guardrails import clamp_text

_PROMPT_PATH = Path(__file__).parent / "prompt.yaml"
_SYSTEM_PROMPT, _FEW_SHOTS = load_prompt(_PROMPT_PATH)


def _format_signal(req: TriageRequest, max_chars: int) -> str:
    attachments = ", ".join(req.attachments) if req.attachments else "(none)"
    description = clamp_text(req.description or "", max_chars)
    return (
        f"subject: {req.subject}\n"
        f"channel: {req.channel}\n"
        f"created_at: {req.created_at}\n"
        f"reporter: {req.reporter.name} — {req.reporter.department}\n"
        f"attachments: {attachments}\n"
        f"description: {description}"
    )


def build_messages(req: TriageRequest, *, max_chars: int) -> list[ChatCompletionMessageParam]:
    """Assemble the system prompt, few-shot examples, and the signal to classify."""
    return [
        ChatCompletionSystemMessageParam(role="system", content=_SYSTEM_PROMPT),
        *few_shot_messages(_FEW_SHOTS),
        ChatCompletionUserMessageParam(role="user", content=_format_signal(req, max_chars)),
    ]
