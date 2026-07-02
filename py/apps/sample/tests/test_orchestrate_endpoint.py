"""Endpoint tests for /orchestrate using mocked clients (no network)."""

import asyncio
from typing import Any

import main
from fastapi.testclient import TestClient
from llm_client import LLMResult
from llm_client import LLMUnavailableError
from main import app
from main import get_llm_client
from main import get_tool_client
from orchestrate.schema import PlanDecision
from orchestrate.schema import PlannedCall
from orchestrate.tool_client import ToolCallResult
from pytest import MonkeyPatch
from settings import get_settings

_WORKFLOW = {
    "task_id": "TASK-0001",
    "goal": "Log the incident response action for audit",
    "available_tools": [
        {
            "name": "audit_log",
            "description": "Log an action for compliance and audit trail.",
            "endpoint": "http://tools.local/audit_log",
            "parameters": [{"name": "action", "type": "string", "description": "Action type", "required": True}],
        }
    ],
    "constraints": ["Log all incident responses"],
}


class _FakeLLM:
    def __init__(
        self,
        decision: PlanDecision | None = None,
        error: Exception | None = None,
        *,
        prompt_tokens: int = 5,
        completion_tokens: int = 7,
        delay_seconds: float = 0.0,
    ) -> None:
        self._decision = decision
        self._error = error
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._delay_seconds = delay_seconds

    async def complete(
        self,
        *,
        deployment: str,
        messages: object,
        response_format: object,
        max_completion_tokens: int,
    ) -> LLMResult:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        assert self._decision is not None
        return LLMResult(
            parsed=self._decision,
            model="gpt-5.4-nano",
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )

    async def aclose(self) -> None:
        return None


class _FakeTools:
    async def call(self, tool: object, params: dict[str, Any]) -> ToolCallResult:
        return ToolCallResult(status_code=200, body={"logged": True}, success=True)

    async def aclose(self) -> None:
        return None


def _use_clients(llm: _FakeLLM, tools: _FakeTools) -> None:
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_tool_client] = lambda: tools


def _complete_decision() -> PlanDecision:
    return PlanDecision(
        calls=[PlannedCall(tool="audit_log", parameters_json='{"action": "incident_response"}')],
        workflow_complete=True,
        status="completed",
    )


def test_orchestrate_happy_path_returns_trace_and_headers() -> None:
    _use_clients(_FakeLLM(decision=_complete_decision(), prompt_tokens=23, completion_tokens=29), _FakeTools())
    with TestClient(app) as client:
        resp = client.post("/orchestrate", json=_WORKFLOW, headers={"X-Request-ID": "orchestrate-req-1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "TASK-0001"
    assert body["status"] == "completed"
    assert len(body["steps_executed"]) == 1
    assert body["steps_executed"][0]["tool"] == "audit_log"
    assert resp.headers["X-Model-Name"] == get_settings().orchestrate_model
    assert resp.headers["X-Prompt-Tokens"] == "23"
    assert resp.headers["X-Completion-Tokens"] == "29"
    assert resp.headers["X-Request-ID"] == "orchestrate-req-1"


def test_orchestrate_failure_returns_200_envelope() -> None:
    _use_clients(_FakeLLM(error=RuntimeError("planner upstream down")), _FakeTools())
    with TestClient(app) as client:
        resp = client.post("/orchestrate", json=_WORKFLOW)

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "TASK-0001"
    assert body["status"] == "failed"
    assert body["steps_executed"] == []
    assert resp.headers["X-Model-Name"] == get_settings().orchestrate_model
    assert resp.headers["X-Orchestrate-Error"] == "RuntimeError"
    assert resp.headers["X-Request-ID"]


def test_orchestrate_open_llm_circuit_returns_503() -> None:
    _use_clients(_FakeLLM(error=LLMUnavailableError("circuit open")), _FakeTools())
    with TestClient(app) as client:
        resp = client.post("/orchestrate", json=_WORKFLOW)

    assert resp.status_code == 503
    assert resp.json() == {"detail": "service unavailable", "error_code": "LLMUnavailableError"}
    assert resp.headers["X-Model-Name"] == get_settings().orchestrate_model
    assert resp.headers["X-Orchestrate-Error"] == "LLMUnavailableError"


def test_orchestrate_workflow_timeout_returns_200_envelope(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings().model_copy(update={"orchestrate_workflow_timeout_seconds": 0.01})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    _use_clients(_FakeLLM(decision=_complete_decision(), delay_seconds=0.05), _FakeTools())
    with TestClient(app) as client:
        resp = client.post("/orchestrate", json=_WORKFLOW)

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "TASK-0001"
    assert body["status"] == "failed"
    assert body["steps_executed"] == []
    assert resp.headers["X-Orchestrate-Error"] == "TimeoutError"
