"""Investigation session persistence.

An investigation captures a single user query, the agent trace log, the final
result, and the agent-state snapshots needed to rebuild the live architecture
view after a page refresh.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Investigation:
    """A persisted investigation session."""

    investigation_id: int
    query: str
    status: str
    started_at: str
    completed_at: str | None
    final_state: dict[str, Any] | None
    trace_log: list[dict[str, Any]]
    agent_states: dict[str, Any]
    pipeline_artifacts: dict[str, Any]
    error: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def create_investigation(
    conn: sqlite3.Connection,
    query: str,
    status: str = "running",
) -> int:
    """Create a new investigation row and return its id."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO investigations (
            query, status, started_at, trace_log, agent_states, pipeline_artifacts
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            query,
            status,
            _now(),
            json.dumps([]),
            json.dumps({}),
            json.dumps({}),
        ),
    )
    return int(cur.lastrowid) if cur.lastrowid is not None else 0


def _to_json(value: Any) -> Any:
    """Serialize list/dict values to JSON text for storage."""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def update_investigation(
    conn: sqlite3.Connection,
    investigation_id: int,
    *,
    status: str | None = None,
    completed_at: str | None = None,
    final_state: dict[str, Any] | None = None,
    trace_log: list[dict[str, Any]] | None = None,
    agent_states: dict[str, Any] | None = None,
    pipeline_artifacts: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Update one or more columns on an existing investigation."""
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if completed_at is not None:
        fields.append("completed_at = ?")
        values.append(completed_at)
    if final_state is not None:
        fields.append("final_state = ?")
        values.append(_to_json(final_state))
    if trace_log is not None:
        fields.append("trace_log = ?")
        values.append(_to_json(trace_log))
    if agent_states is not None:
        fields.append("agent_states = ?")
        values.append(_to_json(agent_states))
    if pipeline_artifacts is not None:
        fields.append("pipeline_artifacts = ?")
        values.append(_to_json(pipeline_artifacts))
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if not fields:
        return

    values.append(investigation_id)
    conn.execute(
        f"UPDATE investigations SET {', '.join(fields)} WHERE investigation_id = ?",
        tuple(values),
    )


def _row_to_investigation(row: sqlite3.Row) -> Investigation:
    # Handle missing pipeline_artifacts column for backward compatibility
    pipeline_artifacts_json = row["pipeline_artifacts"] if "pipeline_artifacts" in row.keys() else "{}"
    return Investigation(
        investigation_id=row["investigation_id"],
        query=row["query"],
        status=row["status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        final_state=_load_json(row["final_state"], None),
        trace_log=_load_json(row["trace_log"], []),
        agent_states=_load_json(row["agent_states"], {}),
        pipeline_artifacts=_load_json(pipeline_artifacts_json, {}),
        error=row["error"],
    )


def get_investigation(
    conn: sqlite3.Connection, investigation_id: int
) -> Investigation | None:
    """Return a single investigation by id, or None if missing."""
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT investigation_id, query, status, started_at, completed_at,
                   final_state, trace_log, agent_states, pipeline_artifacts, error
            FROM investigations
            WHERE investigation_id = ?
            """,
            (investigation_id,),
        )
        row = cur.fetchone()
        return _row_to_investigation(row) if row else None
    finally:
        conn.row_factory = None


def list_investigations(
    conn: sqlite3.Connection, limit: int = 20
) -> list[Investigation]:
    """Return investigations ordered newest-first."""
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT investigation_id, query, status, started_at, completed_at,
                   final_state, trace_log, agent_states, pipeline_artifacts, error
            FROM investigations
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_row_to_investigation(row) for row in cur.fetchall()]
    finally:
        conn.row_factory = None


def get_latest_investigation(
    conn: sqlite3.Connection,
) -> Investigation | None:
    """Return the most recently started investigation, or None."""
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT investigation_id, query, status, started_at, completed_at,
                   final_state, trace_log, agent_states, pipeline_artifacts, error
            FROM investigations
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return _row_to_investigation(row) if row else None
    finally:
        conn.row_factory = None


def append_investigation_trace(
    conn: sqlite3.Connection,
    investigation_id: int,
    event: dict[str, Any],
) -> None:
    """Append an event to the trace log and refresh agent_states if present.

    The ``status`` payload on update/final events is treated as the canonical
    agent-state snapshot for the architecture view.
    """
    inv = get_investigation(conn, investigation_id)
    if inv is None:
        return

    trace = inv.trace_log + [event]
    agent_states = event.get("status") if isinstance(event.get("status"), dict) else inv.agent_states
    update_investigation(
        conn,
        investigation_id,
        trace_log=trace,
        agent_states=agent_states,
    )
