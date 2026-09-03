"""Deterministic Forecast generator for Trends and Opportunities."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from ode.trends import get_active_trends, get_trend_by_id, get_trend_signals

HORIZON_DAYS = 7
MODEL_VERSION = "linear+rolling+seasonal-v1"


@dataclass
class Forecast:
    forecast_id: int
    target_type: str
    target_id: int
    created_at: str
    horizon: str
    confidence: float
    model_version: str
    predictions: list[dict[str, Any]]


def _parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _forecast_trend(
    conn: sqlite3.Connection, trend_id: int, horizon: int = HORIZON_DAYS
) -> Forecast:
    """Create a deterministic forecast for a trend's metric values."""
    signals = get_trend_signals(conn, trend_id)
    values = [float(s["value"]) for s in signals]
    dates = [_parse_timestamp(s["timestamp"]) for s in signals]

    if len(values) < 2:
        last_value = values[-1] if values else 0.0
        predictions = [
            {
                "date": (dates[-1] + timedelta(days=i + 1)).isoformat()
                if dates
                else datetime.now(timezone.utc).isoformat(),
                "value": round(last_value, 2),
            }
            for i in range(horizon)
        ]
        confidence = 0.0
    else:
        x = np.array([(d - dates[0]).days for d in dates], dtype=float)
        y = np.array(values, dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = float(np.sum((y - y_pred) ** 2))
        y_mean = float(np.mean(y))
        ss_tot = float(np.sum((y - y_mean) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        confidence = max(0.0, min(1.0, r2))

        window = min(3, len(values))
        rolling_avg = float(np.mean(values[-window:]))

        last_date = dates[-1]
        last_x = x[-1]
        predictions = []
        for i in range(1, horizon + 1):
            future_x = last_x + i
            linear = slope * future_x + intercept
            seasonal = 0.0  # stub for future seasonal component
            combined = (linear + rolling_avg + seasonal) / 2.0
            predictions.append(
                {
                    "date": (last_date + timedelta(days=i)).isoformat(),
                    "value": round(float(combined), 2),
                }
            )

    created_at = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO forecasts (target_type, target_id, created_at, horizon,
                               confidence, model_version, predictions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trend",
            trend_id,
            created_at,
            f"{horizon} days",
            confidence,
            MODEL_VERSION,
            json.dumps(predictions),
        ),
    )
    forecast_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
    conn.commit()
    return Forecast(
        forecast_id=forecast_id,
        target_type="trend",
        target_id=trend_id,
        created_at=created_at,
        horizon=f"{horizon} days",
        confidence=confidence,
        model_version=MODEL_VERSION,
        predictions=predictions,
    )


def _forecast_opportunity(
    conn: sqlite3.Connection,
    opportunity: Any,
    horizon: int = HORIZON_DAYS,
) -> Forecast:
    """Create a deterministic forecast for an Opportunity's score."""
    trend = get_trend_by_id(conn, opportunity.trend_id)
    daily_delta = trend.momentum / 100.0 if trend is not None else 0.0
    current_score = float(opportunity.score)
    evidence = float(opportunity.score_components.get("evidence_quality", 0.0))
    confidence = max(0.0, min(1.0, evidence / 100.0))

    predictions = []
    now = datetime.now(timezone.utc)
    for i in range(1, horizon + 1):
        projected = current_score + i * daily_delta
        projected = max(0.0, min(100.0, projected))
        predictions.append(
            {
                "date": (now + timedelta(days=i)).isoformat(),
                "value": round(projected, 2),
            }
        )

    created_at = now.isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO forecasts (target_type, target_id, created_at, horizon,
                               confidence, model_version, predictions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "opportunity",
            opportunity.opportunity_id,
            created_at,
            f"{horizon} days",
            confidence,
            MODEL_VERSION,
            json.dumps(predictions),
        ),
    )
    forecast_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
    conn.commit()
    return Forecast(
        forecast_id=forecast_id,
        target_type="opportunity",
        target_id=opportunity.opportunity_id,
        created_at=created_at,
        horizon=f"{horizon} days",
        confidence=confidence,
        model_version=MODEL_VERSION,
        predictions=predictions,
    )


def generate_forecasts(
    conn: sqlite3.Connection, persona_name: str = "Engineer", horizon: int = HORIZON_DAYS
) -> list[Forecast]:
    """Generate forecasts for all active trends and scored opportunities."""
    from ode.opportunities import list_opportunities

    forecasts: list[Forecast] = []
    for trend in get_active_trends(conn):
        forecasts.append(_forecast_trend(conn, trend.trend_id, horizon))
    for opp in list_opportunities(conn, persona_name=persona_name):
        forecasts.append(_forecast_opportunity(conn, opp, horizon))
    return forecasts


def get_forecast_for_target(
    conn: sqlite3.Connection, target_type: str, target_id: int
) -> Forecast | None:
    """Return the most recent forecast for a target."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT forecast_id, target_type, target_id, created_at, horizon,
               confidence, model_version, predictions
        FROM forecasts
        WHERE target_type = ? AND target_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (target_type, target_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return Forecast(
        forecast_id=row[0],
        target_type=row[1],
        target_id=row[2],
        created_at=row[3],
        horizon=row[4],
        confidence=row[5],
        model_version=row[6],
        predictions=json.loads(row[7]),
    )


def format_forecast(forecast: Forecast | None) -> str:
    """Return a short human-readable summary of a forecast."""
    if forecast is None or not forecast.predictions:
        return "No forecast available."
    first = forecast.predictions[0]["value"]
    last = forecast.predictions[-1]["value"]
    return (
        f"7-day forecast from {first:.1f} to {last:.1f} "
        f"with confidence {forecast.confidence:.2f}"
    )
