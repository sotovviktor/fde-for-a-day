"""Document-extraction orchestration: parse schema, decode the image, call the
shared LLM client in JSON mode, normalize the reply, and echo the document id.
"""

from typing import Any

from llm_client import AzureLLMClient
from llm_client import TaskResult
from models import ExtractRequest
from settings import Settings

from extract.images import build_image_data_uri
from extract.normalize import normalize_output
from extract.prompt import build_messages
from extract.schema import parse_schema


async def extract_document(
    req: ExtractRequest,
    llm_client: AzureLLMClient,
    settings: Settings,
) -> TaskResult[dict[str, Any]]:
    """Extract one document end-to-end (image → vision LLM call → normalization)."""
    schema = parse_schema(req.json_schema)
    data_uri = build_image_data_uri(req.content, max_bytes=settings.max_image_bytes, content_format=req.content_format)
    messages = build_messages(req.json_schema, data_uri)
    result = await llm_client.complete(
        deployment=settings.extract_model,
        messages=messages,
        max_completion_tokens=settings.extract_max_output_tokens,
        timeout=settings.extract_timeout_seconds,
    )
    output = normalize_output(result.parsed, schema)
    output["document_id"] = req.document_id
    return TaskResult(
        output=output,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
