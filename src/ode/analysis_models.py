"""Data models for the multi-stage signal analysis pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ClassifiedSignal:
    """A raw signal augmented with classification metadata."""

    id: str
    entity: str
    metric: str
    value: str
    source: str
    source_url: str
    evidence_quality: float

    signal_type: str = "adoption_indicator"
    signal_category: str = ""
    sentiment: str = "neutral"
    intensity: str = "medium"
    maturity_indicator: str = "active_development"
    temporal_signal: str = "stable"
    stakeholders: list[str] = field(default_factory=list)
    extracted_claims: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class SignalCluster:
    """A group of related classified signals."""

    cluster_id: str
    label: str
    signal_ids: list[str]
    dominant_signal_type: str
    source_diversity: int
    avg_confidence: float
    signals: list[ClassifiedSignal] = field(default_factory=list)


@dataclass
class Theme:
    """A market pattern distilled from one or more clusters."""

    theme_id: str
    theme_name: str
    what_is_happening: str
    evidence_summary: str
    strength: str
    trajectory: str
    affected_stakeholders: list[str]
    cluster_ids: list[str]
    signal_count: int
    source_count: int


@dataclass
class Problem:
    """An unmet need revealed by themes."""

    problem_id: str
    problem_statement: str
    who_has_this_problem: list[str] = field(default_factory=list)
    current_workarounds: str = ""
    severity: str = "medium"
    theme_ids: list[str] = field(default_factory=list)
    signal_count: int = 0


@dataclass
class Insight:
    """A non-obvious conclusion connecting multiple problems and themes."""

    insight_id: str
    observation: str
    connection: str
    implication: str
    timing: str
    confidence: float = 0.0
    problem_ids: list[str] = field(default_factory=list)
    theme_ids: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Complete output of the multi-stage signal analysis pipeline."""

    classified_signals: list[ClassifiedSignal]
    clusters: list[SignalCluster]
    themes: list[Theme]
    problems: list[Problem]
    insights: list[Insight]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["theme_names"] = self.theme_names
        data["problem_statements"] = self.problem_statements
        return data

    @property
    def theme_names(self) -> list[str]:
        return [t.theme_name for t in self.themes]

    @property
    def problem_statements(self) -> list[str]:
        return [p.problem_statement for p in self.problems]
