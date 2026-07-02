"""Unit tests for the orchestration executor loop (real clients, mocked methods, no network)."""

from typing import Any
from typing import Literal

from llm_client import AzureLLMClient
from llm_client import LLMResult
from models import OrchestrateRequest
from models import ToolDefinition
from orchestrate.executor import orchestrate
from orchestrate.schema import PlanDecision
from orchestrate.schema import PlannedCall
from orchestrate.tool_client import ToolCallResult
from orchestrate.tool_client import ToolClient
from pytest import MonkeyPatch
from settings import Settings
from settings import get_settings

_Status = Literal["completed", "partial", "failed"]


class _Planner:
    """Scripts a sequence of PlanDecisions, one per planning round."""

    def __init__(self, decisions: list[PlanDecision]) -> None:
        self._decisions = decisions
        self.calls = 0

    async def complete(
        self,
        *,
        deployment: str,
        messages: object,
        response_format: object,
        max_completion_tokens: int,
    ) -> LLMResult:
        decision = self._decisions[min(self.calls, len(self._decisions) - 1)]
        self.calls += 1
        return LLMResult(parsed=decision, model="gpt-5.4-nano", prompt_tokens=3, completion_tokens=4)


class _Tools:
    """Records dispatched calls and returns a canned result (success by default)."""

    def __init__(self, result: ToolCallResult | None = None) -> None:
        self._result = result or ToolCallResult(status_code=200, body={"ok": True}, success=True)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: ToolDefinition, params: dict[str, Any]) -> ToolCallResult:
        self.calls.append((tool.name, params))
        return self._result


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=name, endpoint=f"http://tools.local/{name}", parameters=[])


def _req(
    tool_names: list[str], goal: str = "do the workflow", constraints: list[str] | None = None
) -> OrchestrateRequest:
    return OrchestrateRequest(
        task_id="TASK-1",
        goal=goal,
        available_tools=[_tool(name) for name in tool_names],
        constraints=constraints or [],
    )


def _decision(
    calls: list[tuple[str, str]],
    *,
    complete: bool,
    status: _Status = "partial",
    constraints_satisfied: list[str] | None = None,
    emails_skipped: int = 0,
    skip_reasons: dict[str, int] | None = None,
) -> PlanDecision:
    return PlanDecision(
        calls=[PlannedCall(tool=tool, parameters_json=params) for tool, params in calls],
        workflow_complete=complete,
        status=status,
        constraints_satisfied=constraints_satisfied or [],
        emails_skipped=emails_skipped,
        skip_reasons=skip_reasons or {},
    )


def _clients(monkeypatch: MonkeyPatch, planner: _Planner, tools: _Tools) -> tuple[AzureLLMClient, ToolClient]:
    """Build real clients (offline) and replace their network methods with fakes."""
    settings = get_settings()
    llm = AzureLLMClient(settings)
    tool_client = ToolClient(settings)
    monkeypatch.setattr(llm, "complete", planner.complete)
    monkeypatch.setattr(tool_client, "call", tools.call)
    return llm, tool_client


async def _run(req: OrchestrateRequest, llm: AzureLLMClient, tool_client: ToolClient, settings: Settings | None = None):
    try:
        return await orchestrate(req, llm, tool_client, settings or get_settings())
    finally:
        await llm.aclose()
        await tool_client.aclose()


async def test_single_round_records_steps_and_completes(monkeypatch: MonkeyPatch) -> None:
    planner = _Planner(
        [
            _decision(
                [
                    ("inventory_query", '{"warehouse": "APAC-SOUTH"}'),
                    ("notification_send", '{"user_id": "oncall_engineer", "channel": "sms"}'),
                ],
                complete=True,
                status="completed",
            )
        ]
    )
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["inventory_query", "notification_send"]), llm, tool_client)

    assert planner.calls == 1
    assert result.output.status == "completed"
    assert [step.tool for step in result.output.steps_executed] == ["inventory_query", "notification_send"]
    assert result.output.steps_executed[0].parameters == {"warehouse": "APAC-SOUTH"}
    assert result.output.steps_executed[0].success is True
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4


async def test_multi_round_threads_observations_and_derives_aggregates(monkeypatch: MonkeyPatch) -> None:
    planner = _Planner(
        [
            _decision([("crm_search", '{"filter": "cold"}')], complete=False),
            _decision(
                [
                    ("subscription_check", '{"account_id": "ACC-1"}'),
                    ("subscription_check", '{"account_id": "ACC-3"}'),
                ],
                complete=False,
            ),
            _decision(
                [("email_send", '{"account_id": "ACC-1"}'), ("email_send", '{"account_id": "ACC-3"}')],
                complete=True,
                status="completed",
            ),
        ]
    )
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["crm_search", "subscription_check", "email_send"]), llm, tool_client)

    assert planner.calls == 3
    assert len(result.output.steps_executed) == 5
    assert [step.tool for step in result.output.steps_executed] == [
        "crm_search",
        "subscription_check",
        "subscription_check",
        "email_send",
        "email_send",
    ]
    assert [step.parameters.get("account_id") for step in result.output.steps_executed[1:]] == [
        "ACC-1",
        "ACC-3",
        "ACC-1",
        "ACC-3",
    ]
    assert result.output.status == "completed"
    assert result.output.emails_sent == 2
    assert result.output.accounts_processed == 2  # ACC-1, ACC-3
    assert result.prompt_tokens == 9  # summed across 3 rounds


