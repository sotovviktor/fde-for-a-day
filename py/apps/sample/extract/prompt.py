"""Load the extraction system prompt from ``prompt.yaml`` and build the chat
messages (system instructions + the schema and the document image) for one
document."""

from pathlib import Path

from openai.types.chat import ChatCompletionContentPartImageParam
from openai.types.chat import ChatCompletionContentPartTextParam
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat import ChatCompletionSystemMessageParam
from openai.types.chat import ChatCompletionUserMessageParam
from prompt_loading import load_prompt

_PROMPT_PATH = Path(__file__).parent / "prompt.yaml"
_SYSTEM_PROMPT, _ = load_prompt(_PROMPT_PATH)

_NO_SCHEMA_FALLBACK = "(no schema provided; extract every clearly labeled field as a JSON object)"

_INSTRUCTION = (
    "Extract the fields defined by the JSON schema below from the document image "
    "and return a single JSON object that conforms to it."
)


def build_messages(schema_raw: str | None, data_uri: str) -> list[ChatCompletionMessageParam]:
    """Assemble the system prompt plus a user message carrying the schema and image."""
    schema_text = schema_raw or _NO_SCHEMA_FALLBACK
    user_text = f"{_INSTRUCTION}\n\nJSON schema:\n{schema_text}"
    return [
        ChatCompletionSystemMessageParam(role="system", content=_SYSTEM_PROMPT),
        ChatCompletionUserMessageParam(
            role="user",
            content=[
                ChatCompletionContentPartTextParam(type="text", text=user_text),
                ChatCompletionContentPartImageParam(type="image_url", image_url={"url": data_uri, "detail": "high"}),
            ],
        ),
    ]
