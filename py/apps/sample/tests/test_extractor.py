"""Unit test for the document extractor with a mocked LLM call (no network)."""

import base64
from typing import Any

from extract.extractor import extract_document
from llm_client import AzureLLMClient
from llm_client import LLMResult
from models import ExtractRequest
from pytest import MonkeyPatch
from settings import get_settings

# A valid 1x1 PNG so the image decoder/validator accepts the payload.
_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0minimal-jpeg-for-mime-detection").decode("ascii")

_SCHEMA = '{"type":"object","properties":{"amount":{"type":"number"},"name":{"type":"string"}}}'


def _make_request() -> ExtractRequest:
    return ExtractRequest(
        document_id="DOC-OCR-0001",
        content=_PNG_B64,
        content_format="image_base64",
        json_schema=_SCHEMA,
    )


async def test_extract_document_normalizes_and_reports_usage(monkeypatch: MonkeyPatch) -> None:
    # Model output mixes a formatted number, an exact string, and a junk key
    # so we can assert coercion, fidelity preservation, and schema pruning.
    model_output = {"amount": "$1,234.50", "name": "Acme, Inc.", "extra": "drop me"}

    captured_messages: object | None = None

    async def fake_complete(
        *,
        deployment: str,
        messages: object,
        max_completion_tokens: int,
        timeout: float,
    ) -> LLMResult:
        nonlocal captured_messages
        captured_messages = messages
        return LLMResult(parsed=model_output, model="gpt-5.4-nano", prompt_tokens=13, completion_tokens=21)

    # Build a real client (offline construction) but replace its network call.
    client = AzureLLMClient(get_settings())
    monkeypatch.setattr(client, "complete", fake_complete)

    try:
        result = await extract_document(_make_request(), client, get_settings())
    finally:
        await client.aclose()

    assert result.output["document_id"] == "DOC-OCR-0001"
    assert result.output["amount"] == 1234.5  # number coerced from "$1,234.50"
    assert result.output["name"] == "Acme, Inc."  # string preserved verbatim
    assert "extra" not in result.output  # pruned to schema keys
    assert result.prompt_tokens == 13
    assert result.completion_tokens == 21
    assert _image_url(captured_messages).startswith("data:image/png;base64,")


async def test_extract_document_detects_jpeg_mime(monkeypatch: MonkeyPatch) -> None:
    captured_messages: object | None = None

    async def fake_complete(
        *,
        deployment: str,
        messages: object,
        max_completion_tokens: int,
        timeout: float,
    ) -> LLMResult:
        nonlocal captured_messages
        captured_messages = messages
        return LLMResult(parsed={"name": "Acme"}, model="gpt-5.4-nano", prompt_tokens=1, completion_tokens=2)

    client = AzureLLMClient(get_settings())
    monkeypatch.setattr(client, "complete", fake_complete)
    request = ExtractRequest(
        document_id="DOC-OCR-0002",
        content=_JPEG_B64,
        content_format="image_base64",
        json_schema=_SCHEMA,
    )

    try:
        await extract_document(request, client, get_settings())
    finally:
        await client.aclose()

    assert _image_url(captured_messages).startswith("data:image/jpeg;base64,")


def _image_url(messages: object | None) -> str:
    assert isinstance(messages, list)
    content = messages[1]["content"]
    assert isinstance(content, list)
    image_part: dict[str, Any] = content[1]
    return image_part["image_url"]["url"]
