"""Query clarification for broad or ambiguous research intents.

When a user query names only a role, domain, or general direction without a
concrete technology, the opportunity discovery pipeline cannot research it
effectively.  This module decides whether to pause and ask a concise,
multiple-choice clarifying question.

The decision first checks whether the extracted ``primary_technology`` (or a
concrete technology named in the query) is clearly non-generic; if so, it
skips clarification immediately.  Otherwise it asks Ollama for a structured
judgement, and if Ollama is unavailable or returns an unparseable response,
deterministic heuristics take over.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from ode.llm import _ollama_generate

logger = logging.getLogger(__name__)

# Stopwords used to clean the extracted ``primary_technology`` before checking
# whether it is a generic role or domain label.
_STOPWORDS: set[str] = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "shall",
    "may",
    "might",
    "must",
    "can",
    "need",
    "want",
    "what",
    "how",
    "when",
    "where",
    "why",
    "who",
    "which",
    "whom",
    "whose",
    "this",
    "that",
    "these",
    "those",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "its",
    "our",
    "their",
    "and",
    "or",
    "but",
    "nor",
    "for",
    "with",
    "without",
    "from",
    "to",
    "of",
    "in",
    "on",
    "at",
    "by",
    "about",
    "into",
    "onto",
    "upon",
    "over",
    "under",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "among",
    "within",
    "not",
    "no",
    "only",
    "just",
    "also",
    "than",
    "then",
    "now",
    "here",
    "there",
    "as",
    "if",
}

# Generic role and domain words.  These are intentionally broad vocabulary
# (e.g. "engineer", "cloud", "framework"), not a list of specific technologies.
_ROLE_WORDS: set[str] = {
    "admin",
    "administrator",
    "analyst",
    "architect",
    "beginner",
    "coder",
    "consultant",
    "designer",
    "developer",
    "engineer",
    "expert",
    "freelancer",
    "lead",
    "leader",
    "manager",
    "professional",
    "programmer",
    "scientist",
    "specialist",
    "student",
}

_DOMAIN_WORDS: set[str] = {
    "ai",
    "artificialintelligence",
    "assurance",
    "backend",
    "cloud",
    "cloudcomputing",
    "coding",
    "computer",
    "computing",
    "data",
    "dataengineering",
    "database",
    "design",
    "development",
    "devops",
    "engineering",
    "framework",
    "frontend",
    "fullstack",
    "language",
    "library",
    "machinelearning",
    "mobile",
    "mobiledevelopment",
    "operations",
    "platform",
    "product",
    "programming",
    "project",
    "qa",
    "quality",
    "science",
    "security",
    "software",
    "softwareengineering",
    "stack",
    "systems",
    "technology",
    "testing",
    "tool",
    "web",
    "webdevelopment",
}

_GENERIC_WORDS: set[str] = _ROLE_WORDS | _DOMAIN_WORDS

# Query prefixes that signal the user is asking for a recommendation without
# having named a concrete technology.
_BROAD_PATTERNS: tuple[str, ...] = (
    "should i use",
    "what should",
    "should i",
    "what to",
    "how to",
)

_DEFAULT_QUESTION = "Your question is quite broad. Which category are you asking about?"

_DEFAULT_OPTIONS: list[str] = [
    "Framework",
    "Database",
    "Cloud platform",
    "Full stack",
    "Programming language",
    "Tooling or library",
]

_CLARIFY_PROMPT = (
    "You are a research-intent clarifier for a technology opportunity discovery engine. "
    "Decide whether the user query is too broad or ambiguous to research effectively. "
    "A query is broad if it asks what to learn, use, build, or compare but only names a "
    "role, domain, or general direction without a concrete, specific technology "
    "(e.g. 'backend engineer', 'developer', 'cloud engineering', 'what should I learn next').\n\n"
    "Query: {query}\n"
    "Primary technology / role extracted: {primary}\n"
    "Intent type: {intent_type}\n\n"
    "Return a JSON object with exactly these keys:\n"
    "- needs_clarification (bool)\n"
    "- clarifying_question (string, concise, multiple-choice; empty if needs_clarification is false)\n"
    "- clarification_options (list of 3-5 short category strings; empty if needs_clarification is false)\n\n"
    "The question should ask which broad category the user means (framework, database, "
    "cloud platform, full stack, programming language, tooling, methodology, etc.). "
    "Do not name specific technologies or products in the options; use only generic categories."
)


def _normalize_phrases(text: str) -> str:
    """Collapse common multi-word domain phrases into single tokens."""
    text = re.sub(r"\bfront[- ]?end\b", "frontend", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfull[- ]?stack\b", "fullstack", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmachine[- ]?learning\b", "machinelearning", text, flags=re.IGNORECASE)
    text = re.sub(r"\bartificial[- ]?intelligence\b", "artificialintelligence", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcloud[- ]?computing\b", "cloudcomputing", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsoftware[- ]?engineering\b", "softwareengineering", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdata[- ]?engineering\b", "dataengineering", text, flags=re.IGNORECASE)
    text = re.sub(r"\bweb[- ]?development\b", "webdevelopment", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmobile[- ]?development\b", "mobiledevelopment", text, flags=re.IGNORECASE)
    return text.lower()


def _content_words(text: str) -> list[str]:
    """Return lowercase content words of at least two characters."""
    normalized = _normalize_phrases(text)
    return [
        word
        for word in re.findall(r"[a-z]+", normalized)
        if word not in _STOPWORDS and len(word) >= 2
    ]


def _is_generic_word(word: str) -> bool:
    """Return True when *word* (or its simple plural) is a generic role/domain term."""
    if word in _GENERIC_WORDS:
        return True
    if word.endswith("s") and word[:-1] in _GENERIC_WORDS:
        return True
    if word.endswith("ies") and word[:-3] + "y" in _GENERIC_WORDS:
        return True
    return False


def _primary_is_generic_role_or_domain(primary: str) -> bool:
    """Return True when *primary* is a generic role or domain label.

    A label is generic when every content word is a broad role, domain, or
    technology-category term.  Concrete names like ``Google Cloud`` or
    ``PostgreSQL`` therefore fail the check because they contain at least one
    non-generic content word.
    """
    words = _content_words(primary)
    if not words:
        return False
    return all(_is_generic_word(word) for word in words)


def _primary_is_unparsed(query: str, primary: str) -> bool:
    """Return True when no concrete technology could be isolated from *query*."""
    q = re.sub(r"[^\w]+", "", query.lower())
    p = re.sub(r"[^\w]+", "", primary.lower())
    return q == p and len(q) > 2 and len(query.split()) > 1


def _query_is_broad(query: str, primary: str) -> bool:
    """Deterministic check for broad or ambiguous queries.

    Broad criteria:
    - The query asks what to learn/use/build but names only a role or domain.
    - ``primary_technology`` is a generic role/domain label.
    - The query contains ``"should I use"`` or ``"what should"`` without a
      concrete technology.
    """
    q = query.lower().strip()
    p = primary.strip()

    if _primary_is_generic_role_or_domain(p):
        return True

    if any(pattern in q for pattern in _BROAD_PATTERNS):
        if not p or _primary_is_generic_role_or_domain(p) or _primary_is_unparsed(query, p):
            return True

    return False


def maybe_clarify(query: str, intent: dict[str, Any]) -> dict[str, Any]:
    """Return *intent*, possibly augmented with a clarifying question.

    If *query* is too broad or ambiguous to research effectively (e.g. it
    names only a role or domain without a concrete technology), the returned
    dict includes ``needs_clarification``, ``clarifying_question`` and
    ``clarification_options``.  Otherwise it sets ``needs_clarification`` to
    ``False`` and leaves the rest of the intent unchanged.

    The function first checks whether the extracted ``primary_technology``
    (or a concrete technology named in the query) is clearly non-generic; if
    so, it skips the LLM and returns immediately.  Otherwise it asks Ollama for
    a structured judgement, and if Ollama is unavailable or returns an
    unparseable response, deterministic heuristics take over.
    """
    start_time = time.time()
    result: dict[str, Any] = {**intent}
    primary = str(intent.get("primary_technology", "") or "").strip()
    intent_type = str(intent.get("intent", "") or "").strip()

    # Skip clarification when the primary technology is clearly concrete.
    if primary and not _primary_is_generic_role_or_domain(primary):
        result["needs_clarification"] = False
        logger.info("Clarification: skipped (concrete primary technology) (%.2fs)", time.time() - start_time)
        return result

    # If the primary is empty but the query names a concrete technology to learn/use,
    # also skip clarification and backfill the missed primary.
    if not primary:
        learn_match = re.search(
            r"(?:learn|use|adopt|try|switch to|migrate to)\s+([A-Za-z][A-Za-z0-9.#+\-]*)",
            query,
            re.IGNORECASE,
        )
        if learn_match:
            candidate = learn_match.group(1).strip().rstrip("?.")
            # Ignore stopwords / articles so "learn to code" or "use a framework"
            # don't leak through as concrete technologies.
            if (
                candidate
                and len(candidate) >= 2
                and candidate.lower() not in _STOPWORDS
                and not _primary_is_generic_role_or_domain(candidate)
            ):
                result["needs_clarification"] = False
                result["primary_technology"] = candidate
                logger.info("Clarification: skipped (learn/use pattern) (%.2fs)", time.time() - start_time)
                return result

    prompt = _CLARIFY_PROMPT.format(
        query=query,
        primary=primary,
        intent_type=intent_type,
    )

    llm_start = time.time()
    raw = _ollama_generate(prompt, format="json")
    llm_duration = time.time() - llm_start
    logger.info("Clarification LLM call: %.2fs", llm_duration)

    if raw:
        try:
            parsed = json.loads(raw.strip())
            if not isinstance(parsed, dict):
                raise ValueError("LLM clarification JSON was not a JSON object")
            raw_needs = parsed.get("needs_clarification", False)
            if isinstance(raw_needs, str):
                needs = raw_needs.strip().lower() in {"true", "yes", "1"}
            else:
                needs = bool(raw_needs)

            if needs:
                question = str(parsed.get("clarifying_question", "") or "").strip()
                options = parsed.get("clarification_options")
                if isinstance(options, list):
                    options = [str(option) for option in options if option]
                else:
                    options = []
                result["needs_clarification"] = True
                result["clarifying_question"] = question or _DEFAULT_QUESTION
                result["clarification_options"] = options or _DEFAULT_OPTIONS.copy()
            else:
                result["needs_clarification"] = False
            logger.info("Clarification total: %.2fs (LLM: %.2fs)", time.time() - start_time, llm_duration)
            return result
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Ollama clarification parse failed: %s", exc)

    if _query_is_broad(query, primary):
        result["needs_clarification"] = True
        result["clarifying_question"] = _DEFAULT_QUESTION
        result["clarification_options"] = _DEFAULT_OPTIONS.copy()
    else:
        result["needs_clarification"] = False

    logger.info("Clarification: rule-based fallback (%.2fs)", time.time() - start_time)
    return result
