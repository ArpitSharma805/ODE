"""Tests for query clarification gating."""

import pytest

from ode.clarify import maybe_clarify


@pytest.mark.parametrize(
    ("query", "intent", "expected"),
    [
        # Concrete technologies should skip clarification.
        ("React", {"primary_technology": "React"}, False),
        ("React.js", {"primary_technology": "React.js"}, False),
        ("Go", {"primary_technology": "Go"}, False),
        ("MCP", {"primary_technology": "MCP"}, False),
        # Generic roles/domains should still ask for clarification.
        ("backend engineer", {"primary_technology": "backend engineer"}, True),
        ("developer", {"primary_technology": "developer"}, True),
        ("cloud", {"primary_technology": "cloud"}, True),
    ],
)
def test_concrete_technology_gate(query, intent, expected):
    result = maybe_clarify(query, intent)
    assert result["needs_clarification"] is expected


def test_query_backfills_missing_primary():
    result = maybe_clarify("Should I learn Go?", {"primary_technology": "", "intent": "Opportunity Discovery"})
    assert result["needs_clarification"] is False
    assert result["primary_technology"] == "Go"


def test_query_backfill_rejects_stopword():
    """"learn to code" should not backfill "to" as a concrete technology."""
    result = maybe_clarify("Should I learn to code?", {"primary_technology": "", "intent": "Opportunity Discovery"})
    assert result["needs_clarification"] is True
    assert result["primary_technology"] == ""


def test_query_backfill_rejects_generic():
    """"use a framework" should not be treated as a concrete technology."""
    result = maybe_clarify("Should I use a framework?", {"primary_technology": "", "intent": "Opportunity Discovery"})
    assert result["needs_clarification"] is True
    assert result["primary_technology"] == ""
