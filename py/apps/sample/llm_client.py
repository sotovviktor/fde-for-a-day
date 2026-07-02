"""Shared Azure OpenAI client."""

import asyncio
import email.utils
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity.aio import DefaultAzureCredential
from azure.identity.aio import get_bearer_token_provider
from openai import AsyncAzureOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError
from settings import Settings

from ms.common.models.base import FrozenBaseModel

logger = logging.getLogger(__name__)

# Entra ID scope for data-plane calls to Azure OpenAI / AI Foundry model deployments.
_AZURE_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class LLMError(Exception):
    """Raised when an LLM call fails or returns unusable content."""


class LLMUnavailableError(LLMError):
    """Raised when the upstream LLM service is unavailable."""


@dataclass(frozen=True)
class LLMResult:
    """The output of a single LLM call plus usage metadata."""

    parsed: Any
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class TaskResult[OutputT]:
    """A task's processed output plus the LLM usage that produced it."""

    output: OutputT
    prompt_tokens: int
    completion_tokens: int


class AzureLLMClient:
    """A reusable Azure OpenAI client with a bounded connection pool and retries."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Prefer a static key when one is configured (local dev, tests); otherwise
        # authenticate passwordlessly with the container's managed identity.
        api_key = settings.azure_openai_api_key or None
        self._credential: DefaultAzureCredential | None = None
        token_provider = None
        if api_key is None:
            self._credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(self._credential, _AZURE_TOKEN_SCOPE)
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=api_key,
            azure_ad_token_provider=token_provider,
            api_version=settings.azure_openai_api_version,
            max_retries=settings.llm_max_retries,
            http_client=httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=settings.max_concurrency,
                    max_keepalive_connections=10,
                    keepalive_expiry=30,
                ),
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=None,
                    write=10.0,
                    pool=5.0,
                ),
            ),
        )

    async def _create_with_retry(self, *, timeout: float, **kwargs: Any) -> Any:
        """Call ``chat.completions.create`` with a per-attempt timeout and backoff.

        Retries up to ``settings.llm_max_retries`` times. The wait between attempts
        honors an upstream ``Retry-After`` header when one is sent (e.g. on 429s),
        otherwise falls back to ``2 ** attempt`` seconds. Raises
        :class:`LLMUnavailableError` once the attempt budget is exhausted.
        """
        max_retries = max(1, self._settings.llm_max_retries)
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            retry_after: float | None = None
            try:
                return await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs),
                    timeout=timeout,
                )
            except TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "azure_openai_timeout attempt=%d/%d timeout_s=%.1f",
                    attempt + 1,
                    max_retries,
                    timeout,
                )
            except Exception as exc:
                last_exc = exc
                retry_after = self._parse_retry_after_header(getattr(getattr(exc, "response", None), "headers", None))
                logger.warning(
                    "azure_openai_retry attempt=%d/%d error=%s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
            if attempt < max_retries - 1:
                backoff = retry_after if retry_after is not None and retry_after > 0 else float(2**attempt)
                await asyncio.sleep(backoff)
        raise LLMUnavailableError(f"LLM call failed after {max_retries} attempts: {last_exc}") from last_exc

    def _parse_retry_after_header(self, response_headers: httpx.Headers | None = None) -> float | None:
        """Return seconds to wait before retrying per the ``Retry-After`` header, or None.

        About the Retry-After header: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Retry-After
        See also https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Retry-After#syntax
        """
        if response_headers is None:
            return None

        # First, try the non-standard `retry-after-ms` header for milliseconds,
        # which is more precise than integer-seconds `retry-after`.
        retry_ms_header = response_headers.get("retry-after-ms", None)
        if retry_ms_header is not None:
            try:
                return float(retry_ms_header) / 1000
            except ValueError:
                pass

        # Next, try parsing `retry-after` as seconds (allowing nonstandard floats).
        retry_header = response_headers.get("retry-after")
        if retry_header is None:
            return None
        try:
            # The spec says integer seconds, but respect a float if one is sent.
            return float(retry_header)
        except ValueError:
            pass

        # Last, try parsing `retry-after` as an HTTP date.
        retry_date_tuple = email.utils.parsedate_tz(retry_header)
        if retry_date_tuple is None:
            return None
        retry_date = email.utils.mktime_tz(retry_date_tuple)
        return float(retry_date - time.time())

    async def complete(
        self,
        *,
        deployment: str,
        messages: list[ChatCompletionMessageParam],
        max_completion_tokens: int,
        response_format: type[FrozenBaseModel] | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        """Call the given Azure deployment and return its JSON output plus usage."""
        call_timeout = timeout if timeout is not None else self._settings.llm_timeout_seconds
        completion = await self._create_with_retry(
            model=deployment,
            messages=messages,
            response_format={"type": "json_object"},
            max_completion_tokens=max_completion_tokens,
            timeout=call_timeout,
        )
        content = completion.choices[0].message.content
        if not content:
            raise LLMError("model returned empty content")
        try:
            parsed_json = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {exc}") from exc
        if not isinstance(parsed_json, dict):
            raise LLMError("model returned JSON that is not an object")
        try:
            parsed = response_format.model_validate(parsed_json) if response_format is not None else parsed_json
        except ValidationError as exc:
            raise LLMError(f"model returned JSON that does not match response_format: {exc}") from exc

        usage = completion.usage
        return LLMResult(
            parsed=parsed,
            model=completion.model or deployment,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client and credential. Call once on shutdown."""
        await self._client.close()
        if self._credential is not None:
            await self._credential.close()
