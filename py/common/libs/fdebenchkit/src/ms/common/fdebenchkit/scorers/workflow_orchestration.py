# Copyright (c) Microsoft. All rights reserved.
"""Deterministic scoring for Task 3: Workflow Orchestration.

Scores candidate execution traces against gold plans. Five dimensions,
all deterministic — no LLM involved.

Dimensions and weights (informed by τ-bench outcome-based scoring):
  1. goal_completion       — 20% — data-driven outcome assertions on end-state
  2. tool_selection        — 15% — multiset F1 on tools
  3. parameter_accuracy    — 05% — per-call parameter match (demoted — low variance)
  4. ordering_correctness  — 20% — dependency constraint satisfaction (causal only)
  5. constraint_compliance — 40% — data-driven outcome assertions (primary differentiator)

Scoring philosophy:
  - Outcome-weighted: constraint_compliance checks business outcomes, not trace shape.
  - Strict on correctness: missing parameters penalized, not ignored.
  - No free points: empty submissions score 0 everywhere.
  - State-transition, not trace-replay: harmless reordering not penalized.
  - Data-driven assertions preferred over hardcoded template logic.

All metrics are deterministic. Fully auditable.
"""

from collections import Counter
from collections import defaultdict
from typing import Any

from ms.common.fdebenchkit.scorers._utils import normalize_text

# ── Weights (sum to 1.0) ─────────────────────────────────────────────

# Weight philosophy (informed by τ-bench outcome-based scoring):
# Outcome dimensions weighted highest. parameter_accuracy is demoted
# because it has near-zero variance (mean=0.996, std=0.063) — it doesn't
# differentiate candidates. Its weight is redistributed to ordering
# (harder to game) and constraints (outcome-based).

WEIGHT_GOAL_COMPLETION = 0.20  # did the workflow complete correctly?
WEIGHT_TOOL_SELECTION = 0.15  # were the right tools used? (trace)
WEIGHT_PARAMETER_ACCURACY = 0.05  # were parameters correct? (low variance, demoted)
WEIGHT_ORDERING = 0.20  # were dependencies respected? (hard to game)
WEIGHT_CONSTRAINTS = 0.40  # were outcomes correct? (outcome — highest weight)

DIMENSION_WEIGHTS: dict[str, float] = {
    "goal_completion": WEIGHT_GOAL_COMPLETION,
    "tool_selection": WEIGHT_TOOL_SELECTION,
    "parameter_accuracy": WEIGHT_PARAMETER_ACCURACY,
    "ordering_correctness": WEIGHT_ORDERING,
    "constraint_compliance": WEIGHT_CONSTRAINTS,
}

# ── Text normalization ────────────────────────────────────────────────

# Use normalize_text from _utils as the local normalizer
_normalize = normalize_text


def _param_value_match(candidate: object, gold: object) -> float:
    """Compare a single parameter value. Returns 0.0 or 1.0.

    Handles strings (normalized), numbers (exact), booleans (exact),
    and dicts/lists (recursive key-by-key).
    """
    if candidate is None and gold is None:
        return 1.0
    if candidate is None or gold is None:
        return 0.0

    # Both strings → normalize and compare
    if isinstance(candidate, str) and isinstance(gold, str):
        return 1.0 if _normalize(candidate) == _normalize(gold) else 0.0

    # Both numbers
    if isinstance(candidate, (int, float)) and isinstance(gold, (int, float)):
        return 1.0 if candidate == gold else 0.0

    # Both bools
    if isinstance(candidate, bool) and isinstance(gold, bool):
        return 1.0 if candidate == gold else 0.0

    # Both dicts → compare key-by-key
    if isinstance(candidate, dict) and isinstance(gold, dict):
        if not gold:
            return 1.0 if not candidate else 0.0
        scores = []
        for key in gold:
            if key in candidate:
                scores.append(_param_value_match(candidate[key], gold[key]))
            else:
                scores.append(0.0)
        return sum(scores) / len(scores) if scores else 0.0

    # Both lists → order-independent set overlap
    if isinstance(candidate, list) and isinstance(gold, list):
        if not gold:
            return 1.0 if not candidate else 0.0
        matched = 0
        remaining = list(candidate)
        for g_item in gold:
            for i, c_item in enumerate(remaining):
                if _param_value_match(c_item, g_item) >= 0.9:
                    matched += 1
                    remaining.pop(i)
                    break
        precision = matched / len(candidate) if candidate else 0.0
        recall = matched / len(gold) if gold else 0.0
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    # Type mismatch — try string comparison as fallback
    return 1.0 if _normalize(str(candidate)) == _normalize(str(gold)) else 0.0


