"""Tier 1 scoring runner — full end-to-end evaluation against a live endpoint.

The runner validates each required task endpoint, executes the configured
benchmark tasks, aggregates task accuracy, and adds shared efficiency points.
"""

import logging
from typing import Any

import httpx

from ms.common.fdebenchkit import TaskResolutionResult
from ms.common.fdebenchkit import validate_resolution_result
from ms.common.fdebenchkit.caller import CallResults
from ms.common.fdebenchkit.caller import call_endpoint
from ms.common.fdebenchkit.probes import run_probes
from ms.common.fdebenchkit.registry import TaskRun
from ms.common.fdebenchkit.registry import get_task_definition
from ms.common.fdebenchkit.weights import EFFICIENCY_WEIGHT_COST as WEIGHT_COST
from ms.common.fdebenchkit.weights import EFFICIENCY_WEIGHT_LATENCY as WEIGHT_LATENCY
from ms.common.fdebenchkit.weights import LATENCY_BEST_MS
from ms.common.fdebenchkit.weights import LATENCY_P50_BEST_MS
from ms.common.fdebenchkit.weights import LATENCY_P50_WORST_MS
from ms.common.fdebenchkit.weights import LATENCY_P95_BEST_MS
from ms.common.fdebenchkit.weights import LATENCY_P95_WORST_MS
from ms.common.fdebenchkit.weights import LATENCY_WORST_MS
from ms.common.fdebenchkit.weights import ROBUSTNESS_WEIGHT_ADVERSARIAL as WEIGHT_ADVERSARIAL
from ms.common.fdebenchkit.weights import ROBUSTNESS_WEIGHT_API_RESILIENCE as WEIGHT_API_RESILIENCE
from ms.common.fdebenchkit.weights import TIER1_WEIGHT_EFFICIENCY as WEIGHT_EFFICIENCY
from ms.common.fdebenchkit.weights import TIER1_WEIGHT_RESOLUTION as WEIGHT_RESOLUTION
from ms.common.fdebenchkit.weights import TIER1_WEIGHT_ROBUSTNESS as WEIGHT_ROBUSTNESS
from ms.common.fdebenchkit.weights import compute_latency_score
from ms.common.models.base import FrozenBaseModel

try:
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer(__name__)
except ModuleNotFoundError:  # pragma: no cover - exercised without OTel installed
    from contextlib import nullcontext as _nullcontext

    class _NoopTracer:
        """Fallback tracer that produces no-op context managers."""

        def start_as_current_span(self, name: str, **kwargs: Any) -> Any:
            return _nullcontext()

    _tracer = _NoopTracer()  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── Efficiency normalization (from weights.py — single source of truth) ──
_LATENCY_BEST_MS = LATENCY_BEST_MS
_LATENCY_WORST_MS = LATENCY_WORST_MS

# ── Circuit breaker thresholds ───────────────────────────────────────
# Cross-task: abort remaining tasks if a task's error rate exceeds this.
_TASK_CIRCUIT_BREAKER_ERROR_RATE = 0.80
# Within-task: forwarded to caller.call_endpoint.
_CALLER_CIRCUIT_BREAKER_THRESHOLD = 10

# ── Model tier scoring ───────────────────────────────────────────────
# Deterministic cost score from the Azure AI Foundry model catalog
# + Azure OpenAI pricing page (April 2026). Prefix-matched.
#
# Tier 1 (100%): Nano/OSS      — $0.05–$0.15/M input
# Tier 2 (90%):  Mini/Haiku    — $0.15–$0.60/M input
# Tier 3 (75%):  Standard      — $1.00–$2.50/M input
# Tier 4 (50%):  Expensive     — $2.50–$10.00/M input
# Tier 5 (30%):  Premium       — $10.00+/M input
# Unknown/missing: 0%

