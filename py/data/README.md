# Data

Public evaluation datasets and JSON schemas for all three FDEBench tasks. Used by the local eval harness at `py/apps/eval/run_eval.py`.

## Layout

```
data/
├── task1/                          # Signal Triage
│   ├── input_schema.json           # POST /triage request schema
│   ├── output_schema.json          # POST /triage response schema
│   ├── sample.json                 # 25 signals — local dev set
│   ├── sample_gold.json            # Gold answers for sample set
│   ├── public_eval_50.json         # 50 signals — public eval set
│   └── public_eval_50_gold.json    # Gold answers for public eval
├── task2/                          # Document Extraction (OCR)
│   ├── input_schema.json           # POST /extract request schema
│   ├── output_schema.json          # POST /extract response schema
│   ├── public_eval_50.json         # 10 document images — public eval set
│   └── public_eval_50_gold.json    # Gold answers for public eval
└── task3/                          # Workflow Orchestration
    ├── input_schema.json           # POST /orchestrate request schema
    ├── output_schema.json          # POST /orchestrate response schema
    ├── public_eval_50.json         # 50 workflow instances — public eval set
    ├── public_eval_50_gold.json    # Gold answers for public eval
    └── public_eval_50_mock_responses.json  # Deterministic mock tool responses
```

## What these datasets are for

The public eval sets are the same format as the hidden eval sets the platform uses for scoring. Use them to test your endpoints locally before submitting:

```bash
cd py/apps/eval
python run_eval.py --endpoint http://localhost:8000
```

The harness loads these datasets automatically. Task 1 defaults to the 50-item public eval set; pass `--dataset` and `--gold` to use the 25-item sample set instead.

## Hidden eval sets (not included)

The platform scores your submission against larger hidden datasets:

| Task | Hidden items | Standard | Adversarial |
|------|-------------|----------|-------------|
| Task 1 | ~1,000 | ~700 | ~300 |
| Task 2 | ~500 | ~350 | ~150 |
| Task 3 | ~500 | ~350 | ~150 |

Adversarial items are tagged with `"difficulty": "adversarial"` and scored separately for the Robustness dimension.

## Schemas

Each task directory contains `input_schema.json` and `output_schema.json` defining the request and response contracts. Your endpoints must accept and return JSON matching these schemas.

## Task 3: Mock Tool Responses

The file `task3/public_eval_50_mock_responses.json` contains the deterministic tool responses used by the mock tool service. When you run the eval harness, it starts a local mock server that loads this file and serves canned responses for every tool call in the 50 public eval scenarios.

**You don't need to do anything special with this file** — the eval harness and mock service handle it automatically. But if you want to understand what tools return (e.g., what `crm_search` gives back for `TASK-0170`), open this file and look:

```json
{
  "TASK-0170": [
    {"tool_name": "crm_search", "call_index": 0, "status_code": 200, "response_body": {"accounts": [...]}},
    {"tool_name": "subscription_check", "call_index": 0, "status_code": 200, "response_body": {...}},
    ...
  ]
}
```

Each tool can have multiple responses (indexed by `call_index`) for scenarios where the same tool is called more than once. The mock service tracks how many times each tool has been called per scenario and returns the appropriate response.

Some adversarial scenarios include tool failures (`status_code: 500`) — your orchestration logic should handle these gracefully.
