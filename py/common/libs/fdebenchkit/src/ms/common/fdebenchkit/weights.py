# Copyright (c) Microsoft. All rights reserved.
"""FDEBench scoring functions — Tier 1 composite and cross-task aggregation.

All formulas match ``docs/context/10-fdebench.md`` exactly.
"""

import logging

from ms.common.fdebenchkit.models import EfficiencyResult
from ms.common.fdebenchkit.models import FDEBenchComposite
from ms.common.fdebenchkit.models import RobustnessResult
from ms.common.fdebenchkit.models import TaskResolutionResult
from ms.common.fdebenchkit.models import Tier1Score

logger = logging.getLogger(__name__)

# ── Tier 1 weights (from 10-fdebench.md) ─────────────────────────────

TIER1_WEIGHT_RESOLUTION = 0.50
TIER1_WEIGHT_EFFICIENCY = 0.20
TIER1_WEIGHT_ROBUSTNESS = 0.30

# ── Efficiency sub-weights ────────────────────────────────────────────

EFFICIENCY_WEIGHT_LATENCY = 0.60
EFFICIENCY_WEIGHT_COST = 0.40

# ── Efficiency normalization thresholds ───────────────────────────────
#
# Latency scoring is now a 50/50 blend of normalized P50 and P95 against
# per-task thresholds (see ``tier1/registry.py`` for the per-task values).
# These module-level constants are retained as **default fallbacks only**
# (used by the standalone ``compute_efficiency`` SDK helper); the runner
# always reads thresholds from the task definition.
#
# Calibration of the per-task thresholds:
# * P50 ``best_ms`` = budget-derived target for a well-engineered solution
#   (one LLM call, mini model, same-region, warm pool — see the budget
#   decomposition in ``docs/experimentation/analysis/learnings/
#   l1improvements.md`` §2.2.3).
# * P50 ``worst_ms`` = 4 × ``best_ms`` (preserves a meaningful 0–1 range;
#   the previous 10× ramp put realistic submissions on the flat tail).
# * P95 ``best_ms`` = 3 × P50 ``best_ms`` (Azure OpenAI empirical
#   tail-to-median ratio for chat-completions endpoints) — EXCEPT for
#   the vision/OCR Extract task, where the budget number was the least
#   empirically grounded; that one is calibrated UP to a value reachable
#   by the best cohort-1 submission. See ``registry.py`` for the
#   per-task overrides.
# * P95 ``worst_ms`` ≈ cohort-1 P75 of observed P95 latencies, rounded —
#   ensures the slowest quartile of cohort-1 submissions land in the
#   discriminating part of the ramp rather than clamping to 0. Cohort-1
#   P75 of P95: triage 4 240, extract 22 174, orchestrate 10 472 ms
#   (n = 22 / 20 / 20 from ``docs/experimentation/analysis/out/
#   tasks_anon.csv``, snapshot 2026-04-26).
#
# ── Safety-widening playbook ─────────────────────────────────────────
# If cohort-2 week-1 data shows ``latency_score`` mean < 0.25 on any
# task (= the cohort is being squeezed by an aspirational best_ms):
#   1. Raise that task's ``latency_p95_best_ms`` to the new cohort's
#      P25 of measured P95 (so the top quartile earns ~1.0).
#   2. Raise ``latency_p50_best_ms`` proportionally (P95_best / 3).
#   3. Keep ``worst_ms`` anchored to the new cohort's P75 of P95.
#   4. Re-run ``test_tier1_runner.TestNormalizeLatency`` and the
#      reference-submission CI gate (L1-33). Document the change in
#      ``learnings/l1improvements.md`` §2.2.7 with the calibration date.
# This keeps the score discriminating and fair regardless of how
# different cohort 2 turns out to be.

LATENCY_P50_BEST_MS = 500.0  # P50 < 500ms → 1.0
LATENCY_P50_WORST_MS = 2000.0  # P50 > 2000ms → 0.0
LATENCY_P95_BEST_MS = 1500.0  # P95 < 1500ms → 1.0
LATENCY_P95_WORST_MS = 6000.0  # P95 > 6000ms → 0.0

# Sub-weights for the latency blend.
LATENCY_P50_WEIGHT = 0.5
LATENCY_P95_WEIGHT = 0.5

