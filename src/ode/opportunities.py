"""Opportunity data model, scoring and listing helpers."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ode.trends import Trend


@dataclass
class Opportunity:
    opportunity_id: int
    trend_id: int
    persona_id: int
    title: str
    description: str
    why_now: str
    who_benefits: str
    recommended_action: str
    supporting_evidence: str
    score: float
    score_components: dict[str, Any]
    lifecycle_state: str
    emerged_date: str
    valid_until: str
    last_score_date: str
    category: str = ""
    why_existing_solutions_fail: str = ""
    business_model: str = ""
    risk_assessment: str = ""
    execution_roadmap: dict[str, Any] | None = None


def _days_between(start_date: str, end_date: str) -> int:
    try:
        return max(
            1,
            (datetime.fromisoformat(end_date) - datetime.fromisoformat(start_date)).days,
        )
    except ValueError:
        return 1


def score_opportunity(
    trend: Trend,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the Opportunity Score (0–100) from weighted, bounded components:
    - Evidence Strength (35%)
    - Momentum (25%)
    - Adoption & Growth (25%)
    - Execution Readiness (15%)

    Persona fit is intentionally excluded from scoring. Without explicit user
    profile data it adds noise and can make strong recommendations look arbitrary.
    """
    # Evidence Strength (35%): how trustworthy the underlying signals are
    evidence_strength_score = min(35.0, max(0.0, trend.evidence_quality / 100.0 * 35.0))

    # Momentum (25%): strength and direction of the trend
    momentum_score = min(25.0, max(0.0, abs(trend.momentum) * 0.25))

    # Adoption & Growth (25%): combines adoption (signal volume) and growth (velocity)
    # Adoption component: ecosystem presence, proxied by signal volume
    adoption_score = min(15.0, max(0.0, trend.signal_volume / 50.0 * 15.0))

    # Growth component: velocity of momentum change
    days = _days_between(trend.start_date, trend.last_updated_date)
    velocity = abs(trend.momentum) / max(days, 30)
    growth_score = min(10.0, max(0.0, velocity * 10.0))

    # Combined adoption & growth score
    adoption_growth_score = adoption_score + growth_score

    # Execution Readiness (15%): based on implementation feasibility
    # Proxy: documentation maturity and signal diversity
    execution_readiness_score = min(15.0, max(0.0, trend.evidence_quality / 100.0 * 15.0))

    # Calculate total (0-100)
    total_score = (
        evidence_strength_score
        + momentum_score
        + adoption_growth_score
        + execution_readiness_score
    )

    components = {
        "evidence_strength": round(evidence_strength_score, 2),
        "momentum": round(momentum_score, 2),
        "adoption_growth": round(adoption_growth_score, 2),
        "execution_readiness": round(execution_readiness_score, 2),
        "total": round(total_score, 2),
        "signal_volume": int(trend.signal_volume or 0),
        "growth_velocity": float(trend.growth_velocity or 0.0),
    }

    return components


def list_opportunities(
    conn: sqlite3.Connection, persona_name: str | None = None
) -> list[Opportunity]:
    """Return opportunities ordered by score descending."""
    cur = conn.cursor()
    query = """
        SELECT o.opportunity_id, o.trend_id, o.persona_id, o.title, o.category,
               o.description, o.why_now, o.who_benefits, o.recommended_action,
               o.supporting_evidence, o.why_existing_solutions_fail,
               o.business_model, o.risk_assessment, o.score, o.score_components,
               o.lifecycle_state, o.emerged_date, o.valid_until,
               o.last_score_date, o.execution_roadmap
        FROM opportunities o
        {}
        ORDER BY o.score DESC
    """
    where = ""
    params: tuple[Any, ...] = ()
    if persona_name:
        where = "JOIN personas p ON o.persona_id = p.persona_id WHERE p.name = ?"
        params = (persona_name,)
    cur.execute(query.format(where), params)

    return [
        Opportunity(
            opportunity_id=row[0],
            trend_id=row[1],
            persona_id=row[2],
            title=row[3],
            category=row[4] or "",
            description=row[5],
            why_now=row[6],
            who_benefits=row[7],
            recommended_action=row[8],
            supporting_evidence=row[9],
            why_existing_solutions_fail=row[10] or "",
            business_model=row[11] or "",
            risk_assessment=row[12] or "",
            score=row[13],
            score_components=json.loads(row[14] or "{}"),
            lifecycle_state=row[15],
            emerged_date=row[16],
            valid_until=row[17],
            last_score_date=row[18],
            execution_roadmap=json.loads(row[19] or "{}") if len(row) > 19 else None,
        )
        for row in cur.fetchall()
    ]
