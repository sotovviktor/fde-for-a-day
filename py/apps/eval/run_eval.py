#!/usr/bin/env python3
"""FDEBench Local Eval Harness — score your solution the same way the platform does.

Uses the same fdebenchkit library that powers the production scorer.
Runs all 3 tasks (or a single task) against your deployed endpoint and
prints the full Tier 1 breakdown: Resolution + Efficiency + Robustness.

Usage:
    cd py/apps/eval

    # Score all 3 tasks (full FDEBench composite)
    python run_eval.py --endpoint http://localhost:8000

    # Score one task only
    python run_eval.py --endpoint http://localhost:8000 --task triage
    python run_eval.py --endpoint http://localhost:8000 --task extract
    python run_eval.py --endpoint http://localhost:8000 --task orchestrate

    # Custom dataset (Task 1)
    python run_eval.py \\
        --endpoint http://localhost:8000 \\
        --dataset ../../data/task1/sample.json \\
        --gold ../../data/task1/sample_gold.json

Scoring formula:
    tier1_k = 0.50 x Resolution + 0.20 x Efficiency + 0.30 x Robustness
    fdebench = mean(tier1_task1, tier1_task2, tier1_task3)
"""

import argparse
import asyncio
import json
import logging
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# Add libraries to the import path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "common" / "libs" / "fdebenchkit" / "src"))  # noqa: TID251
sys.path.insert(0, str(_REPO_ROOT / "common" / "libs" / "models" / "src"))  # noqa: TID251

from ms.common.fdebenchkit.registry import TaskRun  # noqa: E402
from ms.common.fdebenchkit.registry import get_task_definition  # noqa: E402
from ms.common.fdebenchkit.runner import PreflightValidationError  # noqa: E402
from ms.common.fdebenchkit.runner import ScoringResult  # noqa: E402
from ms.common.fdebenchkit.runner import run_scoring  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-5s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Data paths (relative to this script) ─────────────────────────────
_DATA_DIR = _REPO_ROOT / "data"

_TASK_DATASETS: dict[str, dict[str, Path]] = {
    "triage": {
        "input": _DATA_DIR / "task1" / "sample.json",
        "gold": _DATA_DIR / "task1" / "sample_gold.json",
    },
    "triage-50": {
        "input": _DATA_DIR / "task1" / "public_eval_50.json",
        "gold": _DATA_DIR / "task1" / "public_eval_50_gold.json",
    },
    "extract": {
        "input": _DATA_DIR / "task2" / "public_eval_50.json",
        "gold": _DATA_DIR / "task2" / "public_eval_50_gold.json",
    },
    "orchestrate": {
        "input": _DATA_DIR / "task3" / "public_eval_50.json",
        "gold": _DATA_DIR / "task3" / "public_eval_50_gold.json",
    },
}

_TASK_ID_MAP = {
    "triage": "ticket_triage",
    "triage-50": "ticket_triage",
    "extract": "document_extraction",
    "orchestrate": "workflow_orchestration",
}

_MOCK_RESPONSES_PATH = _DATA_DIR / "task3" / "public_eval_50_mock_responses.json"
_MOCK_SERVICE_SCRIPT = Path(__file__).resolve().parent / "mock_tool_service.py"
_MOCK_SERVICE_PORT = 9090
_MOCK_BASE_URL = f"http://127.0.0.1:{_MOCK_SERVICE_PORT}/scenario"


