"""Golden-query evaluation suite for ODE quality metrics."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ode.eval import (
    ConceptMetrics,
    IntentMetrics,
    ReportMetrics,
    RetrievalMetrics,
    evaluate_concepts,
    evaluate_intent,
    evaluate_learning_report,
    evaluate_retrieval_plan,
    run_golden_eval,
)

GOLDEN_PATH = Path(__file__).parent / "golden_queries.json"


@pytest.fixture
def golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_intent_classification_quality(golden):
    """classify_intent should match expected intent and cover expected topics."""
    for case in golden["intent_cases"]:
        metrics = evaluate_intent(case["query"], case)
        assert metrics.intent_match, f"Intent mismatch for: {case['query']}"
        assert metrics.topic_recall >= 0.5, f"Low topic recall for: {case['query']}"


def test_concept_deduplication(golden):
    """ConceptRegistry should collapse aliases while keeping distinct concepts separate."""
    for case in golden["concept_cases"]:
        metrics = evaluate_concepts(case["raw"], case["expected_groups"])
        assert metrics.dedupe_reduction > 0 or len(case["expected_groups"]) == len(case["raw"])
        assert metrics.canonical_coverage >= 0.75, f"Poor canonical coverage for {case['name']}"


def test_retrieval_plan_coverage(golden):
    """build_retrieval_plan should emit a relevant primary concept and aliases."""
    for case in golden["retrieval_cases"]:
        metrics = evaluate_retrieval_plan(case["query"], case.get("intent"), case)
        assert metrics.primary_match, f"Primary mismatch for: {case['query']}"
        assert metrics.alias_recall >= 0.5, f"Low alias recall for: {case['query']}"
        assert metrics.query_count > 0, f"No retrieval queries for: {case['query']}"


def test_learning_report_quality(golden):
    """Learning reports must be human-centric and avoid score tables."""
    for case in golden["report_cases"]:
        from ode.opportunities import Opportunity

        opp = Opportunity(
            opportunity_id=1,
            trend_id=1,
            persona_id=1,
            title=case["topic"],
            description="Test opportunity for report evaluation.",
            why_now="The market is moving toward this skill.",
            who_benefits="Software engineers building scalable systems.",
            recommended_action="Start with fundamentals and build a small project.",
            supporting_evidence="GitHub growth 40%\nTavily article on demand",
            score=case["score"],
            score_components={},
            lifecycle_state="Active",
            emerged_date="2026-08-13",
            valid_until="2026-09-13",
            last_score_date="2026-08-13",
            category="Skill",
        )
        report, metrics = evaluate_learning_report(case["topic"], [opp])
        assert metrics.required_section_coverage >= 0.75, (
            f"Missing required sections for {case['topic']}: {report[:500]}"
        )
        assert metrics.forbidden_word_count == 0, (
            f"Report exposes scores for {case['topic']}: {report[:500]}"
        )
        assert metrics.has_confidence, f"Missing outcome statement for {case['topic']}"
        assert metrics.has_evidence, f"Missing evidence for {case['topic']}"
        assert metrics.answers_three_questions, f"Does not answer three questions for {case['topic']}"


def test_golden_eval_runs():
    """The aggregate golden eval should run without raising."""
    # Keep Ollama calls from slowing/hanging the test.
    os.environ.setdefault("OLLAMA_TIMEOUT", "0.001")
    results = run_golden_eval(str(GOLDEN_PATH))
    assert results["intent"]
    assert results["concepts"]
    assert results["retrieval"]
    assert results["reports"]
