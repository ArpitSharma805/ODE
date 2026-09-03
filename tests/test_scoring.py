"""Unit tests for the deterministic Opportunity scoring formula."""

from datetime import datetime, timezone

import pytest

from ode.opportunities import score_opportunity
from ode.trends import Trend


def _make_trend(
    entity: str,
    metric: str,
    momentum: float,
    signal_volume: int,
    evidence_quality: float,
    start_offset_days: int = 5,
) -> Trend:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1 + start_offset_days, tzinfo=timezone.utc)
    return Trend(
        trend_id=1,
        entity=entity,
        metric=metric,
        start_date=start.isoformat(),
        last_updated_date=end.isoformat(),
        end_date=None,
        status="Active",
        momentum=momentum,
        signal_volume=signal_volume,
        evidence_quality=evidence_quality,
        growth_velocity=0.0,
        created_date=start.isoformat(),
        forecast=None,
    )


def test_score_opportunity_components_and_total() -> None:
    """The four-component score is deterministic and capped at 100."""
    trend = _make_trend("LangGraph", "stars", momentum=150.0, signal_volume=5, evidence_quality=80.0)
    components = score_opportunity(trend)

    assert components["momentum"] == 25.0  # 150 * 0.25 = 37.5, capped at 25.0
    assert components["evidence_strength"] == 28.0  # 80 / 100 * 35 = 28.0
    assert components["adoption_growth"] == 11.5  # 1.5 (adoption) + 10.0 (growth) = 11.5
    assert components["execution_readiness"] == 12.0  # 80 / 100 * 15 = 12.0
    assert components["total"] == 76.5  # 28.0 + 25.0 + 11.5 + 12.0 = 76.5


def test_score_opportunity_maxes_at_100() -> None:
    """Very strong trends are capped at 100."""
    trend = _make_trend(
        "artificial intelligence library",
        "stars",
        momentum=500.0,
        signal_volume=100,
        evidence_quality=100.0,
    )
    components = score_opportunity(trend)
    assert components["adoption_growth"] == 25.0  # 15.0 (adoption) + 10.0 (growth) = 25.0 (max)
    assert components["total"] == 100.0  # 35.0 + 25.0 + 25.0 + 15.0 = 100.0
