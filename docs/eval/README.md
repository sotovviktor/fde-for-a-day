# Eval Harness

Runs the same scoring as the platform, locally. Full Tier 1 breakdown: resolution, efficiency, robustness, probes, per task.

Code: `py/apps/eval/run_eval.py`. Scoring library: `py/common/libs/fdebenchkit/`.

| Guide | What's inside |
|-------|---------------|
| [FDEBench scoring](fdebench.md) | Full breakdown of the scoring formula: resolution dimensions per task, efficiency thresholds, robustness probes, Tier 2 engineering review |
| [Load testing](load-testing.md) | How to stress-test your API locally and in production: hey benchmarks, concurrency tests, cold start checks, performance bottleneck fixes |

## Quick start

```bash
cd py

# All 3 tasks (recommended)
make eval

# Single task
make eval-triage
make eval-extract
make eval-orchestrate
```

Or run the harness directly:

```bash
cd py/apps/eval

# All 3 tasks
python run_eval.py --endpoint http://localhost:8000

# Single task
python run_eval.py --endpoint http://localhost:8000 --task triage
python run_eval.py --endpoint http://localhost:8000 --task extract
python run_eval.py --endpoint http://localhost:8000 --task orchestrate

# Custom dataset (Task 1)
python run_eval.py \
  --endpoint http://localhost:8000 \
  --dataset ../../py/data/task1/sample.json \
  --gold ../../py/data/task1/sample_gold.json
```

For Task 3 (orchestration), the harness automatically starts a mock tool service on port 9090 that serves deterministic tool responses. Your `/orchestrate` endpoint can call these tools via HTTP during evaluation. See `py/apps/eval/mock_tool_service.py` for details.

> **Task 3 cannot be scored against a remote endpoint.** The harness rewrites
> every tool endpoint in the scenario to `http://127.0.0.1:9090/...` (the local
> mock service) and POSTs that to your `/orchestrate` URL. A cloud-deployed
> server cannot reach `127.0.0.1` on your laptop, so every tool call fails and
> the score collapses to ~0. Always run `--task orchestrate` against a local
> server (e.g. `--endpoint http://localhost:8000`). When scoring a deployed
> endpoint, omit `orchestrate` and run only `--task triage` and `--task extract`.
> The platform-side hidden eval bypasses this entirely — its mock service runs
> in-cluster and tool URLs resolve normally.

> **T3 local scores are calibration-only.** The shipped
> `py/data/task3/public_eval_50_mock_responses.json` is the deterministic
> answer key for the 50 public scenarios; the local mock service replays
> it byte-for-byte, so a correct orchestration loop will trend toward a
> perfect public T3. Use the local score to verify your wiring (contract,
> parsing, constraints) — not as a leaderboard preview. The hidden eval
> rotates every `task_id` per submission and serves from a private mock
> service. See [Task 3 README](../challenge/task3/README.md#local-testing)
> for details.

## What it scores

```
tier1_k = 0.50 x Resolution + 0.20 x Efficiency + 0.30 x Robustness
fdebench = mean(tier1_task1, tier1_task2, tier1_task3)
```

- Resolution (50%): per-task scoring against gold answers. Weights differ by task.
- Efficiency (20%): P95 latency + model tier cost from `X-Model-Name` header.
- Robustness (30%): adversarial accuracy (60%) + 7 API resilience probes (40%).

## Output

For each run, the harness prints:

- FDEBench composite and per-task Tier 1 scores
- Per-task resolution dimensions and weights
- Latency, model name, and cost-tier score
- Adversarial accuracy and API resilience
- Probe results (pass/fail per probe)
- Items scored and items errored

## Checked-in datasets

| Task | Items | Input | Gold |
|------|-------|-------|------|
| Triage (sample) | 25 | `py/data/task1/sample.json` | `sample_gold.json` |
| Triage | 50 | `py/data/task1/public_eval_50.json` | `public_eval_50_gold.json` |
| Extract | 50 | `py/data/task2/public_eval_50.json` | `public_eval_50_gold.json` |
| Orchestrate | 50 | `py/data/task3/public_eval_50.json` | `public_eval_50_gold.json` |

## fdebenchkit library

Located at `py/common/libs/fdebenchkit/`. The harness adds it to `sys.path` automatically.

| File | What it does |
|------|-------------|
| `scorers/ticket_triage.py` | Task 1 scoring (5 dimensions) |
| `scorers/document_extraction.py` | Task 2 scoring (2 dimensions: information accuracy + text fidelity) |
| `scorers/workflow_orchestration.py` | Task 3 scoring (5 dimensions) |
| `weights.py` | All Tier 1 weights and formulas |
| `probes.py` | 7 API resilience probes |
| `caller.py` | Async HTTP caller with retries and latency tracking |
| `runner.py` | End-to-end Tier 1 orchestrator |
| `registry.py` | Task definitions (endpoints, required keys, weights) |

## Dependencies

```bash
cd py && uv sync
```
