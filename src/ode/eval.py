"""Evaluation harness for ODE retrieval, concept clustering, and report quality."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ode.agents.report_agent import _format_learning_report_human, _format_career_report_human
from ode.concepts import ConceptRegistry
from ode.intent import classify_intent
from ode.opportunities import Opportunity, score_opportunity
from ode.retrieval import RetrievalPlan, build_retrieval_plan
from ode.trends import Trend


@dataclass
class IntentMetrics:
    intent_match: bool
    topic_recall: float
    topic_precision: float


@dataclass
class ConceptMetrics:
    dedupe_reduction: float
    canonical_coverage: float
    alias_coverage: float


@dataclass
class RetrievalMetrics:
    primary_match: bool
    alias_recall: float
    query_count: int


@dataclass
class ReportMetrics:
    required_section_coverage: float
    forbidden_word_count: int
    has_confidence: bool
    has_evidence: bool
    answers_three_questions: bool


def _canonical_set(values: list[str]) -> set[str]:
    return {v.lower().strip() for v in values if v and v.strip()}


def evaluate_intent(query: str, expected: dict[str, Any]) -> IntentMetrics:
    """Score classify_intent output against a golden label."""
    result = classify_intent(query)
    expected_intent = expected.get("intent")
    expected_topics = _canonical_set(expected.get("topics", []))

    actual_topics = _canonical_set(result.get("topics", []))
    intent_match = (result.get("intent") or "").lower() == (expected_intent or "").lower()

    if expected_topics:
        recall = len(expected_topics & actual_topics) / len(expected_topics)
        precision = len(expected_topics & actual_topics) / max(len(actual_topics), 1)
    else:
        recall = 1.0
        precision = 1.0 if not actual_topics else 0.0

    return IntentMetrics(
        intent_match=intent_match,
        topic_recall=recall,
        topic_precision=precision,
    )


def evaluate_concepts(raw_names: list[str], expected_groups: list[list[str]]) -> ConceptMetrics:
    """Score ConceptRegistry deduplication and alias detection."""
    registry = ConceptRegistry(use_llm=False)
    deduped = registry.dedupe(raw_names)

    total_raw = len(raw_names)
    deduped_count = len(deduped)
    reduction = (total_raw - deduped_count) / max(total_raw, 1)

    # Map each expected group to its canonical and ensure all members resolve to it.
    correct_canonical = 0
    total_members = 0
    for group in expected_groups:
        concepts = [registry.resolve(name) for name in group]
        canonicals = {c.canonical for c in concepts if c}
        total_members += len(group)
        if len(canonicals) == 1:
            correct_canonical += len(group)

    canonical_coverage = correct_canonical / max(total_members, 1)

    # Alias coverage: for each expected alias group, how many members are in aliases_for.
    alias_hits = 0
    alias_total = 0
    for group in expected_groups:
        if not group:
            continue
        canonical = registry.canonical(group[0])
        expected_aliases = _canonical_set(group)
        actual_aliases = _canonical_set(registry.aliases_for(group[0]))
        alias_hits += len(expected_aliases & actual_aliases)
        alias_total += len(expected_aliases)

    alias_coverage = alias_hits / max(alias_total, 1)

    return ConceptMetrics(
        dedupe_reduction=reduction,
        canonical_coverage=canonical_coverage,
        alias_coverage=alias_coverage,
    )


def evaluate_retrieval_plan(
    query: str,
    intent: dict[str, Any] | None,
    expected: dict[str, Any],
) -> RetrievalMetrics:
    """Score a RetrievalPlan for primary/alias coverage."""
    plan = build_retrieval_plan(query, intent or {})
    expected_primary = expected.get("primary", "").lower().strip()
    expected_aliases = _canonical_set(expected.get("aliases", []))

    # Use a registry to expand the plan's aliases for comparison.
    registry = ConceptRegistry(use_llm=False)
    primary_match = expected_primary in {plan.primary.lower(), registry.canonical(plan.primary).lower()} if expected_primary else True
    plan_aliases = set()
    for alias in [plan.primary] + plan.aliases:
        plan_aliases.update(_canonical_set(registry.aliases_for(alias)))
        plan_aliases.add(registry.canonical(alias).lower())

    if expected_aliases:
        alias_recall = len(expected_aliases & plan_aliases) / len(expected_aliases)
    else:
        alias_recall = 1.0

    return RetrievalMetrics(
        primary_match=primary_match,
        alias_recall=alias_recall,
        query_count=len(plan.github_queries) + len(plan.tavily_queries),
    )


def evaluate_report(
    report_text: str,
    required_sections: list[str],
    forbidden_words: list[str],
) -> ReportMetrics:
    """Score a generated report for required sections and disallowed content."""
    text_lower = report_text.lower()

    required_count = 0
    for section in required_sections:
        if section.lower() in text_lower:
            required_count += 1

    forbidden_count = sum(1 for word in forbidden_words if word.lower() in text_lower)

    has_confidence = "confidence" in text_lower and any(
        label in text_lower
        for label in ["low", "medium", "high"]
    )
    has_evidence = "evidence" in text_lower or "sources" in text_lower

    why_care = any(phrase in text_lower for phrase in ["why should i care", "why learn it", "why it matters"])
    why_now = any(phrase in text_lower for phrase in ["why now", "current market", "market trend", "timing"])
    what_next = any(phrase in text_lower for phrase in ["what do i do next", "learning roadmap", "next steps", "recommended action"])

    return ReportMetrics(
        required_section_coverage=required_count / max(len(required_sections), 1),
        forbidden_word_count=forbidden_count,
        has_confidence=has_confidence,
        has_evidence=has_evidence,
        answers_three_questions=why_care and why_now and what_next,
    )


def evaluate_learning_report(
    topic: str,
    opportunities: list[Opportunity],
) -> tuple[str, ReportMetrics]:
    """Generate and score a learning report for a single topic."""
    report = _format_learning_report_human(topic, opportunities)
    required = [
        "Learning Outcome",
        "Learning Roadmap",
        "Example Projects",
        "Risks & Tradeoffs",
        "Final Recommendation",
    ]
    forbidden = ["score:", "total score", "weighted score", "score =", "score components", "Executive Summary", "Source Intelligence", "GitHub MCP counts", "Tavily MCP counts"]

    # Updated evaluation for new format - confidence is no longer required
    text_lower = report.lower()
    required_count = 0
    for section in required:
        if section.lower() in text_lower:
            required_count += 1

    forbidden_count = sum(1 for word in forbidden if word.lower() in text_lower)

    # Check for outcome-oriented content instead of confidence
    has_outcome = any(phrase in text_lower for phrase in ["by completing this roadmap", "you will be able to", "learning outcome"])
    has_roadmap = "learning roadmap" in text_lower or "phase" in text_lower
    has_projects = "example projects" in text_lower or "projects" in text_lower

    return report, ReportMetrics(
        required_section_coverage=required_count / max(len(required), 1),
        forbidden_word_count=forbidden_count,
        has_confidence=has_outcome,  # Reuse field for outcome check
        has_evidence=has_roadmap and has_projects,
        answers_three_questions=has_outcome and has_roadmap and has_projects,
    )


def run_golden_eval(golden_path: str) -> dict[str, Any]:
    """Run the golden query set and return aggregate metrics."""
    with open(golden_path, encoding="utf-8") as f:
        golden = json.load(f)

    results: dict[str, Any] = {
        "intent": [],
        "concepts": [],
        "retrieval": [],
        "reports": [],
    }

    for case in golden.get("intent_cases", []):
        intent_metrics = evaluate_intent(case["query"], case)
        results["intent"].append({"query": case["query"], "metrics": intent_metrics})

    for case in golden.get("concept_cases", []):
        concept_metrics = evaluate_concepts(case["raw"], case["expected_groups"])
        results["concepts"].append({"name": case["name"], "metrics": concept_metrics})

    for case in golden.get("retrieval_cases", []):
        retrieval_metrics = evaluate_retrieval_plan(case["query"], case.get("intent"), case)
        results["retrieval"].append({"query": case["query"], "metrics": retrieval_metrics})

    for case in golden.get("report_cases", []):
        opp = Opportunity(
            opportunity_id=1,
            trend_id=1,
            persona_id=1,
            title=case["topic"],
            description="Test opportunity",
            why_now="Market is growing.",
            who_benefits="Engineers",
            recommended_action="Study it.",
            supporting_evidence="GitHub stars up 40%\nTavily article mentions demand",
            score=case.get("score", 0.75),
            score_components={},
            lifecycle_state="Active",
            emerged_date="2026-08-13",
            valid_until="2026-09-13",
            last_score_date="2026-08-13",
            category="Skill",
        )
        report_text, report_metrics = evaluate_learning_report(case["topic"], [opp])
        results["reports"].append({"topic": case["topic"], "metrics": report_metrics})

    return results