# Backwards-compatible aliases — value tracks P95 thresholds. Some external
# code still imports ``LATENCY_BEST_MS`` / ``LATENCY_WORST_MS``; new code
# should prefer the explicit P50/P95 constants above.
LATENCY_BEST_MS = LATENCY_P95_BEST_MS
LATENCY_WORST_MS = LATENCY_P95_WORST_MS

# ── Robustness sub-weights ────────────────────────────────────────────

ROBUSTNESS_WEIGHT_ADVERSARIAL = 0.60
ROBUSTNESS_WEIGHT_API_RESILIENCE = 0.40


# Note: Tier-2 (LLM-as-judge) weights live only in the platform copy of
# fdebenchkit (``py/libs/fdebenchkit`` in the platform repo) — they
# affect judge views, not the participant scoreboard, and importing the
# tier2.rubric module here would pull in agentkit unnecessarily.


def _normalize_linear(value: float, best: float, worst: float) -> float:
    """Linearly normalize a value between best (1.0) and worst (0.0).

    Values beyond the range are clamped to [0.0, 1.0].
    """
    if best == worst:
        return 1.0 if value <= best else 0.0
    score = (worst - value) / (worst - best)
    return max(0.0, min(1.0, score))


def validate_resolution_result(result: TaskResolutionResult) -> list[str]:
    """Validate that a resolution result conforms to the FDEBench contract.

    Returns a list of violations (empty = valid).
    """
    violations: list[str] = []

    if not (0.0 <= result.resolution <= 100.0):
        violations.append(f"resolution {result.resolution} not in [0, 100]")

    for dim, score in result.dimension_scores.items():
        if not (0.0 <= score <= 1.0):
            violations.append(f"dimension '{dim}' score {score} not in [0, 1]")

    weight_sum = sum(result.dimension_weights.values())
    if abs(weight_sum - 1.0) > 0.01:
        violations.append(f"dimension weights sum to {weight_sum:.4f}, expected 1.0")

    for dim in result.dimension_scores:
        if dim not in result.dimension_weights:
            violations.append(f"dimension '{dim}' in scores but not in weights")

    # Verify resolution ≈ weighted sum × 100
    recomputed = (
        sum(result.dimension_weights.get(dim, 0) * score for dim, score in result.dimension_scores.items()) * 100
    )
    if abs(result.resolution - recomputed) > 1.0:
        violations.append(f"resolution {result.resolution:.1f} != recomputed {recomputed:.1f}")

    return violations


def compute_latency_score(
    latency_p50_ms: float,
    latency_p95_ms: float,
    *,
    p50_best_ms: float = LATENCY_P50_BEST_MS,
    p50_worst_ms: float = LATENCY_P50_WORST_MS,
    p95_best_ms: float = LATENCY_P95_BEST_MS,
    p95_worst_ms: float = LATENCY_P95_WORST_MS,
) -> tuple[float, float, float]:
    """Compute the blended latency score.

    Formula:
      latency_score = 0.5 × normalize(P50, p50_best, p50_worst)
                    + 0.5 × normalize(P95, p95_best, p95_worst)

    Returns ``(latency_score, p50_score, p95_score)`` so callers can
    persist the sub-scores. Each component is in ``[0, 1]``.
    """
    p50_score = _normalize_linear(latency_p50_ms, p50_best_ms, p50_worst_ms)
    p95_score = _normalize_linear(latency_p95_ms, p95_best_ms, p95_worst_ms)
    blended = LATENCY_P50_WEIGHT * p50_score + LATENCY_P95_WEIGHT * p95_score
    return blended, p50_score, p95_score


