"""Shared prompt-loading helpers for the task prompt modules.

Keeps YAML loading and few-shot message assembly in one place so each task's
``prompt.py`` only describes how it formats its own request.
"""

from pathlib import Path

import yaml
from openai.types.chat import ChatCompletionAssistantMessageParam
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionUserMessageParam


def load_prompt(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Load a prompt YAML file, returning its system prompt and few-shot examples."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    system_prompt: str = data["system"]
    few_shots: list[dict[str, str]] = data.get("few_shots") or []
    return system_prompt, few_shots


def few_shot_messages(few_shots: list[dict[str, str]]) -> list[ChatCompletionMessageParam]:
    """Turn ``{input, output}`` few-shot pairs into alternating user/assistant messages."""
    messages: list[ChatCompletionMessageParam] = []
    for shot in few_shots:
        messages.append(ChatCompletionUserMessageParam(role="user", content=shot["input"]))
        messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=shot["output"]))
    return messages
