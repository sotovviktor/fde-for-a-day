# FDEBench — development commands
# Run `make` to see available targets.

.DEFAULT_GOAL := help

# Common library paths (fallback for namespace package resolution)
export PYTHONPATH := $(CURDIR)/common/libs/models/src:$(CURDIR)/common/libs/fdebenchkit/src:$(CURDIR)/common/libs/fastapi/src

.PHONY: help setup run test eval eval-triage eval-extract eval-orchestrate mock-tools

help: ## Show this help
	@echo "FDEBench Development Commands"
	@echo ""
	@echo "  make setup              Install dependencies"
	@echo "  make run                Start the sample solution on :8000"
	@echo "  make eval               Score all 3 tasks"
	@echo "  make eval-triage        Score Task 1 only"
	@echo "  make eval-extract       Score Task 2 only"
	@echo "  make eval-orchestrate   Score Task 3 only"
	@echo "  make mock-tools         Start mock tool service on :9090"
	@echo "  make test               Run unit tests (mocked LLM; no network)"
	@echo ""
	@echo "Typical workflow:"
	@echo "  make setup        # once"
	@echo "  make run          # terminal 1"
	@echo "  make eval         # terminal 2"

setup: ## Install all dependencies
	uv sync --all-packages
	@# Fix macOS hidden-flag issue that prevents .pth files from loading
	@# (macOS sets UF_HIDDEN on .venv because it starts with '.')
	@if command -v chflags >/dev/null 2>&1; then \
		chflags -R nohidden .venv 2>/dev/null || true; \
	fi
	@echo ""
	@echo "Setup complete. Run 'make run' to start the sample solution."

run: ## Start the sample solution on port 8000
	cd apps/sample && uvicorn main:app --port 8000 --reload

eval: ## Score all 3 tasks against http://localhost:8000
	cd apps/eval && python run_eval.py --endpoint http://localhost:8000

eval-triage: ## Score Task 1 only
	cd apps/eval && python run_eval.py --endpoint http://localhost:8000 --task triage

eval-extract: ## Score Task 2 only
	cd apps/eval && python run_eval.py --endpoint http://localhost:8000 --task extract

eval-orchestrate: ## Score Task 3 only
	cd apps/eval && python run_eval.py --endpoint http://localhost:8000 --task orchestrate

mock-tools: ## Start the mock tool service for Task 3 (port 9090)
	cd apps/eval && python mock_tool_service.py

test: ## Run the sample app unit tests (mocked LLM; no network)
	cd apps/sample && uv run python -m pytest tests -q
