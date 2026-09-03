"""Trend Detector: cluster Signals into Trends by entity/metric/time window."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

MIN_SIGNALS_FOR_ACTIVE = 3


@dataclass
class Trend:
    trend_id: int
    entity: str
    metric: str
    start_date: str
    last_updated_date: str
    end_date: str | None
    status: str
    momentum: float
    signal_volume: int
    evidence_quality: float
    growth_velocity: float
    created_date: str
    forecast: dict[str, Any] | None = None
    contributing_signal_ids: list[int] = field(default_factory=list)


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _compute_momentum(values: list[float]) -> float:
    if len(values) < 2 or values[0] == 0:
        return 0.0
    return (values[-1] - values[0]) / values[0] * 100.0


def _cluster_signals(conn: sqlite3.Connection) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group signals by (entity, metric), sorted by timestamp ascending."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT signal_id, entity, metric, value, timestamp,
               ingest_date, evidence_quality, confidence
        FROM signals
        ORDER BY timestamp, signal_id
        """
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        key = (row[1], row[2])
        groups.setdefault(key, []).append(
            {
                "signal_id": row[0],
                "entity": row[1],
                "metric": row[2],
                "value": row[3],
                "timestamp": row[4],
                "ingest_date": row[5],
                "evidence_quality": row[6],
                "confidence": row[7],
            }
        )
    return groups


def run_trend_detection(
    conn: sqlite3.Connection, min_signals: int = MIN_SIGNALS_FOR_ACTIVE
) -> list[Trend]:
    """Clear existing trends and recompute from all Signals."""
    cur = conn.cursor()
    cur.execute("DELETE FROM trend_signals")
    cur.execute("DELETE FROM trends")
    now = datetime.now(timezone.utc).isoformat()

    groups = _cluster_signals(conn)
    active_trends: list[Trend] = []

    for (entity, metric), signals in groups.items():
        values = [_to_float(s["value"]) for s in signals]
        timestamps = [s["timestamp"] for s in signals]
        signal_ids = [s["signal_id"] for s in signals]
        evidence_quality = sum(float(s["evidence_quality"]) for s in signals) / len(signals)
        momentum = _compute_momentum(values)

        status = "Active" if len(signals) >= min_signals else "Candidate"
        start_date = timestamps[0]
        last_updated_date = timestamps[-1]
        end_date = None

        cur.execute(
            """
            INSERT INTO trends (
                entity, metric, start_date, last_updated_date, end_date,
                status, momentum, signal_volume, evidence_quality,
                growth_velocity, created_date, forecast
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity,
                metric,
                start_date,
                last_updated_date,
                end_date,
                status,
                momentum,
                len(signals),
                evidence_quality,
                1.0,
                now,
                json.dumps(None),
            ),
        )
        trend_id = int(cur.lastrowid) if cur.lastrowid is not None else 0

        for signal_id in signal_ids:
            cur.execute(
                "INSERT INTO trend_signals (trend_id, signal_id) VALUES (?, ?)",
                (trend_id, signal_id),
            )

        trend = Trend(
            trend_id=trend_id,
            entity=entity,
            metric=metric,
            start_date=start_date,
            last_updated_date=last_updated_date,
            end_date=end_date,
            status=status,
            momentum=momentum,
            signal_volume=len(signals),
            evidence_quality=evidence_quality,
            growth_velocity=1.0,
            created_date=now,
            forecast=None,
            contributing_signal_ids=signal_ids,
        )
        if status == "Active":
            active_trends.append(trend)

    conn.commit()
    return active_trends


def _load_contributing_signal_ids(
    conn: sqlite3.Connection, trend_ids: list[int]
) -> dict[int, list[int]]:
    """Return a mapping of trend_id to contributing signal ids."""
    if not trend_ids:
        return {}
    cur = conn.cursor()
    placeholders = ",".join("?" * len(trend_ids))
    cur.execute(
        f"""
        SELECT trend_id, signal_id
        FROM trend_signals
        WHERE trend_id IN ({placeholders})
        """,
        tuple(trend_ids),
    )
    mapping: dict[int, list[int]] = {tid: [] for tid in trend_ids}
    for tid, sid in cur.fetchall():
        mapping[tid].append(sid)
    return mapping


def get_active_trends(conn: sqlite3.Connection) -> list[Trend]:
    """Return all active trends with their contributing signal ids."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT trend_id, entity, metric, start_date, last_updated_date,
               end_date, status, momentum, signal_volume, evidence_quality,
               growth_velocity, created_date, forecast
        FROM trends
        WHERE status = 'Active'
        ORDER BY trend_id
        """
    )
    rows = cur.fetchall()
    signal_ids_map = _load_contributing_signal_ids(conn, [row[0] for row in rows])
    return [
        Trend(
            trend_id=row[0],
            entity=row[1],
            metric=row[2],
            start_date=row[3],
            last_updated_date=row[4],
            end_date=row[5],
            status=row[6],
            momentum=row[7],
            signal_volume=row[8],
            evidence_quality=row[9],
            growth_velocity=row[10],
            created_date=row[11],
            forecast=json.loads(row[12]) if row[12] else None,
            contributing_signal_ids=signal_ids_map.get(row[0], []),
        )
        for row in rows
    ]


def get_trend_by_id(conn: sqlite3.Connection, trend_id: int) -> Trend | None:
    """Return a single trend by id, or None if not found."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT trend_id, entity, metric, start_date, last_updated_date,
               end_date, status, momentum, signal_volume, evidence_quality,
               growth_velocity, created_date, forecast
        FROM trends
        WHERE trend_id = ?
        """,
        (trend_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return Trend(
        trend_id=row[0],
        entity=row[1],
        metric=row[2],
        start_date=row[3],
        last_updated_date=row[4],
        end_date=row[5],
        status=row[6],
        momentum=row[7],
        signal_volume=row[8],
        evidence_quality=row[9],
        growth_velocity=row[10],
        created_date=row[11],
        forecast=json.loads(row[12]) if row[12] else None,
    )


def get_trend_signals(conn: sqlite3.Connection, trend_id: int) -> list[dict[str, Any]]:
    """Return the contributing signals for a trend."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.signal_id, s.entity, s.metric, s.value, s.timestamp,
               s.evidence_quality
        FROM signals s
        JOIN trend_signals ts ON s.signal_id = ts.signal_id
        WHERE ts.trend_id = ?
        ORDER BY s.timestamp
        """,
        (trend_id,),
    )
    columns = ["signal_id", "entity", "metric", "value", "timestamp", "evidence_quality"]
    return [dict(zip(columns, row)) for row in cur.fetchall()]
