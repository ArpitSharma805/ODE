"""PipelineContext object for carrying technology profile through pipeline stages.

This module provides the context object that accumulates state through the
analysis pipeline and provides convenient access to the resolved technology profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ode.technology_resolver import TechnologyProfile, ResolvedQuery


@dataclass
class PipelineContext:
    """Context object that carries state through the analysis pipeline."""

    # Input
    query: str
    intent: dict = field(default_factory=dict)

    # Resolution (set after Technology Resolver)
    resolved: Optional[ResolvedQuery] = None

    # Convenience accessor
    @property
    def profile(self) -> Optional[TechnologyProfile]:
        """Get the primary technology profile from the resolved query."""
        return self.resolved.primary_profile if self.resolved else None

    # Accumulated through pipeline
    raw_signals: list = field(default_factory=list)
    filtered_signals: list = field(default_factory=list)
    signal_clusters: list = field(default_factory=list)
    trends: list = field(default_factory=list)
    opportunities: list = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    stage_timings: dict = field(default_factory=dict)
    stage_errors: dict = field(default_factory=dict)

    def record_stage(self, stage_name: str, duration_seconds: float, error: str | None = None) -> None:
        """Record timing and error information for a pipeline stage."""
        self.stage_timings[stage_name] = duration_seconds
        if error:
            self.stage_errors[stage_name] = error

    def get_timing_summary(self) -> str:
        """Get a formatted summary of stage timings."""
        if not self.stage_timings:
            return "No timing data available"

        lines = ["Pipeline Stage Timings:"]
        for stage, duration in sorted(self.stage_timings.items()):
            error = self.stage_errors.get(stage)
            status = f" ({error})" if error else ""
            lines.append(f"  {stage}: {duration:.2f}s{status}")

        total = sum(self.stage_timings.values())
        lines.append(f"  Total: {total:.2f}s")

        return "\n".join(lines)

    def has_errors(self) -> bool:
        """Check if any pipeline stage had errors."""
        return bool(self.stage_errors)

    def get_error_summary(self) -> str:
        """Get a formatted summary of stage errors."""
        if not self.stage_errors:
            return "No errors"

        lines = ["Pipeline Stage Errors:"]
        for stage, error in self.stage_errors.items():
            lines.append(f"  {stage}: {error}")

        return "\n".join(lines)
