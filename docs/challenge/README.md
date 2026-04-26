# The Challenge

**FDE Hackathon FY26** — three endpoints, one deployed service, scored by FDEBench.

This challenge tests whether you can build AI-powered solutions that are accurate, fast, resilient, and well-engineered. Each task is a different kind of problem. Your final score is the average across all three — consistency matters.

## Folder Layout

| Path | Purpose |
|---|---|
| [task1/README.md](task1/README.md) | Task 1 brief: signal triage |
| [task1/customer_brief.md](task1/customer_brief.md) | Task 1 customer and domain context |
| [task1/routing_guide.md](task1/routing_guide.md) | Task 1 routing, priority, and escalation rules |
| [task1/engineering_review.md](task1/engineering_review.md) | Task 1 Tier 2 engineering review guidance |
| [task2/README.md](task2/README.md) | Task 2 brief: document extraction |
| [task2/customer_brief.md](task2/customer_brief.md) | Task 2 customer context |
| [task2/field_guide.md](task2/field_guide.md) | Task 2 extraction guidance |
| [task2/engineering_review.md](task2/engineering_review.md) | Task 2 Tier 2 engineering review guidance |
| [task3/README.md](task3/README.md) | Task 3 brief: workflow orchestration |
| [task3/customer_brief.md](task3/customer_brief.md) | Task 3 customer context |
| [task3/execution_guide.md](task3/execution_guide.md) | Task 3 execution and constraint guide |
| [task3/engineering_review.md](task3/engineering_review.md) | Task 3 Tier 2 engineering review guidance |

## What You Are Building

You are building one deployed API with four endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `POST /triage` | Task 1: route noisy mission signals correctly |
| `POST /extract` | Task 2: extract structured data from document images |
| `POST /orchestrate` | Task 3: execute a constrained multi-step workflow |

Each task is scored independently. Your final FDEBench score is the mean of the three task scores, so consistency matters more than a single standout endpoint.

## How To Read The Tasks

Open the task README, check the response contract and scoring weights, then read the support docs in the same folder. Each task folder stands alone.

## FDEBench Summary

FDEBench has two tiers:

- **Tier 1** is the public deterministic score.
- **Tier 2** is the judge-only engineering review of your repository.

### Tier 1 Formula

```
tier1_k = 0.50 x Resolution_k + 0.20 x Efficiency_k + 0.30 x Robustness_k
fdebench = mean(tier1_task1, tier1_task2, tier1_task3)
```

| Dimension | Weight | What it means |
|---|---|---|
| Resolution | 50% | Did the endpoint produce the correct task outcome? |
| Efficiency | 20% | Was it fast and cost-aware enough to be practical? |
| Robustness | 30% | Did it survive hard cases and API resilience probes? |

### Efficiency Detail

```
efficiency = (0.60 x latency_score + 0.40 x cost_score) x 100
```

**Latency:** P95 response time, normalized linearly with per-task thresholds:

| Task | Best (1.0) | Worst (0.0) |
|------|-----------|-------------|
| Triage (`/triage`) | ≤ 500 ms | ≥ 5,000 ms |
| Extract (`/extract`) | ≤ 2,000 ms | ≥ 20,000 ms |
| Orchestrate (`/orchestrate`) | ≤ 1,000 ms | ≥ 10,000 ms |

**Cost:** based on model tier from `X-Model-Name` response header. Return this header on every call.

| Tier | Score | Example models |
|---|---|---|
| Tier 1 | 1.0 | gpt-4.1-nano, phi-4, llama-4 |
| Tier 2 | 0.9 | gpt-4.1-mini, gpt-4o-mini, deepseek-r1 |
| Tier 3 | 0.75 | gpt-4.1, gpt-4o, gpt-5, claude-sonnet, o4-mini |
| Tier 4 | 0.5 | gpt-5-pro, o3, gpt-4-turbo |
| Tier 5 | 0.3 | o1, o3-pro, claude-opus, gpt-4.5 |
| Missing | 0.0 | No `X-Model-Name` header |

### Robustness Detail

```
robustness = (0.60 x adversarial_accuracy + 0.40 x api_resilience) x 100
```

**Adversarial accuracy** (60%): same Resolution scoring function, applied only to a tagged adversarial subset of the hidden eval set.

**API resilience** (40%): 7 probes, binary pass/fail, run per task endpoint before the scoring run:

| # | Probe | What the platform sends | Pass condition |
|---|---|---|---|
| 1 | Malformed JSON | `{"broken}` | HTTP 400 (not 500, not hang) |
| 2 | Empty body | `{}` | HTTP 400 or 422 |
| 3 | Missing required field | Valid JSON, key field removed | HTTP 400/422 or valid response with defaults |
| 4 | Huge payload | 50KB body | HTTP 413, valid response, or clean rejection (not crash) |
| 5 | Wrong content type | `Content-Type: text/plain` | HTTP 415 or still returns valid JSON |
| 6 | Concurrent burst | 20 requests in 500ms | >= 18 of 20 return valid responses |
| 7 | Cold start | Normal request after 60s idle | Returns valid response |

### Tier 1 Platform Behavior

During scoring, the platform:

1. Validates `GET /health` and smoke-tests each task endpoint.
2. Runs resilience probes against each endpoint.
3. Sends hidden eval items with concurrency.
4. Applies per-task Resolution, Efficiency, and Robustness scoring.
5. Averages the three task scores into the final FDEBench composite.

Your service needs to handle concurrent requests, validate inputs, and return stable JSON. Don't crash under load.

### Tier 2 Engineering Review

Judges read your repository for:

- **Code Quality** (25%) — structure, types, error handling, testing, readability
- **Architecture Design** (30%) — AI pipeline, decomposition, API design, trade-off reasoning, scalability
- **AI Problem Solving** (25%) — prompt engineering, evaluation methodology, model/cost awareness
- **Engineering Maturity** (20%) — deployment, config/secrets, observability, security

Tier 2 is repo-level, not task-level. The agents evaluate all of your code together.

Each task folder includes an `engineering_review.md` with what judges specifically look for in that task.

## Data and Local Eval

- Data contracts and public datasets live in [py/data/](../../py/data/) (organized as `task1/`, `task2/`, `task3/`).
- Local scoring harness lives at `py/apps/eval/run_eval.py`. See [../eval/](../eval/) for docs.
- Submission requirements live in [../submission/](../submission/).

## Quick Start

```bash
# 1. Read the challenge overview and task briefs
open docs/challenge/README.md
open docs/challenge/task1/README.md
open docs/challenge/task2/README.md
open docs/challenge/task3/README.md

# 2. Read the support docs inside each task folder

# 3. Build the 3 task endpoints plus health check

# 4. Run the local scorer
cd py/apps/eval
python run_eval.py --endpoint http://localhost:8000
```
