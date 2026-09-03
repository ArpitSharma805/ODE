"""Computed confidence metrics based on evidence quality.

This module replaces LLM-generated confidence with deterministic metrics
computed from actual evidence quality, signal diversity, and other factors.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class ConfidenceScore:
    """Computed confidence score based on evidence metrics."""

    overall: float  # 0.0 - 1.0
    factors: dict[str, float]
    interpretation: str


def compute_recency_score(signals: list[dict[str, Any]]) -> float:
    """Compute recency score based on signal timestamps.

    Args:
        signals: List of signal dictionaries

    Returns:
        Recency score between 0.0 and 1.0
    """
    if not signals:
        return 0.0

    now = datetime.now()
    recency_scores = []

    for signal in signals:
        timestamp_str = signal.get("raw_metadata", {}).get("timestamp") if signal.get("raw_metadata") else None
        if timestamp_str:
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                days_old = (now - timestamp).days
                # Signal within 30 days = 1.0, within 90 days = 0.5, older = 0.0
                if days_old <= 30:
                    recency_scores.append(1.0)
                elif days_old <= 90:
                    recency_scores.append(0.5)
                else:
                    recency_scores.append(0.0)
            except (ValueError, TypeError):
                recency_scores.append(0.0)
        else:
            recency_scores.append(0.0)

    if not recency_scores:
        return 0.0

    return sum(recency_scores) / len(recency_scores)


def compute_specificity_score(signals: list[dict[str, Any]], profile: Any) -> float:
    """Compute specificity score based on technology-specific vocabulary.

    Args:
        signals: List of signal dictionaries
        profile: TechnologyProfile with core_terms

    Returns:
        Specificity score between 0.0 and 1.0
    """
    if not signals or not profile:
        return 0.0

    core_terms = getattr(profile, "core_terms", [])
    if not core_terms:
        return 0.5  # Neutral if no core terms defined

    specificity_scores = []

    for signal in signals:
        signal_text = f"{signal.get('entity', '')} {signal.get('value', '')} {signal.get('metric', '')}".lower()
        term_hits = sum(1 for term in core_terms if term.lower() in signal_text)
        specificity_scores.append(term_hits / len(core_terms))

    if not specificity_scores:
        return 0.0

    return sum(specificity_scores) / len(specificity_scores)


def compute_trend_agreement(trends: list[Any]) -> float:
    """Compute trend agreement score based on trend consistency.

    Args:
        trends: List of trend objects

    Returns:
        Trend agreement score between 0.0 and 1.0
    """
    if not trends:
        return 0.0

    # If we have multiple trends, check if they're consistent
    if len(trends) < 2:
        return 0.5  # Neutral with single trend

    # Simple heuristic: check if trends have similar directions
    directions = []
    for trend in trends:
        direction = getattr(trend, "direction", "").lower()
        if "accelerating" in direction or "growth" in direction or "increasing" in direction:
            directions.append(1)
        elif "decelerating" in direction or "declining" in direction or "decreasing" in direction:
            directions.append(-1)
        else:
            directions.append(0)

    if not directions:
        return 0.5

    # Agreement is high if most trends point in the same direction
    positive_count = sum(1 for d in directions if d > 0)
    negative_count = sum(1 for d in directions if d < 0)

    max_agreement = max(positive_count, negative_count)
    return max_agreement / len(directions)


def compute_confidence_score(
    signals: list[dict[str, Any]],
    profile: Any,
    trends: list[Any],
    opportunities: list[Any],
    section_status: dict[str, bool] | None = None,
) -> ConfidenceScore:
    """Compute confidence score from actual evidence quality and section generation success.

    This replaces LLM-generated confidence with a deterministic metric
    computed from signal volume, diversity, recency, specificity, trend consistency,
    and section generation success.

    Args:
        signals: List of filtered signals
        profile: TechnologyProfile
        trends: List of trends
        opportunities: List of opportunities
        section_status: Dictionary mapping section names to generation success (True/False)

    Returns:
        ConfidenceScore with overall score and factors
    """
    # Compute individual factors
    signal_volume = min(len(signals) / 20, 1.0)  # 20+ signals = 1.0
    signal_diversity = len(set(s.get("source_type", "unknown") for s in signals)) / 5  # 5+ source types = 1.0
    signal_recency = compute_recency_score(signals)
    evidence_specificity = compute_specificity_score(signals, profile)
    trend_consistency = compute_trend_agreement(trends)
    opportunity_evidence = min(
        sum(len(getattr(o, "supporting_evidence", "") or "") for o in opportunities) / 1000, 1.0
    )

    factors = {
        "signal_volume": signal_volume,
        "signal_diversity": signal_diversity,
        "signal_recency": signal_recency,
        "evidence_specificity": evidence_specificity,
        "trend_consistency": trend_consistency,
        "opportunity_evidence": opportunity_evidence,
    }

    # Weighted average for base confidence
    weights = {
        "signal_volume": 0.12,
        "signal_diversity": 0.12,
        "signal_recency": 0.15,
        "evidence_specificity": 0.15,
        "trend_consistency": 0.12,
        "opportunity_evidence": 0.12,
        "section_success_rate": 0.22,  # New factor for section generation success
    }

    base_confidence = sum(factors[k] * weights[k] for k in factors)

    # Adjust confidence based on section generation success
    if section_status:
        total_sections = len(section_status)
        failed_sections = sum(1 for success in section_status.values() if not success)

        if total_sections > 0:
            failure_ratio = failed_sections / total_sections

            # Penalize confidence based on failure ratio
            if failure_ratio >= 0.5:  # More than half failed
                base_confidence = min(base_confidence, 0.4)  # Cap at 40%
            elif failure_ratio >= 0.3:  # 30-50% failed
                base_confidence = base_confidence * 0.7  # Reduce by 30%
            elif failure_ratio >= 0.1:  # 10-30% failed
                base_confidence = base_confidence * 0.9  # Reduce by 10%

            # Additional penalty if execution roadmap completely failed
            execution_roadmap_failed = not section_status.get("execution_roadmap", True)
            if execution_roadmap_failed:
                base_confidence = min(base_confidence, 0.5)  # Cap at 50%

            factors["section_success_rate"] = 1.0 - failure_ratio
        else:
            factors["section_success_rate"] = 1.0
    else:
        factors["section_success_rate"] = 1.0

    overall = sum(factors[k] * weights[k] for k in factors)
    overall = round(overall, 2)

    # Update interpretation to account for section failures
    if section_status and section_status.get("section_success_rate", 1.0) < 0.5:
        interpretation = f"Low confidence: More than half of report sections failed to generate (success rate: {section_status['section_success_rate']:.0%})"
    elif overall >= 0.8:
        interpretation = "High confidence: Strong, diverse, and recent evidence with consistent trends"
    elif overall >= 0.6:
        interpretation = "Moderate confidence: Good evidence with some gaps or inconsistencies"
    elif overall >= 0.4:
        interpretation = "Low confidence: Limited evidence or inconsistent signals"
    else:
        interpretation = "Very low confidence: Insufficient evidence for reliable conclusions"

    return ConfidenceScore(
        overall=overall,
        factors=factors,
        interpretation=interpretation,
    )


def interpret_confidence(overall: float, factors: dict[str, float]) -> str:
    """Generate a human-readable interpretation of the confidence score.

    Args:
        overall: Overall confidence score
        factors: Individual factor scores

    Returns:
        Human-readable interpretation
    """
    # Identify strongest and weakest factors
    sorted_factors = sorted(factors.items(), key=lambda x: x[1], reverse=True)
    strongest = sorted_factors[0] if sorted_factors else ("none", 0)
    weakest = sorted_factors[-1] if sorted_factors else ("none", 0)

    lines = [
        f"Overall confidence: {overall:.2f}",
        f"Strongest factor: {strongest[0]} ({strongest[1]:.2f})",
        f"Weakest factor: {weakest[0]} ({weakest[1]:.2f})",
    ]

    if overall >= 0.8:
        lines.append("Assessment: High confidence based on strong evidence across multiple dimensions")
    elif overall >= 0.6:
        lines.append("Assessment: Moderate confidence - consider additional research")
    elif overall >= 0.4:
        lines.append("Assessment: Low confidence - treat findings as preliminary")
    else:
        lines.append("Assessment: Very low confidence - insufficient evidence")

    return "\n".join(lines)