def _mapping_matches(candidate: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Return whether ``candidate`` contains ``expected`` as a recursive subset."""
    for key, expected_value in expected.items():
        candidate_value = candidate.get(key)
        if isinstance(expected_value, dict):
            if not isinstance(candidate_value, dict):
                return False
            if not _mapping_matches(candidate_value, expected_value):
                return False
            continue
        if _param_value_match(candidate_value, expected_value) < 1.0:
            return False
    return True


def _count_matching_calls(
    candidate_steps: list[dict[str, Any]],
    tool: str | None,
    match: dict[str, Any] | None = None,
) -> int:
    normalized_tool = _normalize(tool or "")
    count = 0
    for step in candidate_steps:
        if normalized_tool and _normalize(step.get("tool", "")) != normalized_tool:
            continue
        if match and not _mapping_matches(step.get("parameters", {}), match):
            continue
        count += 1
    return count


# ── Dimension scorers ─────────────────────────────────────────────────


def score_goal_completion(
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
    candidate_response: dict[str, Any] | None = None,
) -> float:
    """Score goal completion using end-state semantics before trace shape."""
    if not candidate_steps:
        return 0.0

    if candidate_response is not None and _normalize(str(candidate_response.get("status", "completed"))) != "completed":
        return 0.0

    gold_steps = gold.get("steps", [])
    if not gold_steps:
        return 1.0 if not candidate_steps else 0.0

    goal_assertions = [
        assertion for assertion in gold.get("outcome_assertions", []) if assertion.get("dimension") == "goal_completion"
    ]
    if goal_assertions:
        return evaluate_outcome_assertions(candidate_steps, goal_assertions)

    template_id = gold.get("expected_outcome", {}).get("template_id")
    template_score = _score_template_goal_completion(template_id, candidate_steps, gold)
    if template_score is not None:
        return template_score

    gold_tool_counts = Counter(s["tool"] for s in gold_steps)
    cand_tool_counts = Counter(s.get("tool", "") for s in candidate_steps)

    covered = sum(min(gold_tool_counts[t], cand_tool_counts.get(t, 0)) for t in gold_tool_counts)
    step_coverage = covered / len(gold_steps)

    # Final tool match bonus: 20% of score
    gold_final_tool = gold_steps[-1]["tool"]
    candidate_final_tool = candidate_steps[-1].get("tool", "")
    final_match = 1.0 if _normalize(candidate_final_tool) == _normalize(gold_final_tool) else 0.0

    # Weighted: 80% step coverage + 20% final tool
    return 0.8 * step_coverage + 0.2 * final_match


def _alt_tool_map(gold: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Build {alt_tool_name: {primary_tool_name: credit_factor}} from gold.

    Reads ``gold["acceptable_alternatives"]``, a mapping of step id to either
    a list of alt tool names (each gets the default 0.9 credit factor) or a
    dict {alt_tool_name: credit_factor}. Step ids are coerced to strings so
    callers can use either ints or strings.

    Used by ``score_tool_selection`` to give partial credit when a candidate
    picked a generic-but-plausible tool over the spec'd specific one.
    """
    raw = gold.get("acceptable_alternatives") or {}
    if not isinstance(raw, dict):
        return {}
    steps = gold.get("steps", []) or []
    primary_by_step: dict[str, str] = {}
    for step in steps:
        step_id = step.get("step")
        tool = step.get("tool")
        if step_id is None or not tool:
            continue
        primary_by_step[str(step_id)] = _normalize(tool)

    alt_map: dict[str, dict[str, float]] = {}
    for step_id, alts in raw.items():
        primary = primary_by_step.get(str(step_id))
        if not primary:
            continue
        if isinstance(alts, dict):
            entries = [(name, float(cf)) for name, cf in alts.items()]
        elif isinstance(alts, list):
            entries = [(name, 0.9) for name in alts]
        else:
            continue
        for alt_name, credit_factor in entries:
            normalized = _normalize(alt_name)
            if not normalized or normalized == primary:
                continue
            credit_factor = max(0.0, min(1.0, credit_factor))
            alt_map.setdefault(normalized, {})[primary] = credit_factor
    return alt_map


def score_tool_selection(
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
) -> float:
    """Set F1 on unique tools used.

    Measures: did the candidate call the right tools (ignoring order/params)?
    Penalizes both missing tools (recall) and unnecessary tools (precision).

    Honors ``gold["acceptable_alternatives"]`` (per-step alt tool list / map):
    when the candidate substitutes an alt tool for a missing primary, the
    substitution is credited at the alt's ``credit_factor`` (default 0.9)
    instead of being treated as a wrong tool.
    """
    if not candidate_steps:
        return 0.0

    gold_tools = gold.get("expected_tools_used", [])
    if not gold_tools:
        return 1.0 if not candidate_steps else 0.0

    gold_counts = Counter(_normalize(s["tool"]) for s in gold.get("steps", []))
    candidate_counts = Counter(_normalize(s.get("tool", "")) for s in candidate_steps)

    # Per-tool min overlap (full credit for exact matches)
    all_tools = set(gold_counts) | set(candidate_counts)
    hard_tp = sum(min(gold_counts.get(t, 0), candidate_counts.get(t, 0)) for t in all_tools)

    # Substitution credit for acceptable alternatives.
    substituted_tp = 0.0
    alt_map = _alt_tool_map(gold)
    if alt_map:
        primary_deficits = {
            primary: gold_counts.get(primary, 0) - candidate_counts.get(primary, 0)
            for primary in {p for primaries in alt_map.values() for p in primaries}
        }
        for alt_tool, primary_map in alt_map.items():
            available = candidate_counts.get(alt_tool, 0)
            if available <= 0:
                continue
            # Iterate primaries by descending credit_factor so the best
            # substitution wins when a single alt could satisfy multiple primaries.
            for primary, credit_factor in sorted(primary_map.items(), key=lambda item: item[1], reverse=True):
                deficit = primary_deficits.get(primary, 0)
                if deficit <= 0:
                    continue
                substitutions = min(deficit, available)
                substituted_tp += substitutions * credit_factor
                primary_deficits[primary] = deficit - substitutions
                available -= substitutions
                if available <= 0:
                    break

    tp_with_subs = hard_tp + substituted_tp
    gold_total = sum(gold_counts.values())
    candidate_total = sum(candidate_counts.values())

    precision = tp_with_subs / candidate_total if candidate_total > 0 else 0.0
    recall = tp_with_subs / gold_total if gold_total > 0 else 0.0

    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score_parameter_accuracy(
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
) -> float:
    """Parameter match for the calls the candidate actually executed."""
    gold_steps = gold.get("steps", [])
    if not gold_steps:
        return 1.0 if not candidate_steps else 0.0
    if not candidate_steps:
        return 0.0

    gold_by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for gold_step in gold_steps:
        gold_by_tool[_normalize(gold_step.get("tool", ""))].append(gold_step)

    step_scores: list[float] = []
    used_indices: dict[str, set[int]] = defaultdict(set)

    for candidate_step in candidate_steps:
        tool = _normalize(candidate_step.get("tool", ""))
        pool = gold_by_tool.get(tool, [])

        if not pool:
            step_scores.append(0.0)
            continue

        best_score = 0.0
        best_idx = -1
        for idx, gold_step in enumerate(pool):
            if idx in used_indices[tool]:
                continue
            gold_params = gold_step.get("parameters", {})
            candidate_params = candidate_step.get("parameters", {})
            if not gold_params and not candidate_params:
                score = 1.0
            elif not gold_params:
                score = 0.0  # gold expects no params, candidate sent some
            else:
                # Score all gold keys (missing candidate keys = 0)
                param_scores = []
                for key, gold_val in gold_params.items():
                    cand_val = candidate_params.get(key)
                    param_scores.append(_param_value_match(cand_val, gold_val))
                # Penalize extra keys the candidate sent that gold doesn't have
                extra_keys = set(candidate_params.keys()) - set(gold_params.keys())
                for _ in extra_keys:
                    param_scores.append(0.0)
                score = sum(param_scores) / len(param_scores) if param_scores else 0.0

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx >= 0:
            used_indices[tool].add(best_idx)
        step_scores.append(best_score)

    return sum(step_scores) / len(step_scores) if step_scores else 0.0


def score_ordering_correctness(
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
) -> float:
    """Were dependency constraints in the gold plan respected?

    For each gold step with depends_on, check that the candidate executed
    all dependencies BEFORE that step in the candidate's execution order.

    Uses position in the candidate's steps_executed list (not step numbers)
    to determine actual execution order.
    """
    gold_steps = gold.get("steps", [])
    if not gold_steps:
        return 1.0
    if not candidate_steps:
        return 0.0

    candidate_by_tool: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for pos, cs in enumerate(candidate_steps):
        candidate_by_tool[_normalize(cs.get("tool", ""))].append((pos, cs))

    # Map each gold step to its candidate execution position
    gold_step_to_position: dict[int, int] = {}
    used: dict[str, set[int]] = defaultdict(set)

    for gs in gold_steps:
        tool = _normalize(gs["tool"])
        pool = candidate_by_tool.get(tool, [])

        # Find best unused match by parameters
        best_pos = -1
        best_score = -1.0
        best_pool_idx = -1

        for pool_idx, (pos, cs) in enumerate(pool):
            if pool_idx in used[tool]:
                continue
            # Score parameters
            gold_params = gs.get("parameters", {})
            cand_params = cs.get("parameters", {})
            if not gold_params:
                score = 1.0
            else:
                scores = [_param_value_match(cand_params.get(k), v) for k, v in gold_params.items()]
                score = sum(scores) / len(scores) if scores else 0.0

            if score > best_score:
                best_score = score
                best_pos = pos
                best_pool_idx = pool_idx

        if best_pool_idx >= 0:
            used[tool].add(best_pool_idx)
            gold_step_to_position[gs["step"]] = best_pos

    # Now check dependencies using matched positions
    dep_checks: list[float] = []

    for gs in gold_steps:
        deps = _hard_dependencies(gs, gold_steps)
        if not deps:
            continue

        current_pos = gold_step_to_position.get(gs["step"])
        if current_pos is None:
            dep_checks.append(0.0)  # Step not found in candidate
            continue

        all_satisfied = True
        for dep_step_num in deps:
            dep_pos = gold_step_to_position.get(dep_step_num)
            if dep_pos is None:
                all_satisfied = False  # Dependency not executed
                break
            if dep_pos >= current_pos:
                all_satisfied = False  # Dependency executed AFTER current step
                break

        dep_checks.append(1.0 if all_satisfied else 0.0)

    # Gold-record-level ``ordered_dependencies``: explicit list of
    # (earlier_step_id, later_step_id) pairs that MUST hold even when the
    # per-step ``depends_on`` heuristic in ``_hard_dependencies`` would have
    # softened or skipped the constraint. Each pair contributes a single
    # 0.0/1.0 to the dep_checks average.
    for pair in gold.get("ordered_dependencies") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        earlier_pos = gold_step_to_position.get(pair[0])
        later_pos = gold_step_to_position.get(pair[1])
        if earlier_pos is None or later_pos is None:
            dep_checks.append(0.0)
            continue
        dep_checks.append(1.0 if earlier_pos < later_pos else 0.0)

    return sum(dep_checks) / len(dep_checks) if dep_checks else 1.0


def score_constraint_compliance(
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
    candidate_response: dict[str, Any] | None = None,
) -> float:
    """Were outcomes correct? Primary differentiation dimension.

    Uses data-driven ``outcome_assertions`` from the gold data when available
    (preferred — generic, auditable, template-agnostic). Falls back to
    template-specific hardcoded checks, then generic heuristics.

    Empty candidate always scores 0 — no free points.
    """
    if not candidate_steps:
        return 0.0

    # Preferred: data-driven constraint assertions (τ-bench style)
    assertions = [
        assertion
        for assertion in gold.get("outcome_assertions", [])
        if assertion.get("dimension") == "constraint_compliance"
    ]
    if assertions:
        return evaluate_outcome_assertions(candidate_steps, assertions)

    # Fallback: template-specific hardcoded checks
    gold_steps = gold.get("steps", [])
    constraints = gold.get("constraints", [])
    if not constraints:
        return 1.0

    template_id = gold.get("expected_outcome", {}).get("template_id")
    template_score = _score_template_constraints(template_id, candidate_steps, gold)
    if template_score is not None:
        return template_score

    checks: list[float] = []

    # Check 1: If gold has audit_log steps, candidate must have them too
    gold_has_audit = any(s["tool"] == "audit_log" for s in gold_steps)
    if gold_has_audit:
        candidate_has_audit = any(_normalize(s.get("tool", "")) == "audit_log" for s in candidate_steps)
        checks.append(1.0 if candidate_has_audit else 0.0)

    # Check 2: Tool count within bounds (penalize BOTH over and under)
    gold_tool_count = len(gold_steps)
    candidate_tool_count = len(candidate_steps)
    if gold_tool_count > 0:
        ratio = candidate_tool_count / gold_tool_count
        if ratio > 2.0 or ratio < 0.3:
            checks.append(0.0)  # Way off
        elif ratio > 1.5 or ratio < 0.5:
            checks.append(0.5)  # Somewhat off
        else:
            checks.append(1.0)  # Within bounds

    # Check 3: Notification routing — if gold notifies specific teams,
    # candidate should notify the same teams
    gold_notifications = [s["parameters"].get("user_id", "") for s in gold_steps if s["tool"] == "notification_send"]
    candidate_notifications = [
        s.get("parameters", {}).get("user_id", "")
        for s in candidate_steps
        if _normalize(s.get("tool", "")) == "notification_send"
    ]
    if gold_notifications:
        gold_targets = {_normalize(n) for n in gold_notifications if n}
        candidate_targets = {_normalize(n) for n in candidate_notifications if n}
        if gold_targets:
            overlap = len(gold_targets & candidate_targets)
            checks.append(overlap / len(gold_targets))

    # Check 4: Email targets — if gold sends to specific accounts,
    # candidate should send to the same accounts (not to skipped ones)
    gold_emails = [s["parameters"].get("account_id", "") for s in gold_steps if s["tool"] == "email_send"]
    candidate_emails = [
        s.get("parameters", {}).get("account_id", "")
        for s in candidate_steps
        if _normalize(s.get("tool", "")) == "email_send"
    ]
    if gold_emails:
        gold_email_targets = {_normalize(e) for e in gold_emails if e}
        candidate_email_targets = {_normalize(e) for e in candidate_emails if e}
        if gold_email_targets:
            # Recall: did candidate email all the right accounts?
            recall = len(gold_email_targets & candidate_email_targets) / len(gold_email_targets)
            # Precision: did candidate avoid emailing wrong accounts?
            precision = (
                len(gold_email_targets & candidate_email_targets) / len(candidate_email_targets)
                if candidate_email_targets
                else 0.0
            )
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            checks.append(f1)

    return sum(checks) / len(checks) if checks else 1.0


def _step_counts(steps: list[dict[str, Any]]) -> Counter[str]:
    return Counter(_normalize(step.get("tool", "")) for step in steps)


def _step_parameters(steps: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    normalized_tool = _normalize(tool_name)
    return [step.get("parameters", {}) for step in steps if _normalize(step.get("tool", "")) == normalized_tool]


def _count_matching_params(steps: list[dict[str, Any]], tool_name: str, predicate: Any) -> int:
    return sum(1 for params in _step_parameters(steps, tool_name) if predicate(params))


def _has_audit_action(steps: list[dict[str, Any]], action: str) -> bool:
    expected = _normalize(action)
    return any(
        _normalize(step.get("parameters", {}).get("action", "")) == expected
        for step in steps
        if _normalize(step.get("tool", "")) == "audit_log"
    )


def _score_boolean_checks(checks: list[bool]) -> float:
    if not checks:
        return 1.0
    return sum(1.0 for check in checks if check) / len(checks)


def _score_template_goal_completion(
    template_id: str | None,
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
) -> float | None:
    if not template_id:
        return None

    outcome = gold.get("expected_outcome", {})
    counts = _step_counts(candidate_steps)

    if template_id == "re_engagement_campaign":
        expected_checks = sum(1 for step in gold.get("steps", []) if step.get("tool") == "subscription_check")
        emails_sent = int(outcome.get("emails_sent", 0))
        return _score_boolean_checks(
            [
                counts["subscription_check"] >= expected_checks,
                counts["email_send"] == emails_sent,
                counts["audit_log"] >= emails_sent,
            ]
        )

    if template_id == "inventory_restock":
        expected_queries = sum(1 for step in gold.get("steps", []) if step.get("tool") == "inventory_query")
        return _score_boolean_checks(
            [
                counts["inventory_query"] >= expected_queries,
                counts["notification_send"] == int(outcome.get("alerts_sent", 0)),
            ]
        )

    if template_id == "onboarding_workflow":
        onboarded = bool(outcome.get("onboarded"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps, "email_send", lambda params: _normalize(params.get("template", "")) == "welcome"
                )
                == (1 if onboarded else 0),
                _count_matching_params(
                    candidate_steps,
                    "email_send",
                    lambda params: _normalize(params.get("template", "")) == "kickoff_invite",
                )
                == (1 if onboarded else 0),
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "sales_team",
                )
                == (0 if onboarded else 1),
                _has_audit_action(candidate_steps, "onboarding_started" if onboarded else "onboarding_blocked"),
            ]
        )

    if template_id == "churn_risk_analysis":
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "lead_retention",
                )
                == int(outcome.get("high_risk", 0)),
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "lead_customer_success",
                )
                == int(outcome.get("medium_risk", 0)),
                counts["audit_log"] == int(outcome.get("alerts_sent", 0)),
            ]
        )

    if template_id == "meeting_scheduler":
        scheduled = bool(outcome.get("scheduled"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "email_send",
                    lambda params: _normalize(params.get("template", "")) == "meeting_invite",
                )
                == (1 if scheduled else 0),
                counts["notification_send"] == (0 if scheduled else 1),
                _has_audit_action(candidate_steps, "meeting_scheduled" if scheduled else "meeting_blocked"),
            ]
        )

    if template_id == "incident_response":
        escalated = bool(outcome.get("escalated"))
        return _score_boolean_checks(
            [
                _count_matching_params(candidate_steps, "inventory_query", lambda _params: True)
                >= int(outcome.get("warehouses_affected", 0)),
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "oncall_engineer",
                )
                == 1,
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "engineering_manager",
                )
                == (1 if escalated else 0),
                _has_audit_action(candidate_steps, "incident_response"),
            ]
        )

    if template_id == "contract_renewal":
        expected_plan = _normalize(str(outcome.get("plan", "")))
        needs_approval = bool(outcome.get("needs_approval"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "email_send",
                    lambda params: _normalize(params.get("template", "")) == "renewal_quote"
                    and _normalize(str(params.get("variables", {}).get("plan", ""))) == expected_plan,
                )
                == 1,
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "finance_approver",
                )
                == (1 if needs_approval else 0),
                _has_audit_action(candidate_steps, "renewal_initiated"),
            ]
        )

    return None


