"""FDEBench solution service.

Endpoints:
    GET  /health       liveness check
    POST /triage       Task 1: signal triage (implemented)
    POST /extract      Task 2: document extraction (implemented)
    POST /orchestrate  Task 3: workflow orchestration (implemented)

A single shared Azure OpenAI client is created once at startup and reused across
every request (and every task endpoint) via the FastAPI lifespan + dependency
injection. Malformed requests at the HTTP/JSON layer return 4xx for the
resilience probes; task handlers choose whether valid internal failures return a
customer-visible error response or a scored fallback envelope.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from typing import Any

from extract.extractor import extract_document
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse
from llm_client import AzureLLMClient
from models import ExtractRequest
from models import OrchestrateRequest
from models import OrchestrateResponse
from models import TriageRequest
from models import TriageResponse
from orchestrate.executor import orchestrate
from orchestrate.tool_client import ToolClient
from settings import get_settings
from triage.classifier import classify
from web import register_middleware
from web import run_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the shared LLM + tool clients at startup and close them on shutdown."""
    settings = get_settings()
    app.state.llm_client = AzureLLMClient(settings)
    app.state.tool_client = ToolClient(settings)
    try:
        yield
    finally:
        await app.state.llm_client.aclose()
        await app.state.tool_client.aclose()


app = FastAPI(title="FDEBench Solution", lifespan=lifespan)
register_middleware(app)


def get_llm_client(request: Request) -> AzureLLMClient:
    """Return the shared LLM client created at startup."""
    return request.app.state.llm_client


LLMClientDep = Annotated[AzureLLMClient, Depends(get_llm_client)]


def get_tool_client(request: Request) -> ToolClient:
    """Return the shared tool HTTP client created at startup."""
    return request.app.state.tool_client


ToolClientDep = Annotated[ToolClient, Depends(get_tool_client)]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Task 1: Signal Triage


@app.post("/triage", response_model=TriageResponse)
async def triage(req: TriageRequest, response: Response, llm_client: LLMClientDep) -> TriageResponse | JSONResponse:
    settings = get_settings()
    return await run_endpoint(
        response,
        model_name=settings.triage_model,
        error_header="X-Triage-Error",
        log_message="triage classification failed for %s",
        log_id=req.ticket_id,
        task=lambda: classify(req, llm_client, settings),
        failure_status_code=503,
        failure_detail="triage service unavailable",
    )


# Task 2: Document Extraction


def _fallback_extract(document_id: str) -> dict[str, Any]:
    """Safe default returned when extraction fails on a valid request."""
    return {"document_id": document_id}


@app.post("/extract", response_model=None)
async def extract(req: ExtractRequest, response: Response, llm_client: LLMClientDep) -> dict[str, Any] | JSONResponse:
    settings = get_settings()
    return await run_endpoint(
        response,
        model_name=settings.extract_model,
        error_header="X-Extract-Error",
        log_message="extraction failed for %s",
        log_id=req.document_id,
        task=lambda: extract_document(req, llm_client, settings),
        fallback=_fallback_extract(req.document_id),
    )


# Task 3: Workflow Orchestration


def _fallback_orchestrate(task_id: str) -> OrchestrateResponse:
    """Safe default returned when orchestration fails on a valid request."""
    return OrchestrateResponse(task_id=task_id, status="failed", steps_executed=[])


@app.post("/orchestrate", response_model=OrchestrateResponse)
async def orchestrate_endpoint(
    req: OrchestrateRequest,
    response: Response,
    llm_client: LLMClientDep,
    tool_client: ToolClientDep,
) -> OrchestrateResponse | JSONResponse:
    settings = get_settings()
    return await run_endpoint(
        response,
        model_name=settings.orchestrate_model,
        error_header="X-Orchestrate-Error",
        log_message="orchestration failed for %s",
        log_id=req.task_id,
        task=lambda: asyncio.wait_for(
            orchestrate(req, llm_client, tool_client, settings),
            timeout=settings.orchestrate_workflow_timeout_seconds,
        ),
        fallback=_fallback_orchestrate(req.task_id),
    )
