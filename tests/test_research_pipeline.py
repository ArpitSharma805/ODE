"""Unit tests for the new research planning, signal normalization, evidence,
and clarification modules.
"""

from __future__ import annotations

import os

import pytest

from ode.clarify import maybe_clarify
from ode.evidence import validate_evidence
from ode.research import _construct_github_queries, _filter_github_queries, build_research_plan
from ode.retrieval import RetrievalPlan
from ode.signals import Signal, normalize_signals


@pytest.fixture
def sample_raw_signals():
    """A small set of raw signals from mixed sources."""
    return [
        {
            "source_id": 1,
            "entity": "LangGraph",
            "metric": "github_stars",
            "value": "1200",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 90,
        },
        {
            "source_id": 1,
            "entity": "LangGraph",
            "metric": "github_open_issues",
            "value": "Feature request: need better observability",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 80,
        },
        {
            "source_id": 2,
            "entity": "LangGraph (https://example.com/langgraph)",
            "metric": "tavily_search_result",
            "value": "Market demand is increasing for agent orchestration tools.",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 75,
        },
    ]


@pytest.fixture
def retrieval_plan():
    return RetrievalPlan(primary="LangGraph", aliases=["lang graph"], github_queries=[], tavily_queries=[])


class TestResearchPlan:
    """Research plan selects sources based on intent."""

    def test_build_research_plan_uses_intent_sources(self):
        intent = {"intent": "Opportunity Discovery", "primary_technology": "LangGraph", "topics": ["LangGraph"]}
        plan = build_research_plan("opportunities in LangGraph", intent)
        assert plan.sources
        assert "github" in plan.sources
        assert "tavily" in plan.sources

    def test_build_research_plan_skill_learning_prefers_jobs_and_github(self):
        intent = {"intent": "Skill Learning", "primary_technology": "Go", "topics": ["Go"]}
        plan = build_research_plan("Should I learn Go?", intent)
        assert "github" in plan.sources
        assert "jobs" in plan.sources
        assert "context7" in plan.sources


class TestSignalNormalization:
    """Raw MCP outputs are converted into the canonical Signal schema."""

    def test_normalize_signals_classifies_pain_and_adoption(self, sample_raw_signals, retrieval_plan):
        signals = normalize_signals(sample_raw_signals, retrieval_plan, use_llm=False)
        assert len(signals) == 3
        types = {s.signal_type for s in signals}
        assert "adoption" in types
        assert "developer_pain" in types

    def test_normalize_signals_extracts_problem_phrase(self, sample_raw_signals, retrieval_plan):
        signals = normalize_signals(sample_raw_signals, retrieval_plan, use_llm=False)
        pain = next((s for s in signals if s.signal_type == "developer_pain"), None)
        assert pain is not None
        assert pain.problem

    def test_normalize_signals_keeps_source_and_url(self, sample_raw_signals, retrieval_plan):
        signals = normalize_signals(sample_raw_signals, retrieval_plan, use_llm=False)
        for s in signals:
            assert s.source
            assert 0.0 <= s.confidence <= 1.0


class TestEvidenceValidation:
    """Evidence must come from multiple independent sources."""

    def test_validate_evidence_passes_with_diverse_sources(self):
        signals = [
            Signal(signal_type="adoption", source="github_mcp", entity="LangGraph", confidence=0.9),
            Signal(signal_type="market_demand", source="tavily_mcp", entity="LangGraph", confidence=0.8),
            Signal(signal_type="community_discussion", source="hackernews", entity="LangGraph", confidence=0.7),
        ]
        assessment = validate_evidence(signals, min_source_types=2, min_signals=3)
        assert assessment.validated
        assert assessment.confidence > 0.0

    def test_validate_evidence_fails_with_single_source(self):
        signals = [
            Signal(signal_type="adoption", source="github_mcp", entity="LangGraph", confidence=0.9),
            Signal(signal_type="developer_pain", source="github_mcp", entity="LangGraph", confidence=0.8),
            Signal(signal_type="adoption", source="github_mcp", entity="LangGraph", confidence=0.7),
        ]
        assessment = validate_evidence(signals, min_source_types=2, min_signals=3)
        assert not assessment.validated
        assert "github" in assessment.rationale.lower()


class TestClarification:
    """Broad or ambiguous queries trigger a clarifying question."""

    def test_maybe_clarify_flags_generic_role_query(self):
        intent = {"intent": "Skill Learning", "primary_technology": "Backend Engineer", "topics": []}
        result = maybe_clarify("What should a backend engineer use?", intent)
        assert result.get("needs_clarification") is True
        assert result.get("clarifying_question")
        assert result.get("clarification_options")

    def test_maybe_clarify_leaves_specific_query_unchanged(self):
        intent = {"intent": "Technology Evaluation", "primary_technology": "PostgreSQL", "topics": ["PostgreSQL"]}
        result = maybe_clarify("Should I use PostgreSQL?", intent)
        assert result.get("needs_clarification") is False


class TestGitHubQueryFiltering:
    """GitHub queries should exclude job/career terms and abstract business words to avoid repo filtering collisions."""

    def test_filter_github_queries_removes_job_term_queries(self):
        queries = ["MCP job openings", "career opportunities", "hiring", "jobs", "mcp server job"]
        filtered = _filter_github_queries(queries)
        assert len(filtered) == 0, "Queries containing job terms should be filtered out"

    def test_filter_github_queries_removes_abstract_business_terms(self):
        queries = ["MCP opportunities", "market analysis", "projects", "repositories", "market trends"]
        filtered = _filter_github_queries(queries)
        assert len(filtered) == 0, "Queries containing abstract business terms should be filtered out"

    def test_filter_github_queries_keeps_technical_queries(self):
        queries = ["modelcontextprotocol", "mcp server", "llm monitoring", "agent tracing"]
        filtered = _filter_github_queries(queries)
        assert len(filtered) == 4, "Technical queries should be kept"
        assert "modelcontextprotocol" in filtered
        assert "mcp server" in filtered

    def test_filter_github_queries_keeps_mixed_technical_queries(self):
        queries = ["MCP tools", "agent security", "mcp server tools"]
        filtered = _filter_github_queries(queries)
        assert len(filtered) == 3, "Mixed technical queries should be kept"
        assert "MCP tools" in filtered
        assert "agent security" in filtered

    def test_filter_github_queries_removes_salary_recruiting_terms(self):
        queries = ["salary", "recruiting", "recruitment", "mcp salary"]
        filtered = _filter_github_queries(queries)
        assert len(filtered) == 0, "Queries with salary/recruiting terms should be filtered out"


class TestGeneralGitHubQueryConstruction:
    """GitHub queries should use generic software component terms for any technology."""

    def test_construct_github_queries_generic_for_any_tech(self):
        # Test with different technologies to ensure it works generically
        for primary in ["Wasm", "Kubernetes", "React", "Docker", "MCP"]:
            queries = _construct_github_queries(primary)
            assert len(queries) > 1, f"Should generate multiple queries for {primary}"
            assert primary in queries[0], f"First query should be the primary technology: {primary}"
            # Check for generic component terms
            has_component = any(term in q for q in queries for term in ["server", "sdk", "plugin", "library", "framework", "tool", "api", "client"])
            assert has_component, f"Should include generic component terms for {primary}"

    def test_construct_github_queries_includes_component_terms(self):
        queries = _construct_github_queries("Wasm")
        assert "Wasm server" in queries
        assert "Wasm sdk" in queries
        assert "Wasm plugin" in queries
