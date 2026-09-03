"""Tests for signal quality and recommendation sanitization improvements."""

from __future__ import annotations

import pytest

from ode.agents.report_agent import _sanitize_vendor_mentions, generate_chat_response
from ode.opportunities import Opportunity
from ode.retrieval import MIN_REPO_STARS, RetrievalPlan, rank_repos
from ode.signals import normalize_signals


def _repo(
    full_name: str,
    stars: int,
    description: str = "",
    name: str | None = None,
) -> dict:
    return {
        "full_name": full_name,
        "name": name or full_name.split("/")[-1],
        "description": description,
        "stargazers_count": stars,
        "forks_count": 0,
        "watchers_count": 0,
        "open_issues_count": 0,
        "pushed_at": None,
        "updated_at": None,
        "archived": False,
        "fork": False,
    }


def test_rank_repos_requires_minimum_stars() -> None:
    """rank_repos excludes repositories below the star floor."""
    plan = RetrievalPlan(primary="Python", aliases=["python"])
    low_star = _repo("someone/learn-python", MIN_REPO_STARS - 1, "A tutorial repo")
    high_star = _repo("python/cpython", 15_000, "Python programming language")

    ranked = rank_repos([low_star, high_star], plan)

    ranked_names = {r["full_name"] for r in ranked}
    assert high_star["full_name"] in ranked_names
    assert low_star["full_name"] not in ranked_names


def test_rank_repos_excludes_tutorial_repos_for_non_learning() -> None:
    """rank_repos drops tutorial/homework repos for opportunity discovery."""
    plan = RetrievalPlan(primary="Python", aliases=["python"], intent="Opportunity Discovery")
    tutorial = _repo("foo/python-tutorial", 100, "Course material")

    ranked = rank_repos([tutorial], plan)
    assert ranked == []


def test_rank_repos_allows_tutorial_repos_for_skill_learning() -> None:
    """rank_repos keeps tutorial repos when the user is explicitly learning."""
    plan = RetrievalPlan(primary="Python", aliases=["python"], intent="Skill Learning")
    tutorial = _repo("foo/python-tutorial", 100, "Course material")

    ranked = rank_repos([tutorial], plan)
    assert len(ranked) == 1


def test_rank_repos_allows_tutorial_repos_for_career() -> None:
    """rank_repos keeps tutorial repos for career-development queries too."""
    plan = RetrievalPlan(primary="Python", aliases=["python"], intent="Career Development")
    tutorial = _repo("foo/python-tutorial", 100, "Course material")

    ranked = rank_repos([tutorial], plan)
    assert len(ranked) == 1


def test_rank_repos_keeps_non_tutorial_course_substrings() -> None:
    """Substring matches like 'coursera-dl' are not confused with tutorial repos."""
    plan = RetrievalPlan(primary="Python", aliases=["python"], intent="Opportunity Discovery")
    non_tutorial = _repo("foo/coursera-dl", 500, "Python downloader for online class archives")

    ranked = rank_repos([non_tutorial], plan)
    assert len(ranked) == 1


def test_normalize_signals_drops_trivial_commit_messages() -> None:
    """normalize_signals removes noise commit messages but keeps meaningful ones."""
    plan = RetrievalPlan(primary="LangGraph", aliases=["langgraph"])
    raw = [
        {
            "source_id": 1,
            "entity": "langchain-ai/langgraph",
            "metric": "github_commit_messages",
            "value": "Initial commit; Add files via upload; fix graph recursion bug",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 80,
        }
    ]

    signals = normalize_signals(raw, plan, use_llm=False)
    assert len(signals) == 1
    value = signals[0].value
    assert "Initial commit" not in value
    assert "Add files via upload" not in value
    assert "fix graph recursion bug" in value


def test_normalize_signals_drops_all_trivial_commit_messages() -> None:
    """A commit-messages signal made up only of noise is dropped entirely."""
    plan = RetrievalPlan(primary="LangGraph", aliases=["langgraph"])
    raw = [
        {
            "source_id": 1,
            "entity": "foo/bar",
            "metric": "github_commit_messages",
            "value": "Initial commit; Update README.md",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 80,
        }
    ]

    signals = normalize_signals(raw, plan, use_llm=False)
    assert signals == []


def test_normalize_signals_downweights_github_commits_for_learning() -> None:
    """For learning/career queries, raw GitHub commit signals are downweighted."""
    plan = RetrievalPlan(primary="Python", aliases=["python"], intent="Skill Learning")
    raw = [
        {
            "source_id": 1,
            "entity": "python/cpython",
            "metric": "github_commits",
            "value": "5",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 80,
        },
        {
            "source_id": 2,
            "entity": "python",
            "metric": "hackernews_result",
            "value": "Strong hiring demand for Python engineers",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 80,
        },
    ]

    signals = normalize_signals(raw, plan, use_llm=False)
    commits = next((s for s in signals if s.metric == "github_commits"), None)
    hackernews = next((s for s in signals if s.metric == "hackernews_result"), None)
    assert commits is not None
    assert hackernews is not None
    assert commits.confidence < hackernews.confidence


def test_normalize_signals_keeps_github_repo_high_for_opportunities() -> None:
    """For opportunity queries, GitHub repo and Tavily signals stay at full weight."""
    plan = RetrievalPlan(primary="MCP", aliases=["model context protocol"], intent="Opportunity Discovery")
    raw = [
        {
            "source_id": 1,
            "entity": "MCP",
            "metric": "github_repo_results",
            "value": "12",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 90,
        },
        {
            "source_id": 2,
            "entity": "MCP",
            "metric": "tavily_search_result",
            "value": "Market demand increasing for agent protocols",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evidence_quality": 75,
        },
    ]

    signals = normalize_signals(raw, plan, use_llm=False)
    github = next(s for s in signals if s.metric == "github_repo_results")
    tavily = next(s for s in signals if s.metric == "tavily_search_result")
    assert round(github.confidence, 4) == 0.9
    assert round(tavily.confidence, 4) == 0.75


def _skill_opportunity(title: str, evidence: str = "") -> Opportunity:
    return Opportunity(
        opportunity_id=0,
        trend_id=0,
        persona_id=0,
        title=title,
        description="",
        why_now="",
        who_benefits="",
        recommended_action="",
        supporting_evidence=evidence,
        score=0.0,
        score_components={},
        lifecycle_state="",
        emerged_date="",
        valid_until="",
        last_score_date="",
        category="Skill",
    )


def test_sanitize_vendor_mentions_removes_named_course_recommendations() -> None:
    """_sanitize_vendor_mentions strips specific paid-instructor/course references."""
    text = (
        "Follow the comprehensive Python learning path recommended by Andrei Neagoie's "
        "verified course. Then build projects using the official Python documentation."
    )
    sanitized = _sanitize_vendor_mentions(text)
    assert "Andrei Neagoie" not in sanitized
    assert "verified course" not in sanitized
    assert "official Python documentation" in sanitized


def test_generate_chat_response_sanitizes_course_mentions_for_skill_learning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skill-learning reports do not promote paid instructors or commercial courses."""
    monkeypatch.setenv("OLLAMA_TIMEOUT", "0.001")
    opp = _skill_opportunity(
        "Python",
        "Recommended: Andrei Neagoie's verified Python course is the best path.",
    )
    context = {
        "intent": {"intent": "Skill Learning"},
        "signals": [],
        "agent_states": {},
    }
    response = generate_chat_response("Should I learn Python?", [opp], context=context)
    assert "Andrei Neagoie" not in response.answer
    assert "verified course" not in response.answer
    assert "official documentation" in response.answer or "community learning" in response.answer
