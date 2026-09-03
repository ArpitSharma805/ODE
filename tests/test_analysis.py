"""Tests for the multi-stage signal analysis pipeline."""

from ode.agents.signal_analyst import analyze_signals


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


def _run_analyze():
    gen = analyze_signals(_MCP_SIGNALS, "MCP opportunities", {"primary_technology": "MCP"}, {})
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def test_analyze_produces_classified_clusters_themes():
    result = _run_analyze()
    assert result.classified_signals
    assert result.clusters
    assert result.themes
    assert result.problems
    assert result.insights


def test_classified_signals_have_new_fields():
    result = _run_analyze()
    classified = result.classified_signals
    assert classified
    for sig in classified:
        assert sig.id
        assert sig.signal_type
        assert sig.signal_category is not None
        assert sig.sentiment
        assert sig.intensity
        assert sig.maturity_indicator
        assert sig.temporal_signal
        assert sig.extracted_claims


def test_themes_have_required_fields():
    result = _run_analyze()
    for theme in result.themes:
        assert theme.theme_name
        assert theme.what_is_happening
        assert theme.evidence_summary
        assert theme.strength
        assert theme.trajectory
        assert theme.signal_count > 0


def test_analysis_result_serializes():
    result = _run_analyze()
    data = result.to_dict()
    assert "themes" in data
    assert "problems" in data
    assert "insights" in data
    assert data["theme_names"] == [t["theme_name"] for t in data["themes"]]