def _score_template_constraints(
    template_id: str | None,
    candidate_steps: list[dict[str, Any]],
    gold: dict[str, Any],
) -> float | None:
    if not template_id:
        return None

    outcome = gold.get("expected_outcome", {})
    counts = _step_counts(candidate_steps)

    if template_id == "re_engagement_campaign":
        emailed_accounts = {
            _normalize(params.get("account_id", ""))
            for params in _step_parameters(candidate_steps, "email_send")
            if params.get("account_id")
        }
        skipped_accounts = {_normalize(account_id) for account_id in outcome.get("skipped", [])}
        return _score_boolean_checks(
            [
                not (emailed_accounts & skipped_accounts),
                counts["email_send"] <= int(outcome.get("emails_sent", 0)),
                _count_matching_params(
                    candidate_steps, "audit_log", lambda params: _normalize(params.get("action", "")) == "email_sent"
                )
                >= int(outcome.get("emails_sent", 0)),
            ]
        )

    if template_id == "inventory_restock":
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("channel", "")) == "slack",
                )
                == counts["notification_send"],
                counts["notification_send"] == int(outcome.get("alerts_sent", 0)),
            ]
        )

    if template_id == "onboarding_workflow":
        onboarded = bool(outcome.get("onboarded"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "sales_team",
                )
                == (0 if onboarded else 1),
                _count_matching_params(
                    candidate_steps, "email_send", lambda params: _normalize(params.get("template", "")) == "welcome"
                )
                == (1 if onboarded else 0),
                counts["audit_log"] >= 1,
            ]
        )

    if template_id == "churn_risk_analysis":
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "lead_retention",
                )
                == int(outcome.get("high_risk", 0)),
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "lead_customer_success",
                )
                == int(outcome.get("medium_risk", 0)),
            ]
        )

    if template_id == "meeting_scheduler":
        scheduled = bool(outcome.get("scheduled"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("channel", "")) == "slack",
                )
                == counts["notification_send"],
                _count_matching_params(
                    candidate_steps,
                    "email_send",
                    lambda params: _normalize(params.get("template", "")) == "meeting_invite",
                )
                == (1 if scheduled else 0),
            ]
        )

    if template_id == "incident_response":
        escalated = bool(outcome.get("escalated"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "oncall_engineer"
                    and _normalize(params.get("channel", "")) == "sms",
                )
                == 1,
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "engineering_manager"
                    and _normalize(params.get("channel", "")) == "slack",
                )
                == (1 if escalated else 0),
            ]
        )

    if template_id == "contract_renewal":
        needs_approval = bool(outcome.get("needs_approval"))
        return _score_boolean_checks(
            [
                _count_matching_params(
                    candidate_steps,
                    "notification_send",
                    lambda params: _normalize(params.get("user_id", "")) == "finance_approver",
                )
                == (1 if needs_approval else 0),
                counts["audit_log"] >= 1,
            ]
        )

    return None


