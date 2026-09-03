"""Graduated validation for content specificity and generic language detection.

This module provides graduated scoring for content validation to reduce
false rejections while maintaining quality standards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Generic phrases that should trigger reduced confidence
GENERIC_PHRASES_BLOCKLIST = [
    "growing ecosystem activity",
    "strong developer interest",
    "increasing adoption",
    "validate assumptions",
    "build product",
    "scale solution",
    "significant opportunity",
    "strong momentum",
    "early mover advantage",
    "market is ready",
    "perfect timing",
    "growing demand",
    "poised for growth",
    "rapidly evolving",
    "game changer",
    "paradigm shift",
    "untapped potential",
    "first-mover advantage",
    "low-hanging fruit",
    "competitive advantage",
    "strategic positioning",
    "value proposition",
    "key takeaway",
    "bottom line",
    "net-net",
]


@dataclass
class ValidationResult:
    """Result of content validation with graduated confidence levels."""

    valid: bool
    confidence: str  # "high" | "moderate" | "low"
    score: float  # 0.0 - 1.0
    reason: str = ""
    warning: str = ""


def validate_specificity(
    text: str,
    profile: Any,
    min_length: int = 50,
) -> ValidationResult:
    """Validate content specificity using graduated scoring.

    Instead of binary pass/fail, uses graduated confidence levels:
    - High confidence (>= 0.3): Content is specific and technology-relevant
    - Moderate confidence (>= 0.15): Content has some specificity but could be improved
    - Low confidence (>= 0.05): Borderline content, accepted but flagged
    - Invalid (< 0.05): Content is too generic or too short

    Args:
        text: The content to validate
        profile: TechnologyProfile with core_terms
        min_length: Minimum character length for valid content

    Returns:
        ValidationResult with confidence level and score
    """
    if text is None or len(text.strip()) < min_length:
        return ValidationResult(
            valid=False,
            confidence="none",
            score=0.0,
            reason=f"Too short (minimum {min_length} characters required)"
        )

    # Check for blocked generic phrases
    generic_hits = [p for p in GENERIC_PHRASES_BLOCKLIST if p.lower() in text.lower()]

    # Check for technology-specific vocabulary
    core_terms = getattr(profile, "core_terms", []) if profile else []
    core_term_hits = [
        t for t in core_terms
        if t.lower() in text.lower()
    ]

    # Calculate specificity score
    specificity_score = len(core_term_hits) / max(len(core_terms), 1) if core_terms else 0.5
    generic_penalty = len(generic_hits) * 0.15

    final_score = max(specificity_score - generic_penalty, 0.0)

    # Graduated thresholds
    if final_score >= 0.3:
        return ValidationResult(
            valid=True,
            confidence="high",
            score=final_score,
            reason=f"High specificity with {len(core_term_hits)} core terms"
        )
    elif final_score >= 0.15:
        return ValidationResult(
            valid=True,
            confidence="moderate",
            score=final_score,
            reason=f"Moderate specificity with {len(core_term_hits)} core terms"
        )
    elif final_score >= 0.05:
        # Borderline — accept but flag
        return ValidationResult(
            valid=True,
            confidence="low",
            score=final_score,
            warning="Low technology specificity - consider adding more specific terminology",
            reason=f"Low specificity (score={final_score:.2f}). Generic phrases found: {generic_hits}. Core terms found: {core_term_hits}"
        )
    else:
        return ValidationResult(
            valid=False,
            confidence="none",
            score=final_score,
            reason=f"Insufficient specificity (score={final_score:.2f}). Generic phrases found: {generic_hits}. Core terms found: {core_term_hits}"
        )


def validate_technology_specificity(
    response: str,
    technology_name: str,
    profile: Any = None,
) -> tuple[bool, str]:
    """Validate that content is technology-specific (legacy compatibility wrapper).

    This provides backward compatibility with existing validation logic
    while using the new graduated scoring internally.

    Args:
        response: The generated content
        technology_name: Name of the technology
        profile: TechnologyProfile (optional)

    Returns:
        Tuple of (is_valid, reason)
    """
    validation = validate_specificity(response, profile)

    # Consider content valid if it has at least low confidence
    if validation.valid and validation.confidence in ("high", "moderate", "low"):
        return True, validation.reason

    return False, validation.reason


def get_confidence_indicator(confidence: str) -> str:
    """Get a visual indicator for confidence level.

    Args:
        confidence: Confidence level ("high", "moderate", "low", "none")

    Returns:
        Emoji indicator string
    """
    indicators = {
        "high": "🟢",
        "moderate": "🟡",
        "low": "🟠",
        "none": "🔴"
    }
    return indicators.get(confidence, "⚪")


def format_validation_result(validation: ValidationResult) -> str:
    """Format validation result for display.

    Args:
        validation: ValidationResult to format

    Returns:
        Formatted string with confidence indicator and details
    """
    indicator = get_confidence_indicator(validation.confidence)
    lines = [
        f"{indicator} Confidence: {validation.confidence.upper()} (score: {validation.score:.2f})",
        f"Reason: {validation.reason}"
    ]

    if validation.warning:
        lines.append(f"⚠️  Warning: {validation.warning}")

    return "\n".join(lines)
