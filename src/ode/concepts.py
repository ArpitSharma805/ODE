"""Generic Concept Registry: normalize, canonicalize, and cluster technology concepts."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ode.llm import _ollama_generate

logger = logging.getLogger(__name__)


_STOP_TOKENS = {
    "the", "and", "for", "are", "but", "not", "you", "can", "had", "was", "one",
    "our", "out", "how", "its", "new", "now", "old", "use", "way", "who", "did",
    "she", "too", "any", "try", "let", "put", "end", "why", "here", "show", "very",
    "through", "just", "form", "great", "think", "where", "help", "much", "before",
    "right", "mean", "same", "tell", "come", "good", "long", "make", "many", "over",
    "such", "take", "than", "them", "well", "were", "with", "have", "from", "they",
    "been", "said", "time", "that", "this", "will", "about", "could", "other", "after",
    "first", "never", "these", "being", "every", "might", "shall", "still", "those",
    "while", "only", "also", "back", "know", "year", "some", "work", "life", "even",
    "most", "more", "day", "way", "own", "under", "last", "find", "give", "get",
    "made", "used", "does", "has", "is", "are", "was", "a", "an", "in", "on",
    "at", "to", "of", "it", "as", "or", "by", "up", "so", "if", "no", "be", "do",
}

# Generic suffixes/prefixes that create aliases but do not change the core concept.
# These are linguistic, not technology-specific.
_GENERIC_SUFFIXES = {
    "programming", "language", "lang", "script", "js", "development",
    "framework", "library", "tool", "tools", "platform", "ecosystem",
    "backend", "frontend", "fullstack", "full stack", "engineering",
}


def _normalize(text: str) -> str:
    """Normalize a concept string for comparison and indexing."""
    cleaned = re.sub(r"[^\w\s]", " ", str(text).lower())
    tokens = [t for t in cleaned.split() if t and t not in _STOP_TOKENS and len(t) > 1]
    return " ".join(tokens)


def _core_tokens(text: str) -> set[str]:
    """Return the core concept tokens with generic suffixes stripped."""
    normalized = _normalize(text)
    core: set[str] = set()
    for token in normalized.split():
        if token in _GENERIC_SUFFIXES:
            continue
        # Strip a generic suffix from the end of a token (e.g. golang -> go, reactjs -> react).
        for suffix in sorted(_GENERIC_SUFFIXES, key=len, reverse=True):
            if token.endswith(suffix) and len(token) > len(suffix):
                stem = token[: -len(suffix)]
                if stem and len(stem) >= 2:
                    token = stem
                    break
        if token and len(token) >= 2:
            core.add(token)
    return core


@dataclass
class Concept:
    """A canonical concept with aliases and extracted sub-concepts."""

    canonical: str
    aliases: set[str] = field(default_factory=set)
    sub_concepts: list[str] = field(default_factory=list)


class ConceptRegistry:
    """Resolve raw concept names to canonical concepts, aliases, and clusters.

    The registry is intentionally generic: it does not encode any technology-specific
    aliases. When Ollama is available it can suggest canonical forms and aliases;
    otherwise it falls back to token-overlap clustering.
    """

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
        self._concepts: dict[str, Concept] = {}
        self._alias: dict[str, str] = {}

    def normalize(self, text: str) -> str:
        return _normalize(text)

    def _register_one(self, canonical: str, aliases: set[str]) -> Concept:
        canonical = canonical.strip().lower()
        if not canonical:
            raise ValueError("canonical name cannot be empty")
        key = self.normalize(canonical)
        if not key:
            key = canonical

        if key in self._concepts:
            concept = self._concepts[key]
            concept.aliases.update(aliases)
        else:
            concept = Concept(canonical=canonical, aliases=set(aliases))
            self._concepts[key] = concept

        for alias in {canonical} | aliases:
            alias_key = self.normalize(alias)
            if alias_key and alias_key != key:
                self._alias[alias_key] = key
            elif not alias_key and alias.strip().lower() != key:
                self._alias[alias.strip().lower()] = key

        return concept

    def _llm_canonicalize_batch(
        self,
        raw_names: list[str],
        context: str = "",
    ) -> dict[str, tuple[str, list[str]]] | None:
        if not self.use_llm or not raw_names:
            return None

        prompt = (
            "You are a generic concept normalizer for a technology research system. "
            "Given a list of raw concept names, return valid JSON with key 'concepts'. "
            "Each item must have: raw (exact original string), canonical (clean 1-4 word name), "
            "aliases (list of common synonyms or alternate forms, excluding the canonical). "
            "Rules: do not add commentary; do not fabricate aliases unless they are commonly used; "
            "if two raw names clearly mean the same thing, give them the same canonical. "
            "Keep names domain-agnostic (e.g. 'Go Programming' -> 'Go').\n\n"
            f"Context: {context}\n"
            f"Raw names: {json.dumps(raw_names)}\n"
        )

        raw = _ollama_generate(prompt, format="json")
        if not raw:
            return None

        try:
            parsed = json.loads(raw.strip())
            items = parsed.get("concepts") if isinstance(parsed, dict) else parsed
            if not isinstance(items, list):
                return None
            result: dict[str, tuple[str, list[str]]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_name = str(item.get("raw", "")).strip()
                canonical = (str(item.get("canonical", "")).strip() or raw_name).lower()
                aliases = [
                    str(a).strip()
                    for a in item.get("aliases", [])
                    if a and str(a).strip() and str(a).strip().lower() != canonical.lower()
                ]
                if raw_name:
                    result[raw_name] = (canonical, aliases)
            return result
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Concept LLM canonicalization parse failed: %s", exc)
            return None

    def _similarity(self, a: str, b: str) -> float:
        """Jaccard similarity over core concept tokens (generic suffixes removed)."""
        tokens_a = _core_tokens(a)
        tokens_b = _core_tokens(b)
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    def _merge_by_similarity(
        self,
        raw_names: list[str],
        threshold: float = 0.6,
    ) -> dict[str, list[str]]:
        """Deterministic fallback clustering by token overlap."""
        clusters: list[list[str]] = []
        for name in raw_names:
            best_idx = -1
            best_score = 0.0
            for idx, cluster in enumerate(clusters):
                representative = cluster[0]
                score = self._similarity(name, representative)
                for existing in cluster:
                    score = max(score, self._similarity(name, existing))
                    if score >= threshold:
                        break
                if score > best_score:
                    best_score = score
                    best_idx = idx
                if best_score >= threshold:
                    break
            if best_score >= threshold and best_idx >= 0:
                clusters[best_idx].append(name)
            else:
                clusters.append([name])

        mapping: dict[str, list[str]] = {}
        for cluster in clusters:
            canonical = cluster[0]
            for name in cluster:
                mapping[name] = [n for n in cluster if n != name] + [canonical]
            if len(cluster) > 1:
                mapping[canonical] = [n for n in cluster if n != canonical]
        return mapping

    def register(
        self,
        raw_names: list[str],
        context: str = "",
    ) -> dict[str, Concept]:
        """Register raw names and return a map raw -> canonical Concept."""
        unique = list(dict.fromkeys(str(n).strip() for n in raw_names if str(n).strip()))
        if not unique:
            return {}

        llm_result = self._llm_canonicalize_batch(unique, context)

        if llm_result:
            for raw_name, (canonical, aliases) in llm_result.items():
                aliases_set = set(aliases) | {raw_name}
                self._register_one(canonical, aliases_set)
        else:
            cluster_map = self._merge_by_similarity(unique)
            seen_canonical: dict[str, Concept] = {}
            for name in unique:
                if name in seen_canonical:
                    continue
                cluster = [name] + [n for n in cluster_map.get(name, []) if n != name]
                canonical = min(cluster, key=len)
                aliases = {n for n in cluster if n != canonical}
                concept = self._register_one(canonical, aliases)
                for member in cluster:
                    seen_canonical[member] = concept

        return {raw: self.resolve(raw) for raw in unique if self.resolve(raw)}

    def resolve(self, raw: str) -> Concept | None:
        """Return the canonical Concept for a raw name, or None."""
        key = self.normalize(raw)
        if not key:
            key = raw.strip().lower()
        canonical_key = self._alias.get(key)
        if not canonical_key and key in self._concepts:
            canonical_key = key
        if not canonical_key:
            return None
        return self._concepts.get(canonical_key)

    def canonical(self, raw: str) -> str:
        concept = self.resolve(raw)
        return concept.canonical if concept else raw.strip()

    def aliases_for(self, raw: str) -> list[str]:
        concept = self.resolve(raw)
        if not concept:
            return [raw.strip()]
        return sorted({concept.canonical} | concept.aliases)

    def cluster(self, raw_names: list[str], context: str = "") -> list[list[str]]:
        """Group raw names into clusters of equivalent concepts."""
        mapping = self.register(raw_names, context)
        groups: dict[str, list[str]] = {}
        for name, concept in mapping.items():
            key = self.normalize(concept.canonical)
            groups.setdefault(key, []).append(name)
        return list(groups.values())

    def dedupe(self, raw_names: list[str], context: str = "") -> list[str]:
        """Return raw names deduplicated by canonical concept, preserving order."""
        clusters = self.cluster(raw_names, context)
        canonical_order: list[str] = []
        seen: set[str] = set()
        for cluster in clusters:
            canonical = self.canonical(cluster[0])
            key = self.normalize(canonical)
            if key and key not in seen:
                seen.add(key)
                canonical_order.append(canonical)
        return canonical_order

    def extract_sub_concepts(
        self,
        concept_name: str,
        evidence_bullets: list[str],
    ) -> list[str]:
        """Return sub-concepts/sub-skills for a concept from evidence."""
        if not self.use_llm:
            return self._fallback_sub_concepts(concept_name, evidence_bullets)

        prompt = (
            "You are a generic sub-skill extractor for a technology research system. "
            "Given a concept and supporting evidence bullets, return valid JSON with key 'sub_concepts': "
            "a list of 4-8 distinct sub-skills or sub-topics mentioned in the evidence. "
            "Keep each item 1-4 words and generic (do not include specific company or repo names).\n\n"
            f"Concept: {concept_name}\n"
            f"Evidence:\n" + "\n".join(evidence_bullets[:10]) + "\n"
        )
        raw = _ollama_generate(prompt, format="json")
        if raw:
            try:
                parsed = json.loads(raw.strip())
                sub_concepts = [
                    str(s).strip()
                    for s in parsed.get("sub_concepts", [])
                    if s
                ]
                if sub_concepts:
                    return sub_concepts
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("Sub-concept LLM parse failed: %s", exc)

        return self._fallback_sub_concepts(concept_name, evidence_bullets)

    def _fallback_sub_concepts(self, concept_name: str, evidence_bullets: list[str]) -> list[str]:
        """Deterministic fallback: extract repeated non-stopword bigrams from evidence."""
        text = " ".join(evidence_bullets).lower()
        tokens = [t for t in re.findall(r"\b[a-zA-Z]{3,}\b", text) if t not in _STOP_TOKENS]
        bigrams: dict[str, int] = {}
        for i in range(len(tokens) - 1):
            bg = f"{tokens[i]} {tokens[i + 1]}"
            if concept_name.lower() in bg or any(t in concept_name.lower() for t in bg.split()):
                continue
            bigrams[bg] = bigrams.get(bg, 0) + 1
        # Prefer bigrams that appear more than once, then common unigrams adjacent to the concept
        top = sorted(bigrams.items(), key=lambda x: x[1], reverse=True)[:8]
        return [bg for bg, count in top if count > 1]
