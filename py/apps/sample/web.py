"""Shared HTTP plumbing for the FDEBench solution service.

Cross-cutting concerns:

* request-id and JSON content-type middleware plus validation-error mapping,
  attached to the app by :func:`register_middleware`;
* :func:`run_endpoint`, the shared response contract that runs a task and turns
  an internal failure into either a scored fallback envelope or a
  customer-visible error response.
"""

import logging
from collections.abc import Awaitable
from collections.abc import Callable
from contextvars import ContextVar
from uuid import uuid4

from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from llm_client import LLMUnavailableError
from llm_client import TaskResult

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
request_id_context: ContextVar[str] = ContextVar("request_id", default="")


def _safe_header_value(value: str, *, max_chars: int = 64) -> str:
    """Return a short printable-ASCII value safe for response headers."""
    ascii_value = value.encode("ascii", errors="replace").decode("ascii")
    cleaned = "".join(char if 32 <= ord(char) < 127 else "?" for char in ascii_value)
    return cleaned[:max_chars] or "Error"


async def add_request_id(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach a request id to every response and make it available to task code."""
    request_id = _safe_header_value(request.headers.get(REQUEST_ID_HEADER) or str(uuid4()))
    token = request_id_context.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_context.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def enforce_json_content_type(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Reject non-JSON POST bodies with 415 (API resilience probe 5)."""
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if content_type and not content_type.startswith("application/json"):
            return JSONResponse(
                status_code=415,  # Unsupported Media Type
                content={"detail": "Content-Type must be application/json"},
            )
    return await call_next(request)


async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Map malformed JSON to 400 (probe 1); schema errors to 422 (probes 2-3)."""
    is_json_error = any(error.get("type") == "json_invalid" for error in exc.errors())
    status_code = 400 if is_json_error else 422
    return JSONResponse(status_code=status_code, content={"detail": "invalid request"})


def register_middleware(app: FastAPI) -> None:
    """Attach the shared middleware and exception handlers to ``app``.

    Registration order matches the previous inline decorators: ``add_request_id``
    is added first (innermost), ``enforce_json_content_type`` second (outermost),
    so a non-JSON POST is rejected before task code runs.
    """
    app.middleware("http")(add_request_id)
    app.middleware("http")(enforce_json_content_type)
    app.exception_handler(RequestValidationError)(on_validation_error)


async def run_endpoint[OutputT](
    response: Response,
    *,
    model_name: str,
    error_header: str,
    log_message: str,
    log_id: str,
    task: Callable[[], Awaitable[TaskResult[OutputT]]],
    fallback: OutputT | None = None,
    failure_status_code: int | None = None,
    failure_detail: str = "service unavailable",
) -> OutputT | JSONResponse:
    """Run a task behind the shared response contract.

    Sets the reported model header, runs ``task``, and on any internal failure of
    an otherwise valid request either returns a scored fallback envelope or a
    customer-visible error response. On success, echoes token-usage headers.
    """
    response.headers["X-Model-Name"] = model_name
    try:
        result = await task()
    except LLMUnavailableError as exc:
        logger.exception(log_message, log_id)
        error_code = _safe_header_value(type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": failure_detail, "error_code": error_code},
            headers={"X-Model-Name": model_name, error_header: error_code},
        )
    except Exception as exc:
        logger.exception(log_message, log_id)
        error_code = _safe_header_value(type(exc).__name__)
        response.headers[error_header] = error_code
        if failure_status_code is not None:
            return JSONResponse(
                status_code=failure_status_code,
                content={"detail": failure_detail, "error_code": error_code},
                headers={"X-Model-Name": model_name, error_header: error_code},
            )
        if fallback is None:
            raise
        return fallback
    response.headers["X-Prompt-Tokens"] = str(result.prompt_tokens)
    response.headers["X-Completion-Tokens"] = str(result.completion_tokens)
    return result.output
