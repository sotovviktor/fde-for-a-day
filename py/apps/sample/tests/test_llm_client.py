"""Unit tests for AzureLLMClient construction and retry behavior (no network)."""

import asyncio
from types import SimpleNamespace

import httpx
import llm_client
from llm_client import AzureLLMClient
from llm_client import LLMUnavailableError
from pytest import MonkeyPatch
from settings import get_settings

from ms.common.models.base import FrozenBaseModel


class _TypedOutput(FrozenBaseModel):
    ok: bool


def _ok_completion() -> SimpleNamespace:
    """A minimal successful chat.completions payload with usage metadata."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        model="test",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
    )


async def _no_sleep(_seconds: float) -> None:
    """Stand-in for asyncio.sleep so retry backoff does not slow tests."""


class _ResponseCarrier(Exception):
    """An error that carries an httpx response, like openai.APIStatusError."""

    def __init__(self, message: str, *, headers: httpx.Headers) -> None:
        super().__init__(message)
        self.response = SimpleNamespace(headers=headers)


async def test_static_key_auth_skips_default_credential(monkeypatch: MonkeyPatch) -> None:
    """With a key configured, no DefaultAzureCredential is constructed (no IMDS call)."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("DefaultAzureCredential must not be constructed when a key is set")

    monkeypatch.setattr(llm_client, "DefaultAzureCredential", _boom)
    settings = get_settings().model_copy(update={"azure_openai_api_key": "test-key"})

    client = AzureLLMClient(settings)
    try:
        assert client._credential is None
    finally:
        await client.aclose()


async def test_complete_retries_until_success(monkeypatch: MonkeyPatch) -> None:
    """Transient create failures are retried within the budget, then succeed."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    settings = get_settings().model_copy(update={"azure_openai_api_key": "test-key", "llm_max_retries": 3})
    client = AzureLLMClient(settings)
    calls = 0

    async def flaky_create(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("upstream hiccup")
        return _ok_completion()

    monkeypatch.setattr(client._client.chat.completions, "create", flaky_create)

    try:
        result = await client.complete(deployment="test", messages=[], max_completion_tokens=10)
    finally:
        await client.aclose()

    assert calls == 3
    assert result.parsed == {"ok": True}


async def test_complete_raises_unavailable_after_exhausting_retries(monkeypatch: MonkeyPatch) -> None:
    """Once the retry budget is spent, callers see LLMUnavailableError (mapped to 503)."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    settings = get_settings().model_copy(update={"azure_openai_api_key": "test-key", "llm_max_retries": 3})
    client = AzureLLMClient(settings)
    calls = 0

    async def failing_create(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(client._client.chat.completions, "create", failing_create)

    try:
        try:
            await client.complete(deployment="test", messages=[], max_completion_tokens=10)
        except LLMUnavailableError as exc:
            assert "after 3 attempts" in str(exc)
        else:  # pragma: no cover - defensive assertion branch
            raise AssertionError("expected LLMUnavailableError")
    finally:
        await client.aclose()

    assert calls == 3


async def test_complete_retries_on_timeout(monkeypatch: MonkeyPatch) -> None:
    """A per-attempt timeout is retried like any other transient failure."""
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    settings = get_settings().model_copy(update={"azure_openai_api_key": "test-key", "llm_max_retries": 2})
    client = AzureLLMClient(settings)
    calls = 0

    async def timeout_then_ok(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return _ok_completion()

    monkeypatch.setattr(client._client.chat.completions, "create", timeout_then_ok)

    try:
        result = await client.complete(deployment="test", messages=[], max_completion_tokens=10)
    finally:
        await client.aclose()

    assert calls == 2
    assert result.parsed == {"ok": True}


async def test_response_format_uses_json_object_create_call(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings().model_copy(update={"azure_openai_api_key": "test-key"})
    client = AzureLLMClient(settings)
    calls = 0

    async def fake_create(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            model="test",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
        )

    async def fail_parse(**kwargs: object) -> object:  # pragma: no cover - must not be called
        raise AssertionError("chat.completions.parse should not be used")

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    monkeypatch.setattr(client._client.chat.completions, "parse", fail_parse)

    try:
        result = await client.complete(
            deployment="test",
            messages=[],
            response_format=_TypedOutput,
            max_completion_tokens=10,
        )
    finally:
        await client.aclose()

    assert calls == 1
    assert result.parsed == _TypedOutput(ok=True)
    assert result.prompt_tokens == 1
    assert result.completion_tokens == 2


async def test_parse_retry_after_header_supported_formats() -> None:
    """The helper understands retry-after-ms, integer seconds, and a missing header."""
    client = AzureLLMClient(get_settings().model_copy(update={"azure_openai_api_key": "test-key"}))
    try:
        assert client._parse_retry_after_header(None) is None
        assert client._parse_retry_after_header(httpx.Headers({"retry-after-ms": "1500"})) == 1.5
        assert client._parse_retry_after_header(httpx.Headers({"retry-after": "3"})) == 3.0
        assert client._parse_retry_after_header(httpx.Headers({})) is None
    finally:
        await client.aclose()


async def test_retry_backoff_honors_retry_after_header(monkeypatch: MonkeyPatch) -> None:
    """A Retry-After header on a failed attempt overrides the exponential backoff."""
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    settings = get_settings().model_copy(update={"azure_openai_api_key": "test-key", "llm_max_retries": 2})
    client = AzureLLMClient(settings)
    calls = 0

    async def rate_limited_then_ok(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _ResponseCarrier("rate limited", headers=httpx.Headers({"retry-after": "7"}))
        return _ok_completion()

    monkeypatch.setattr(client._client.chat.completions, "create", rate_limited_then_ok)

    try:
        result = await client.complete(deployment="test", messages=[], max_completion_tokens=10)
    finally:
        await client.aclose()

    assert calls == 2
    assert slept == [7.0]
    assert result.parsed == {"ok": True}