_MODEL_TIER_SCORES: dict[str, float] = {
    # ── Tier 1 (100%): Nano + OSS — $0.05–$0.15/M input ──────────
    "gpt-5.4-nano": 1.0,
    "gpt-5-nano": 1.0,
    "gpt-4.1-nano": 1.0,
    "gpt-oss-20b": 1.0,
    "gpt-oss-120b": 1.0,
    "gpt-oss-safeguard": 1.0,
    "phi-4": 1.0,
    "phi-4-mini": 1.0,
    "phi-4-multimodal": 1.0,
    "phi-4-reasoning": 1.0,
    "phi-3": 1.0,
    "phi-3.5": 1.0,
    "phi-3-mini": 1.0,
    "phi-3-small": 1.0,
    "phi-3-medium": 1.0,
    "llama-3.3": 1.0,
    "llama-4": 1.0,
    "qwen": 1.0,
    "nvidia-egm": 1.0,
    "liquidai": 1.0,
    # ── Tier 2 (90%): Mini/Haiku — $0.15–$0.60/M input ───────────
    "gpt-5.4-mini": 0.9,
    "gpt-5.1-codex-mini": 0.9,
    "gpt-5-mini": 0.9,
    "gpt-4.1-mini": 0.9,
    "gpt-4o-mini": 0.9,
    "gpt-35-turbo": 0.9,
    "gpt-3.5-turbo": 0.9,
    "claude-haiku": 0.9,
    "mistral-small": 0.9,
    "mistral-nemo": 0.9,
    "ministral": 0.9,
    "ai21-jamba-1.5-mini": 0.9,
    "deepseek-v3": 0.9,
    "deepseek-r1": 0.9,
    "cohere-command": 0.9,
    # ── Tier 3 (75%): Standard — $1.00–$2.50/M input ─────────────
    "o4-mini": 0.75,
    "o3-mini": 0.75,
    "o1-mini": 0.75,
    "gpt-5.4": 0.75,
    "gpt-5.3": 0.75,
    "gpt-5.2": 0.75,
    "gpt-5.1": 0.75,
    "gpt-5": 0.75,
    "gpt-5-chat": 0.75,
    "gpt-5-codex": 0.75,
    "gpt-5.1-chat": 0.75,
    "gpt-5.1-codex": 0.75,
    "gpt-5.2-chat": 0.75,
    "gpt-5.2-codex": 0.75,
    "gpt-5.3-chat": 0.75,
    "gpt-5.3-codex": 0.75,
    "gpt-4.1": 0.75,
    "gpt-4o": 0.75,
    "claude-sonnet": 0.75,
    "mistral-large": 0.75,
    "mistral-medium": 0.75,
    "ai21-jamba-1.5-large": 0.75,
    "kimi": 0.75,
    "grok-3-mini": 0.75,
    "grok-4-fast": 0.75,
    "grok-4-1-fast": 0.75,
    "grok-code": 0.75,
    # ── Tier 4 (50%): Expensive — $2.50–$10.00/M input ───────────
    "gpt-5.4-pro": 0.5,
    "gpt-5-pro": 0.5,
    "gpt-4-turbo": 0.5,
    "o3": 0.5,
    "grok-3": 0.5,
    "grok-4": 0.5,
    "grok-4-20": 0.5,
    "computer-use-preview": 0.5,
    # ── Tier 5 (30%): Premium — $10.00+/M input ──────────────────
    "o1": 0.3,
    "o3-pro": 0.3,
    "o3-deep-research": 0.3,
    "gpt-5.1-codex-max": 0.3,
    "gpt-5.2-codex-max": 0.3,
    "gpt-5.3-codex-max": 0.3,
    "gpt-5.4-codex-max": 0.3,
    "gpt-4": 0.3,
    "gpt-4.5": 0.3,
    "claude-opus": 0.3,
}
_FALLBACK_TIER_SCORE = 0.0
_NO_MODEL_REPORTED_SCORE = 0.0


class PreflightValidationError(RuntimeError):
    """Raised when a submission fails scorer-side endpoint preflight validation."""

    def __init__(self, message: str, validation_summary: dict[str, Any]) -> None:
        super().__init__(message)
        self.validation_summary = validation_summary


class TaskScoreSummary(FrozenBaseModel):
    """Summary of one task's deterministic score.

    Each task gets its own Tier 1 composite:
        tier1_k = 0.50 × R_k + 0.20 × E_k + 0.30 × B_k
    """

    task_id: str
    label: str
    endpoint_path: str
    item_label: str
    resolution: float  # 0–100
    adversarial_accuracy: float  # 0–100 (resolution on adversarial subset)
    dimension_scores: dict[str, float]
    adversarial_dimension_scores: dict[str, float]
    dimension_weights: dict[str, float]
    items_scored: int
    items_errored: int

    # ── Per-task efficiency (E_k) ─────────────────────────────────
    efficiency_score: float = 0.0  # 0–100
    latency_score: float = 0.0  # 0–1 (blend of P50 and P95 sub-scores)
    latency_p50_score: float = 0.0  # 0–1 (normalized P50)
    latency_p95_score: float = 0.0  # 0–1 (normalized P95)
    latency_p50_ms: float = 0.0  # raw milliseconds
    latency_p95_ms: float = 0.0  # raw milliseconds
    cost_score: float = 0.0  # 0–1 (model tier)
    primary_model: str = ""

    # ── Per-task robustness (B_k) ─────────────────────────────────
    robustness_score: float = 0.0  # 0–100
    api_resilience: float = 0.0  # 0–100
    probe_results: dict[str, bool] | None = None

    # ── Per-task Tier 1 composite ─────────────────────────────────
    tier1_score: float = 0.0  # 0–100

    def to_cosmos_dict(self) -> dict[str, Any]:
        """Serialize a task summary for Cosmos DB."""
        return {
            "task_id": self.task_id,
            "label": self.label,
            "endpoint_path": self.endpoint_path,
            "item_label": self.item_label,
            "resolution": self.resolution,
            "adversarial_accuracy": self.adversarial_accuracy,
            "dimension_scores": self.dimension_scores,
            "adversarial_dimension_scores": self.adversarial_dimension_scores,
            "dimension_weights": self.dimension_weights,
            "items_scored": self.items_scored,
            "items_errored": self.items_errored,
            "efficiency_score": self.efficiency_score,
            "latency_score": self.latency_score,
            "latency_p50_score": self.latency_p50_score,
            "latency_p95_score": self.latency_p95_score,
            "latency_p50_ms": round(self.latency_p50_ms, 1),
            "latency_p95_ms": round(self.latency_p95_ms, 1),
            "cost_score": self.cost_score,
            "primary_model": self.primary_model,
            "robustness_score": self.robustness_score,
            "api_resilience": self.api_resilience,
            "probe_results": self.probe_results,
            "tier1_score": self.tier1_score,
        }


