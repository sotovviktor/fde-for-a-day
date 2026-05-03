# Be a Microsoft FDE for a Day

<p align="center">Three real customer problems. One API. Score yourself with FDEBench.</p>

<p align="center">
  <a href="https://youtu.be/Lp6fq74zYBY">
    <img src="https://img.youtube.com/vi/Lp6fq74zYBY/maxresdefault.jpg" alt="Be a Microsoft FDE for a Day — watch the intro" width="720">
  </a>
</p>

<p align="center"><sub>▶ 2-minute cold open from the Delta team. Watch this first.</sub></p>

## Overview

You're deploying an API that solves three business problems, scored by [FDEBench](docs/challenge/README.md).

Each task comes from a real customer engagement: noisy inputs, messy documents, multi-step workflows with constraints. Your solution needs to actually work, not just pass tests. FDEBench scores it on accuracy, latency, cost, resilience, and code quality. Judges read your repo too.

| Task | Endpoint | What you're solving |
|------|----------|---------------------|
| Signal Triage | `POST /triage` | Classify and route noisy mission signals |
| Document Extraction | `POST /extract` | Extract structured data from document images (receipts, invoices, forms, financial statements) |
| Workflow Orchestration | `POST /orchestrate` | Execute multi-step business workflows with tool calls, constraints, and failure handling |

## Getting started

1. Read [docs/challenge/README.md](docs/challenge/README.md) for the scoring formula and what you're building.
2. Pick a task folder ([task1/](docs/challenge/task1/), [task2/](docs/challenge/task2/), [task3/](docs/challenge/task3/)) and read its README plus the support docs in the same folder.
3. Look at the data and schemas in [py/data/](py/data/).
4. Set up and run the local scorer:

```bash
cd py
make setup   # install deps (once)
make run     # start the sample app on :8000 (terminal 1)
make eval    # score all 3 tasks (terminal 2)
```

You can also score individual tasks: `make eval-triage`, `make eval-extract`, `make eval-orchestrate`.

The local scorer gives you a full FDEBench breakdown: resolution, efficiency, robustness, probes.

When you're ready, see [docs/submission/](docs/submission/) for the submission checklist and submit at [aka.ms/fde/hackathon](https://aka.ms/fde/hackaton).

## Repository structure

```
├── docs/
│   ├── challenge/       # Task specs, scoring rubric, FDEBench framework
│   ├── data/            # Public datasets + JSON schemas (task1/, task2/, task3/)
│   ├── eval/            # Eval harness documentation
│   └── submission/      # Submission format and checklist
├── py/                  # Python workspace (uv)
│   ├── common/libs/     # Provided: FastAPI helpers, Pydantic models, fdebenchkit
│   ├── libs/            # Your libraries
│   └── apps/            # Your applications + eval harness
├── ts/                  # TypeScript workspace (pnpm)
│   ├── libs/            # Your libraries
│   └── apps/            # Your applications
└── infra/               # Infrastructure as Code (Pulumi + Azure)
    └── app/             # Your Pulumi program
```

## Development environment

Work locally or in any cloud-hosted environment. A [devcontainer](.devcontainer/) is included if you want a pre-configured setup, but it's optional.

Requirements: Python 3.12+, Node.js 22+, [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/).

```bash
# Python: install dependencies and fix namespace packages
cd py && make setup

# TypeScript (optional)
cd ts && pnpm install

# Pre-commit hooks (optional)
uvx pre-commit install
```

> **macOS note:** if you see `ModuleNotFoundError: No module named 'ms'` after a fresh `uv sync`, run `make setup`. It fixes a macOS quirk where hidden-file flags prevent Python from loading namespace packages.

## Rules

- Five submissions per person.
- Any language, any framework, any AI model.
- AI coding assistants (Copilot, Cursor, Claude) are encouraged.
- Must be deployed and callable via HTTPS.
- Documentation is required: `docs/architecture.md`, `docs/methodology.md`, `docs/evals.md`.

## How you're scored

FDEBench has two tiers. Tier 1 drives the public leaderboard. Tier 2 informs finalist selection.

### Tier 1: deterministic (public leaderboard)

Your deployed API is called with a hidden eval set per task (~1,000 items for Task 1, ~500 each for Tasks 2 and 3). Scoring is fully deterministic, no LLM judges.

```
tier1_k = 0.50 x Resolution + 0.20 x Efficiency + 0.30 x Robustness
fdebench = mean(tier1_task1, tier1_task2, tier1_task3)
```

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| Resolution | 50% | Did you produce the right answer for the task's business outcome? |
| Efficiency | 20% | Was it fast and cheap enough to be operationally usable? |
| Robustness | 30% | Does your API survive adversarial cases, malformed input, concurrency, and cold starts? |

Per-task Tier 1 scores are averaged into a composite FDEBench score (0-100).

Scoring code: [py/common/libs/fdebenchkit/](py/common/libs/fdebenchkit/).

### Tier 2: LLM-as-judge

Judges only (not public). Four agents read your repository and score engineering quality, weighted equally. These scores help judges differentiate finalists with similar Tier 1 scores.

| Agent | Weight | Focus |
|-------|--------|-------|
| Code Quality | 25% | Structure, types, error handling, testing, readability |
| Architecture Design | 25% | AI pipeline, decomposition, API design, tradeoff reasoning |
| AI Problem Solving | 25% | Prompt engineering, evaluation methodology, model and cost awareness |
| Engineering Maturity | 25% | Deployment readiness, config and secrets, observability, security |

Full scoring details: [docs/challenge/README.md](docs/challenge/README.md).

## Before you submit

- `GET /health` returns HTTP 200.
- `POST /triage` returns valid JSON per the output schema.
- `POST /extract` returns valid JSON per the output schema.
- `POST /orchestrate` returns valid JSON per the output schema.
- `docs/architecture.md`, `docs/methodology.md`, `docs/evals.md` are substantive, not placeholders.
- Deployed via HTTPS, handles 10+ concurrent requests, responds in under 30s.
- Public GitHub repository with a README explaining how to install, run, and test.

See [docs/submission/](docs/submission/) for the full checklist, then submit at [aka.ms/fde/hackathon](https://aka.ms/fde/hackaton).

## License

[MIT](LICENSE)
