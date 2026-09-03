"""Tests for the generic Concept Registry."""

import os
from unittest.mock import patch

from ode.concepts import ConceptRegistry
from ode.retrieval import build_retrieval_plan


def test_registry_dedupes_and_canonicalizes_aliases():
    registry = ConceptRegistry(use_llm=False)
    concepts = registry.register(["golang", "Go Programming", "go"])

    assert "go" == registry.canonical("golang")
    assert "go" == registry.canonical("Go Programming")
    assert "go" == registry.canonical("go")
    assert "go" in {c.canonical for c in concepts.values()}

    aliases = {a.lower() for a in registry.aliases_for("golang")}
    assert "go" in aliases
    assert "golang" in aliases
    assert "go programming" in aliases


def test_registry_dedupe_preserves_first_occurrence_order():
    registry = ConceptRegistry(use_llm=False)
    result = registry.dedupe(["Rust", "rust programming", "Go", "golang", "rust"])
    assert result == ["rust", "go"]


def test_registry_sub_concepts_fallback_without_llm():
    registry = ConceptRegistry(use_llm=False)
    evidence = [
        "Rust is used for systems programming",
        "Rust ownership model prevents data races",
        "Rust async ecosystem is growing",
    ]
    subs = registry.extract_sub_concepts("Rust", evidence)
    assert isinstance(subs, list)


def test_retrieval_plan_uses_concept_registry_for_aliases():
    # Force Ollama to time out immediately so we exercise the fallback path.
    env = {"OLLAMA_TIMEOUT": "0.001"}
    with patch.dict(os.environ, env):
        plan = build_retrieval_plan("should backend engineers learn Go or Rust?", {})

    assert plan.primary
    assert plan.primary.lower() in plan.aliases[0].lower()
    # Aliases are canonicalized/deduped.
    assert len(plan.aliases) == len(set(plan.aliases))