def compute_efficiency(
    latency_p95_ms: float,
    cost_score: float,
    *,
    latency_p50_ms: float | None = None,
    p50_best_ms: float = LATENCY_P50_BEST_MS,
    p50_worst_ms: float = LATENCY_P50_WORST_MS,
    p95_best_ms: float = LATENCY_P95_BEST_MS,
    p95_worst_ms: float = LATENCY_P95_WORST_MS,
) -> EfficiencyResult:
    """Compute the efficiency score from latency and model-tier cost.

    Formula:
      efficiency    = 0.60 × latency_score + 0.40 × cost_score
      latency_score = 0.5 × P50_score + 0.5 × P95_score

    ``latency_p50_ms`` defaults to ``latency_p95_ms`` when omitted, which
    preserves the legacy ``compute_efficiency(latency_p95_ms, cost_score)``
    call shape — but new callers should pass both percentiles so the P50
    sub-score reflects the typical request rather than the tail.
    """
    p50 = latency_p50_ms if latency_p50_ms is not None else latency_p95_ms
    latency_score, p50_score, p95_score = compute_latency_score(
        p50,
        latency_p95_ms,
        p50_best_ms=p50_best_ms,
        p50_worst_ms=p50_worst_ms,
        p95_best_ms=p95_best_ms,
        p95_worst_ms=p95_worst_ms,
    )

    efficiency = EFFICIENCY_WEIGHT_LATENCY * latency_score + EFFICIENCY_WEIGHT_COST * cost_score

    return EfficiencyResult(
        latency_score=round(latency_score, 4),
        latency_p50_score=round(p50_score, 4),
        latency_p95_score=round(p95_score, 4),
        cost_score=round(cost_score, 4),
        efficiency=round(efficiency, 4),
        latency_p50_ms=p50,
        latency_p95_ms=latency_p95_ms,
        cost_per_item_usd=0.0,  # Deprecated: model-tier scoring replaces per-item cost
    )


def compute_robustness(
    adversarial_accuracy: float,
    probes_passed: int,
    probes_total: int,
) -> RobustnessResult:
    """Compute the robustness score from adversarial accuracy and API probes.

    Formula:
      robustness = 0.60 × adversarial_accuracy + 0.40 × api_resilience
      api_resilience = probes_passed / probes_total

    ``adversarial_accuracy`` is the resolution score on the adversarial
    subset only (0.0–1.0). It uses the same task-specific scorer but
    filtered to adversarial instances.
    """
    api_resilience = probes_passed / probes_total if probes_total > 0 else 0.0

    robustness = (
        ROBUSTNESS_WEIGHT_ADVERSARIAL * adversarial_accuracy + ROBUSTNESS_WEIGHT_API_RESILIENCE * api_resilience
    )

    return RobustnessResult(
        adversarial_accuracy=round(adversarial_accuracy, 4),
        api_resilience=round(api_resilience, 4),
        robustness=round(robustness, 4),
        probes_passed=probes_passed,
        probes_total=probes_total,
    )


def compute_tier1(
    task_id: str,
    resolution: float,
    efficiency: float,
    robustness: float,
) -> Tier1Score:
    """Compute the Tier 1 composite for a single task.

    Formula:
      tier1 = 0.50 × resolution + 0.20 × efficiency + 0.30 × robustness

    All inputs are 0–100. Output is 0–100.
    """
    tier1 = (
        TIER1_WEIGHT_RESOLUTION * resolution
        + TIER1_WEIGHT_EFFICIENCY * efficiency
        + TIER1_WEIGHT_ROBUSTNESS * robustness
    )

    return Tier1Score(
        task_id=task_id,
        resolution=round(resolution, 1),
        efficiency=round(efficiency, 1),
        robustness=round(robustness, 1),
        tier1=round(tier1, 1),
    )


def compute_fdebench_composite(
    task_scores: list[Tier1Score],
    *,
    aggregation: str = "mean",
) -> FDEBenchComposite:
    """Compute the FDEBench composite across all tasks.

    Aggregation methods:
      - "mean": average of per-task Tier 1 scores (default)
      - "min":  worst-task score (rewards consistency)

    Raises ValueError if no task scores provided.
    """
    if not task_scores:
        msg = "At least one task score is required"
        raise ValueError(msg)

    tier1_values = [ts.tier1 for ts in task_scores]

    if aggregation == "mean":
        fdebench = sum(tier1_values) / len(tier1_values)
    elif aggregation == "min":
        fdebench = min(tier1_values)
    else:
        msg = f"Unknown aggregation: {aggregation!r}. Use 'mean' or 'min'."
        raise ValueError(msg)

    return FDEBenchComposite(
        task_scores=task_scores,
        aggregation=aggregation,
        fdebench=round(fdebench, 1),
    )
