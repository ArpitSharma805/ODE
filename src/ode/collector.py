"""Collector agent: fetch data from a source and persist Signals."""

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ode.sources import Source, SourceType, get_source, update_source_status


def run_ingestion(conn: sqlite3.Connection, source_id: int) -> int:
    """Run a full ingestion for the given source and return the number of signals created."""
    source = get_source(conn, source_id)
    if source is None:
        raise ValueError(f"Source {source_id} not found")

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ingestion_runs (source_id, start_time, status, signals_created)
        VALUES (?, ?, ?, ?)
        """,
        (source.source_id, now, "running", 0),
    )
    run_id = int(cur.lastrowid) if cur.lastrowid is not None else 0

    signals_created = 0
    try:
        if source.source_type == SourceType.FIXTURE_CSV:
            signals_created = _ingest_fixture_csv(conn, source, run_id, now)
        else:
            raise ValueError(f"Unsupported source type: {source.source_type}")

        end_time = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """
            UPDATE ingestion_runs
            SET end_time = ?, status = ?, signals_created = ?
            WHERE run_id = ?
            """,
            (end_time, "completed", signals_created, run_id),
        )
        update_source_status(conn, source.source_id, "Active")
        conn.commit()
    except Exception as exc:
        end_time = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """
            UPDATE ingestion_runs
            SET end_time = ?, status = ?, errors = ?
            WHERE run_id = ?
            """,
            (end_time, "failed", str(exc), run_id),
        )
        update_source_status(conn, source.source_id, "Failing")
        conn.commit()
        raise

    return signals_created


def _ingest_fixture_csv(
    conn: sqlite3.Connection, source: Source, run_id: int, ingest_date: str
) -> int:
    """Read a CSV fixture and store each row as a Signal."""
    path = Path(source.endpoint)
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {source.endpoint}")

    evidence_quality = float(source.trust_tier)
    confidence = 1.0
    count = 0
    cur = conn.cursor()

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entity = row.get("entity", "").strip()
            metric = row.get("metric", "").strip()
            value = row.get("value", "").strip()
            timestamp = row.get("timestamp", "").strip()
            if not entity or not metric or not value:
                continue
            cur.execute(
                """
                INSERT INTO signals (
                    source_id, ingestion_run_id, source_type, entity, metric,
                    value, timestamp, ingest_date, evidence_quality, confidence, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    run_id,
                    source.source_type,
                    entity,
                    metric,
                    value,
                    timestamp,
                    ingest_date,
                    evidence_quality,
                    confidence,
                    json.dumps([]),
                ),
            )
            count += 1

    return count
