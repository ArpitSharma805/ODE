"""Tests for investigation persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ode.db import init_database
from ode.investigations import (
    Investigation,
    append_investigation_trace,
    create_investigation,
    get_investigation,
    get_latest_investigation,
    list_investigations,
    update_investigation,
)


@pytest.fixture
def inv_db(tmp_path: Path) -> sqlite3.Connection:
    """An in-memory-ish SQLite connection with the investigations table."""
    db_path = tmp_path / "investigations.sqlite"
    init_database(str(db_path))
    return sqlite3.connect(str(db_path))


class TestInvestigationsPersistence:
    """CRUD and trace-logging for investigation sessions."""

    def test_schema_creates_investigations_table(self, inv_db: sqlite3.Connection):
        cur = inv_db.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='investigations'"
        )
        assert cur.fetchone() is not None

    def test_create_and_get_investigation(self, inv_db: sqlite3.Connection):
        inv_id = create_investigation(inv_db, "What opportunities exist in MCP?")
        assert inv_id > 0

        inv = get_investigation(inv_db, inv_id)
        assert inv.query == "What opportunities exist in MCP?"
        assert inv.status == "running"
        assert inv.final_state is None
        assert inv.trace_log == []
        assert inv.agent_states == {}

    def test_update_investigation_final_state(self, inv_db: sqlite3.Connection):
        inv_id = create_investigation(inv_db, "MCP?")
        update_investigation(
            inv_db,
            inv_id,
            status="completed",
            final_state={"opportunities": [{"title": "MCP Tooling"}]},
        )
        inv = get_investigation(inv_db, inv_id)
        assert inv.status == "completed"
        assert inv.final_state["opportunities"][0]["title"] == "MCP Tooling"

    def test_append_trace_updates_trace_log_and_agent_states(
        self, inv_db: sqlite3.Connection
    ):
        inv_id = create_investigation(inv_db, "MCP?")
        event = {
            "type": "update",
            "agent": "Signal Analyst",
            "status": {
                "Signal Analyst": {"status": "running", "detail": "searching..."}
            },
        }
        append_investigation_trace(inv_db, inv_id, event)
        inv = get_investigation(inv_db, inv_id)
        assert len(inv.trace_log) == 1
        assert inv.agent_states["Signal Analyst"]["status"] == "running"

    def test_list_and_latest_investigations(self, inv_db: sqlite3.Connection):
        id1 = create_investigation(inv_db, "Query A")
        id2 = create_investigation(inv_db, "Query B")
        update_investigation(inv_db, id1, status="completed")

        rows = list_investigations(inv_db)
        assert [r.investigation_id for r in rows] == [id2, id1]

        latest = get_latest_investigation(inv_db)
        assert latest is not None
        assert latest.investigation_id == id2