def _port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _start_mock_service() -> subprocess.Popen | None:
    """Start the mock tool service for Task 3 in a subprocess.

    Returns the process handle, or None if the service is already
    running or mock data is unavailable.
    """
    if not _MOCK_RESPONSES_PATH.exists():
        logger.warning("Mock responses not found: %s — Task 3 tool calls will fail", _MOCK_RESPONSES_PATH)
        return None

    if _port_in_use(_MOCK_SERVICE_PORT):
        logger.info("Mock tool service already running on port %d", _MOCK_SERVICE_PORT)
        return None

    logger.info("Starting mock tool service on port %d ...", _MOCK_SERVICE_PORT)
    proc = subprocess.Popen(
        [sys.executable, str(_MOCK_SERVICE_SCRIPT), "--port", str(_MOCK_SERVICE_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Wait for the server to be ready (up to 5 seconds)
    for _ in range(50):
        if _port_in_use(_MOCK_SERVICE_PORT):
            logger.info("Mock tool service ready")
            return proc
        time.sleep(0.1)
    logger.warning("Mock tool service did not start within 5s — Task 3 tool calls may fail")
    return proc


def _stop_mock_service(proc: subprocess.Popen | None) -> None:
    """Gracefully shut down the mock service subprocess."""
    if proc is None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _rewrite_task3_urls(items: list[dict]) -> list[dict]:
    """Replace placeholder tool endpoints with the local mock service URL.

    Each Task 3 item gets a ``mock_service_url`` field pointing at the
    local mock service (``http://127.0.0.1:9090/scenario/{task_id}``),
    and each tool's ``endpoint`` is rewritten to the matching mock URL.
    This is the same rewriting the production platform does.
    """
    rewritten: list[dict] = []
    for item in items:
        updated = dict(item)
        task_id = updated.get("task_id", "")
        mock_url = f"{_MOCK_BASE_URL}/{task_id}"
        updated["mock_service_url"] = mock_url
        # Rewrite each tool's endpoint so candidates' code calls the mock
        if "available_tools" in updated:
            new_tools = []
            for tool in updated["available_tools"]:
                t = dict(tool)
                t["endpoint"] = f"{mock_url}/{t['name']}"
                new_tools.append(t)
            updated["available_tools"] = new_tools
        rewritten.append(updated)
    return rewritten


def _inline_t2_images(items: list[dict], dataset_root: Path) -> tuple[list[dict], int, int]:
    """Convert Task 2 ``image_path`` items to ``image_base64`` in-place.

    The shipped public set uses ``content_format = "image_path"`` so a
    candidate's *local* solution can read PNGs straight from disk
    (saves ~71MB of base64 in the JSON). When scoring against a
    remote deployment the candidate's container has no access to the
    candidate's local filesystem, so we replace the relative path with
    the actual image bytes inlined as base64.

    Returns ``(rewritten_items, n_inlined, n_skipped)``. Items that
    are not ``image_path`` (e.g. legacy ``image_base64``,
    ``blob_image``) pass through unchanged.
    """
    import base64  # noqa: PLC0415 # stdlib, scoped to this transform

    rewritten: list[dict] = []
    n_inlined = 0
    n_skipped = 0
    for item in items:
        if item.get("content_format") != "image_path":
            rewritten.append(item)
            n_skipped += 1
            continue
        rel = item.get("content") or ""
        path = (dataset_root / rel).resolve()
        if not path.is_file():
            logger.warning("inline_skip: %s -> %s not found", item.get("document_id"), path)
            rewritten.append(item)
            n_skipped += 1
            continue
        new = dict(item)
        new["content_format"] = "image_base64"
        new["content"] = base64.b64encode(path.read_bytes()).decode("ascii")
        rewritten.append(new)
        n_inlined += 1
    return rewritten, n_inlined, n_skipped


def _load_dataset(path: Path, id_field: str) -> list[dict]:
    """Load a JSON array dataset."""
    if not path.exists():
        logger.error("Dataset not found: %s", path)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        logger.error("Dataset must be a JSON array: %s", path)
        sys.exit(1)
    logger.info("Loaded %d items from %s", len(data), path.name)
    return data


def _build_task_runs(
    tasks: list[str],
    *,
    custom_input: Path | None = None,
    custom_gold: Path | None = None,
    inline_t2_images: bool = False,
) -> list[TaskRun]:
    """Build TaskRun objects for the requested tasks.

    When ``inline_t2_images`` is True, Task 2 ``image_path`` items are
    rewritten to ``image_base64`` (the bytes inlined). Use this when
    scoring an endpoint that does NOT have access to the candidate's
    local filesystem (i.e., any deployed endpoint). See
    :func:`_inline_t2_images` for details.
    """
    runs: list[TaskRun] = []

    for task_key in tasks:
        task_id = _TASK_ID_MAP[task_key]
        definition = get_task_definition(task_id)

        if custom_input and custom_gold and task_key in ("triage", "triage-50"):
            input_items = _load_dataset(custom_input, definition.request_id_key)
            gold_items = _load_dataset(custom_gold, definition.request_id_key)
        else:
            paths = _TASK_DATASETS[task_key]
            input_items = _load_dataset(paths["input"], definition.request_id_key)
            gold_items = _load_dataset(paths["gold"], definition.request_id_key)

        # Task 2: optionally inline images for non-local endpoints.
        if task_id == "document_extraction" and inline_t2_images:
            dataset_root = _TASK_DATASETS[task_key]["input"].parent
            input_items, n_inlined, n_skipped = _inline_t2_images(input_items, dataset_root)
            logger.info(
                "Task 2 image_path → image_base64: inlined=%d skipped=%d (remote endpoint)",
                n_inlined,
                n_skipped,
            )

        # Task 3: rewrite tool endpoints to point at the local mock service
        if task_id == "workflow_orchestration":
            input_items = _rewrite_task3_urls(input_items)

        runs.append(
            TaskRun(
                definition=definition,
                input_items=input_items,
                gold_items=gold_items,
            )
        )

    return runs


def _print_report(result: ScoringResult) -> None:
    """Print a readable scoring report."""
    w = 60

    print()
    print("=" * w)
    print("  FDEBench Local Eval Results")
    print("=" * w)
    print()
    print(f"  FDEBench Composite:  {result.total:6.1f} / 100")
    print(f"  Resolution (avg):    {result.resolution_score:6.1f} / 100")
    print(f"  Efficiency (avg):    {result.efficiency_score:6.1f} / 100")
    print(f"  Robustness (avg):    {result.robustness_score:6.1f} / 100")
    print()

    # Per-task breakdown
    for task in result.task_scores:
        print("-" * w)
        print(f"  {task.label}")
        print("-" * w)
        print(f"    Tier 1 Score:      {task.tier1_score:6.1f}")
        print(f"    Resolution:        {task.resolution:6.1f}")
        print(f"    Efficiency:        {task.efficiency_score:6.1f}")
        print(f"    Robustness:        {task.robustness_score:6.1f}")
        print()

        # Dimension breakdown
        print("    Resolution dimensions:")
        for dim, score in sorted(task.dimension_scores.items()):
            weight = task.dimension_weights.get(dim, 0)
            print(f"      {dim:25s}  {score:.3f}  (weight: {weight:.0%})")
        print()

        # Efficiency detail
        print(f"    Latency (P95):     {task.latency_p95_ms:8.0f} ms  (score: {task.latency_score:.3f})")
        print(f"    Model:             {task.primary_model or '(no X-Model-Name header)'}")
        print(f"    Cost tier score:   {task.cost_score:.3f}")
        print()

        # Robustness detail
        print(f"    Adversarial acc:   {task.adversarial_accuracy:6.1f}")
        print(f"    API resilience:    {task.api_resilience:6.1f}")
        if task.probe_results:
            print("    Probes:")
            for probe, passed in sorted(task.probe_results.items()):
                status = "PASS" if passed else "FAIL"
                print(f"      {probe:25s}  {status}")
        print()

        print(f"    Items scored:      {task.items_scored}")
        print(f"    Items errored:     {task.items_errored}")
        print()

    if result.errors:
        print("-" * w)
        print("  Errors:")
        for error in result.errors[:20]:
            print(f"    {error}")
        if len(result.errors) > 20:
            print(f"    ... and {len(result.errors) - 20} more")
        print()

    print("=" * w)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FDEBench Local Eval Harness — score your solution locally.",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Base URL of your deployed service (e.g., http://localhost:8000)",
    )
    parser.add_argument(
        "--task",
        choices=["triage", "extract", "orchestrate", "all"],
        default="all",
        help="Which task(s) to score (default: all)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Custom input dataset (Task 1 only, backward compat)",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        help="Custom gold dataset (Task 1 only, backward compat)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent requests (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--inline-t2-images",
        choices=("yes", "no"),
        default="yes",
        help=(
            "Whether to inline Task 2 images as base64 in the request body. "
            "Default 'yes' matches the production scoring contract: the "
            "platform downloads each blob, applies per-submission anti-"
            "reconstruction perturbation, then base64-inlines before "
            "POSTing to the candidate's /extract. Setting 'no' sends the "
            "raw 'image_path' record (only meaningful when scoring an "
            "endpoint that has filesystem access to py/data/task2/images/, "
            "which is no longer a supported contract for the reference "
            "solution; kept for harness debugging only)."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    mock_proc: subprocess.Popen | None = None

    # Determine which tasks to run
    if args.dataset and args.gold:
        # Backward compat: custom dataset = Task 1 only
        task_keys = ["triage"]
    elif args.task == "all":
        task_keys = ["triage", "extract", "orchestrate"]
    elif args.task == "triage":
        # Use the 50-item dataset for standalone triage runs
        task_keys = ["triage-50"]
    else:
        task_keys = [args.task]

    # Check data files exist
    for key in task_keys:
        if key in ("triage",) and args.dataset:
            continue
        paths = _TASK_DATASETS[key]
        for label, path in paths.items():
            if not path.exists():
                logger.error("Missing %s dataset: %s", label, path)
                sys.exit(1)

    # Start mock tool service if Task 3 is included
    if any(k == "orchestrate" for k in task_keys):
        # Task 3 requires the mock service running locally on
        # 127.0.0.1:9090. Tool endpoints in each scenario are rewritten to
        # point at that local URL, then sent in the request body to the
        # candidate's /orchestrate endpoint. A *remote* (e.g. cloud-deployed)
        # endpoint cannot reach the host running this harness, so every tool
        # call will fail and scores will collapse to ~0. Score Task 3 only
        # against a locally-running solution.
        from urllib.parse import urlparse  # noqa: PLC0415

        host = (urlparse(args.endpoint).hostname or "").lower()
        if host not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            logger.warning(
                "Task 3 against non-local endpoint (%s) will likely score ~0: "
                "tool endpoints resolve to http://127.0.0.1:9090 which the "
                "remote server cannot reach. Run the participant solution "
                "locally for Task 3, or omit --task orchestrate when scoring "
                "a deployed endpoint.",
                args.endpoint,
            )
        mock_proc = _start_mock_service()

    logger.info("Endpoint: %s", args.endpoint)
    logger.info("Tasks: %s", ", ".join(task_keys))

    # Pattern A (cohort-2 single-format contract): the candidate's
    # /extract endpoint receives a uniform ``image_base64`` payload
    # regardless of who is sending it. The platform-side scorer
    # downloads each blob, applies per-submission anti-reconstruction
    # perturbation, then base64-inlines before POSTing. The local
    # harness mirrors that by inlining the disk PNGs here. Setting
    # ``--inline-t2-images=no`` keeps the raw ``image_path`` record
    # (kept only for debugging the participant repo's data shape).
    inline_t2 = args.inline_t2_images == "yes"
    if "extract" in task_keys:
        if inline_t2:
            logger.info("Task 2 mode: image_base64 (inlining ~50 PNGs from py/data/task2/images/)")
        else:
            logger.warning(
                "Task 2 mode: image_path (raw record). Reference solution drops anything "
                "other than image_base64; use --inline-t2-images=yes for real scoring."
            )

    task_runs = _build_task_runs(
        task_keys,
        custom_input=args.dataset,
        custom_gold=args.gold,
        inline_t2_images=inline_t2,
    )

    try:
        result = await run_scoring(
            args.endpoint,
            task_runs=task_runs,
            concurrency=args.concurrency,
            timeout=args.timeout,
            max_retries=2,
            warm_up_requests=3,
        )
    except PreflightValidationError as exc:
        logger.error("Preflight failed: %s", exc)
        if exc.validation_summary:
            for check in exc.validation_summary.get("endpoint_checks", []):
                status = "OK" if check.get("ok") else "FAIL"
                logger.error("  %s %s: %s", status, check.get("name"), check.get("error_message", ""))
        sys.exit(1)

    _print_report(result)

    # Shut down mock service
    _stop_mock_service(mock_proc)

    # Exit code: 0 if scored, 1 if all items errored
    if result.items_scored == 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
