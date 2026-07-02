# Evaluation Results

Use this file to record the output of the local eval harness at [py/apps/eval/run_eval.py](../py/apps/eval/run_eval.py). Fill in the numbers from your latest run, then add concise analysis.

## Run configuration

| Field | Value |
|---|---|
| Endpoint | |
| Command | `python py/apps/eval/run_eval.py --endpoint ...` |
| Run date | 2026-07-01 |
| Models used | gpt-5.4-nano |
| Notes | All three tasks were executed as separate single-task runs with identical config, so the Local runner summary below is the cross-task mean (`aggregation="mean"`) of the three — matching what a single combined run would report. Per-task numbers are in the Per-task summary and each task section. Transient call errors: Task 1 `errors=1`, Task 2 `errors=2` (coincides with the failed `concurrent_burst` probe), Task 3 `errors=0`; none affected scored items (0 errored across all tasks). |

## Local runner summary

These fields map directly to the top-level runner output.

| Metric | Score |
|---|---|
| FDEBench Composite | 65.6 |
| Resolution (avg) | 64.9 |
| Efficiency (avg) | 50.4 |
| Robustness (avg) | 77.0 |

## Per-task summary

These rows mirror the task summary block printed by the local runner.

| Task | Tier 1 Score | Resolution | Efficiency | Robustness | Items scored | Items errored |
|---|---|---|---|---|---|---|
| Signal Triage | 65.5 | 62.3 | 55.4 | 77.4 | 50 | 0 |
| Document Extraction | 65.1 | 64.2 | 55.7 | 72.8 | 50 | 0 |
| Workflow Orchestration | 66.3 | 68.1 | 40.0 | 80.9 | 50 | 0 |

## Task 1: Signal Triage

### Resolution dimensions

| Dimension | Weight | Score | Notes |
|---|---|---|---|
| `category` | 24% | 0.760 | Strongest dimension. |
| `priority` | 24% | 0.641 | Borderline severity calls miss. |
| `routing` | 24% | 0.711 | Mostly correct queue selection. |
| `missing_info` | 17% | 0.249 | Weakest dimension; main drag on resolution. |
| `escalation` | 11% | 0.667 | Moderate. |

### Operational metrics

| Metric | Value |
|---|---|
| Tier 1 Score | 65.5 |
| Resolution | 62.3 |
| Efficiency | 55.4 |
| Robustness | 77.4 |
| Latency (P95) | 2811 ms |
| Latency score | 0.257 |
| Model | gpt-5.4-nano |
| Cost tier score | 1.000 |
| Adversarial accuracy | 62.3 |
| API resilience | 100.0 |
| Items scored | 50 |
| Items errored | 0 |

### Probe results

| Probe | Pass/Fail | Notes |
|---|---|---|
| malformed_json | PASS | |
| empty_body | PASS | |
| missing_fields | PASS | |
| huge_payload | PASS | |
| wrong_content_type | PASS | |
| concurrent_burst | PASS | |
| slow_followup | PASS | |

### Error analysis

`missing_info` (0.249) is by far the weakest dimension and the biggest drag on resolution — the model under/over-detects which required fields are absent. `priority` (0.641) and `escalation` (0.667) are moderate, driven by borderline severity calls, while `category` (0.760) and `routing` (0.711) are the strongest. Efficiency (55.4) is capped by latency: P95 2811 ms (P50 2032 ms) yields a latency score of just 0.257, while the cost tier is maxed at 1.000 on gpt-5.4-nano. Robustness (77.4) is solid — all 7 probes pass (API resilience 100.0) — and is bounded by adversarial accuracy (62.3).

## Task 2: Document Extraction

### Resolution dimensions

| Dimension | Weight | Score | Notes |
|---|---|---|---|
| `information_accuracy` | 70% | 0.661 | Dominant dimension (70%); moderate field-level accuracy. |
| `text_fidelity` | 30% | 0.600 | Verbatim text capture is the weaker of the two. |

### Operational metrics

| Metric | Value |
|---|---|
| Tier 1 Score | 65.1 |
| Resolution | 64.2 |
| Efficiency | 55.7 |
| Robustness | 72.8 |
| Latency (P95) | 14203 ms |
| Latency score | 0.262 |
| Model | gpt-5.4-nano |
| Cost tier score | 1.000 |
| Adversarial accuracy | 64.2 |
| API resilience | 85.7 |
| Items scored | 50 |
| Items errored | 0 |

### Probe results

| Probe | Pass/Fail | Notes |
|---|---|---|
| malformed_json | PASS | |
| empty_body | PASS | |
| missing_fields | PASS | |
| huge_payload | PASS | |
| wrong_content_type | PASS | |
| concurrent_burst | FAIL | Only failing probe; lines up with the 2 transient call errors under load. |
| slow_followup | PASS | |

### Error analysis

