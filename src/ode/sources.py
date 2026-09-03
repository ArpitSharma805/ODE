"""Source configuration and persistence."""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class Source:
    source_id: int
    name: str
    source_type: str
    trust_tier: int
    refresh_frequency: str
    endpoint: str
    owner: str
    status: str
    metadata: dict[str, Any]


class SourceType:
    FIXTURE_CSV = "fixture_csv"


def create_source(
    conn: sqlite3.Connection,
    *,
    name: str,
    source_type: str,
    trust_tier: int,
    endpoint: str,
    refresh_frequency: str = "",
    owner: str = "",
    status: str = "Active",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Insert a new source and return its id."""
    metadata_json = json.dumps(metadata or {})
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sources (
            name, source_type, trust_tier, refresh_frequency,
            endpoint, owner, status, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            source_type,
            trust_tier,
            refresh_frequency,
            endpoint,
            owner,
            status,
            metadata_json,
        ),
    )
    return int(cur.lastrowid) if cur.lastrowid is not None else 0


def list_sources(conn: sqlite3.Connection) -> list[Source]:
    """Return all configured sources."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source_id, name, source_type, trust_tier, refresh_frequency,
               endpoint, owner, status, metadata
        FROM sources
        ORDER BY source_id
        """
    )
    return [
        Source(
            source_id=row[0],
            name=row[1],
            source_type=row[2],
            trust_tier=row[3],
            refresh_frequency=row[4],
            endpoint=row[5],
            owner=row[6],
            status=row[7],
            metadata=json.loads(row[8] or "{}"),
        )
        for row in cur.fetchall()
    ]


def get_source(conn: sqlite3.Connection, source_id: int) -> Source | None:
    """Return a single source by id, or None if not found."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source_id, name, source_type, trust_tier, refresh_frequency,
               endpoint, owner, status, metadata
        FROM sources
        WHERE source_id = ?
        """,
        (source_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return Source(
        source_id=row[0],
        name=row[1],
        source_type=row[2],
        trust_tier=row[3],
        refresh_frequency=row[4],
        endpoint=row[5],
        owner=row[6],
        status=row[7],
        metadata=json.loads(row[8] or "{}"),
    )


def update_source_status(
    conn: sqlite3.Connection, source_id: int, status: str
) -> None:
    """Update the health status of a source."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE sources SET status = ? WHERE source_id = ?",
        (status, source_id),
    )
