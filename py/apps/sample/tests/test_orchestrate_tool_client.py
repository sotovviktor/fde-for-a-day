"""Unit tests for the workflow ToolClient using a mock HTTP transport (no network)."""

import httpx
from models import ToolDefinition
from orchestrate.tool_client import ToolClient
from pytest import MonkeyPatch
from settings import get_settings


def _tool(endpoint: str = "http://tools.local/crm_search") -> ToolDefinition:
    return ToolDefinition(name="crm_search", description="Search accounts", endpoint=endpoint, parameters=[])


def _make_client(monkeypatch: MonkeyPatch, handler: httpx.MockTransport, *, max_retries: int = 1) -> ToolClient:
    settings = get_settings().model_copy(update={"tool_max_retries": max_retries})
    tool_client = ToolClient(settings)
    monkeypatch.setattr(tool_client, "_client", httpx.AsyncClient(transport=handler, timeout=1.0))
    return tool_client


async def test_call_success_returns_parsed_body(monkeypatch: MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"accounts": [{"account_id": "ACC-1"}], "total": 1})

    tool_client = _make_client(monkeypatch, httpx.MockTransport(handler))
    result = await tool_client.call(_tool(), {"filter": "all"})
    await tool_client.aclose()

    assert result.success is True
    assert result.status_code == 200
    assert result.body == {"accounts": [{"account_id": "ACC-1"}], "total": 1}


async def test_call_retries_transient_5xx_then_reports_failure(monkeypatch: MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # Retry-After: 0 keeps the retry backoff instant in the test.
        return httpx.Response(503, headers={"retry-after": "0"}, json={"error": "down"})

    tool_client = _make_client(monkeypatch, httpx.MockTransport(handler), max_retries=1)
    result = await tool_client.call(_tool(), {})
    await tool_client.aclose()

    assert calls == 2  # initial attempt + one retry
    assert result.success is False
    assert result.status_code == 503


async def test_call_timeout_returns_failure(monkeypatch: MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow tool")

    tool_client = _make_client(monkeypatch, httpx.MockTransport(handler), max_retries=0)
    result = await tool_client.call(_tool(), {})
    await tool_client.aclose()

    assert result.success is False
    assert "timeout" in (result.error or "")


async def test_call_rejects_non_http_scheme_without_dispatching(monkeypatch: MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    tool_client = _make_client(monkeypatch, httpx.MockTransport(handler))
    result = await tool_client.call(_tool(endpoint="file:///etc/passwd"), {})
    await tool_client.aclose()

    assert calls == 0  # blocked before any HTTP call
    assert result.success is False
    assert "scheme" in (result.error or "")
