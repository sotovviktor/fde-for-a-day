"""Shared test configuration.

Dummy Azure settings are exported *before* the app is imported so ``get_settings()``
never needs real credentials or network access. A static ``AZURE_OPENAI_API_KEY`` is
set on purpose: ``AzureLLMClient`` then uses key auth and never constructs
``DefaultAzureCredential`` (which could otherwise reach the instance metadata endpoint).
"""
# ruff: noqa: E402 -- env must be configured before importing the app

import os

os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-key")
os.environ.setdefault("AZURE_OPENAI_API_VERSION", "2024-10-21")

from collections.abc import Iterator

import pytest
from main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    """Reset FastAPI dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()
