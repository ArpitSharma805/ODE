"""Tests for the LangGraph-backed orchestrator."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from ode.agents.orchestrator import pipeline_graph, run_copilot
from ode.db import init_database
from ode.seed import seed_demo_data


@pytest.fixture
def demo_db(tmp_path: Path) -> sqlite3.Connection:
    """Return an in-memory sqlite connection seeded with demo data."""
    db_path = tmp_path / "test.sqlite"
    init_database(str(db_path))
    conn = sqlite3.connect(str(db_path))
    seed_demo_data(conn)
    return conn


def test_pipeline_graph_is_compiled_state_graph() -> None:
    """The pipeline is a compiled langgraph StateGraph."""
    from langgraph.graph.state import CompiledStateGraph

    assert isinstance(pipeline_graph, CompiledStateGraph)


def test_run_copilot_yields_updates_and_final(demo_db: sqlite3.Connection) -> None:
    """run_copilot is a generator that emits per-node updates and a final event."""
    generator = run_copilot(
        "What opportunities exist in LangGraph?",
        demo_db,
        seed_only=True,
    )
    assert isinstance(generator, Generator)

    events: list[dict[str, Any]] = list(generator)
    assert events
    assert events[-1]["type"] == "final"

    update_events = [e for e in events if e.get("type") == "update"]
    assert update_events
    for event in update_events:
        assert "status" in event
        assert "agent" in event
        assert event["agent"] in {
            "Intent Analyzer",
            "Research Planner",
            "Signal Analyst",
            "Trend Analyst",
            "Opportunity Analyst",
            "Report Agent",
        }


def test_run_copilot_final_event_schema(demo_db: sqlite3.Connection) -> None:
    """The final event contains the keys expected by the UI and API."""
    events = list(
        run_copilot(
            "What opportunities exist in LangGraph?",
            demo_db,
            seed_only=True,
        )
    )
    final = events[-1]
    assert final["type"] == "final"
    for key in (
        "status",
        "answer",
        "top_opportunity",
        "top_trend",
        "opportunities",
        "signals",
        "discovered_repos",
        "intent",
        "persona_name",
    ):
        assert key in final


def test_run_copilot_status_tracks_agents(demo_db: sqlite3.Connection) -> None:
    """The status object in updates tracks every agent and ends in a terminal state."""
    events = list(
        run_copilot(
            "What opportunities exist in LangGraph?",
            demo_db,
            seed_only=True,
        )
    )
    final_status = events[-1]["status"]
    for agent in (
        "Intent Analyzer",
        "Research Planner",
        "Signal Analyst",
        "Trend Analyst",
        "Opportunity Analyst",
        "Report Agent",
    ):
        assert agent in final_status
        assert final_status[agent].get("status") in ("completed", "failed")
    assert "intent" in final_status
    assert "persona" in final_status