def _hard_dependencies(current_step: dict[str, Any], gold_steps: list[dict[str, Any]]) -> list[int]:
    dependencies = list(current_step.get("depends_on", []))
    if not dependencies:
        return []

    if _normalize(current_step.get("tool", "")) == "audit_log":
        return []

    if dependencies == [current_step.get("step", 0) - 1]:
        previous_step_num = dependencies[0]
        if previous_step_num <= 0:
            return []
        previous_step = gold_steps[previous_step_num - 1]
        previous_tool = _normalize(previous_step.get("tool", ""))
        current_tool = _normalize(current_step.get("tool", ""))
        if previous_tool == "audit_log":
            return []
        if previous_tool == current_tool:
            return []

    return dependencies


# ── Outcome assertions evaluator ──────────────────────────────────────


def evaluate_outcome_assertions(
    candidate_steps: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
) -> float:
    """Evaluate data-driven outcome assertions against candidate steps.

    Each assertion is a dict with:
      - check: "call_count" (default) — count tool calls matching criteria
            - check: "tool_count" — compare total executed call count to bounds
      - tool: which tool to check
            - match: dict of param key/value pairs ALL of which must match as a recursive subset
      - equals: exact count required
      - min: minimum count required
      - max: maximum count required

    Returns fraction of assertions satisfied (0.0–1.0).
    """
    if not assertions:
        return 1.0
    if not candidate_steps:
        return 0.0

    results: list[bool] = []

    for assertion in assertions:
        check = assertion.get("check", "call_count")
        equals = assertion.get("equals")
        minimum = assertion.get("min")
        maximum = assertion.get("max")

        if check == "call_count":
            count = _count_matching_calls(candidate_steps, assertion.get("tool"), assertion.get("match"))

            passed = True
            if equals is not None:
                passed = passed and (count == equals)
            if minimum is not None:
                passed = passed and (count >= minimum)
            if maximum is not None:
                passed = passed and (count <= maximum)
            results.append(passed)

        elif check == "tool_count":
            count = len(candidate_steps)

            passed = True
            if equals is not None:
                passed = passed and (count == equals)
            if minimum is not None:
                passed = passed and (count >= minimum)
            if maximum is not None:
                passed = passed and (count <= maximum)
            results.append(passed)
        else:
            # Unknown check type — skip
            pass

    return sum(1.0 for r in results if r) / len(results) if results else 1.0


