"""Tests for architecture and investigation API endpoints."""

from __future__ import annotations

import os
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _ollama_timeout():
    """Fast-fail LLM calls during these tests."""
    old = os.environ.get("OLLAMA_TIMEOUT")
    os.environ["OLLAMA_TIMEOUT"] = "0.001"
    yield
    if old is None:
        os.environ.pop("OLLAMA_TIMEOUT", None)
    else:
        os.environ["OLLAMA_TIMEOUT"] = old


@pytest.fixture
def client(tmp_path: Any):
    """A FastAPI TestClient backed by a temporary database."""
    from fastapi.testclient import TestClient
    from ode.api import main
    from ode.db import init_database

    db_path = tmp_path / "api.sqlite"
    original = main.DEFAULT_DB_PATH
    main.DEFAULT_DB_PATH = str(db_path)
    init_database(str(db_path))
    with TestClient(main.app) as test_client:
        yield test_client
    main.DEFAULT_DB_PATH = original


def _create_investigation_row(client, query: str) -> int:
    """Create an investigation row directly in the temporary app database."""
    from ode.api import main
    from ode.db import get_db_connection
    from ode.investigations import create_investigation

    conn = get_db_connection(main.DEFAULT_DB_PATH)
    try:
        return create_investigation(conn, query)
    finally:
        conn.close()


class TestArchitectureAPI:
    """The /api/architecture endpoint exposes the live system view."""

    def test_architecture_returns_pipeline_sources_and_counts(self, client):
        response = client.get("/api/architecture")
        assert response.status_code == 200, response.text
        data = response.json()
        assert "pipeline" in data
        assert "sources" in data
        assert "counts" in data
        assert "latest" in data
        pipeline_names = [node["name"] for node in data["pipeline"]]
        assert pipeline_names == [
            "Query",
            "Intent Analyzer",
            "Research Planner",
            "Signal Analyst",
            "Trend Analyst",
            "Opportunity Analyst",
            "Report Agent",
        ]


class TestInvestigationsAPI:
    """Investigations can be created, listed, and retrieved."""

    def test_get_investigations_lists_created_investigations(self, client):
        _create_investigation_row(client, "A")
        _create_investigation_row(client, "B")
        response = client.get("/api/investigations")
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 2
        assert data[0]["query"] == "B"

    def test_get_investigation_by_id_returns_saved_payload(self, client):
        inv_id = _create_investigation_row(client, "MCP?")

        from ode.api import main
        from ode.db import get_db_connection
        from ode.investigations import update_investigation

        conn = get_db_connection(main.DEFAULT_DB_PATH)
        try:
            update_investigation(
                conn,
                inv_id,
                status="completed",
                final_state={"opportunities": [{"title": "MCP Tooling"}]},
            )
        finally:
            conn.close()

        response = client.get(f"/api/investigations/{inv_id}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["query"] == "MCP?"
        assert data["status"] == "completed"
        assert data["final_state"]["opportunities"][0]["title"] == "MCP Tooling"

    def test_query_endpoint_persists_investigation(self, client):
        response = client.post("/api/query", json={"query": "MCP?"})
        assert response.status_code == 200, response.text

        list_resp = client.get("/api/investigations")
        investigations = list_resp.json()
        assert len(investigations) >= 1
        assert any(inv["query"] == "MCP?" for inv in investigations)
