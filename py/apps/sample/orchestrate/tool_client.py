"""Shared HTTP client for calling Task 3 workflow tools.

A single :class:`ToolClient` wraps one ``httpx.AsyncClient`` created at
application startup and reused across every request (shared connection pool).
The orchestration executor calls tool endpoints through it; the LLM planner
only ever chooses tool *names*, so the URL always comes from the request's
``available_tools`` (never from model output). Combined with scheme validation
this keeps tool dispatch SSRF-safe.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from models import ToolDefinition
from settings import Settings
from text_utils import truncate

logger = logging.getLogger(__name__)

# Transient upstream responses worth retrying (429 + 5xx).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BACKOFF_SECONDS = 5.0
_SUMMARY_MAX_CHARS = 200


@dataclass(frozen=True)
class ToolCallResult:
    """Outcome of a single tool HTTP call."""

    status_code: int
    body: Any
    success: bool
    error: str | None = None

    def summarize(self) -> str:
        """Short, human-readable summary of the response for the step trace."""
        if not self.success and self.error:
            return f"error: {self.error}"
        text = self.body if isinstance(self.body, str) else repr(self.body)
        return truncate(text, _SUMMARY_MAX_CHARS)


def _retry_after_seconds(response: httpx.Response | None, attempt: int) -> float:
    """Honor an upstream ``Retry-After`` header, else bounded exponential backoff."""
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
    return min(2.0 ** (attempt - 1), _MAX_BACKOFF_SECONDS)


def _validate_endpoint(endpoint: str) -> str | None:
    """Return an error string if the endpoint is not a plain http(s) URL."""
    parts = urlsplit(endpoint)
    if parts.scheme not in _ALLOWED_SCHEMES:
        return f"disallowed scheme: {parts.scheme or '(none)'}"
    if not parts.hostname:
        return "missing host"
    return None


def _safe_json(response: httpx.Response) -> Any:
    """Parse the response body as JSON, falling back to raw text."""
    try:
        return response.json()
    except ValueError:
        return response.text


class ToolClient:
    """A reusable HTTP client for workflow tool calls with bounded concurrency and retries."""

    def __init__(self, settings: Settings) -> None:
        self._max_retries = settings.tool_max_retries
        # Bound concurrency at the connection-pool layer so a request awaiting a
        # retry backoff does not hold a slot (unlike an app-level semaphore).
        self._client = httpx.AsyncClient(
            timeout=settings.tool_timeout_seconds,
            limits=httpx.Limits(
                max_connections=settings.max_tool_concurrency,
                max_keepalive_connections=settings.max_tool_concurrency,
            ),
        )

    async def call(self, tool: ToolDefinition, params: dict[str, Any]) -> ToolCallResult:
        """POST ``params`` to the tool's endpoint, retrying transient failures.

        The endpoint always comes from the request's tool definition, not from
        the model; we still validate the scheme as defense in depth.
        """
        error = _validate_endpoint(tool.endpoint)
        if error is not None:
            logger.warning("rejected tool endpoint for %s: %s", tool.name, error)
            return ToolCallResult(status_code=0, body=None, success=False, error=error)

        return await self._post_with_retries(tool.endpoint, params, tool.name)

    async def _post_with_retries(self, endpoint: str, params: dict[str, Any], tool_name: str) -> ToolCallResult:
        attempt = 0
        while True:
            try:
                response = await self._client.post(endpoint, json=params)
            except httpx.TimeoutException as exc:
                attempt += 1
                if attempt > self._max_retries:
                    return ToolCallResult(status_code=0, body=None, success=False, error=f"timeout: {exc}")
                await asyncio.sleep(_retry_after_seconds(None, attempt))
                continue
            except httpx.HTTPError as exc:
                return ToolCallResult(status_code=0, body=None, success=False, error=f"request failed: {exc}")

            if response.status_code in _RETRYABLE_STATUS:
                attempt += 1
                if attempt > self._max_retries:
                    return ToolCallResult(
                        status_code=response.status_code,
                        body=_safe_json(response),
                        success=False,
                        error=f"upstream {response.status_code}",
                    )
                await asyncio.sleep(_retry_after_seconds(response, attempt))
                continue

            success = response.is_success
            return ToolCallResult(
                status_code=response.status_code,
                body=_safe_json(response),
                success=success,
                error=None if success else f"status {response.status_code}",
            )

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call once on application shutdown."""
        await self._client.aclose()