# ── Per-task scorer ───────────────────────────────────────────────────


def score_task(
    candidate_response: dict[str, Any],
    gold: dict[str, Any],
) -> dict[str, float]:
    """Score a single task response against its gold plan.

    Returns per-dimension scores (0.0–1.0 each) and a weighted total.
    """
    candidate_steps = candidate_response.get("steps_executed", [])

    goal_completion = score_goal_completion(candidate_steps, gold, candidate_response)
    tool_selection = score_tool_selection(candidate_steps, gold)
    parameter_accuracy = score_parameter_accuracy(candidate_steps, gold)
    ordering = score_ordering_correctness(candidate_steps, gold)
    constraints = score_constraint_compliance(candidate_steps, gold, candidate_response)

    total = (
        WEIGHT_GOAL_COMPLETION * goal_completion
        + WEIGHT_TOOL_SELECTION * tool_selection
        + WEIGHT_PARAMETER_ACCURACY * parameter_accuracy
        + WEIGHT_ORDERING * ordering
        + WEIGHT_CONSTRAINTS * constraints
    )

    return {
        "goal_completion": round(goal_completion, 4),
        "tool_selection": round(tool_selection, 4),
        "parameter_accuracy": round(parameter_accuracy, 4),
        "ordering_correctness": round(ordering, 4),
        "constraint_compliance": round(constraints, 4),
        "total": round(total, 4),
    }


