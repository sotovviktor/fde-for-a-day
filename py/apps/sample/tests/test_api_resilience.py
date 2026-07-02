"""Shared API-resilience probes across all task endpoints (no LLM needed).

These requests fail at the HTTP / validation / middleware layer before reaching a
task handler, so they need no dependency override.
"""

import pytest
from fastapi.testclient import TestClient
from main import app

_ENDPOINTS = ["/triage", "/extract", "/orchestrate"]


@pytest.mark.parametrize("path", _ENDPOINTS)
def test_malformed_json_returns_400(path: str) -> None:
    with TestClient(app) as client:
        resp = client.post(path, content='{"broken', headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.parametrize("path", _ENDPOINTS)
def test_empty_body_returns_422(path: str) -> None:
    with TestClient(app) as client:
        resp = client.post(path, json={})
    assert resp.status_code == 422


@pytest.mark.parametrize("path", _ENDPOINTS)
def test_wrong_content_type_returns_415(path: str) -> None:
    with TestClient(app) as client:
        resp = client.post(path, content="plain text", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
