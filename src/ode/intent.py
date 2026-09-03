"""Query intent classification for the ODE copilot."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from ode.concepts import ConceptRegistry
from ode.llm import _ollama_generate
from ode.mcp_client import call_tool
from ode.retrieval import _extract_primary_topic

logger = logging.getLogger(__name__)

INTENT_TYPES = [
    "Skill Learning",
    "Career Development",
    "Technology Evaluation",
    "Opportunity Discovery",
    "Market Intelligence",
    "Product Ideas",
    "Business Opportunities",
]

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "Skill Learning": ["learn", "learning", "skill", "worth learning", "should i learn", "study"],
    "Career Development": [
        "career", "job", "salary", "hiring", "promotion", "interview", "resume",
        "should i learn next", "what should", "learn next", "path for",
    ],
    "Technology Evaluation": ["vs", "versus", "compare", "comparison", "better than", "should i use", "worth using"],
    "Opportunity Discovery": ["opportunities in", "opportunities", "emerging", "invest"],
    "Market Intelligence": ["market", "growing", "fastest", "ecosystem", "trends", "adoption"],
    "Product Ideas": ["product idea", "startup idea", "build", "build an app", "product"],
    "Business Opportunities": ["business", "enterprise", "revenue", "market size", "b2b"],
}

_STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "shall", "may", "might", "must", "can", "need", "want",
    "what", "how", "when", "where", "why", "who", "which", "whom", "whose",
    "this", "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "its", "our", "their",
    "and", "or", "but", "nor", "for", "with", "without", "from", "to", "of", "in",
    "on", "at", "by", "about", "into", "onto", "upon", "over", "under", "through",
    "during", "before", "after", "above", "below", "between", "among", "within",
    "learn", "learning", "learned", "learns", "next", "worth", "good", "best",
    "better", "use", "using", "used", "new", "old", "now", "then", "today",
    "tomorrow", "next", "last", "first", "second", "third", "year", "years",
    "2026", "2025", "2024", "2027",
    "person", "people", "someone", "everyone", "anyone", "engineer", "developer",
    "programmer", "coder", "admin", "analyst", "architect", "manager", "user",
    "users", "team", "teams", "company", "companies", "organization", "organizations",
    "somebody", "everybody", "anybody", "student", "beginner", "professional",
}

_TECHNOLOGY_RULES: list[tuple[list[str], str, list[str], str]] = [
    (["html", "css"], "HTML", ["html", "css", "javascript", "typescript", "react"], "Frontend Development"),
    (["react"], "React", ["react", "javascript", "typescript", "nextjs", "frontend"], "Frontend Development"),
    (["typescript"], "TypeScript", ["typescript", "javascript", "react", "frontend"], "Frontend Development"),
    (["javascript"], "JavaScript", ["javascript", "html", "css", "typescript", "react"], "Frontend Development"),
    (["frontend", "front end"], "Frontend", ["html", "css", "javascript", "typescript", "react", "nextjs"], "Frontend Development"),
    (["mcp"], "MCP", ["mcp", "model context protocol", "mcp security", "mcp enterprise"], "AI Infrastructure"),
    (["agent", "agents", "agentic"], "Agent", ["ai agent", "agent infrastructure", "multi agent"], "AI Infrastructure"),
    (["llm"], "LLM", ["large language model", "llm tooling", "open source llm"], "AI Infrastructure"),
    (["cloud"], "Cloud", ["cloud engineering", "cloud platform", "devops"], "Cloud Engineering"),
    (["python"], "Python", ["python programming", "python ecosystem", "python libraries"], "Backend Development"),
    (["go", "golang"], "Go", ["golang backend", "go programming", "go ecosystem"], "Backend Development"),
    (["rust"], "Rust", ["rust programming", "rust systems", "rust ecosystem"], "Systems Development"),
    (["kubernetes", "k8s"], "Kubernetes", ["kubernetes", "container orchestration", "cloud native"], "Cloud Engineering"),
    (["postgres", "postgresql"], "PostgreSQL", ["postgresql", "relational database", "sql"], "Backend Development"),
    (["docker"], "Docker", ["docker", "containers", "containerization"], "DevOps"),
    (["fastapi"], "FastAPI", ["fastapi", "python api", "python web"], "Backend Development"),
    (["system design"], "System Design", ["system design", "distributed systems", "scalability"], "Backend Development"),
]

_ROLE_RULES: list[tuple[list[str], str, str, list[str], str]] = [
    (
        ["backend engineer", "backend developer", "back end"],
        "Backend Engineer",
        "Backend Engineering",
        ["system design", "databases", "distributed systems", "kubernetes", "docker", "postgresql", "fastapi", "go", "rust", "python"],
        "Backend Development",
    ),
    (
        ["frontend engineer", "frontend developer", "front end"],
        "Frontend Engineer",
        "Frontend Development",
        ["html", "css", "javascript", "typescript", "react", "nextjs"],
        "Frontend Development",
    ),
    (
        ["ai engineer", "ai developer", "machine learning engineer", "ml engineer"],
        "AI Engineer",
        "AI Engineering",
        ["llm", "agents", "mcp", "langchain", "rag", "python"],
        "AI Infrastructure",
    ),
    (
        ["devops engineer", "sre", "site reliability engineer", "platform engineer"],
        "DevOps Engineer",
        "DevOps",
        ["kubernetes", "docker", "terraform", "observability", "ci/cd", "aws"],
        "Cloud Engineering",
    ),
]

# Domain phrases extracted after prepositions such as "in", "about", "for".
# Each tuple is (list of matching phrases, domain/primary, persona, topics, domain label).
_DOMAIN_RULES: list[tuple[list[str], str, str, list[str], str]] = [
    (
        ["software testing", "qa engineering", "quality engineering", "test automation"],
        "Software Testing",
        "QA Engineer",
        ["playwright", "selenium", "test automation", "api testing", "performance testing", "ci/cd testing", "quality engineering", "cypress"],
        "Quality Engineering",
    ),
    (
        ["cloud computing", "cloud engineering", "cloud infrastructure"],
        "Cloud Computing",
        "Cloud Engineer",
        ["aws", "azure", "gcp", "kubernetes", "docker", "serverless", "terraform", "cloud infrastructure"],
        "Cloud Engineering",
    ),
    (
        ["databases", "database engineering", "database systems"],
        "Databases",
        "Database Engineer",
        ["postgresql", "mongodb", "mysql", "redis", "sql", "database design", "query optimization"],
        "Backend Development",
    ),
    (
        ["data engineering", "data pipelines"],
        "Data Engineering",
        "Data Engineer",
        ["apache spark", "apache kafka", "dbt", "airflow", "etl", "data pipelines"],
        "Data Engineering",
    ),
    (
        ["machine learning", "ml engineering", "deep learning"],
        "Machine Learning",
        "ML Engineer",
        ["pytorch", "tensorflow", "scikit-learn", "llm", "model deployment", "mlops"],
        "AI Infrastructure",
    ),
]


def _extract_domain(query: str) -> tuple[str, str, list[str], str, str] | None:
    """Extract a domain from prepositional phrases like 'in software testing'."""
    q = query.lower()
    # Look for "in X", "about X", "for X" and stop at sentence end or next preposition.
    matches = re.findall(r"\b(?:in|about|for)\s+([^?.!]+?)(?:\?|\.|!|$|\s+(?:in|about|for|to|and|or)\s)", q)
    for raw in matches:
        cleaned = " ".join(w for w in raw.split() if w not in _STOPWORDS).strip()
        for phrases, primary, persona, topics, domain in _DOMAIN_RULES:
            if any(_query_contains_phrase(query, p) for p in phrases):
                extras = _content_terms(query, exclude={primary.lower()})
                all_topics = _dedupe_topics([primary] + topics[:8] + extras[:4])
                return primary, cleaned, all_topics, domain, persona
        # Use the cleaned phrase only if it contains a technology or domain term.
        if cleaned and len(cleaned) > 1:
            # Heuristic: if the cleaned phrase is a single word and not a generic noun, accept it.
            words = cleaned.split()
            if len(words) == 1 and words[0] in _STOPWORDS:
                continue
            primary = cleaned.title()
            extras = _content_terms(query, exclude={cleaned.lower()})
            return primary, cleaned, _dedupe_topics([cleaned, f"{cleaned} fundamentals", f"{cleaned} ecosystem"] + extras[:4]), "General Technology", "Engineer"
    return None


def _keyword_intent(query: str) -> str:
    q = query.lower()
    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if _query_contains_phrase(q, kw):
                scores[intent] = scores.get(intent, 0) + len(kw)
    if scores:
        return max(scores, key=scores.get)  # type: ignore[arg-type]
    return "Opportunity Discovery"


def _clean_words(query: str) -> list[str]:
    return [
        w.strip("?.!,'\"")
        for w in query.lower().split()
        if len(w) > 1 and w.strip("?.!,'\"") not in _STOPWORDS
    ]


def _title_case_topic(text: str) -> str:
    """Return a display-ready topic phrase, preserving short acronyms as uppercase."""
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in text.split())


def _concept_registry() -> ConceptRegistry:
    """Return a deterministic-only registry for intent-side normalization."""
    return ConceptRegistry(use_llm=False)


def _dedupe_topics(topics: list[str]) -> list[str]:
    """Return topics deduplicated and canonicalized by the concept registry."""
    registry = _concept_registry()
    return registry.dedupe(topics)


def _query_contains_phrase(query: str, term: str) -> bool:
    """Match a rule term against the query using whole-word boundaries for
    single-word terms and substring matching for multi-word phrases.
    """
    lowered = query.lower()
    if " " in term:
        return term in lowered
    return bool(re.search(r"\b" + re.escape(term) + r"\b", lowered))


def _content_terms(query: str, exclude: set[str] | None = None) -> list[str]:
    """Return meaningful content words from the query, excluding a primary term."""
    excluded = exclude or set()
    return [w for w in _clean_words(query) if w not in excluded]


def _keyword_tech(query: str) -> tuple[str, list[str], str, list[str], str]:
    q = query.lower()
    domain_result = _extract_domain(query)
    if domain_result:
        primary, cleaned, topics, domain_label, persona = domain_result
        # If the extracted domain is too generic, look for a known technology term first.
        if domain_label == "General Technology":
            for terms, tech, themes, tech_domain in _TECHNOLOGY_RULES:
                if any(_query_contains_phrase(q, t) for t in terms):
                    extras = _content_terms(query, exclude={tech.lower()})
                    phrase = " ".join([tech.lower()] + extras[:3])
                    search_themes = _dedupe_topics([tech.lower()] + themes[:6] + extras[:2])
                    topics = _dedupe_topics(([phrase] if extras else []) + [tech.lower()] + extras[:4])
                    return tech, search_themes, tech_domain, topics, "Engineer"
        themes = _dedupe_topics([primary] + [t for t in topics[:4]])
        return primary, themes, domain_label, _dedupe_topics([primary] + topics[:8]), persona
    for role_terms, persona, primary, topics, domain_label in _ROLE_RULES:
        if any(_query_contains_phrase(q, t) for t in role_terms):
            extras = _content_terms(query, exclude={primary.lower()})
            phrase = " ".join([primary.lower()] + extras[:3])
            search_themes = _dedupe_topics([primary] + [t for t in topics[:4]] + extras[:2])
            deduped_topics = _dedupe_topics(([phrase] if extras else []) + [primary] + topics[:8] + extras[:4])
            return primary, search_themes, domain_label, deduped_topics, persona
    for terms, tech, themes, domain_label in _TECHNOLOGY_RULES:
        if any(_query_contains_phrase(q, t) for t in terms):
            extras = _content_terms(query, exclude={tech.lower()})
            phrase = " ".join([tech.lower()] + extras[:3])
            search_themes = _dedupe_topics([tech.lower()] + themes[:6] + extras[:2])
            topics = _dedupe_topics(([phrase] if extras else []) + [tech.lower()] + extras[:4])
            return tech, search_themes, domain_label, topics, "Engineer"
    # Fall back to extracting the specific noun phrase from the query so multi-word
    # topics like "AI observability" are preserved instead of collapsing to a generic word.
    raw_primary = _extract_primary_topic(query, None)
    primary = _title_case_topic(raw_primary)
    primary_lower = primary.lower()
    extras = _content_terms(query, exclude={primary_lower})
    phrase = " ".join([primary_lower] + extras[:3])
    search_themes = _dedupe_topics([primary_lower, f"{primary_lower} development", f"{primary_lower} ecosystem"] + extras[:2])
    topics = _dedupe_topics(([phrase] if extras else []) + [primary_lower] + extras[:4])
    return primary, search_themes, "General Technology", topics, "Engineer"


def _rule_based(query: str) -> dict[str, Any]:
    intent = _keyword_intent(query)
    tech, themes, domain, topics, persona = _keyword_tech(query)
    topics = _dedupe_topics(topics)
    themes = _dedupe_topics(themes)[:6]
    return {
        "intent": intent,
        "confidence": 0.7,
        "primary_technology": tech,
        "persona_name": persona,
        "domain": domain,
        "topics": topics,
        "search_themes": themes,
        "tavily_query": f"{' '.join(topics[:3])} {intent.lower()} 2026",
    }


def classify_intent(query: str) -> dict[str, Any]:
    """Classify user query intent. Prefer Ollama; fall back to keyword rules."""
    q_lower = query.lower()
    start_time = time.time()

    # Fast-path: opportunity discovery patterns
    opportunity_patterns = [
        r"what opportunities (?:exist )?(?:in|for|with)\s+",
        r"opportunities (?:in|for|with)\s+",
        r"opportunities in\s+",
        r"opportunities for\s+",
    ]
    if any(re.search(pattern, q_lower) for pattern in opportunity_patterns):
        logger.info("Intent classification: fast-path opportunity discovery (%.2fs)", time.time() - start_time)
        fallback = _keyword_tech(query)
        f_primary, f_themes, f_domain, f_topics, f_persona = fallback
        themes = _dedupe_topics(f_themes[:6])
        topics = _dedupe_topics([f_primary] + f_topics[:8])
        return {
            "intent": "Opportunity Discovery",
            "confidence": 0.95,
            "primary_technology": f_primary,
            "persona_name": f_persona,
            "domain": f_domain,
            "topics": topics,
            "search_themes": themes,
            "tavily_query": f"{' '.join(topics[:3])} opportunity discovery 2026",
        }

    # Priority check: "worth learning" patterns should route to learning/career intents
    learning_patterns = [
        "worth learning",
        "should i learn",
        "is it worth",
        "how to learn",
        "learning roadmap",
        "should i study",
    ]
    if any(pattern in q_lower for pattern in learning_patterns):
        # Override to Skill Learning or Career Development
        if "career" in q_lower or "job" in q_lower or "salary" in q_lower:
            base_intent = "Career Development"
        else:
            base_intent = "Skill Learning"

        # Use keyword rules for technology extraction
        fallback = _keyword_tech(query)
        f_primary, f_themes, f_domain, f_topics, f_persona = fallback
        themes = _dedupe_topics(f_themes[:6])
        topics = _dedupe_topics([f_primary] + f_topics[:8])

        logger.info("Intent classification: fast-path learning (%.2fs)", time.time() - start_time)
        return {
            "intent": base_intent,
            "confidence": 0.9,
            "primary_technology": f_primary,
            "persona_name": f_persona,
            "domain": f_domain,
            "topics": topics,
            "search_themes": themes,
            "tavily_query": f"{' '.join(topics[:3])} {base_intent.lower()} 2026",
        }

    # Check for explicit comparison conjunctions before classifying as technology_comparison
    comparison_conjunctions = [" vs ", " versus ", " compared to ", " or "]
    has_explicit_comparison = any(conj in q_lower for conj in comparison_conjunctions)

    prompt = (
        "You are an intent classifier for an opportunity discovery engine.\n"
        f"Query: {query}\n"
        f"Classify into one of: {', '.join(INTENT_TYPES)}.\n"
        "Return JSON with keys: intent (string), confidence (0-1), primary_technology (string), "
        "domain (string), topics (list of 3-6 individual technology keywords), "
        "search_themes (list of 3-6 short GitHub search strings), "
        "tavily_query (one refined web search query)."
    )

    llm_start = time.time()
    raw = _ollama_generate(prompt, format="json")
    llm_duration = time.time() - llm_start
    logger.info("Intent classification LLM call: %.2fs", llm_duration)

    if raw:
        try:
            parsed = json.loads(raw.strip())
            if not isinstance(parsed, dict):
                raise ValueError("LLM intent JSON was not a JSON object")
            intent = parsed.get("intent", _keyword_intent(query))
            if intent not in INTENT_TYPES:
                intent = _keyword_intent(query)

            # Override technology_comparison if no explicit comparison conjunctions
            if intent == "Technology Evaluation" and not has_explicit_comparison:
                intent = _keyword_intent(query)

            fallback = _keyword_tech(query)
            f_primary, f_themes, f_domain, f_topics, f_persona = fallback

            # When the keyword rules know the technology, prefer their focused topics
            # and search themes over broad, often hallucinated Ollama expansions.
            if f_domain != "General Technology":
                primary = f_primary
                topics = f_topics
                themes = f_themes
            else:
                themes = parsed.get("search_themes") or f_themes
                topics = parsed.get("topics") or f_topics
                primary = parsed.get("primary_technology", "") or f_primary

            if not isinstance(themes, list):
                themes = [str(themes)]
            if not isinstance(topics, list):
                topics = [str(topics)]
            topics = [
                t for t in topics
                if str(t).lower() not in _STOPWORDS and len(str(t)) > 1
            ]
            primary_clean = primary.strip().lower()
            if not primary or primary_clean in _STOPWORDS or len(primary_clean) < 2:
                primary = f_primary
            themes = _dedupe_topics(themes)
            topics = _dedupe_topics([primary] + topics)

            logger.info("Intent classification total: %.2fs (LLM: %.2fs)", time.time() - start_time, llm_duration)
            return {
                "intent": intent,
                "confidence": float(parsed.get("confidence", 1.0)),
                "primary_technology": primary,
                "persona_name": parsed.get("persona_name") or f_persona,
                "domain": parsed.get("domain", "") or f_domain,
                "topics": topics,
                "search_themes": themes[:6],
                "tavily_query": parsed.get("tavily_query", f"{' '.join(topics[:3])} {intent.lower()} 2026"),
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Ollama intent parse failed, using rules")

    logger.info("Intent classification: rule-based fallback (%.2fs)", time.time() - start_time)
    return _rule_based(query)


def _disambiguate_entity(
    entity: str,
    query: str,
    topics: list[str],
) -> tuple[str, float, list[str]]:
    """Disambiguate entities using evidence from Context7 and GitHub.

    Returns (disambiguated_entity, confidence, supporting_signals).
    """
    entity_lower = entity.lower()
    if entity_lower != "mcp":
        return entity, 1.0, []

    # For MCP, check evidence to determine if it's Model Context Protocol
    supporting_signals: list[str] = []
    confidence = 0.5

    # Check Context7 for evidence of Model Context Protocol
    context7_signals = [
        "model context protocol",
        "anthropic",
        "ai agent",
        "tool calling",
        "agent framework",
        "modelcontextprotocol.io",
        "claude",
        "llm tooling",
    ]

    # Check GitHub for evidence
    github_signals = [
        "modelcontextprotocol",
        "anthropics/modelcontextprotocol",
        "mcp server",
        "mcp client",
        "mcp tool",
    ]

    def _check_signals(text: str, signals: list[str], source: str) -> None:
        matched = {sig for sig in signals if sig in text}
        for sig in matched:
            supporting_signals.append(f"{source}: {sig}")

    # Use Context7 to check for evidence
    try:
        context7_result = call_tool("context7", "search", {"query": entity, "limit": 5})
        if context7_result:
            if hasattr(context7_result, "__iter__") and not isinstance(context7_result, str):
                for result in context7_result:
                    _check_signals(str(result).lower(), context7_signals, "Context7")
            else:
                _check_signals(str(context7_result).lower(), context7_signals, "Context7")
            confidence = min(1.0, confidence + 0.15 * len({s.split(": ", 1)[1] for s in supporting_signals if s.startswith("Context7:")}))
    except Exception as e:
        logger.warning("Context7 disambiguation failed: %s", e)

    # Use GitHub to check for evidence
    try:
        github_result = call_tool("github", "search_repos", {"query": f"{entity} model context", "limit": 3})
        if github_result:
            if hasattr(github_result, "__iter__") and not isinstance(github_result, str):
                for result in github_result:
                    _check_signals(str(result).lower(), github_signals, "GitHub")
            else:
                _check_signals(str(github_result).lower(), github_signals, "GitHub")
            confidence = min(1.0, confidence + 0.15 * len({s.split(": ", 1)[1] for s in supporting_signals if s.startswith("GitHub:")}))
    except Exception as e:
        logger.warning("GitHub disambiguation failed: %s", e)

    # Check if the query itself contains context clues
    query_lower = query.lower()
    ai_context = ["ai", "agent", "llm", "anthropic", "claude", "tool", "integration"]
    if any(ctx in query_lower for ctx in ai_context):
        confidence += 0.2
        supporting_signals.append("Query context: AI/agent-related")

    # Cap confidence at 1.0
    confidence = min(confidence, 1.0)

    # If confidence is high for Model Context Protocol, return that
    if confidence >= 0.6:
        return "Model Context Protocol (MCP)", confidence, supporting_signals
    else:
        # Otherwise return original entity with lower confidence
        return entity, confidence, supporting_signals
