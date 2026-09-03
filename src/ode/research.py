"""Research planning for ODE signals.

A :class:`ResearchPlan` layers source selection, expected signal types, and
clarification detection on top of the query expansion produced by
:func:`ode.retrieval.build_retrieval_plan`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from typing import Any

from ode.intent import INTENT_TYPES, classify_intent
from ode.llm import _ollama_generate
from ode.retrieval import (
    RetrievalPlan,
    _dedupe_preserve,
    build_retrieval_plan,
)

logger = logging.getLogger(__name__)

__all__ = ["ResearchPlan", "build_research_plan", "ResearchDepth", "get_research_depth"]

# Research depth modes for signal collection and analysis
class ResearchDepth:
    """Research depth configuration for signal collection and analysis."""

    FAST = "fast"  # 20 signals, 60-90 seconds
    STANDARD = "standard"  # 50 signals, 2-3 minutes
    DEEP = "deep"  # 100+ signals, 5+ minutes

    _CONFIG = {
        FAST: {
            "max_signals": 20,
            "max_themes": 3,
            "max_trends": 3,
            "max_opportunities": 3,
            "signal_summary_limit": 20,
            "synthesis_limit": 20,
        },
        STANDARD: {
            "max_signals": 50,
            "max_themes": 5,
            "max_trends": 5,
            "max_opportunities": 5,
            "signal_summary_limit": 40,
            "synthesis_limit": 50,
        },
        DEEP: {
            "max_signals": 100,
            "max_themes": 8,
            "max_trends": 8,
            "max_opportunities": 8,
            "signal_summary_limit": 80,
            "synthesis_limit": 100,
        },
    }

    @classmethod
    def get_config(cls, depth: str) -> dict[str, int]:
        """Get configuration for a research depth mode."""
        return cls._CONFIG.get(depth, cls._CONFIG[cls.STANDARD])

    @classmethod
    def get_default(cls) -> str:
        """Get default research depth mode."""
        return cls.FAST


def get_research_depth(intent: dict[str, Any] | None = None) -> str:
    """Determine research depth based on intent and configuration.

    Args:
        intent: The intent dict from the clarifier

    Returns:
        Research depth mode (fast, standard, or deep)
    """
    # Check for explicit depth specification in intent
    if intent and isinstance(intent, dict):
        depth = intent.get("research_depth")
        if depth in [ResearchDepth.FAST, ResearchDepth.STANDARD, ResearchDepth.DEEP]:
            return depth

    # Default to fast mode for quick analysis
    return ResearchDepth.get_default()

# MCP research sources the planner may choose from.
_AVAILABLE_SOURCES = [
    "github",
    "tavily",
    "context7",
    "jobs",
    "hackernews",
    "producthunt",
    "reddit",
    "news",
]

# Strategic signal types the planner may expect to see.
_VALID_SIGNAL_TYPES = [
    "developer_pain",
    "enterprise_pain",
    "adoption_signal",
    "growth_signal",
    "market_event",
    "community_discussion",
    "ecosystem_gap",
    "competitive_gap",
    "operational_bottleneck",
    "hiring_signal",
    "startup_signal",
    "adoption",
    "market_demand",
    "hiring",
    "product_launch",
]

# Generic words that do not, by themselves, identify a specific research target.
_BROAD_STOPWORDS = {
    "what",
    "which",
    "how",
    "should",
    "would",
    "could",
    "can",
    "will",
    "are",
    "is",
    "the",
    "a",
    "an",
    "in",
    "on",
    "for",
    "to",
    "and",
    "or",
    "of",
    "with",
    "about",
    "new",
    "next",
    "best",
    "good",
    "worth",
    "learn",
    "learning",
    "use",
    "using",
    "vs",
    "versus",
    "compare",
    "comparison",
    "emerging",
    "trends",
    "opportunities",
    "ideas",
}

# Generic concepts that, standing alone, indicate the query needs narrowing.
_GENERIC_CONCEPTS = {
    "technology",
    "technologies",
    "tech",
    "software",
    "development",
    "engineering",
    "framework",
    "frameworks",
    "tool",
    "tools",
    "platform",
    "platforms",
    "language",
    "languages",
    "product",
    "products",
    "business",
    "market",
    "markets",
    "opportunity",
    "opportunities",
    "idea",
    "ideas",
    "skill",
    "skills",
    "career",
    "job",
    "jobs",
    "industry",
    "company",
    "companies",
    "application",
    "applications",
    "system",
    "systems",
    "database",
    "databases",
    "service",
    "services",
    "solution",
    "solutions",
}

# Intent-based source selection with Tier 1 source prioritization.
# Tier 1 Sources: Hacker News, Product Hunt, Job Market Data, Package Registry Data, Technology Research Sources
# GitHub is deprioritized and should have minimal influence, especially commits.
_DEFAULT_SOURCE_MAP: dict[str, list[str]] = {
    "Skill Learning": ["jobs", "hackernews", "tavily", "context7", "github"],
    "Career Development": ["jobs", "hackernews", "tavily", "context7", "github"],
    "Technology Evaluation": ["hackernews", "tavily", "context7", "jobs", "github"],
    "Opportunity Discovery": [
        "hackernews",
        "tavily",
        "jobs",
        "github",
        "context7",
    ],
    "Market Intelligence": ["hackernews", "tavily", "jobs"],
    "Product Ideas": ["hackernews", "tavily", "jobs", "github"],
    "Business Opportunities": ["hackernews", "tavily", "jobs"],
}

# Intent-based signal type selection (generic).
_DEFAULT_SIGNAL_MAP: dict[str, list[str]] = {
    "Skill Learning": ["adoption_signal", "community_discussion", "hiring_signal", "market_demand", "growth_signal"],
    "Career Development": ["hiring_signal", "market_demand", "adoption_signal", "community_discussion", "growth_signal"],
    "Technology Evaluation": ["developer_pain", "enterprise_pain", "adoption_signal", "community_discussion", "competitive_gap"],
    "Opportunity Discovery": [
        "developer_pain",
        "enterprise_pain",
        "ecosystem_gap",
        "operational_bottleneck",
        "adoption_signal",
        "growth_signal",
        "market_event",
        "startup_signal",
        "community_discussion",
        "market_demand",
        "hiring_signal",
    ],
    "Market Intelligence": ["market_event", "growth_signal", "market_demand", "startup_signal", "community_discussion", "adoption_signal"],
    "Product Ideas": [
        "developer_pain",
        "enterprise_pain",
        "ecosystem_gap",
        "operational_bottleneck",
        "market_demand",
        "startup_signal",
        "community_discussion",
        "adoption_signal",
    ],
    "Business Opportunities": ["market_event", "startup_signal", "growth_signal", "market_demand", "hiring_signal", "enterprise_pain"],
}

# Source weighting multipliers based on intent and source type.
# These multipliers are applied during signal normalization to prioritize high-value sources.
# Can be overridden via environment variables: ODE_OPPORTUNITY_DISCOVERY_HACKERNEWS_WEIGHT, etc.
_DEFAULT_SOURCE_WEIGHT_MULTIPLIERS: dict[str, dict[str, float]] = {
    "Opportunity Discovery": {
        "hackernews": 4.0,  # 80% weight - primary signal source
        "github_issues": 1.0,  # 20% weight - focused on unmet needs
        "github_discussions": 1.0,  # 20% weight - focused on operational friction
        "tavily": 1.0,
        "context7": 1.0,
        "github_commits": 0.1,  # Minimal weight - de-emphasize raw commits
        "github": 0.5,  # General GitHub weight
    },
    "Skill Learning": {
        "jobs": 1.40,
        "hackernews": 1.20,
        "tavily": 1.10,
        "context7": 1.10,
        "github": 0.5,
        "github_commits": 0.3,
    },
    "Career Development": {
        "jobs": 1.40,
        "hackernews": 1.20,
        "tavily": 1.10,
        "context7": 1.10,
        "github": 0.5,
        "github_commits": 0.3,
    },
    "Technology Evaluation": {
        "hackernews": 1.15,
        "jobs": 1.25,
        "tavily": 1.10,
        "context7": 1.20,
        "github": 0.5,
        "github_commits": 0.3,
    },
    "Market Intelligence": {
        "hackernews": 1.30,
        "tavily": 1.15,
        "jobs": 1.10,
    },
    "Product Ideas": {
        "hackernews": 1.30,
        "tavily": 1.15,
        "jobs": 1.10,
        "github": 0.5,
    },
    "Business Opportunities": {
        "hackernews": 1.30,
        "tavily": 1.15,
        "jobs": 1.10,
    },
}

# Allow environment variable overrides for experimentation
def _get_weight_multiplier(intent: str, source: str, default: float) -> float:
    """Get weight multiplier with environment variable override support.

    Environment variables follow pattern: ODE_<INTENT>_<SOURCE>_WEIGHT
    Example: ODE_OPPORTUNITY_DISCOVERY_HACKERNEWS_WEIGHT=4.0
    """
    env_var_name = f"ODE_{intent.upper().replace(' ', '_')}_{source.upper()}_WEIGHT"
    env_value = os.getenv(env_var_name)
    if env_value is not None:
        try:
            return float(env_value)
        except ValueError:
            logger.warning(f"Invalid environment variable {env_var_name}={env_value}, using default")
    return default

# Build the actual weight multipliers with environment overrides
_SOURCE_WEIGHT_MULTIPLIERS: dict[str, dict[str, float]] = {}
for intent, weights in _DEFAULT_SOURCE_WEIGHT_MULTIPLIERS.items():
    _SOURCE_WEIGHT_MULTIPLIERS[intent] = {
        source: _get_weight_multiplier(intent, source, default)
        for source, default in weights.items()
    }

# Base recency window per intent.
_DEFAULT_RECENCY: dict[str, int] = {
    "Skill Learning": 365,
    "Career Development": 365,
    "Technology Evaluation": 365,
    "Opportunity Discovery": 180,
    "Market Intelligence": 90,
    "Product Ideas": 90,
    "Business Opportunities": 90,
}

# Generic fallback disambiguation choices for broad queries.
_FALLBACK_CLARIFICATION_OPTIONS = [
    "A specific tool, framework, or language",
    "A career path or skill to learn",
    "A market, business, or product opportunity",
    "A technology comparison or evaluation",
    "A particular domain or industry",
]


@dataclass
class ResearchPlan:
    """A query-specific plan that selects research sources and expected signals.

    Attributes:
        query: The original user query.
        intent: The classified intent label.
        retrieval_plan: Query expansion and search plan from :mod:`ode.retrieval`.
        sources: MCP research sources to query.
        signal_types: Strategic signal types expected for this research.
        recency_days: How far back to look for signals.
        needs_clarification: ``True`` when the query is too broad/ambiguous.
        clarifying_question: Follow-up question for broad queries.
        clarification_options: Suggested disambiguation choices.
        reasoning: Short rationale for the selected plan.
        research_depth: Research depth mode (fast, standard, deep).
    """

    query: str = ""
    intent: str = "Opportunity Discovery"
    retrieval_plan: RetrievalPlan = field(default_factory=RetrievalPlan)
    sources: list[str] = field(default_factory=list)
    signal_types: list[str] = field(default_factory=list)
    recency_days: int = 365
    needs_clarification: bool = False
    clarifying_question: str = ""
    clarification_options: list[str] = field(default_factory=list)
    reasoning: str = ""
    research_depth: str = ResearchDepth.get_default()


def _normalize(text: str) -> str:
    """Normalize text for token comparison."""
    return re.sub(r"[^\w\s]", " ", text.lower())


def _tokens(text: str) -> set[str]:
    """Return meaningful query tokens of at least two characters."""
    return {
        t
        for t in _normalize(text).split()
        if len(t) >= 2 and t not in _BROAD_STOPWORDS
    }


def _intent_type(intent: dict[str, Any] | None) -> str:
    """Return a validated intent label, defaulting to Opportunity Discovery."""
    safe_intent = intent if isinstance(intent, dict) else {}
    intent_type = safe_intent.get("intent", "")
    if intent_type in INTENT_TYPES:
        return str(intent_type)
    return "Opportunity Discovery"


def _fallback_sources(intent_type: str) -> list[str]:
    """Return deterministic MCP sources for the intent."""
    return list(_DEFAULT_SOURCE_MAP.get(intent_type, _DEFAULT_SOURCE_MAP["Opportunity Discovery"]))


def _fallback_signals(intent_type: str) -> list[str]:
    """Return deterministic signal types for the intent."""
    return list(_DEFAULT_SIGNAL_MAP.get(intent_type, _DEFAULT_SIGNAL_MAP["Opportunity Discovery"]))


def get_source_weight_multiplier(intent_type: str, source: str, metric: str) -> float:
    """Return the confidence multiplier for a source based on intent and source type.

    This implements the Tier 1 source strategy where high-value sources like
    Product Hunt, Hacker News, and job market data are boosted, while GitHub
    commits are heavily penalized.

    Args:
        intent_type: The classified intent (e.g., "Opportunity Discovery")
        source: The source name (e.g., "github_mcp", "hackernews")
        metric: The specific metric (e.g., "github_commits", "github_stars")

    Returns:
        A multiplier to apply to signal confidence (default 1.0)
    """
    source_lower = source.lower().replace("_mcp", "").replace("_", "")
    metric_lower = metric.lower()

    # Get the weight map for this intent, defaulting to Opportunity Discovery
    weight_map = _SOURCE_WEIGHT_MULTIPLIERS.get(intent_type, _SOURCE_WEIGHT_MULTIPLIERS["Opportunity Discovery"])

    # Check for specific metric overrides first (e.g., github_commits)
    if "github_commit" in metric_lower:
        return weight_map.get("github_commits", 0.3)
    if "github_issue" in metric_lower:
        return weight_map.get("github_issues", 1.10)
    if "github_discussion" in metric_lower:
        return weight_map.get("github_discussions", 1.10)

    # Check for source-level weights
    if source_lower in weight_map:
        return weight_map[source_lower]

    # Default multiplier
    return 1.0


def _fallback_recency(intent_type: str, sources: list[str], signal_types: list[str]) -> int:
    """Return a deterministic recency window based on intent and selected sources."""
    recency = _DEFAULT_RECENCY.get(intent_type, 180)
    if "product_launch" in signal_types or "producthunt" in sources or "news" in sources:
        recency = min(recency, 90)
    if intent_type in ("Skill Learning", "Career Development"):
        recency = max(recency, 365)
    return recency


def _query_is_broad(query: str, retrieval_plan: RetrievalPlan, intent: dict[str, Any] | None) -> bool:
    """Detect whether a query is too broad to research without clarification.

    The heuristic is intentionally generic: it flags queries whose primary
    concept is an empty or overly generic term, or whose confidence is low.
    """
    primary = retrieval_plan.primary.strip().lower()
    if not primary:
        return True

    primary_words = [w for w in primary.split() if len(w) >= 2 and w not in _BROAD_STOPWORDS]
    if not primary_words:
        return True
    if all(w in _GENERIC_CONCEPTS for w in primary_words):
        return True

    safe_intent = intent if isinstance(intent, dict) else {}
    confidence = float(safe_intent.get("confidence", 1.0) or 1.0)
    if confidence < 0.5 and _tokens(primary).issubset(_GENERIC_CONCEPTS):
        return True

    query_words = _tokens(query)
    if len(query_words) < 2 and primary in _GENERIC_CONCEPTS:
        return True

    return False


def _fallback_clarification(
    query: str,
    retrieval_plan: RetrievalPlan,
    intent: dict[str, Any] | None,
) -> tuple[bool, str, list[str]]:
    """Return deterministic clarification guidance for broad queries."""
    if _query_is_broad(query, retrieval_plan, intent):
        intent_type = _intent_type(intent)
        question = (
            "Your query is a bit broad. Could you narrow it down to a specific "
            "technology, domain, or goal you want to research?"
        )
        options = list(_FALLBACK_CLARIFICATION_OPTIONS)
        if intent_type in ("Skill Learning", "Career Development"):
            options.insert(0, "A specific skill, language, or framework to learn")
        elif intent_type == "Technology Evaluation":
            options.insert(0, "Two or more technologies to compare")
        elif intent_type in ("Opportunity Discovery", "Product Ideas", "Business Opportunities"):
            options.insert(0, "A specific market, domain, or problem space")
        return True, question, list(dict.fromkeys(options))[:5]
    return False, "", []


def _strip_json_block(text: str) -> str:
    """Strip Markdown code fences from a JSON response."""
    text = text.strip()
    if text.startswith("```"):
        match = re.match(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
    return text


def _llm_research_plan(
    query: str,
    intent: dict[str, Any],
    retrieval_plan: RetrievalPlan,
) -> dict[str, Any] | None:
    """Ask Ollama to choose sources, signals, and broadness. Returns None on failure."""
    intent_type = _intent_type(intent)
    prompt = (
        "You are a research planner for an opportunity discovery engine. "
        "Given the user query, intent, and retrieval plan, select the best "
        "MCP research sources and expected signal types.\n\n"
        f"Query: {query}\n"
        f"Intent: {intent_type}\n"
        f"Primary concept: {retrieval_plan.primary}\n"
        f"Aliases: {', '.join(retrieval_plan.aliases)}\n\n"
        f"Available sources: {', '.join(_AVAILABLE_SOURCES)}\n"
        f"Available signal types: {', '.join(_VALID_SIGNAL_TYPES)}\n\n"
        "Return a JSON object with exactly these keys:\n"
        '- "sources": list of source names (subset of available sources)\n'
        '- "signal_types": list of signal types (subset of available signal types)\n'
        '- "needs_clarification": boolean, true only if the query is too broad or '
        "  ambiguous to research without more specifics\n"
        '- "clarifying_question": a short follow-up question when clarification is needed\n'
        '- "clarification_options": list of 3-5 concrete choices a user could pick\n'
        '- "reasoning": one-sentence rationale for the source and signal selection\n\n'
        "Rules:\n"
        "- Do not introduce any technology or topic that the query does not explicitly name.\n"
        "- Skill Learning / Career Development signals: adoption, hiring, community discussion.\n"
        "- Opportunity Discovery / Product Ideas / Business Opportunities: prioritize hackernews, github, tavily for "
        "  technology opportunity discovery. Include jobs only if explicitly career-focused.\n"
        "- Technology Evaluation: focus on developer pain, adoption, and community discussion.\n"
        "- A query is too broad when it lacks a specific technology, domain, or problem to research.\n"
        "- For technology opportunity discovery, always include hackernews and github as primary sources.\n"
        "Return only valid JSON."
    )
    raw = _ollama_generate(prompt, format="json")
    if not raw:
        return None

    try:
        parsed = json.loads(_strip_json_block(raw))
        if not isinstance(parsed, dict):
            return None
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("LLM research plan parse failed: %s", exc)
        return None

    sources = [str(s).strip().lower() for s in parsed.get("sources", []) if s]
    sources = [s for s in sources if s in _AVAILABLE_SOURCES]

    signal_types = [str(s).strip().lower() for s in parsed.get("signal_types", []) if s]
    signal_types = [s for s in signal_types if s in _VALID_SIGNAL_TYPES]

    needs_clarification = bool(parsed.get("needs_clarification", False))
    clarifying_question = str(parsed.get("clarifying_question") or "").strip()
    clarification_options_raw = parsed.get("clarification_options")
    clarification_options = [str(o) for o in (clarification_options_raw or []) if o]
    reasoning = str(parsed.get("reasoning") or "").strip()

    if not sources or not signal_types:
        return None

    return {
        "sources": sources,
        "signal_types": signal_types,
        "needs_clarification": needs_clarification,
        "clarifying_question": clarifying_question,
        "clarification_options": clarification_options,
        "reasoning": reasoning,
    }


def _fallback_research_plan(
    query: str,
    intent: dict[str, Any],
    retrieval_plan: RetrievalPlan,
) -> dict[str, Any]:
    """Return a deterministic research plan when Ollama is unavailable."""
    intent_type = _intent_type(intent)
    sources = _fallback_sources(intent_type)
    signal_types = _fallback_signals(intent_type)
    broad, question, options = _fallback_clarification(query, retrieval_plan, intent)
    recency = _fallback_recency(intent_type, sources, signal_types)
    return {
        "sources": sources,
        "signal_types": signal_types,
        "needs_clarification": broad,
        "clarifying_question": question,
        "clarification_options": options,
        "reasoning": f"Deterministic fallback for {intent_type} intent.",
        "recency_days": recency,
    }


def _expand_search_queries_with_llm(query: str, primary_technology: str) -> list[str]:
    """Ask the analysis LLM for 3-4 industry-standard search terms for the topic."""
    # Lazy import to avoid a circular dependency through ode.agents.
    from ode.agents.signal_analyst import _call_analysis_llm

    prompt = (
        "You are a tech research planner. Given the research topic "
        f"'{primary_technology}' (Query: '{query}'), output 3-4 industry-standard "
        "search terms, alternative names, or developer friction keywords used on "
        "GitHub, HackerNews, and tech news.\n"
        'Return JSON: {"search_queries": ["...", "..."]}.\n'
        'Example for "AI Observability": ["LLM monitoring", "agent tracing", "prompt evaluation", "AI telemetry"].\n'
        'Example for "Go vs Rust": ["golang microservices benchmark", "rust cloud native performance"].'
    )
    raw = _call_analysis_llm(
        "You output only valid JSON with a 'search_queries' list.",
        prompt,
    )
    try:
        parsed = json.loads(_strip_json_block(raw))
        expanded = [str(q).strip() for q in parsed.get("search_queries", []) if q]
        if len(expanded) >= 3:
            return expanded
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("LLM query expansion parse failed; using fallback.")

    # Deterministic fallback using clean, high-yield search term expansions.
    first_word = primary_technology.split()[0] if primary_technology.split() else primary_technology
    return _dedupe_preserve([
        primary_technology,
        f"{primary_technology} tools 2026",
        f"{primary_technology} landscape",
        f"{first_word} telemetry",
        f"{first_word} tracing",
    ])


def _filter_github_queries(queries: list[str]) -> list[str]:
    """Remove job/career/hiring terms and abstract business words from GitHub queries.

    GitHub repository searches should use concrete software/ecosystem terms, not job market terms.
    Job-related terms trigger _is_excluded_repo() which filters out repos containing "job", "career", etc.
    Abstract business terms don't yield relevant GitHub code repositories.
    """
    job_terms = {"job", "jobs", "career", "hiring", "opportunities", "salary", "recruiting", "recruitment"}
    abstract_business_terms = {
        "opportunities", "projects", "repositories", "market analysis", "openings",
        "market trends", "business", "enterprise", "commercial", "industry",
        "landscape", "ecosystem", "analysis", "overview", "guide", "roadmap"
    }
    filtered = []
    for query in queries:
        query_lower = query.lower()
        # Filter out any query containing job terms or abstract business terms
        if any(job_term in query_lower for job_term in job_terms):
            continue  # Skip queries with job terms
        if any(business_term in query_lower for business_term in abstract_business_terms):
            continue  # Skip queries with abstract business terms
        filtered.append(query)
    return filtered


def _construct_github_queries(primary: str) -> list[str]:
    """Construct GitHub queries using generic software component terms.

    This works for any technology dynamically without hardcoding specific tech names.
    Uses the primary technology combined with generic software component patterns.
    """
    primary_clean = primary.strip()
    if not primary_clean:
        return [primary]

    # Generic software component terms that work for any technology
    component_terms = ["server", "sdk", "plugin", "library", "framework", "tool", "api", "client"]

    # Build queries: primary alone + primary with each component term
    queries = [primary_clean]
    for term in component_terms:
        queries.append(f"{primary_clean} {term}")

    return queries


def _expand_with_llm(retrieval_plan: RetrievalPlan, query: str) -> RetrievalPlan:
    """Merge retrieval-plan queries with LLM-expanded search terms."""
    expanded = _expand_search_queries_with_llm(query, retrieval_plan.primary)

    # For GitHub: use generic software component queries based on primary technology
    github_base_queries = _construct_github_queries(retrieval_plan.primary)
    github_queries = _dedupe_preserve(github_base_queries + retrieval_plan.github_queries + expanded)[:12]
    # Filter out job/career terms and abstract business terms from GitHub queries
    github_queries = _filter_github_queries(github_queries)

    # For Tavily: keep business and market queries where news articles exist
    tavily_queries = _dedupe_preserve([retrieval_plan.primary] + retrieval_plan.tavily_queries + expanded)[:8]

    aliases = _dedupe_preserve(retrieval_plan.aliases + expanded)
    return replace(
        retrieval_plan,
        github_queries=github_queries,
        tavily_queries=tavily_queries,
        aliases=aliases,
        min_relevance=0.1,
        min_authority=0.2,
    )


def build_research_plan(query: str, intent: dict[str, Any] | None = None) -> ResearchPlan:
    """Build a :class:`ResearchPlan` for a query.

    Uses Ollama to reason over source and signal selection when available,
    falling back to deterministic intent-based rules. The returned plan
    includes the underlying :class:`ode.retrieval.RetrievalPlan` for query
    expansion, the selected MCP sources, expected signal types, and any
    clarification needed for overly broad queries.
    """
    if intent is None:
        intent = classify_intent(query)

    retrieval_plan = build_retrieval_plan(query, intent)
    llm_result = _llm_research_plan(query, intent, retrieval_plan)
    if llm_result is None:
        llm_result = _fallback_research_plan(query, intent, retrieval_plan)

    intent_type = _intent_type(intent)
    sources = llm_result.get("sources") or _fallback_sources(intent_type)
    signal_types = llm_result.get("signal_types") or _fallback_signals(intent_type)
    needs_clarification = llm_result.get("needs_clarification", False)
    clarifying_question = llm_result.get("clarifying_question", "")
    clarification_options = llm_result.get("clarification_options", [])
    reasoning = llm_result.get("reasoning", "")
    recency = llm_result.get("recency_days") or _fallback_recency(intent_type, sources, signal_types)

    if needs_clarification and (not clarifying_question or not clarification_options):
        _, clarifying_question, clarification_options = _fallback_clarification(
            query, retrieval_plan, intent
        )

    retrieval_plan = replace(retrieval_plan, recency_days=recency)

    retrieval_plan = _expand_with_llm(retrieval_plan, query)

    # Determine research depth from intent
    research_depth = get_research_depth(intent)

    return ResearchPlan(
        query=query,
        intent=intent_type,
        retrieval_plan=retrieval_plan,
        sources=sources,
        signal_types=signal_types,
        recency_days=recency,
        needs_clarification=needs_clarification,
        clarifying_question=clarifying_question,
        clarification_options=clarification_options,
        reasoning=reasoning,
        research_depth=research_depth,
    )