class ScoringResult(FrozenBaseModel):
    """Complete Tier 1 scoring result for one submission.

    Implements the FDEBench formula (per docs/research/fdebench-methodology.md):
        tier1_k = 0.50 × R_k + 0.20 × E_k + 0.30 × B_k
        total   = mean(tier1_k)  across all completed tasks
    """

    # Tier 1 composite
    total: float  # 0–100
    resolution_score: float  # 0–100 (mean across tasks)
    efficiency_score: float  # 0–100
    robustness_score: float  # 0–100

    # Efficiency detail
    latency_score: float  # 0–1
    cost_score: float  # 0–1

    # Robustness detail
    adversarial_accuracy: float  # 0–100 (mean across tasks)
    api_resilience: float  # 0–100
    probe_results: dict[str, bool]

    # Counts
    items_scored: int
    items_errored: int
    latency_p50_ms: float
    latency_p95_ms: float
    cost_per_item: float
    primary_model: str = ""

    # Per-task breakdowns
    task_scores: tuple[TaskScoreSummary, ...]
    errors: tuple[str, ...]
    validation_summary: dict[str, Any] | None = None
    cost_headers_provided: bool = True

    # Circuit breaker state
    circuit_breaker_triggered: bool = False
    aborted_task_ids: tuple[str, ...] = ()

    def to_cosmos_dict(self) -> dict[str, Any]:
        """Serialize to the Cosmos DB ``scores.functional`` shape."""
        d: dict[str, Any] = {
            "total": self.total,
            "resolution_score": self.resolution_score,
            "efficiency_score": self.efficiency_score,
            "robustness_score": self.robustness_score,
            "latency_score": self.latency_score,
            "cost_score": self.cost_score,
            "adversarial_accuracy": self.adversarial_accuracy,
            "api_resilience": self.api_resilience,
            "probe_results": self.probe_results,
            "items_scored": self.items_scored,
            "items_errored": self.items_errored,
            "latency_p50_ms": round(self.latency_p50_ms, 1),
            "latency_p95_ms": round(self.latency_p95_ms, 1),
            "cost_per_item": round(self.cost_per_item, 6),
            "primary_model": self.primary_model,
            "cost_headers_provided": self.cost_headers_provided,
            "task_scores": [task.to_cosmos_dict() for task in self.task_scores],
        }
        if self.circuit_breaker_triggered:
            d["circuit_breaker_triggered"] = True
            d["aborted_task_ids"] = list(self.aborted_task_ids)
        return d


def _endpoint_check(
    name: str,
    url: str,
    *,
    ok: bool,
    status_code: int | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "url": url,
        "ok": ok,
        "status_code": status_code,
        "error_message": error_message,
    }


def _validation_summary(
    endpoint_checks: list[dict[str, Any]],
    error_messages: list[str],
    *,
    can_evaluate: bool,
) -> dict[str, Any]:
    return {
        "artifacts": [],
        "missing_artifacts": [],
        "repo_url_matches": None,
        "api_endpoint_url_matches": None,
        "endpoint_checks": endpoint_checks,
        "error_messages": error_messages,
        "can_evaluate": can_evaluate,
    }