# ── Full submission scorer ────────────────────────────────────────────


def score_submission(
    candidate_responses: list[dict[str, Any]],
    gold_answers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score a full submission for Task 3.

    Matches candidate responses to gold by task_id, then scores each pair.

    Returns:
        - resolution: 0–100 (weighted composite across all dimensions)
        - dimension_scores: per-dimension averages
        - tasks_scored / tasks_errored
        - per_task: full per-task breakdown
    """
    if not gold_answers:
        msg = "Gold answer set is empty"
        raise ValueError(msg)

    candidate_by_id = {c.get("task_id", ""): c for c in candidate_responses}

    per_task: list[dict[str, Any]] = []
    errors: list[str] = []

    all_scores: dict[str, list[float]] = {dim: [] for dim in DIMENSION_WEIGHTS}

    for gold in gold_answers:
        tid = gold["task_id"]
        candidate = candidate_by_id.get(tid)

        if candidate is None:
            errors.append(f"Missing response for task {tid}")
            task_result = {dim: 0.0 for dim in DIMENSION_WEIGHTS}
            task_result["total"] = 0.0
            task_result["task_id"] = tid
            per_task.append(task_result)
            for dim in DIMENSION_WEIGHTS:
                all_scores[dim].append(0.0)
            continue

        task_result = score_task(candidate, gold)
        task_result["task_id"] = tid
        per_task.append(task_result)
        for dim in DIMENSION_WEIGHTS:
            all_scores[dim].append(task_result[dim])

    n = len(gold_answers)
    n_scored = n - len(errors)

    # Average across all tasks per dimension
    dimension_averages = {
        dim: round(sum(scores) / len(scores), 4) if scores else 0.0 for dim, scores in all_scores.items()
    }

    # Weighted composite
    resolution = sum(dimension_averages[dim] * weight for dim, weight in DIMENSION_WEIGHTS.items())

    return {
        "resolution": round(resolution * 100, 1),
        "tasks_scored": n_scored,
        "tasks_errored": len(errors),
        "dimension_scores": dimension_averages,
        "per_task": per_task,
        "errors": errors,
    }
