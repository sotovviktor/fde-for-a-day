"""Endpoint tests for /extract using a mocked LLM client."""

from fastapi.testclient import TestClient
from llm_client import LLMResult
from llm_client import LLMUnavailableError
from main import app
from main import get_llm_client
from settings import get_settings

# A valid 1x1 PNG so the image decoder/validator accepts the payload.
_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

_DOC = {
    "document_id": "DOC-OCR-0001",
    "content": _PNG_B64,
    "content_format": "image_base64",
    "json_schema": '{"type":"object","properties":{"company":{"type":"string"},"amount":{"type":"number"}}}',
}


class _FakeClient:
    """Stand-in for AzureLLMClient injected via a dependency override."""

    def __init__(
        self,
        output: dict | None = None,
        error: Exception | None = None,
        *,
        prompt_tokens: int = 5,
        completion_tokens: int = 7,
    ) -> None:
        self._output = output
        self._error = error
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.calls = 0

    async def complete(
        self,
        *,
        deployment: str,
        messages: object,
        max_completion_tokens: int,
        timeout: float,
    ) -> LLMResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._output is not None
        return LLMResult(
            parsed=self._output,
            model="gpt-5.4-nano",
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )

    async def aclose(self) -> None:
        return None


def _use_client(fake: _FakeClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: fake


def test_extract_happy_path_returns_dynamic_keys_and_headers() -> None:
    _use_client(
        _FakeClient(output={"company": "CLEARPOINT", "amount": "$1,200"}, prompt_tokens=11, completion_tokens=13)
    )
    with TestClient(app) as client:
        resp = client.post("/extract", json=_DOC, headers={"X-Request-ID": "extract-req-1"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"document_id", "company", "amount"}
    assert body["document_id"] == "DOC-OCR-0001"
    assert body["company"] == "CLEARPOINT"  # string preserved
    assert body["amount"] == 1200  # number coerced from "$1,200"
    assert resp.headers["X-Model-Name"] == get_settings().extract_model
    assert resp.headers["X-Prompt-Tokens"] == "11"
    assert resp.headers["X-Completion-Tokens"] == "13"
    assert resp.headers["X-Request-ID"] == "extract-req-1"


def test_extract_failure_returns_200_with_document_id() -> None:
    _use_client(_FakeClient(error=RuntimeError("vision upstream down")))
    with TestClient(app) as client:
        resp = client.post("/extract", json=_DOC)

    assert resp.status_code == 200
    assert resp.json() == {"document_id": "DOC-OCR-0001"}
    assert resp.headers["X-Model-Name"] == get_settings().extract_model
    assert resp.headers["X-Extract-Error"] == "RuntimeError"
    assert resp.headers["X-Request-ID"]


def test_extract_open_llm_circuit_returns_503() -> None:
    _use_client(_FakeClient(error=LLMUnavailableError("circuit open")))
    with TestClient(app) as client:
        resp = client.post("/extract", json=_DOC)

    assert resp.status_code == 503
    assert resp.json() == {"detail": "service unavailable", "error_code": "LLMUnavailableError"}
    assert resp.headers["X-Model-Name"] == get_settings().extract_model
    assert resp.headers["X-Extract-Error"] == "LLMUnavailableError"


def test_extract_error_header_is_http_safe() -> None:
    custom_error = type("Bad\nHeader" + "X" * 100, (Exception,), {})
    _use_client(_FakeClient(error=custom_error("boom")))
    with TestClient(app) as client:
        resp = client.post("/extract", json=_DOC)

    header = resp.headers["X-Extract-Error"]
    assert len(header) <= 64
    assert "\n" not in header
    assert all(32 <= ord(char) < 127 for char in header)


def test_extract_invalid_base64_returns_200_envelope() -> None:
    _use_client(_FakeClient(output={"company": "x"}))
    bad_doc = {**_DOC, "content": "!!!not-valid-base64!!!"}
    with TestClient(app) as client:
        resp = client.post("/extract", json=bad_doc)

    assert resp.status_code == 200
    assert resp.json() == {"document_id": "DOC-OCR-0001"}
    assert resp.headers["X-Extract-Error"] == "ValueError"


def test_extract_unsupported_content_format_returns_200_envelope() -> None:
    fake = _FakeClient(output={"company": "x"})
    _use_client(fake)
    path_doc = {**_DOC, "content_format": "image_path"}
    with TestClient(app) as client:
        resp = client.post("/extract", json=path_doc)

    assert resp.status_code == 200
    assert resp.json() == {"document_id": "DOC-OCR-0001"}
    assert resp.headers["X-Extract-Error"] == "ValueError"
    assert fake.calls == 0