def _normalize_model_name(name: str) -> str:
    """Normalize a model name for tier matching.

    Handles all naming variants:
    - Azure deployment names: gpt-4-1-mini, gpt-4-1
    - Model catalog names: gpt-4.1-mini, gpt-4.1
    - Versioned names: gpt-4.1-mini-2025-04-14, gpt-4-1-mini-2025-04-14
    """
    import re  # noqa: PLC0415 # stdlib re used only in this helper

    s = name.strip().lower()
    # Strip date suffixes like -2025-04-14
    s = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", s)
    # Normalize version separators: gpt-4-1 → gpt-4.1, gpt-5-4 → gpt-5.4
    s = re.sub(r"(gpt-\d+)-(\d+)", r"gpt-\1.\2".replace("gpt-", ""), s)
    # Simpler approach: just try replacing the second hyphen-digit pattern
    s = name.strip().lower()
    s = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", s)
    # gpt-4-1-mini → gpt-4.1-mini, gpt-5-4-mini → gpt-5.4-mini
    s = re.sub(r"^(gpt-)(\d+)-(\d+)", r"\g<1>\2.\3", s)
    return s


def _lookup_model_tier_score(model_name: str) -> float:
    """Score a model based on its tier (deterministic, not self-reported tokens).

    Robust matching: normalizes hyphens/dots/version suffixes before prefix lookup.
    """
    if not model_name:
        return _NO_MODEL_REPORTED_SCORE

    normalized = _normalize_model_name(model_name)

    for prefix in sorted(_MODEL_TIER_SCORES, key=len, reverse=True):
        if normalized.startswith(prefix):
            return _MODEL_TIER_SCORES[prefix]

    logger.warning(
        "unknown_model_tier: model=%s normalized=%s using_fallback=%.1f", model_name, normalized, _FALLBACK_TIER_SCORE
    )
    return _FALLBACK_TIER_SCORE


def _compute_model_tier_cost_score(call_results: CallResults) -> tuple[float, str]:
    """Compute cost score from model tier selection (deterministic, non-gameable)."""
    primary_model = call_results.primary_model
    score = _lookup_model_tier_score(primary_model)

    if not primary_model:
        logger.warning("no_model_header_reported: cost_score=%.1f", score)
    else:
        logger.info("model_tier_cost: model=%s tier_score=%.1f", primary_model, score)

    return score, primary_model


def _normalize_latency(
    p95_ms: float,
    *,
    p50_ms: float | None = None,
    p50_best_ms: float = LATENCY_P50_BEST_MS,
    p50_worst_ms: float = LATENCY_P50_WORST_MS,
    p95_best_ms: float = LATENCY_P95_BEST_MS,
    p95_worst_ms: float = LATENCY_P95_WORST_MS,
    # Legacy aliases — some callers still pass ``best_ms`` / ``worst_ms``
    # from older code paths; treat them as P95 thresholds.
    best_ms: float | None = None,
    worst_ms: float | None = None,
) -> tuple[float, float, float]:
    """Normalize per-task latency to a blended 0–1 score.

    Returns ``(latency_score, p50_score, p95_score)``. Each component
    is clamped to ``[0, 1]``. The blend is 50/50 — a fast tail with a
    slow median earns the same as a slow tail with a fast median.
    """
    if best_ms is not None:
        p95_best_ms = best_ms
    if worst_ms is not None:
        p95_worst_ms = worst_ms
    p50 = p50_ms if p50_ms is not None else p95_ms
    return compute_latency_score(
        p50,
        p95_ms,
        p50_best_ms=p50_best_ms,
        p50_worst_ms=p50_worst_ms,
        p95_best_ms=p95_best_ms,
        p95_worst_ms=p95_worst_ms,
    )


def _infer_mock_reset_url(task_runs: list[TaskRun]) -> set[str]:
    reset_urls: set[str] = set()
    for task_run in task_runs:
        smoke_request = task_run.smoke_request
        mock_service_url = smoke_request.get("mock_service_url")
        if isinstance(mock_service_url, str) and "/scenario/" in mock_service_url:
            base_url = mock_service_url.split("/scenario/", 1)[0].rstrip("/")
            reset_urls.add(f"{base_url}/reset")
    return reset_urls


async def _reset_mock_services(task_runs: list[TaskRun], *, timeout: float) -> None:
    reset_urls = _infer_mock_reset_url(task_runs)
    if not reset_urls:
        return

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
        for reset_url in sorted(reset_urls):
            try:
                response = await client.post(reset_url)
                if response.status_code != 200:
                    logger.warning("mock_service_reset_failed: url=%s status=%s", reset_url, response.status_code)
            except httpx.HTTPError as exc:
                logger.warning("mock_service_reset_error: url=%s error=%s", reset_url, exc)


