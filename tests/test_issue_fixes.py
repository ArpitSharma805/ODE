"""Regression tests for evidence scoping and forced MCP title prefix issues."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ode.db import init_database
from ode.synthesis import synthesize
from ode.trends import Trend


@pytest.fixture(autouse=True)
def _ollama_timeout():
    """Force LLM fast-fail for this test module so unit tests stay offline."""
    old = os.environ.get("OLLAMA_TIMEOUT")
    os.environ["OLLAMA_TIMEOUT"] = "0.001"
    yield
    if old is None:
        os.environ.pop("OLLAMA_TIMEOUT", None)
    else:
        os.environ["OLLAMA_TIMEOUT"] = old


@pytest.fixture
def temp_db():
    """Create an isolated, initialised SQLite database for a test."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    init_database(path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()
    if os.path.exists(path):
        os.unlink(path)


def _create_source(conn: sqlite3.Connection, name: str, source_type: str) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sources (name, source_type, trust_tier, refresh_frequency, endpoint, owner, status, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, source_type, 80, "manual", "", "", "Active", "{}"),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _create_ingestion_run(conn: sqlite3.Connection, source_id: int, signals_count: int = 1) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ingestion_runs (source_id, start_time, end_time, status, signals_created)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_id, now, now, "completed", signals_count),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_signal(
    conn: sqlite3.Connection,
    source_id: int,
    run_id: int,
    entity: str,
    metric: str,
    value: str,
    evidence_quality: float = 80.0,
    source_type: str = "github_mcp",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO signals
            (source_id, ingestion_run_id, source_type, entity, metric, value,
             unit, timestamp, ingest_date, raw_payload, normalized_payload,
             evidence_quality, confidence, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            run_id,
            source_type,
            entity,
            metric,
            value,
            "",
            now,
            now[:10],
            "{}",
            "{}",
            evidence_quality,
            evidence_quality / 100.0,
            "",
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _sample_trend(signal_ids: list[int], entity: str = "Kubernetes") -> Trend:
    now = datetime.now(timezone.utc).isoformat()
    return Trend(
        trend_id=1,
        entity=entity,
        metric="market_trend",
        start_date=now,
        last_updated_date=now,
        end_date=None,
        status="Active",
        momentum=80.0,
        signal_volume=len(signal_ids),
        evidence_quality=80.0,
        growth_velocity=0.0,
        created_date=now,
        forecast={
            "summary": "Test summary",
            "supporting_signals": [],
            "friction": "",
            "gap": "",
        },
        contributing_signal_ids=signal_ids,
    )


class TestEvidenceScoping:
    """Evidence must not bleed across ingestion runs or query contexts."""

    def test_generate_opportunities_scopes_signals_to_current_run(self, temp_db):
        """Signals from a previous ingestion run on the same source must not be used."""
        from ode.agents.opportunity_analyst import generate_opportunities

        source_id = _create_source(temp_db, "GitHub MCP", "github_mcp")
        old_run = _create_ingestion_run(temp_db, source_id)
        new_run = _create_ingestion_run(temp_db, source_id)

        # Previous run: a Kubernetes security signal that should not appear now.
        old_signal = _insert_signal(
            temp_db,
            source_id,
            old_run,
            "kubernetes",
            "github_open_issues",
            "kubernetes security concerns",
        )
        # Current run: a Kubernetes observability signal.
        new_signal = _insert_signal(
            temp_db,
            source_id,
            new_run,
            "kubernetes",
            "web_page_mentions",
            "kubernetes observability tooling",
        )

        trend = _sample_trend([new_signal], entity="kubernetes")
        intent = {
            "intent": "Opportunity Discovery",
            "primary_technology": "kubernetes",
            "topics": ["kubernetes observability tooling", "kubernetes"],
        }

        opportunities, _ = generate_opportunities(
            temp_db,
            persona_name="Engineer",
            trends=[trend],
            intent=intent,
            query="kubernetes observability",
        )

        # Rejected opportunities are now filtered out entirely (return None instead of creating placeholders)
        assert opportunities, "Should have at least one valid opportunity"
        combined_text = " ".join(
            " ".join([o.title, o.description, o.supporting_evidence])
            for o in opportunities
        ).lower()
        assert "security" not in combined_text, "Old security signal leaked into current evidence"
        assert "observability" in combined_text, "Current observability signal should be present"


class TestMcpTitlePrefix:
    """Opportunity titles must not force an 'MCP' prefix for non-MCP queries."""

    def test_synthesize_does_not_force_mcp_for_non_mcp_query(self):
        """Synthesis templates should use the actual primary, not a hardcoded MCP prefix."""
        signals = [
            {
                "entity": "ai observability",
                "metric": "github_repo_results",
                "value": "OpenTelemetry repos show strong activity",
                "evidence_quality": 80.0,
                "source_type": "github_mcp",
            },
            {
                "entity": "ai observability",
                "metric": "github_open_issues",
                "value": "Lack of tracing across AI tool calls",
                "evidence_quality": 75.0,
                "source_type": "github_mcp",
            },
        ]
        intent = {
            "intent": "Opportunity Discovery",
            "primary_technology": "AI Observability",
            "topics": ["ai observability", "observability"],
        }

        result = synthesize(signals, intent)

        assert result.opportunities
        all_text = " ".join(
            " ".join([o.title, o.problem, o.why_existing_solutions_fail, o.potential_solution])
            for o in result.opportunities
        )
        assert "MCP" not in all_text, "Non-MCP query produced MCP-prefixed output"
        assert "AI Observability" in all_text or "Observability" in all_text

    def test_business_theses_strip_forced_mcp_prefix(self):
        """LLM-generated opportunity titles with a forced 'MCP' prefix are normalised."""
        from ode.agents.opportunity_analyst import generate_opportunities
        from ode.llm import generate_business_theses

        fake_response = json.dumps(
            {
                "opportunities": [
                    {
                        "target_trend": "AI Observability",
                        "title": "MCP ModelGuardian",
                        "core_problem": "AI tool calls are invisible.",
                        "why_existing_solutions_fail": "Existing APM tools miss agent-tool boundaries.",
                        "target_users": "AI platform teams",
                        "why_now": "Agent deployments are scaling.",
                        "supporting_evidence": "- OpenTelemetry: 1000 stars",
                        "product_concept": "Observability SDK for AI agents",
                        "business_model": "SaaS",
                        "risk_assessment": "Incumbents may add AI tracing.",
                        "confidence_score": 85,
                        "category": "Product",
                    }
                ]
            }
        )

        with patch("ode.llm._ollama_generate", return_value=fake_response):
            theses = generate_business_theses(
                signals=[
                    {"entity": "ai observability", "metric": "github_repo_results", "value": "OpenTelemetry", "evidence_quality": 80.0, "source_type": "github_mcp"}
                ],
                trends=[{"name": "AI Observability", "summary": "", "friction": "", "gap": "", "confidence": 80, "evidence_count": 1, "evidence_quality": 80.0}],
                persona=None,
                intent={
                    "intent": "Opportunity Discovery",
                    "primary_technology": "AI Observability",
                    "topics": ["ai observability"],
                },
            )

        assert theses
        assert not theses[0]["title"].lower().startswith("mcp "), theses[0]["title"]

    def test_business_theses_strip_mcp_colon_prefix(self):
        """A forced 'MCP:' prefix without whitespace is still normalised."""
        from ode.llm import generate_business_theses

        fake_response = json.dumps(
            {
                "opportunities": [
                    {
                        "target_trend": "AI Observability",
                        "title": "MCP:Guardian",
                        "core_problem": "AI tool calls are invisible.",
                        "why_existing_solutions_fail": "Existing APM tools miss agent-tool boundaries.",
                        "target_users": "AI platform teams",
                        "why_now": "Agent deployments are scaling.",
                        "supporting_evidence": "- OpenTelemetry: 1000 stars",
                        "product_concept": "Observability SDK for AI agents",
                        "business_model": "SaaS",
                        "risk_assessment": "Incumbents may add AI tracing.",
                        "confidence_score": 85,
                        "category": "Product",
                    }
                ]
            }
        )

        with patch("ode.llm._ollama_generate", return_value=fake_response):
            theses = generate_business_theses(
                signals=[
                    {"entity": "ai observability", "metric": "github_repo_results", "value": "OpenTelemetry", "evidence_quality": 80.0, "source_type": "github_mcp"}
                ],
                trends=[{"name": "AI Observability", "summary": "", "friction": "", "gap": "", "confidence": 80, "evidence_count": 1, "evidence_quality": 80.0}],
                persona=None,
                intent={
                    "intent": "Opportunity Discovery",
                    "primary_technology": "AI Observability",
                    "topics": ["ai observability"],
                },
            )

        assert theses
        assert theses[0]["title"] == "Guardian", theses[0]["title"]

    def test_synthesize_does_not_leave_primary_placeholder(self):
        """Synthesis templates must format the {primary} placeholder, not emit it literally."""
        signals = [
            {
                "entity": "ai observability",
                "metric": "github_repo_results",
                "value": "OpenTelemetry repos show strong activity",
                "evidence_quality": 80.0,
                "source_type": "github_mcp",
            },
            {
                "entity": "ai observability",
                "metric": "github_open_issues",
                "value": "Lack of tracing across AI tool calls",
                "evidence_quality": 75.0,
                "source_type": "github_mcp",
            },
        ]
        intent = {
            "intent": "Opportunity Discovery",
            "primary_technology": "AI Observability",
            "topics": ["ai observability"],
        }

        result = synthesize(signals, intent)

        assert result.opportunities
        all_text = " ".join(
            " ".join([o.title, o.problem, o.why_existing_solutions_fail, o.potential_solution])
            for o in result.opportunities
        )
        assert "{primary}" not in all_text, "Synthesis output contains an unformatted {primary} placeholder"

    def test_generate_opportunities_keeps_mcp_when_query_is_about_mcp(self, temp_db):
        """Intentional MCP prefixes are preserved when the query is about MCP."""
        from ode.agents.opportunity_analyst import generate_opportunities

        source_id = _create_source(temp_db, "GitHub MCP", "github_mcp")
        run_id = _create_ingestion_run(temp_db, source_id)
        signal_id = _insert_signal(
            temp_db,
            source_id,
            run_id,
            "mcp",
            "github_open_issues",
            "MCP security and permission management concerns grow",
        )

        trend = _sample_trend([signal_id], entity="mcp")
        intent = {
            "intent": "Opportunity Discovery",
            "primary_technology": "MCP",
            "topics": ["mcp security", "model context protocol"],
        }

        opportunities, _ = generate_opportunities(
            temp_db,
            persona_name="Engineer",
            trends=[trend],
            intent=intent,
            query="MCP security opportunities",
        )

        assert opportunities
        titles = " ".join(o.title for o in opportunities)
        assert "MCP" in titles or "Model Context Protocol" in titles or "Security" in titles
