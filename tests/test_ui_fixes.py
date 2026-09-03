"""Regression tests for UI evidence formatting and signal-noise filtering."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _ollama_timeout():
    """Fast-fail LLM calls during these tests."""
    old = os.environ.get("OLLAMA_TIMEOUT")
    os.environ["OLLAMA_TIMEOUT"] = "0.001"
    yield
    if old is None:
        os.environ.pop("OLLAMA_TIMEOUT", None)
    else:
        os.environ["OLLAMA_TIMEOUT"] = old


def _sample_signals() -> list[dict[str, Any]]:
    """Return two representative signals for evidence-formatting tests."""
    return [
        {
            "source_type": "tavily_mcp",
            "entity": "MCP ecosystem",
            "metric": "tavily_search_result",
            "value": "Strong ecosystem growth",
            "evidence_quality": 85,
        },
        {
            "source_type": "github_mcp",
            "entity": "mcp",
            "metric": "github_stars",
            "value": "1200",
            "evidence_quality": 70,
        },
    ]


class TestEvidenceFormatting:
    """supporting_evidence must render as separated markdown bullets."""

    def test_build_evidence_summary_uses_double_newlines(self):
        from ode.agents.opportunity_analyst import _build_evidence_summary
        from ode.trends import Trend

        trend = Trend(
            trend_id=1,
            entity="MCP",
            metric="market_demand",
            signal_volume=5,
            evidence_quality=80.0,
            momentum=0.5,
            start_date="2026-01-01T00:00:00+00:00",
            last_updated_date="2026-08-14T00:00:00+00:00",
            end_date="2026-08-14T00:00:00+00:00",
            status="active",
            growth_velocity=0.1,
            created_date="2026-08-14T00:00:00+00:00",
            forecast={},
        )
        text = _build_evidence_summary(trend, _sample_signals())
        # Each bullet line starts with "- " and is separated by a blank line.
        assert "\n\n- " in text
        parts = [p for p in text.split("\n\n") if p.startswith("-")]
        assert len(parts) >= 2

    def test_format_opportunity_report_evidence_has_double_newlines(self):
        from ode.agents.report_agent import _format_opportunity_report
        from ode.opportunities import Opportunity
        from ode.synthesis import Synthesis, Narrative

        opp = Opportunity(
            opportunity_id=1,
            trend_id=1,
            persona_id=1,
            title="MCP Tooling",
            category="Product",
            description="A clear workflow gap.",
            why_now="Ecosystem is forming.",
            who_benefits="Platform teams",
            recommended_action="Build a validation prototype.",
            supporting_evidence="- tavily result one\n- tavily result two",
            why_existing_solutions_fail="Existing tools are fragmented.",
            business_model="SaaS",
            risk_assessment="Execution risk.",
            score=75.0,
            score_components={},
            lifecycle_state="emerging",
            emerged_date="2026-08-14",
            valid_until="2027-08-14",
            last_score_date="2026-08-14",
        )
        synthesis = Synthesis(
            narrative=Narrative(evidence_summary=""),
            themes=[],
            problems=[],
            insights=[],
            opportunities=[],
        )
        ctx = {
            "intent": {"intent": "Opportunity Discovery", "primary_technology": "MCP"},
            "signals": _sample_signals(),
            "trends": [],
            "synthesis": synthesis,
            "agent_states": {},
        }
        report = _format_opportunity_report("MCP", [opp], context=ctx)
        # Evidence bullets should be separated by blank lines.
        assert "\n\n- **" in report


class TestNoiseFiltering:
    """Tavily and signal normalization must drop known non-tech noise."""

    def test_tavily_to_signals_filters_noise_phrases(self):
        from ode.mcp.tavily import _to_signals

        data = {
            "results": [
                {"title": "MCP intro", "url": "https://example.com/1", "content": "Model Context Protocol grows", "score": 0.9},
                {"title": "Audio visual systems", "url": "https://example.com/2", "content": "Chicago AV integration", "score": 0.8},
                {"title": "Microsoft certified professional", "url": "https://example.com/3", "content": "Management has the right to revise", "score": 0.8},
            ]
        }
        signals = _to_signals("mcp", data)
        entities = [s["entity"] for s in signals]
        values = [s["value"] for s in signals]
        assert any("MCP intro" in e for e in entities)
        assert not any("audio visual" in v.lower() or "chicago" in v.lower() for v in values)
        assert not any("Microsoft certified professional" in e for e in entities)

    def test_tavily_search_expands_mcp_acronym(self):
        from ode.mcp.tavily import TavilyProvider
        from ode.mcp_client import MCPResult

        provider = TavilyProvider()
        captured: dict[str, Any] = {}

        def fake_call(server: str, tool: str, arguments: dict[str, Any]) -> MCPResult:
            captured["query"] = arguments.get("query")
            return MCPResult(success=True, data=json.dumps({"results": []}), duration=0.1, error="")

        with patch("ode.mcp.tavily.call_tool", fake_call):
            provider.search("mcp ecosystem", max_results=5)

        assert "Model Context Protocol" in captured["query"]
        assert captured["query"] != "mcp ecosystem"

    def test_normalize_signals_drops_noise_results(self):
        from ode.retrieval import RetrievalPlan
        from ode.signals import normalize_signals

        plan = RetrievalPlan(primary="MCP", aliases=["Model Context Protocol"])
        raw = [
            {
                "source_id": 1,
                "source_type": "tavily_mcp",
                "entity": "MCP market",
                "metric": "tavily_search_result",
                "value": "audio visual systems in chicago",
                "evidence_quality": 80,
            },
            {
                "source_id": 1,
                "source_type": "tavily_mcp",
                "entity": "MCP tooling",
                "metric": "tavily_search_result",
                "value": "Model Context Protocol tooling grows",
                "evidence_quality": 80,
            },
        ]
        signals = normalize_signals(raw, plan, use_llm=False)
        assert len(signals) == 1
        assert "tooling" in signals[0].entity.lower()
        assert "model context protocol" in signals[0].entity.lower()