async def _run_preflight_validation(endpoint_url: str, task_runs: list[TaskRun], *, timeout: float) -> dict[str, Any]:
    base_url = endpoint_url.rstrip("/")
    health_url = f"{base_url}/health"

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
        try:
            health_response = await client.get(health_url)
        except httpx.HTTPError as exc:
            message = f"GET /health failed: {exc}"
            raise PreflightValidationError(
                message,
                _validation_summary(
                    [_endpoint_check("health", health_url, ok=False, error_message=str(exc))],
                    [message],
                    can_evaluate=False,
                ),
            ) from exc

        health_check = _endpoint_check(
            "health",
            health_url,
            ok=health_response.status_code == 200,
            status_code=health_response.status_code,
            error_message=None
            if health_response.status_code == 200
            else f"GET /health returned HTTP {health_response.status_code}",
        )
        if health_response.status_code != 200:
            message = f"GET /health returned HTTP {health_response.status_code}"
            raise PreflightValidationError(
                message,
                _validation_summary([health_check], [message], can_evaluate=False),
            )

        endpoint_checks: list[dict[str, Any]] = [health_check]

        for task_run in task_runs:
            task = task_run.definition
            request_url = f"{base_url}{task.endpoint_path}"
            check_name = task.endpoint_path.lstrip("/")

            try:
                response = await client.post(request_url, json=task_run.smoke_request)
            except httpx.HTTPError as exc:
                message = f"POST {task.endpoint_path} failed: {exc}"
                endpoint_checks.append(_endpoint_check(check_name, request_url, ok=False, error_message=str(exc)))
                raise PreflightValidationError(
                    message,
                    _validation_summary(endpoint_checks, [message], can_evaluate=False),
                ) from exc

            endpoint_check = _endpoint_check(
                check_name,
                request_url,
                ok=response.status_code == 200,
                status_code=response.status_code,
                error_message=None
                if response.status_code == 200
                else f"POST {task.endpoint_path} returned HTTP {response.status_code}",
            )
            endpoint_checks.append(endpoint_check)
            if response.status_code != 200:
                message = f"POST {task.endpoint_path} returned HTTP {response.status_code}"
                raise PreflightValidationError(
                    message,
                    _validation_summary(endpoint_checks, [message], can_evaluate=False),
                )

            try:
                payload = response.json()
            except ValueError as exc:
                message = f"POST {task.endpoint_path} did not return valid JSON: {exc}"
                endpoint_checks[-1] = _endpoint_check(
                    check_name,
                    request_url,
                    ok=False,
                    status_code=response.status_code,
                    error_message=message,
                )
                raise PreflightValidationError(
                    message,
                    _validation_summary(endpoint_checks, [message], can_evaluate=False),
                ) from exc

            if not isinstance(payload, dict):
                message = f"POST {task.endpoint_path} must return a JSON object"
                endpoint_checks[-1] = _endpoint_check(
                    check_name,
                    request_url,
                    ok=False,
                    status_code=response.status_code,
                    error_message=message,
                )
                raise PreflightValidationError(
                    message,
                    _validation_summary(endpoint_checks, [message], can_evaluate=False),
                )

            missing_keys = sorted(task.response_required_keys - payload.keys())
            if missing_keys:
                message = f"POST {task.endpoint_path} response is missing keys: {', '.join(missing_keys)}"
                endpoint_checks[-1] = _endpoint_check(
                    check_name,
                    request_url,
                    ok=False,
                    status_code=response.status_code,
                    error_message=message,
                )
                raise PreflightValidationError(
                    message,
                    _validation_summary(endpoint_checks, [message], can_evaluate=False),
                )

    return _validation_summary(endpoint_checks, [], can_evaluate=True)


def _build_candidate_responses(task_run: TaskRun, call_results: CallResults) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    identifier_field = task_run.definition.request_id_key
    for result in call_results.results:
        if result.response is not None:
            responses.append(result.response)
        else:
            responses.append({identifier_field: result.ticket_id})
    return responses


def _score_adversarial_subset(
    task_run: TaskRun,
    candidate_responses: list[dict[str, Any]],
    full_scoring_output: dict[str, Any],
) -> dict[str, Any] | None:
    """Re-score only the adversarial subset of gold data."""
    id_key = task_run.definition.request_id_key
    adversarial_golds = [g for g in task_run.gold_items if g.get("difficulty") == "adversarial"]
    if not adversarial_golds:
        return None

    adversarial_ids = {str(g.get(id_key, "")) for g in adversarial_golds}
    adversarial_candidates = [c for c in candidate_responses if str(c.get(id_key, "")) in adversarial_ids]

    return task_run.definition.scorer(adversarial_candidates, adversarial_golds)