async def test_unknown_tool_and_bad_json_recorded_as_failed_without_dispatch(monkeypatch: MonkeyPatch) -> None:
    planner = _Planner(
        [_decision([("ghost_tool", "{}"), ("inventory_query", "{not valid json")], complete=True, status="completed")]
    )
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["inventory_query"]), llm, tool_client)

    assert tools.calls == []  # neither call was dispatchable
    assert len(result.output.steps_executed) == 2
    assert all(step.success is False for step in result.output.steps_executed)


async def test_failed_tool_call_is_recorded_and_loop_continues(monkeypatch: MonkeyPatch) -> None:
    planner = _Planner([_decision([("email_send", '{"account_id": "ACC-1"}')], complete=True, status="completed")])
    tools = _Tools(ToolCallResult(status_code=500, body=None, success=False, error="upstream 500"))
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["email_send"]), llm, tool_client)

    assert len(result.output.steps_executed) == 1
    assert result.output.steps_executed[0].success is False
    assert result.output.emails_sent is None  # no successful email


async def test_round_cap_stops_loop_and_reports_partial(monkeypatch: MonkeyPatch) -> None:
    # Planner never signals completion; the cap must stop the loop.
    planner = _Planner([_decision([("inventory_query", "{}")], complete=False)])
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)
    settings = get_settings().model_copy(update={"orchestrate_max_rounds": 2})

    result = await _run(_req(["inventory_query"]), llm, tool_client, settings)

    assert planner.calls == 2  # stopped at the round cap
    assert result.output.status == "partial"


async def test_tool_call_cap_truncates_oversized_planner_batch(monkeypatch: MonkeyPatch) -> None:
    planner = _Planner(
        [
            _decision(
                [
                    ("inventory_query", '{"warehouse": "A"}'),
                    ("inventory_query", '{"warehouse": "B"}'),
                    ("inventory_query", '{"warehouse": "C"}'),
                ],
                complete=True,
                status="completed",
            )
        ]
    )
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)
    settings = get_settings().model_copy(update={"orchestrate_max_tool_calls": 2})

    result = await _run(_req(["inventory_query"]), llm, tool_client, settings)

    assert len(result.output.steps_executed) == 2
    assert tools.calls == [
        ("inventory_query", {"warehouse": "A"}),
        ("inventory_query", {"warehouse": "B"}),
    ]
    assert result.output.status == "partial"


async def test_repeated_planner_batch_stops_as_partial(monkeypatch: MonkeyPatch) -> None:
    repeated = _decision([("inventory_query", '{"warehouse": "A"}')], complete=False)
    planner = _Planner([repeated, repeated, _decision([], complete=True, status="completed")])
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["inventory_query"]), llm, tool_client)

    assert planner.calls == 2
    assert len(result.output.steps_executed) == 1
    assert tools.calls == [("inventory_query", {"warehouse": "A"})]
    assert result.output.status == "partial"


async def test_planner_report_populates_response_fields(monkeypatch: MonkeyPatch) -> None:
    constraints = ["Only email active subscribers", "Send at most 5 emails"]
    planner = _Planner(
        [
            _decision(
                [("email_send", '{"account_id": "ACC-1"}')],
                complete=True,
                status="completed",
                # Includes one verbatim constraint and one the planner invented.
                constraints_satisfied=["Only email active subscribers", "a constraint never requested"],
                emails_skipped=2,
                skip_reasons={"inactive_subscriber": 2, "noise": 0},
            )
        ]
    )
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["email_send"], constraints=constraints), llm, tool_client)

    # Only verbatim request constraints survive.
    assert result.output.constraints_satisfied == ["Only email active subscribers"]
    assert result.output.emails_skipped == 2
    assert result.output.skip_reasons == {"inactive_subscriber": 2}  # zero-count reason dropped


async def test_absent_planner_report_leaves_fields_empty(monkeypatch: MonkeyPatch) -> None:
    planner = _Planner([_decision([("inventory_query", "{}")], complete=True, status="completed")])
    tools = _Tools()
    llm, tool_client = _clients(monkeypatch, planner, tools)

    result = await _run(_req(["inventory_query"], constraints=["some constraint"]), llm, tool_client)

    assert result.output.constraints_satisfied == []
    assert result.output.emails_skipped is None
    assert result.output.skip_reasons is None
