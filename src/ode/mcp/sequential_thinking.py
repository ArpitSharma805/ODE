"""Sequential Thinking MCP provider for reasoning-based trend synthesis."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ode.mcp_client import call_tool


@dataclass
class MarketTrend:
    """A market-level trend synthesized from repository signals."""

    name: str
    confidence: int
    summary: str
    supporting_signals: list[str] = field(default_factory=list)
    evidence_count: int = 0
    evidence_quality: float = 0.0
    metric: str = "market_trend"


_SIGNAL_FIELDS = ("entity", "repo_name", "repo_description", "repo_language", "metric", "value")


def _signal_text(signal: Any) -> str:
    if not isinstance(signal, dict):
        return ""
    parts = [str(signal.get(f, "")) for f in _SIGNAL_FIELDS]
    return " ".join(parts).lower()


def _normalize(text: str) -> str:
    return text.replace(" ", "").replace("-", "").replace("_", "")


def _derive_market_trends(
    signals: list[dict[str, Any]],
    intent: dict[str, Any] | None = None,
) -> tuple[list[MarketTrend], dict[str, int]]:
    """Synthesize market trends directly from collected MCP signals and query intent.

    Returns the trend list plus a mapping of cluster name -> signal count.
    """
    if not signals:
        return [], {}

    primary = str((intent or {}).get("primary_technology", "")).lower().strip()
    topics = [str(t).lower().strip() for t in (intent or {}).get("topics", []) if t]
    if not topics and primary:
        topics = [primary]

    groups: dict[str, dict[str, Any]] = {}

    for signal in signals:
        if not isinstance(signal, dict):
            continue
        text = _normalize(_signal_text(signal))
        matched_topic = next(
            (t for t in topics if _normalize(t) in text),
            None,
        )
        if matched_topic:
            topic_title = matched_topic.title()
            summary = f"Evidence signals around {matched_topic}."
            bucket = groups.setdefault(
                matched_topic,
                {
                    "name": topic_title,
                    "summary": summary,
                    "signals": [],
                },
            )
            bucket["signals"].append(signal)
        elif primary and _normalize(primary) in text:
            topic_title = primary.title()
            summary = f"Evidence signals around {primary}."
            bucket = groups.setdefault(
                primary,
                {
                    "name": topic_title,
                    "summary": summary,
                    "signals": [],
                },
            )
            bucket["signals"].append(signal)

    if not groups:
        return [], {}

    trends: list[MarketTrend] = []
    cluster_counts: dict[str, int] = {}
    for topic_key, bucket in groups.items():
        members = bucket["signals"]
        entities = sorted({str(s.get("entity", "")) for s in members if s.get("entity")})
        eqs = [float(s.get("evidence_quality", 0) or 0) for s in members]
        avg_eq = sum(eqs) / len(eqs) if eqs else 0.0
        count = len(members)
        cluster_counts[bucket["name"]] = count
        # Confidence rises with evidence count and average evidence quality.
        confidence = min(100, int(count * 8 + avg_eq * 0.4))
        # Boost a trend whose name matches the user's primary technology.
        if primary and _normalize(primary) == _normalize(topic_key):
            confidence = min(100, confidence + 40)
        trends.append(
            MarketTrend(
                name=bucket["name"],
                confidence=confidence,
                summary=bucket["summary"],
                supporting_signals=entities,
                evidence_count=count,
                evidence_quality=round(avg_eq, 2),
            )
        )
    # Sort by confidence descending.
    return sorted(trends, key=lambda t: t.confidence, reverse=True), cluster_counts


def _signal_summary(signals: list[dict[str, Any]]) -> str:
    """Return a short, safe text summary of signals for reasoning prompts."""
    lines: list[str] = []
    for s in signals[:25]:
        if not isinstance(s, dict):
            continue
        entity = s.get("entity", "")
        metric = s.get("metric", "")
        value = s.get("value", "")
        lines.append(f"- {entity} {metric}={value}")
    return "\n".join(lines)


class SequentialThinkingProvider:
    """Call the Sequential Thinking MCP and synthesize market-level trends."""

    def __init__(self) -> None:
        self.available = True
        self.mcp_calls: list[dict[str, Any]] = []

    def _reasoning_step(
        self,
        step_name: str,
        thought: str,
        thought_number: int,
        total_thoughts: int,
    ) -> bool:
        """Send one thought to the Sequential Thinking MCP and record a trace entry."""
        start = time.time()
        result = call_tool(
            "sequential-thinking",
            "sequentialthinking",
            {
                "thought": thought,
                "thoughtNumber": thought_number,
                "totalThoughts": total_thoughts,
                "nextThoughtNeeded": thought_number < total_thoughts,
            },
        )
        duration = result.duration if result.duration is not None else time.time() - start
        self.mcp_calls.append(
            {
                "server": "sequential-thinking",
                "tool": step_name,
                "success": result.success,
                "duration": duration,
                "error": result.error,
            }
        )
        if not result.success:
            self.available = False
        return result.success

    def synthesize_trends(
        self,
        signals: list[dict[str, Any]],
        intent: dict[str, Any] | None = None,
    ) -> tuple[list[MarketTrend], dict[str, int], list[dict[str, Any]]]:
        """Run Sequential Thinking reasoning and return market-level trends."""
        self.mcp_calls = []
        summary = _signal_summary(signals)

        # Step 1: analyze relationships
        ok = self._reasoning_step(
            "analyze_signals",
            f"Analyze the relationships between these repository signals and group related repositories.\n\n{summary}",
            1,
            4,
        )

        # Step 2: identify patterns
        if ok:
            ok = self._reasoning_step(
                "identify_patterns",
                "Identify recurring technology patterns across the grouped signals, such as agent frameworks, protocol infrastructure, and contributor growth.",
                2,
                4,
            )

        # Step 3: generate themes
        if ok:
            ok = self._reasoning_step(
                "generate_themes",
                "Generate market-level trend names from the identified patterns. Avoid repository names; focus on market movements like 'Agent Infrastructure Expansion'.",
                3,
                4,
            )

        # Step 4: assign confidence
        if ok:
            self._reasoning_step(
                "assign_confidence",
                "Assign confidence scores to each theme based on signal volume and evidence quality, then produce the final trend list.",
                4,
                4,
            )

        trends, cluster_counts = _derive_market_trends(signals, intent)

        if not self.available:
            for call in self.mcp_calls:
                call["fallback"] = True

        return trends, cluster_counts, list(self.mcp_calls)