def _summarize_task_output(
    task_run: TaskRun,
    scoring_output: dict[str, Any],
    adversarial_output: dict[str, Any] | None,
) -> tuple[TaskScoreSummary, list[str]]:
    """Build a TaskScoreSummary from a scorer's raw output."""
    task = task_run.definition

    resolution = float(scoring_output["resolution"])
    scored_key = {
        "ticket_triage": "tickets_scored",
        "document_extraction": "documents_scored",
        "workflow_orchestration": "tasks_scored",
    }.get(task.task_id, "items_scored")
    errored_key = scored_key.replace("_scored", "_errored")
    items_scored = int(scoring_output.get(scored_key, 0))
    items_errored = int(scoring_output.get(errored_key, 0))

    # Validate against fdebenchkit contract
    contract_result = TaskResolutionResult(
        task_id=task.task_id,
        resolution=resolution,
        dimension_scores={k: float(v) for k, v in scoring_output["dimension_scores"].items()},
        dimension_weights=task.dimension_weights,
        items_scored=items_scored,
        items_errored=items_errored,
    )
    violations = validate_resolution_result(contract_result)
    if violations:
        logger.warning("fdebenchkit_contract_violations: task=%s violations=%s", task.task_id, violations)

    # Adversarial subset resolution
    if adversarial_output is not None:
        adversarial_accuracy = float(adversarial_output["resolution"])
        adversarial_dims = {k: float(v) for k, v in adversarial_output["dimension_scores"].items()}
    else:
        adversarial_accuracy = resolution
        adversarial_dims = {k: float(v) for k, v in scoring_output["dimension_scores"].items()}

    summary = TaskScoreSummary(
        task_id=task.task_id,
        label=task.label,
        endpoint_path=task.endpoint_path,
        item_label=task.item_label,
        resolution=round(resolution, 1),
        adversarial_accuracy=round(adversarial_accuracy, 1),
        dimension_scores={key: float(value) for key, value in scoring_output["dimension_scores"].items()},
        adversarial_dimension_scores=adversarial_dims,
        dimension_weights=task.dimension_weights,
        items_scored=items_scored,
        items_errored=items_errored,
    )
    errors = [f"{task.task_id}: {error}" for error in scoring_output.get("errors", [])]
    return summary, errors


def _coerce_task_runs(
    input_tickets: list[dict[str, Any]] | None,
    gold_answers: list[dict[str, Any]] | None,
    task_runs: list[TaskRun] | None,
) -> list[TaskRun]:
    if task_runs is not None:
        return task_runs

    if input_tickets is None or gold_answers is None:
        msg = "Either task_runs or both input_tickets and gold_answers are required"
        raise ValueError(msg)

    return [
        TaskRun(
            definition=get_task_definition("ticket_triage"),
            input_items=input_tickets,
            gold_items=gold_answers,
        )
    ]