`information_accuracy` (0.661, 70% weight) and `text_fidelity` (0.600) are both moderate, with verbatim text capture lagging field extraction. Efficiency (55.7) is latency-bound: P95 14203 ms (P50 6624 ms) yields a latency score of 0.262 — the slowest per-call task of the three — while cost tier is maxed at 1.000 on gpt-5.4-nano. Robustness (72.8) is the lowest of the three tasks: the `concurrent_burst` probe FAILED, dropping API resilience to 85.7 (6/7 probes), and adversarial accuracy is 64.2. The 2 transient call errors (log: `errors=2`) align with the concurrency failure, pointing to instability under simultaneous requests as the top fix.

## Task 3: Workflow Orchestration

### Resolution dimensions

| Dimension | Weight | Score | Notes |
|---|---|---|---|
| `goal_completion` | 20% | 0.452 | Weakest dimension; workflows often stop short of the full goal. |
| `tool_selection` | 15% | 0.759 | Mostly correct tool choices. |
| `parameter_accuracy` | 5% | 0.533 | Weak, but low weight limits impact. |
| `ordering_correctness` | 20% | 0.731 | Steps largely sequenced correctly. |
| `constraint_compliance` | 40% | 0.760 | Strongest driver; highest-weighted dimension holds up. |

### Operational metrics

| Metric | Value |
|---|---|
| Tier 1 Score | 66.3 |
| Resolution | 68.1 |
| Efficiency | 40.0 |
| Robustness | 80.9 |
| Latency (P95) | 11860 ms |
| Latency score | 0.000 |
| Model | gpt-5.4-nano |
| Cost tier score | 1.000 |
| Adversarial accuracy | 68.1 |
| API resilience | 100.0 |
| Items scored | 50 |
| Items errored | 0 |

### Probe results

| Probe | Pass/Fail | Notes |
|---|---|---|
| malformed_json | PASS | |
| empty_body | PASS | |
| missing_fields | PASS | |
| huge_payload | PASS | |
| wrong_content_type | PASS | |
| concurrent_burst | PASS | |
| slow_followup | PASS | |

### Error analysis

`goal_completion` (0.452) is the weakest dimension — workflows frequently stop short of fully achieving the end goal (missing or incomplete steps). `parameter_accuracy` (0.533) is also low, so emitted tool-call arguments are often off, though its 5% weight caps the damage. The high-weight `constraint_compliance` (0.760, 40%) plus `tool_selection` (0.759) and `ordering_correctness` (0.731) carry resolution to 68.1. Efficiency (40.0) is the dominant weakness: P95 11860 ms (P50 7261 ms) fully saturates the latency penalty (score 0.000), reflecting the cost of multi-step orchestration; cost tier is maxed at 1.000 on gpt-5.4-nano. Robustness (80.9) is strong — all 7 probes pass (API resilience 100.0) — and is bounded by adversarial accuracy (68.1).

## Cross-task takeaways

### What improved the score

This doc captures a single snapshot per task (no A/B baseline recorded), so these are the load-bearing contributors to the 65.6 composite rather than measured deltas:

- **Structured JSON output (`response_format` / json_object mode)** keeps model output schema-conformant across all three tasks — that is what lets the deterministic scorers award resolution credit and keeps errored items at 0/50 everywhere.
- **Resilience defaults and guardrails** — planner completion-flag defaults, server-side `ticket_id` injection, the validation-error handler and content-type middleware — hold API resilience at 100.0 on Triage and Orchestrate (all 7 probes pass) and stop malformed/empty/missing-field inputs from erroring.
- **Model-tier choice (gpt-5.4-nano)** maxes the cost sub-score (1.000) on every task, contributing the full 40% cost weight to efficiency and keeping efficiency from collapsing despite weak latency.
- **Strong core resolution dimensions** carry each task: `category`/`routing` (Triage), `constraint_compliance`/`tool_selection` (Orchestrate), and `information_accuracy` (Extract) are all ≥ 0.66.

### Known limitations

Ordered by impact, with the concrete next fix:

- **Latency is the systemic drag (efficiency avg 50.4).** Every task's latency score is ≤ 0.262 and Orchestrate bottoms out at 0.000 (P95 11860 ms). Fix: cut round-trips and prompt size, parallelize independent tool calls, keep a warm/same-region pool, and early-exit once the goal is met.
- **Task 1 — `missing_info` (0.249).** The classifier mis-identifies which required fields are absent. Fix: a per-category required-field checklist in the prompt plus missing-info few-shots, and tighten the guardrail that derives `missing_info`.
- **Task 2 — concurrency instability.** The `concurrent_burst` probe FAILs and the 2 transient call errors cluster there, making Extract the least robust task (72.8, resilience 85.7). Fix: audit async/client reuse under load (connection pooling, per-request client lifetime, backpressure) and add jittered retries. Secondary: `text_fidelity` (0.600) — improve verbatim capture/normalization.
- **Task 3 — `goal_completion` (0.452) and `parameter_accuracy` (0.533).** Workflows stop short of the full goal and emit off arguments. Fix: verify goal satisfaction before setting `workflow_complete`, and ground tool parameters more strictly against the tool schemas.
- **Adversarial accuracy caps robustness (62–68 across tasks).** It is the ceiling on every task's robustness score. Fix: harden prompts against adversarial/edge inputs and expand the adversarial slice used during local iteration.
