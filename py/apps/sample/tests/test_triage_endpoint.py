"""Endpoint tests for /triage using a mocked LLM client."""

from fastapi.testclient import TestClient
from llm_client import LLMResult
from llm_client import LLMUnavailableError
from main import app
from main import get_llm_client
from models import Category
from models import Team
from models import TriageDecision
from settings import get_settings

_SIGNAL = {
    "ticket_id": "SIG-0001",
    "subject": "Comms fragmenting",
    "description": "Long-range subspace links have been fragmenting since 0600.",
    "reporter": {
        "name": "Sarah Chen",
        "email": "sarah.chen@cdss.space",
        "department": "Propulsion Engineering",
    },
    "created_at": "2026-03-17T09:14:00Z",
    "channel": "bridge_terminal",
    "attachments": [],
}


class _FakeClient:
    """Stand-in for AzureLLMClient injected via a dependency override."""

    def __init__(
        self,
        output: TriageDecision | None = None,
        error: Exception | None = None,
        *,
        prompt_tokens: int = 5,
        completion_tokens: int = 7,
    ) -> None:
        self._output = output
        self._error = error
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    async def complete(
        self,
        *,
        deployment: str,
        messages: object,
        response_format: object,
        max_completion_tokens: int,
    ) -> LLMResult:
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


def test_health_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_triage_happy_path_returns_snake_case_and_headers() -> None:
    _use_client(
        _FakeClient(
            output=TriageDecision(
                category=Category.COMMS,
                priority="P3",
                assigned_team=Team.COMMS,
                needs_escalation=False,
                missing_information=[],
                next_best_action="Investigate the relay.",
                remediation_steps=["Check the antenna array."],
            ),
            prompt_tokens=17,
            completion_tokens=19,
        )
    )
    with TestClient(app) as client:
        resp = client.post("/triage", json=_SIGNAL, headers={"X-Request-ID": "triage-req-1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket_id"] == "SIG-0001"
    assert body["category"] == "Communications & Navigation"
    assert body["assigned_team"] == "Deep Space Communications"
    assert "missing_information" in body
    assert "needs_escalation" in body
    assert resp.headers["X-Model-Name"] == get_settings().triage_model
    assert resp.headers["X-Prompt-Tokens"] == "17"
    assert resp.headers["X-Completion-Tokens"] == "19"
    assert resp.headers["X-Request-ID"] == "triage-req-1"


def test_triage_failure_returns_503_with_error_code() -> None:
    _use_client(_FakeClient(error=RuntimeError("upstream down")))
    with TestClient(app) as client:
        resp = client.post("/triage", json=_SIGNAL)

    assert resp.status_code == 503
    body = resp.json()
    assert body == {"detail": "triage service unavailable", "error_code": "RuntimeError"}
    assert resp.headers["X-Model-Name"] == get_settings().triage_model
    assert resp.headers["X-Triage-Error"] == "RuntimeError"
    assert resp.headers["X-Request-ID"]


def test_triage_open_llm_circuit_returns_503() -> None:
    _use_client(_FakeClient(error=LLMUnavailableError("circuit open")))
    with TestClient(app) as client:
        resp = client.post("/triage", json=_SIGNAL)

    assert resp.status_code == 503
    assert resp.json() == {"detail": "triage service unavailable", "error_code": "LLMUnavailableError"}
    assert resp.headers["X-Model-Name"] == get_settings().triage_model
    assert resp.headers["X-Triage-Error"] == "LLMUnavailableError"