async def run_scoring(
    endpoint_url: str,
    input_tickets: list[dict[str, Any]] | None = None,
    gold_answers: list[dict[str, Any]] | None = None,
    *,
    task_runs: list[TaskRun] | None = None,
    concurrency: int = 10,
    timeout: float = 30.0,
    max_retries: int = 2,
    warm_up_requests: int = 3,
) -> ScoringResult:
    """Run deterministic scoring across the configured functional tasks.

    Per-task flow for each task k (matches docs/research/fdebench-methodology.md):
      1. Run 7 API resilience probes → P_k
      2. Call the endpoint for all items → R_k, L_k, S_k, A_k
      3. Compute per-task efficiency E_k and robustness B_k
      4. Compute tier1_k = 0.50·R_k + 0.20·E_k + 0.30·B_k
      5. Circuit breaker: if error rate > 80%, abort remaining tasks.

    Final composite: fdebench = mean(tier1_k)
    """
    resolved_task_runs = _coerce_task_runs(input_tickets, gold_answers, task_runs)
    if not resolved_task_runs:
        msg = "At least one task run is required"
        raise ValueError(msg)

    for task_run in resolved_task_runs:
        if len(task_run.input_items) != len(task_run.gold_items):
            item_label = task_run.definition.item_label
            msg = (
                f"Input {item_label} count ({len(task_run.input_items)}) does not match "
                f"gold answer count ({len(task_run.gold_items)}) for {task_run.definition.task_id}"
            )
            raise ValueError(msg)

    await _reset_mock_services(resolved_task_runs, timeout=timeout)
    with _tracer.start_as_current_span("tier1.preflight_validation"):
        validation_summary = await _run_preflight_validation(endpoint_url, resolved_task_runs, timeout=timeout)
    logger.info("preflight_validation_passed: endpoint=%s tasks=%d", endpoint_url, len(resolved_task_runs))
    await _reset_mock_services(resolved_task_runs, timeout=timeout)

    aggregate_results = CallResults(results=[])
    task_summaries: list[TaskScoreSummary] = []
    all_errors: list[str] = []
    probe_results_all: dict[str, bool] = {}
    circuit_breaker_triggered = False
    aborted_task_ids: list[str] = []

    for idx, task_run in enumerate(resolved_task_runs):
        task_id = task_run.definition.task_id

        # ── Phase 1: API resilience probes (BEFORE scoring) ──────
        # Running probes first detects a dead API early and provides
        # per-task resilience scores for the B_k formula.
        with _tracer.start_as_current_span(
            "tier1.task.probes",
            attributes={"task.id": task_id},
        ):
            probe_result = await run_probes(
                endpoint_url,
                task_run.definition.endpoint_path,
                task_run.smoke_request,
                task_run.definition.response_required_keys,
                timeout=timeout,
            )
        task_probes: dict[str, bool] = probe_result["probes"]
        task_probes_passed = sum(1 for v in task_probes.values() if v)
        task_api_resilience = round(
            task_probes_passed / max(len(task_probes), 1) * 100,
            1,
        )

        # Merge into aggregate (conservative AND across all tasks)
        for probe_name, passed in task_probes.items():
            if probe_name not in probe_results_all:
                probe_results_all[probe_name] = passed
            else:
                probe_results_all[probe_name] = probe_results_all[probe_name] and passed

        # ── Phase 2: Call endpoint for all items ─────────────────
        with _tracer.start_as_current_span(
            "tier1.task.call_endpoint",
            attributes={"task.id": task_id, "items.count": len(task_run.input_items)},
        ):
            call_results = await call_endpoint(
                endpoint_url,
                task_run.input_items,
                endpoint_path=task_run.definition.endpoint_path,
                identifier_field=task_run.definition.request_id_key,
                concurrency=concurrency,
                timeout=timeout,
                max_retries=max_retries,
                warm_up_requests=warm_up_requests,
                warm_up_payload=task_run.smoke_request,
                circuit_breaker_threshold=_CALLER_CIRCUIT_BREAKER_THRESHOLD,
            )
        logger.info(
            "task_calls_complete: task=%s total=%d errors=%d p50=%.0fms p95=%.0fms",
            task_id,
            len(call_results.results),
            call_results.errors,
            call_results.latency_p50_ms,
            call_results.latency_p95_ms,
        )

        aggregate_results.results.extend(call_results.results)
        aggregate_results.errors += call_results.errors
        aggregate_results.total_latency_ms += call_results.total_latency_ms

        # ── Phase 3: Score responses ─────────────────────────────
        candidate_responses = _build_candidate_responses(task_run, call_results)
        scoring_output = task_run.definition.scorer(candidate_responses, task_run.gold_items)
        adversarial_output = _score_adversarial_subset(task_run, candidate_responses, scoring_output)
        base_summary, task_errors = _summarize_task_output(task_run, scoring_output, adversarial_output)
        all_errors.extend(task_errors)

        # ── Phase 4: Per-task efficiency E_k ─────────────────────
        task_latency_score, task_p50_score, task_p95_score = _normalize_latency(
            call_results.latency_p95_ms,
            p50_ms=call_results.latency_p50_ms,
            p50_best_ms=task_run.definition.latency_p50_best_ms,
            p50_worst_ms=task_run.definition.latency_p50_worst_ms,
            p95_best_ms=task_run.definition.latency_p95_best_ms,
            p95_worst_ms=task_run.definition.latency_p95_worst_ms,
        )
        task_cost_score, task_primary_model = _compute_model_tier_cost_score(call_results)
        task_efficiency = round(
            (WEIGHT_LATENCY * task_latency_score + WEIGHT_COST * task_cost_score) * 100,
            1,
        )

        # ── Phase 5: Per-task robustness B_k ─────────────────────
        task_robustness = round(
            WEIGHT_ADVERSARIAL * base_summary.adversarial_accuracy + WEIGHT_API_RESILIENCE * task_api_resilience,
            1,
        )

        # ── Phase 6: Per-task Tier 1 composite ───────────────────
        task_tier1 = round(
            WEIGHT_RESOLUTION * base_summary.resolution
            + WEIGHT_EFFICIENCY * task_efficiency
            + WEIGHT_ROBUSTNESS * task_robustness,
            1,
        )

        enhanced_summary = TaskScoreSummary(
            task_id=base_summary.task_id,
            label=base_summary.label,
            endpoint_path=base_summary.endpoint_path,
            item_label=base_summary.item_label,
            resolution=base_summary.resolution,
            adversarial_accuracy=base_summary.adversarial_accuracy,
            dimension_scores=base_summary.dimension_scores,
            adversarial_dimension_scores=base_summary.adversarial_dimension_scores,
            dimension_weights=base_summary.dimension_weights,
            items_scored=base_summary.items_scored,
            items_errored=base_summary.items_errored,
            efficiency_score=task_efficiency,
            latency_score=round(task_latency_score, 4),
            latency_p50_score=round(task_p50_score, 4),
            latency_p95_score=round(task_p95_score, 4),
            latency_p50_ms=round(call_results.latency_p50_ms, 1),
            latency_p95_ms=round(call_results.latency_p95_ms, 1),
            cost_score=round(task_cost_score, 4),
            primary_model=task_primary_model,
            robustness_score=task_robustness,
            api_resilience=task_api_resilience,
            probe_results=task_probes,
            tier1_score=task_tier1,
        )
        task_summaries.append(enhanced_summary)

        logger.info(
            "task_tier1: task=%s tier1=%.1f R=%.1f E=%.1f B=%.1f "
            "(latency=%.4f cost=%.4f adversarial=%.1f resilience=%.1f)",
            task_id,
            task_tier1,
            base_summary.resolution,
            task_efficiency,
            task_robustness,
            task_latency_score,
            task_cost_score,
            base_summary.adversarial_accuracy,
            task_api_resilience,
        )

        # ── Phase 7: Cross-task circuit breaker ──────────────────
        total_results = len(call_results.results)
        if total_results > 0:
            error_rate = call_results.errors / total_results
            if error_rate > _TASK_CIRCUIT_BREAKER_ERROR_RATE:
                circuit_breaker_triggered = True
                aborted_task_ids = [tr.definition.task_id for tr in resolved_task_runs[idx + 1 :]]
                logger.error(
                    "circuit_breaker: task=%s error_rate=%.0f%% — aborting %d remaining tasks: %s",
                    task_id,
                    error_rate * 100,
                    len(aborted_task_ids),
                    aborted_task_ids,
                )
                break

    # ── Aggregate: fdebench = mean(tier1_k) ──────────────────────
    n_tasks = len(task_summaries)
    total = round(sum(t.tier1_score for t in task_summaries) / n_tasks, 1)
    resolution_score = round(sum(t.resolution for t in task_summaries) / n_tasks, 1)
    efficiency_score = round(sum(t.efficiency_score for t in task_summaries) / n_tasks, 1)
    robustness_score = round(sum(t.robustness_score for t in task_summaries) / n_tasks, 1)
    adversarial_accuracy = round(
        sum(t.adversarial_accuracy for t in task_summaries) / n_tasks,
        1,
    )
    api_resilience = round(
        sum(1 for v in probe_results_all.values() if v) / max(len(probe_results_all), 1) * 100,
        1,
    )

    # Aggregated latency/cost: mean of per-task scores for backward compat
    latency_score = round(sum(t.latency_score for t in task_summaries) / n_tasks, 4)
    cost_score = round(sum(t.cost_score for t in task_summaries) / n_tasks, 4)

    # Primary model: most common across all tasks
    model_names = [t.primary_model for t in task_summaries if t.primary_model]
    if model_names:
        from collections import Counter  # noqa: PLC0415 # stdlib Counter scoped to this aggregation helper

        primary_model = Counter(model_names).most_common(1)[0][0]
    else:
        primary_model = ""

    logger.info(
        "tier1_composite: total=%.1f resolution=%.1f efficiency=%.1f robustness=%.1f "
        "(adversarial=%.1f api_resilience=%.1f circuit_breaker=%s)",
        total,
        resolution_score,
        efficiency_score,
        robustness_score,
        adversarial_accuracy,
        api_resilience,
        circuit_breaker_triggered,
    )

    return ScoringResult(
        total=total,
        resolution_score=resolution_score,
        efficiency_score=efficiency_score,
        robustness_score=robustness_score,
        latency_score=latency_score,
        cost_score=cost_score,
        adversarial_accuracy=adversarial_accuracy,
        api_resilience=api_resilience,
        probe_results=probe_results_all,
        items_scored=sum(t.items_scored for t in task_summaries),
        items_errored=sum(t.items_errored for t in task_summaries),
        latency_p50_ms=aggregate_results.latency_p50_ms,
        latency_p95_ms=aggregate_results.latency_p95_ms,
        cost_per_item=0.0,
        primary_model=primary_model,
        task_scores=tuple(task_summaries),
        errors=tuple(all_errors),
        validation_summary=validation_summary,
        cost_headers_provided=bool(primary_model),
        circuit_breaker_triggered=circuit_breaker_triggered,
        aborted_task_ids=tuple(aborted_task_ids),
    )
