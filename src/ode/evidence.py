"""Evidence chain data model for traceable, evidence-backed content.

This module implements the Evidence and EvidenceBacked traits that ensure
every piece of generated content can trace back to specific signals or derived insights.
"""

from __future__ import annotations

import dataclasses
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Evidence:
    """A single piece of evidence that supports a claim."""

    source_type: str         # "github_repo" | "hackernews_post" | "web_article" | "metric"
    source_url: str
    source_title: str
    collected_at: datetime
    raw_content: str         # the actual text or data point
    extracted_claim: str     # what this evidence supports
    confidence: float = 0.5  # 0.0 - 1.0

    def __post_init__(self) -> None:
        """Validate confidence is in valid range."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Evidence confidence must be between 0.0 and 1.0, got {self.confidence}")


@dataclass
class EvidenceBacked:
    """Mixin for any generated content that must cite evidence.

    This trait ensures that trends, opportunities, and other generated
    content are always backed by specific evidence from the signal collection.
    """

    claim: str
    supporting_evidence: list[Evidence] = field(default_factory=list)
    evidence_sufficiency: str = "moderate"  # "strong" | "moderate" | "weak" | "insufficient"

    def is_sufficiently_evidenced(self, min_count: int = 2, min_confidence: float = 0.5) -> bool:
        """Check if this content has sufficient evidence backing.

        Args:
            min_count: Minimum number of qualifying evidence items required
            min_confidence: Minimum confidence threshold for evidence to qualify

        Returns:
            True if sufficient evidence exists, False otherwise
        """
        qualifying = [e for e in self.supporting_evidence if e.confidence >= min_confidence]
        return len(qualifying) >= min_count

    def add_evidence(self, evidence: Evidence) -> None:
        """Add a piece of evidence to this content."""
        self.supporting_evidence.append(evidence)
        self._update_sufficiency()

    def add_evidence_batch(self, evidence_list: list[Evidence]) -> None:
        """Add multiple pieces of evidence to this content."""
        self.supporting_evidence.extend(evidence_list)
        self._update_sufficiency()

    def _update_sufficiency(self) -> None:
        """Update evidence_sufficiency based on current evidence."""
        if not self.supporting_evidence:
            self.evidence_sufficiency = "insufficient"
            return

        avg_confidence = sum(e.confidence for e in self.supporting_evidence) / len(self.supporting_evidence)

        if len(self.supporting_evidence) >= 3 and avg_confidence >= 0.7:
            self.evidence_sufficiency = "strong"
        elif len(self.supporting_evidence) >= 2 and avg_confidence >= 0.5:
            self.evidence_sufficiency = "moderate"
        elif len(self.supporting_evidence) >= 1:
            self.evidence_sufficiency = "weak"
        else:
            self.evidence_sufficiency = "insufficient"

    def get_evidence_summary(self) -> str:
        """Get a formatted summary of supporting evidence."""
        if not self.supporting_evidence:
            return "No supporting evidence"

        lines = [f"Evidence ({self.evidence_sufficiency}):"]
        for i, evidence in enumerate(self.supporting_evidence[:5], 1):
            lines.append(f"  {i}. [{evidence.source_type}] {evidence.source_title}")
            lines.append(f"     URL: {evidence.source_url}")
            lines.append(f"     Claim: {evidence.extracted_claim}")
            lines.append(f"     Confidence: {evidence.confidence:.2f}")

        if len(self.supporting_evidence) > 5:
            lines.append(f"  ... and {len(self.supporting_evidence) - 5} more")

        return "\n".join(lines)


def create_evidence_from_signal(
    signal: dict[str, Any],
    extracted_claim: str,
    confidence: float = 0.5,
) -> Evidence:
    """Create an Evidence object from a signal dictionary.

    Args:
        signal: Signal dictionary with entity, source_url, source_type, value, etc.
        extracted_claim: What this evidence supports
        confidence: Confidence score for this evidence

    Returns:
        Evidence object
    """
    return Evidence(
        source_type=signal.get("source_type", "unknown"),
        source_url=signal.get("source_url", ""),
        source_title=signal.get("entity", "Unknown"),
        collected_at=datetime.now(),
        raw_content=signal.get("value", ""),
        extracted_claim=extracted_claim,
        confidence=confidence,
    )


def merge_evidence_lists(evidence_lists: list[list[Evidence]]) -> list[Evidence]:
    """Merge multiple evidence lists, deduplicating by URL.

    Args:
        evidence_lists: List of evidence lists to merge

    Returns:
        Merged and deduplicated evidence list
    """
    seen_urls = set()
    merged = []

    for evidence_list in evidence_lists:
        for evidence in evidence_list:
            if evidence.source_url not in seen_urls:
                seen_urls.add(evidence.source_url)
                merged.append(evidence)

    return merged


# ============================================================================
# LEGACY MULTI-SOURCE EVIDENCE VALIDATION
# ============================================================================


@dataclass
class EvidenceAssessment:
    """Result of validating a collection of signals as evidence (legacy)."""

    validated: bool
    source_count: int
    signal_count: int
    confidence: float
    rationale: str


# High-level source categories and keyword synonyms used for generic mapping.
_SOURCE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "github": ("github",),
    "web": ("tavily", "web", "search", "crawl", "url", "browser"),
    "community": ("reddit", "stackoverflow", "discord", "forum", "community", "social", "chat"),
    "market": ("market", "g2", "capterra", "sales", "adoption", "trend"),
    "jobs": ("job", "jobs", "career", "careers", "hiring", "recruit", "linkedin"),
    "docs": ("doc", "docs", "documentation", "readme", "wiki", "knowledge"),
}


def _raw_source_type(signal: Any) -> str:
    """Return the raw source type value from a signal object or mapping."""
    if isinstance(signal, dict):
        raw = signal.get("source_type") or signal.get("source", "")
    else:
        raw = getattr(signal, "source_type", None) or getattr(signal, "source", "")
    return str(raw or "").strip().lower()


def _clean_source_label(raw: str) -> str:
    """Strip common implementation suffixes from a source type string."""
    for suffix in ("_mcp", "_csv", "_api", "_sdk", "_connector"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    return raw


def source_type(signal: Any) -> str:
    """Map a signal to a high-level source category.

    Categories are intentionally generic (e.g., ``github``, ``web``,
    ``community``, ``market``, ``jobs``, ``docs``). Unknown source types
    are returned as their cleaned base name.
    """
    raw = _clean_source_label(_raw_source_type(signal))
    if not raw:
        return "unknown"

    if raw in _SOURCE_CATEGORIES:
        return raw

    for category, synonyms in _SOURCE_CATEGORIES.items():
        if any(synonym in raw for synonym in synonyms):
            return category

    return raw


def validate_evidence(
    signals: list[Any],
    min_source_types: int = 2,
    min_signals: int = 3,
) -> EvidenceAssessment:
    """Assess whether ``signals`` constitute strong, multi-source evidence.

    Validation requires at least ``min_source_types`` distinct high-level
    source categories and ``min_signals`` total signals. The returned
    confidence score is a 0-1 blend of source diversity and signal volume.
    """
    if min_source_types < 0 or min_signals < 0:
        raise ValueError("min_source_types and min_signals must be non-negative")

    signal_count = len(signals)
    category_counts: Counter[str] = Counter(source_type(s) for s in signals)
    source_count = len(category_counts)

    validated = source_count >= min_source_types and signal_count >= min_signals

    source_factor = (
        min(source_count / max(min_source_types, 1), 1.0)
        if min_source_types > 0
        else 1.0
    )
    signal_factor = (
        min(signal_count / max(min_signals, 1), 1.0)
        if min_signals > 0
        else 1.0
    )
    confidence = round((source_factor + signal_factor) / 2.0, 3)

    rationale_parts: list[str] = []
    if signals:
        present = ", ".join(
            f"{category} ({count})"
            for category, count in sorted(category_counts.items())
        )
        rationale_parts.append(f"Present sources: {present}")
    else:
        rationale_parts.append("No signals provided")

    missing: list[str] = []
    if source_count < min_source_types:
        missing.append(f"needs {min_source_types} source types (has {source_count})")
    if signal_count < min_signals:
        missing.append(f"needs {min_signals} signals (has {signal_count})")

    if missing:
        rationale_parts.append("Missing: " + "; ".join(missing))
    else:
        rationale_parts.append("Sufficient source diversity and signal count")

    rationale = ". ".join(rationale_parts) + "."

    logger.info(
        "Evidence validated=%s sources=%d signals=%d confidence=%.3f",
        validated,
        source_count,
        signal_count,
        confidence,
    )

    return EvidenceAssessment(
        validated=validated,
        source_count=source_count,
        signal_count=signal_count,
        confidence=confidence,
        rationale=rationale,
    )
