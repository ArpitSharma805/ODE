"""Tests for the evidence synthesis module."""

import pytest

from ode.synthesis import synthesize


_MCP_SIGNALS = [
    {"entity": "openai", "metric": "adoption", "value": "OpenAI adds MCP support to Agents SDK", "evidence_quality": 85.0, "source_type": "hackernews"},
    {"entity": "claude", "metric": "community_discussion", "value": "Claude Skills discussions show interest in MCP tooling", "evidence_quality": 70.0, "source_type": "hackernews"},
    {"entity": "supabase/mcp", "metric": "developer_pain", "value": "Supabase MCP can leak your entire SQL database", "evidence_quality": 90.0, "source_type": "hackernews"},
    {"entity": "mcp", "metric": "community_discussion", "value": "MCP server registry and discovery remain fragmented across GitHub", "evidence_quality": 75.0, "source_type": "hackernews"},
    {"entity": "mcp observability", "metric": "adoption", "value": "Show HN: Superlog - Observability that installs itself and fixes bugs", "evidence_quality": 80.0, "source_type": "hackernews"},
    {"entity": "mcp testing", "metric": "developer_pain", "value": "MCP testing and validation gaps slow production adoption", "evidence_quality": 65.0, "source_type": "github"},
    {"entity": "mcp security", "metric": "community_discussion", "value": "MCP security and permission management concerns grow", "evidence_quality": 78.0, "source_type": "github"},
    {"entity": "mcp governance", "metric": "market_demand", "value": "Market demand for MCP governance and access controls", "evidence_quality": 72.0, "source_type": "tavily"},
]


def _mcp_intent() -> dict:
    return {
        "intent": "Opportunity Discovery",
        "primary_technology": "MCP",
        "topics": ["mcp observability tooling", "mcp", "observability", "tooling"],
    }


def test_synthesize_extracts_specific_themes():
    result = synthesize(_MCP_SIGNALS, _mcp_intent())

    theme_names = {t.name for t in result.themes}
    assert "MCP Security & Governance" in theme_names
    assert "MCP Observability & Monitoring" in theme_names
    assert "MCP Testing & Validation" in theme_names
    assert "MCP Discovery & Marketplace" in theme_names or "MCP Operations & Deployment" in theme_names


def test_synthesize_generates_problems():
    result = synthesize(_MCP_SIGNALS, _mcp_intent())

    problem_statements = {p.statement for p in result.problems}
    assert any("security" in s.lower() for s in problem_statements)
    assert any("observability" in s.lower() or "visibility" in s.lower() for s in problem_statements)


def test_synthesize_generates_insights():
    result = synthesize(_MCP_SIGNALS, _mcp_intent())

    assert result.insights, "Expected at least one insight"
    insight_text = " ".join(i.statement.lower() for i in result.insights)
    assert "adoption" in insight_text
    assert "security" in insight_text


def test_synthesize_derives_opportunities():
    result = synthesize(_MCP_SIGNALS, _mcp_intent())

    titles = {o.title for o in result.opportunities}
    assert any("security" in t.lower() for t in titles)
    assert any("observability" in t.lower() for t in titles)


def test_synthesize_narrative_has_sections():
    result = synthesize(_MCP_SIGNALS, _mcp_intent())

    assert result.narrative.key_patterns
    assert result.narrative.market_implications
    assert result.narrative.evidence_summary
    assert len(result.narrative.evidence_summary.split(".")) >= 2


def test_synthesize_returns_empty_for_no_signals():
    result = synthesize([], _mcp_intent())
    assert result == synthesize([], None)
    assert not result.themes
