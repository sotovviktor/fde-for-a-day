"""Shared application settings for the FDEBench solution service.

Loaded once from the environment (and an optional ``.env`` file) and reused by
every task endpoint. The Azure OpenAI connection and tuning knobs are shared;
each task selects its own Azure deployment (which is also what we report in the
``X-Model-Name`` response header for cost scoring).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """Shared Azure OpenAI connection settings and global tuning knobs."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Azure OpenAI connection (shared across all tasks).
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2025-04-01-preview"

    # Per-task Azure model.
    triage_model: str = "gpt-5.4-nano"
    extract_model: str = "gpt-5.4-nano"
    orchestrate_model: str = "gpt-5.4-nano"

    # LLM client tuning (shared).
    llm_timeout_seconds: float = 10.0
    llm_max_retries: int = 3
    max_concurrency: int = 20
    max_description_chars: int = 8000

    # Global LLM rate limiting.
    # A token bucket paces request *starts*: it refills at llm_max_rpm * llm_rpm_safety_factor
    # (= 1000 * 0.8 = 800 RPM) and holds up to llm_burst_capacity tokens for short bursts.
    llm_max_rpm: int = 1000
    llm_rpm_safety_factor: float = 0.8
    llm_burst_capacity: float = 40.0
    # Bounds simultaneous in-flight LLM calls (memory/socket safety). Sized so the rate limiter
    # (800 RPM), not the semaphore, is the throughput ceiling: fast tasks (~2.7s latency) need
    # ~36 in-flight to sustain 800 RPM, so 40 gives headroom without starving the quota.
    llm_max_concurrency: int = 40
    llm_retry_backoff_cap_seconds: float = 4.0

    # Task 1 (signal triage) tuning.
    triage_max_output_tokens: int = 800
    # On an Azure content-filter/jailbreak block, triage retries once with the signal
    # description clamped to this many characters.
    triage_content_filter_retry_chars: int = 400

    # Task 2 (document extraction) tuning. Vision calls are slower than the
    # text triage call, so they get a longer per-call timeout and a larger
    # output budget (tables can be big). Keep timeout x (retries + 1) < 60s.
    extract_timeout_seconds: float = 18.0
    extract_max_output_tokens: int = 4096
    max_image_bytes: int = 8_000_000

    # Task 3 orchestration tuning.
    orchestrate_workflow_timeout_seconds: float = 30.0
    orchestrate_max_rounds: int = 4
    orchestrate_max_tool_calls: int = 30
    tool_timeout_seconds: float = 5.0
    tool_max_retries: int = 1
    max_tool_concurrency: int = 8
    orchestrate_max_output_tokens: int = 1200


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
