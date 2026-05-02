# FDEBench — How Your Solution Is Scored

> 📺 **[V3 — FDE on FDEBench scoring](https://youtu.be/JnfGzRVc_xU)** walks through this end to end.
> The exact weights and formulas are implemented in
> [`py/common/libs/fdebenchkit/`](../../py/common/libs/fdebenchkit/) — that
> code is the source of truth. This page mirrors it for reference.

FDEBench is a 2-tier evaluation framework. **Tier 1** (your public leaderboard score) calls your 3 APIs against ~2,000 hidden eval items (T1: 1,000, T2: 500, T3: 500) and computes a single 0–100 score using deterministic metrics. **Tier 2** is an engineering review of your code by judges, applied to top submissions.

---

## How the public set relates to your leaderboard score

The public set in `py/data/task{1,2,3}/public_eval_50.json` is **50 items per task** — a stratified sample of the hidden set. We sample proportionally on `(difficulty × primary class)` so per-field marginal distributions match the hidden set within total-variation distance ≤ 0.05.

**Treat the public set as a calibration sanity-check, not a leaderboard predictor.** At N=50, sampling noise alone is roughly ±10–13 percentage points on most dimensions. A 2-point public-score change is noise; a 10-point change probably means something real. The leaderboard runs on the full hidden set.

The public gold deliberately omits the `difficulty` and `adversarial_subtype` fields — see [Robustness](#robustness--30-of-each-task) below.

---

## Your score (Tier 1)

```
                ┌─────────────┐
  50%           │  Resolution │
  Resolution    │  (accuracy) │
      +         ├─────────────┤
  20%           │  Efficiency │    ──►   Task Score (0–100)
  Efficiency    │ (speed/cost)│
      +         ├─────────────┤
  30%           │  Robustness │
  Robustness    │ (resilience)│
                └─────────────┘

  FDEBench = mean(task1_score, task2_score, task3_score)
```

Each task gets its own composite. Your final FDEBench score is the **mean of all 3 task scores** — rewarding consistency across all endpoints.

---

## Resolution: 50% of each task

How accurate are your API responses? Each task is scored independently using deterministic F1 metrics against hidden gold data.

### Task 1 — Space Signal Triage

Classify deep-space signals: category, priority, routing, missing info, and escalation.

`POST /triage` · ~1,000 signals

| Dimension | Weight | What's measured |
|-----------|--------|-----------------|
| Category F1 | 24% | Macro F1 across 8 signal categories |
| Priority | 24% | Mean ordinal partial credit (P1-P4, off-by-one = 0.67) |
| Routing F1 | 24% | Macro F1 across 7 response divisions |
| Missing Info F1 | 17% | Mean per-ticket set F1 across 16 constrained terms |
| Escalation F1 | 11% | Binary F1 |

### Task 2 — Document Extraction

Extract structured data from document images (receipts, invoices, forms, charts) using OCR. Each document includes a JSON schema defining expected output fields.

`POST /extract` · ~500 documents

| Dimension | Weight | What's measured |
|-----------|--------|-----------------|
| Information accuracy | 70% | Fuzzy token F1 with value normalization |
| Text fidelity | 30% | Exact character-level field match |

### Task 3 — Workflow Orchestration

Multi-step planning and execution: understand the goal, select tools, execute in sequence, and recover from errors while respecting constraints.

`POST /orchestrate` · ~500 workflows

| Dimension | Weight | What's measured |
|-----------|--------|-----------------|
| Goal completion | 20% | Data-driven outcome assertions on end-state |
| Tool selection | 15% | Multiset F1 on tools used |
| Parameter accuracy | 5% | Per-call parameter match (low variance, demoted) |
| Ordering correctness | 20% | Dependency/causal constraint satisfaction |
| Constraint compliance | 40% | Outcome-based assertions (primary differentiator) |

> **Tip:** Test locally with sample data before submitting. Resolution is 50% of your score, so getting every dimension right matters.

---

## Efficiency: 20% of each task

How fast and cheap is your solution?

```
efficiency = 0.60 × latency_score + 0.40 × cost_score
```

### Latency (60%)

P95 latency is normalized linearly, with **per-task thresholds** that reflect each task's nature:

| Task | Best (1.0) | Worst (0.0) | Why |
|------|-----------|------------|-----|
| Triage | ≤ 500 ms | ≥ 5,000 ms | Text classification — fast |
| Extract | ≤ 2,000 ms | ≥ 20,000 ms | Vision/OCR — inherently slower |
| Orchestrate | ≤ 1,000 ms | ≥ 10,000 ms | Multi-step with tool calls |

### Model cost tier (40%)

Based on the model name from your `X-Model-Name` response header:

| Tier | Score | Examples |
|------|-------|---------|
| Nano | 100% | gpt-5-nano, gpt-4.1-nano, phi-4, llama-4, qwen |
| Mini | 90% | gpt-5-mini, gpt-4.1-mini, gpt-4o-mini, claude-haiku, deepseek-r1 |
| Standard | 75% | gpt-5, gpt-4.1, gpt-4o, claude-sonnet, o4-mini |
| Full | 50% | gpt-5-pro, gpt-4-turbo, o3, grok-4 |
| Premium | 30% | o1, o3-pro, claude-opus, gpt-4.5 |

> **Tip:** Choose smaller models when accuracy allows, batch where possible, and cache repeated lookups to reduce both latency and cost.

---

## Robustness: 30% of each task

Can your API handle the unexpected?

```
robustness = 0.60 × adversarial_accuracy + 0.40 × api_resilience
```

> **About the adversarial subset.** Roughly 30% of the hidden set for each task is adversarial — harder cases designed to test robustness. Concretely:
>
> * **T1 — Triage:** missing or contradictory context, ambiguous channels, signal buried in long noisy threads.
> * **T2 — Extract:** deeply nested JSON schemas or coupled-field shapes (e.g. arrays of objects with cross-field constraints).
> * **T3 — Orchestrate:** longer step counts, stricter compliance / ordering constraints, distractor tools.
>
> The `difficulty` label is **internal** — your public gold does not carry it. We do this so candidates optimise for uniform robustness rather than gating engineering effort on a label they can read at submission time.
>
> Each task's robustness sub-score is normalised by per-task adversarial fraction before being summed into the task score, so all 3 tasks contribute equally to the FDEBench aggregate regardless of their internal hard/easy mix. Optimising one task's robustness will not "steal" weight from the other tasks.

### Adversarial accuracy (60%)

Your resolution score is recalculated on the adversarial subset described above. Same scoring dimensions, tougher inputs. Because the public gold doesn't carry `difficulty`, you cannot self-score adversarial accuracy locally — only your aggregate resolution.

### API resilience (40%)

7 automated probes test error handling, concurrency, and cold-start recovery. Each is binary pass/fail.

```
api_resilience = probes_passed / 7
```

| Probe | What we send | Expected response |
|-------|-------------|-------------------|
| Malformed JSON | `{"broken` | 400 |
| Empty body | `{}` | 400 or 422 |
| Missing fields | Required fields omitted | 400/422 or valid response with defaults |
| 50 KB payload | Oversized body | 413 or clean rejection |
| Wrong content-type | `Content-Type: text/plain` | 415 or valid JSON response |
| Concurrent burst | 20 requests in 500 ms | ≥ 18 valid responses |
| Cold start | Request after 60 s idle | Valid response |

> **Tip:** Validate inputs early and return proper HTTP status codes (400, 422, 413, 415). Make sure your API handles concurrent requests and recovers from cold starts. These are low-effort, high-impact wins.

---

## Tier 2: engineering review (judges only)

Applied to top submissions. AI-assisted agents review your **repository code**, not your API responses. Scores are visible only to judges and help differentiate finalists with similar Tier 1 scores.

### Code Quality · 35%

| Dimension | Weight |
|-----------|--------|
| Structure & modularity | 25% |
| Type safety | 20% |
| Error handling | 20% |
| Testing | 25% |
| Readability | 10% |

### Architecture · 40%

| Dimension | Weight |
|-----------|--------|
| AI pipeline design | 30% |
| System decomposition | 25% |
| API design | 20% |
| Trade-off reasoning | 15% |
| Scalability thinking | 10% |

### Engineering Maturity · 25%

| Dimension | Weight |
|-----------|--------|
| Deployment readiness | 30% |
| Config & secrets | 25% |
| Observability | 20% |
| Security awareness | 25% |

> **Tip:** Write clean, well-structured code from the start. Include a proper README, architecture docs, and tests. Keep secrets in environment variables, not hardcoded. Add basic logging and tracing. Judges can see everything in your repo — make it count.

---

## Scoring code

The exact scoring logic lives in the fdebenchkit library at `py/common/libs/fdebenchkit/`:

| File | What it does |
|------|-------------|
| `scorers/ticket_triage.py` | Task 1 resolution scoring (5 dimensions) |
| `scorers/document_extraction.py` | Task 2 resolution scoring (2 dimensions) |
| `scorers/workflow_orchestration.py` | Task 3 resolution scoring (5 dimensions) |
| `weights.py` | All Tier 1 weights, normalization thresholds, and composite formulas |
| `probes.py` | The 7 API resilience probes |

---

## Platform behaviour to know about

A few platform behaviours are not visible from the API surface and will silently break naive assumptions:

* **Eval items are shuffled per submission.** The platform reorders inputs deterministically per submission (keyed off your submission id). Join your responses on `request_id_key`, not on position. Don't rely on stable input order across submissions or across retries of the same submission.
* **You don't get per-dimension or per-item feedback.** After a submission completes you'll see your aggregate task scores and FDEBench composite. You will **not** see per-dimension scores, per-item feedback, agent reasoning traces, or per-probe pass/fail. Use your local `run_eval.py` against `public_eval_50.json` for per-dimension introspection.
* **The `mock_service_url` host in T3 inputs is rewritten at submission time.** The public gold ships `https://example.invalid/scenario/<task_id>` as a deliberate placeholder; the platform substitutes the real per-task scenario URL when your `/orchestrate` endpoint is called. Don't hard-code the placeholder host.
