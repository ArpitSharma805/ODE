"""Retrieval planning, source authority, and relevance ranking for ODE signals."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ode.llm import _ollama_generate

logger = logging.getLogger(__name__)


@dataclass
class RetrievalPlan:
    """A query-specific plan for what to retrieve and how to filter it."""

    intent: str = "Opportunity Discovery"
    primary: str = ""
    aliases: list[str] = field(default_factory=list)
    github_queries: list[str] = field(default_factory=list)
    tavily_queries: list[str] = field(default_factory=list)
    expected_result_type: str = "general"
    recency_days: int = 365
    min_relevance: float = 0.1
    min_authority: float = 0.2


MIN_REPO_STARS = 10

_TUTORIAL_REPO_KEYWORDS = {
    "learn-",
    "tutorial",
    "tutorials",
    "assignment",
    "assignments",
    "course",
    "courses",
    "practice",
    "homework",
    "exercise",
    "exercises",
    "example",
    "examples",
    "sample",
    "samples",
    "curriculum",
    "bootcamp",
    "workbook",
    "lecture",
    "lectures",
    "lesson",
    "lessons",
}

_LEARNING_INTENTS = {"Skill Learning", "Career Development"}

# Words that should not become the primary topic when extracting from free-form queries.
_QUERY_STOPWORDS = {
    "what", "which", "how", "should", "would", "could", "can", "will", "are", "is",
    "the", "a", "an", "in", "on", "for", "to", "and", "or", "of", "with", "about",
    "new", "next", "best", "good", "worth", "learn", "learning", "use", "using",
    "product", "opportunities", "opportunity", "ideas", "idea", "business", "market",
    "exist", "exists", "available", "some", "any", "there", "i", "we", "you", "they",
    "this", "that", "these", "those", "do", "does", "did", "have", "has", "had",
    "be", "been", "being", "was", "were", "it", "its", "my", "our", "your", "their",
}


def _extract_primary_topic(query: str, intent: dict[str, Any] | None) -> str:
    """Return the most specific multi-word topic phrase from the query or intent."""
    if intent:
        primary = str(intent.get("primary_technology") or "").strip()
        # Ensure the primary technology is not a stopword
        if primary and primary.lower() not in _QUERY_STOPWORDS and len(primary) >= 2:
            return primary

    q = re.sub(r"[^\w\s]", " ", query).lower().strip()

    # Handle "Is <Tech>..." patterns - extract the technology after "Is"
    is_match = re.match(r"^is\s+([a-z][a-z0-9\s]{2,30})", q)
    if is_match:
        after_is = is_match.group(1).strip()
        words = [w for w in after_is.split() if w not in _QUERY_STOPWORDS and len(w) >= 2]
        if words:
            return " ".join(words[:4])

    # Prefer the noun phrase after a preposition (e.g. "opportunities in AI agent security").
    for marker in ("opportunities in", "opportunities for", "in", "about", "for"):
        if marker in q:
            tail = q.split(marker, 1)[1].strip()
            words = [w for w in tail.split() if w not in _QUERY_STOPWORDS and len(w) >= 2]
            if len(words) >= 2:
                return " ".join(words[:4])

    # Otherwise keep the leading meaningful tokens up to four words.
    words = [w for w in q.split() if w not in _QUERY_STOPWORDS and len(w) >= 2]
    if len(words) >= 2:
        return " ".join(words[:4])
    return query.strip()


def _dedupe_preserve(items: list[str]) -> list[str]:
    """Deduplicate strings case-insensitively while keeping the first original form."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = re.sub(r"[^\w\s]", " ", item).lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def is_quality_repo(repo: dict[str, Any], intent: str = "Opportunity Discovery") -> bool:
    """Return True when a GitHub repo clears the quality bar for analysis.

    Repos must meet the minimum star floor. Tutorial and homework repos are
    excluded unless the intent is explicitly learning-oriented.
    """
    stars = int(repo.get("stargazers_count") or 0)
    if stars < MIN_REPO_STARS:
        return False

    text = f"{repo.get('name', '')} {repo.get('description', '')} {repo.get('full_name', '')}".lower()
    has_tutorial_marker = any(
        re.search(rf"\b{re.escape(k)}(-|\b)", text) for k in _TUTORIAL_REPO_KEYWORDS
    )
    if has_tutorial_marker and intent not in _LEARNING_INTENTS:
        return False
    return True


# Recognized authoritative organizations / GitHub owners.
# These are generic sources (cloud providers, open-source foundations, major tooling orgs).
_AUTHORITATIVE_ORGS = {
    "google",
    "microsoft",
    "azure",
    "amazon",
    "aws",
    "apple",
    "meta",
    "nvidia",
    "oracle",
    "ibm",
    "salesforce",
    "adobe",
    "cisco",
    "cloudflare",
    "hashicorp",
    "datadog",
    "elastic",
    "mongodb",
    "apache",
    "cncf",
    "golang",
    "python",
    "pallets",
    "vercel",
    "langchain-ai",
    "openai",
    "anthropics",
    "huggingface",
    "pytorch",
    "tensorflow",
    "rust-lang",
    "openssl",
    "mozilla",
    "postgresql",
    "redis",
}

# Domain authority tiers for Tavily / web results.
_AUTHORITATIVE_DOMAINS = {
    "github.com": 1.0,
    "stackoverflow.com": 0.95,
    "docs.rs": 0.95,
    "pkg.go.dev": 0.95,
    "godoc.org": 0.95,
    "developer.mozilla.org": 0.95,
    "medium.com": 0.6,
    "dev.to": 0.7,
    "news.ycombinator.com": 0.6,
    "reddit.com": 0.5,
    "towardsdatascience.com": 0.6,
    "freecodecamp.org": 0.75,
    "codecademy.com": 0.75,
    "wikipedia.org": 0.9,
}


def _normalize(text: str) -> str:
    """Normalize text for token comparison."""
    return re.sub(r"[^\w\s]", " ", text.lower())


def _tokens(text: str) -> set[str]:
    """Return a set of alphanumeric tokens with length > 2."""
    return {t for t in _normalize(text).split() if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap_ratio(text: str, terms: set[str]) -> float:
    """Fraction of provided terms that appear in text."""
    if not terms:
        return 0.0
    text_tokens = _tokens(text)
    matches = terms & text_tokens
    return len(matches) / len(terms)


def _owner_from_full_name(full_name: str) -> str:
    """Extract the GitHub owner/organization from a repo full name."""
    return full_name.split("/")[0].lower() if "/" in full_name else ""


def _repo_recency_score(pushed_at: str | None) -> float:
    """Return 0-1 recency score; 1.0 for very recent, decaying to 0.0 over 365 days."""
    if not pushed_at:
        return 0.5
    try:
        dt = datetime.fromisoformat(str(pushed_at).replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return 0.5
    if days < 0:
        return 0.5
    return max(0.0, 1.0 - (days / 365.0))


def _domain_authority(url: str) -> float:
    """Return authority score for a web URL based on its domain."""
    if not url:
        return 0.5
    try:
        parsed = urlparse(str(url))
        domain = parsed.netloc.lower().lstrip("www.")
    except Exception:
        return 0.5
    if domain in _AUTHORITATIVE_DOMAINS:
        return _AUTHORITATIVE_DOMAINS[domain]
    # Subdomain match for docs sites, e.g. docs.python.org
    for known, score in _AUTHORITATIVE_DOMAINS.items():
        if domain.endswith(known) or known in domain:
            return score
    return 0.5


def repo_authority(repo: dict[str, Any]) -> float:
    """Compute a 0-1 authority score for a GitHub repository result."""
    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or 0)
    watchers = int(repo.get("watchers_count") or 0)
    issues = int(repo.get("open_issues_count") or 0)
    archived = bool(repo.get("archived"))
    fork = bool(repo.get("fork"))
    owner = _owner_from_full_name(str(repo.get("full_name", "")))

    # Normalize star/fork counts with log scaling; 50k stars ~= 1.0
    star_score = min(1.0, (stars + watchers) / 50_000.0)
    fork_score = min(1.0, forks / 5_000.0)
    issue_score = min(1.0, issues / 1_000.0)

    org_score = 0.7 if owner in _AUTHORITATIVE_ORGS else 0.5
    recency_score = _repo_recency_score(repo.get("pushed_at") or repo.get("updated_at"))

    authority = (
        star_score * 0.35
        + fork_score * 0.15
        + issue_score * 0.05
        + org_score * 0.25
        + recency_score * 0.20
    )

    if archived:
        authority *= 0.5
    if fork:
        authority *= 0.8

    return round(max(0.0, min(1.0, authority)), 3)


def repo_relevance(repo: dict[str, Any], plan: RetrievalPlan) -> float:
    """Compute 0-1 relevance of a repo to the retrieval plan."""
    terms: set[str] = _tokens(" ".join([plan.primary] + plan.aliases))
    text = " ".join(
        [
            str(repo.get("name", "")),
            str(repo.get("description", "")),
            str(repo.get("language", "")),
            str(repo.get("full_name", "")),
            " ".join(str(t) for t in repo.get("topics", [])),
        ]
    )
    overlap = _overlap_ratio(text, terms)
    # Add a small Jaccard boost for multi-word matches
    jaccard = _jaccard(_tokens(text), terms)
    score = 0.7 * overlap + 0.3 * jaccard
    return round(max(0.0, min(1.0, score)), 3)


def article_authority(item: dict[str, Any]) -> float:
    """Compute 0-1 authority for a Tavily/web result."""
    url = str(item.get("url", ""))
    domain_score = _domain_authority(url)
    source_score = float(item.get("score") or 0.75)
    # Combine Tavily's own score (0-1) with domain authority
    return round(max(0.0, min(1.0, 0.5 * source_score + 0.5 * domain_score)), 3)


def article_relevance(item: dict[str, Any], plan: RetrievalPlan) -> float:
    """Compute 0-1 relevance of a Tavily result to the retrieval plan."""
    terms: set[str] = _tokens(" ".join([plan.primary] + plan.aliases))
    text = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("content", ""))[:500],
        ]
    )
    overlap = _overlap_ratio(text, terms)
    jaccard = _jaccard(_tokens(text), terms)
    return round(max(0.0, min(1.0, 0.6 * overlap + 0.4 * jaccard)), 3)


def _llm_retrieval_plan(query: str, intent: dict[str, Any] | None) -> RetrievalPlan | None:
    """Ask Ollama for a structured retrieval plan. Returns None if unavailable."""
    intent_type = (intent or {}).get("intent", "Opportunity Discovery")
    primary = (intent or {}).get("primary_technology", "")
    topics = ", ".join(str(t) for t in (intent or {}).get("topics", []) if t)

    prompt = (
        "You are a retrieval planner for an opportunity discovery engine. "
        "Given the user query, intent, and topics, return a JSON object with:\n"
        '- "primary": the single canonical primary concept (1-3 words)\n'
        '- "aliases": list of 2-5 common aliases/synonyms for that concept\n'
        '- "github_queries": list of 3-5 search strings to use with the GitHub search API\n'
        '- "tavily_queries": list of 2-4 web search queries for market/research evidence\n'
        '- "expected_result_type": one of "repo", "article", "docs", "comparison", "general"\n'
        "Rules: do not hardcode a specific technology unless the query explicitly names one. "
        "For comparison queries, include both sides in aliases and queries. "
        "For learning/career queries, include queries about tutorials, roadmaps, and job demand. "
        "Return only valid JSON.\n\n"
        f"Query: {query}\n"
        f"Intent: {intent_type}\n"
        f"Topics: {topics}\n"
    )

    raw = _ollama_generate(prompt, format="json")
    if not raw:
        return None

    try:
        parsed = json.loads(raw.strip())
        if not isinstance(parsed, dict):
            return None
        return RetrievalPlan(
            intent=intent_type,
            primary=str(parsed.get("primary") or primary or "").strip(),
            aliases=[str(a).strip() for a in parsed.get("aliases", []) if a],
            github_queries=[str(q).strip() for q in parsed.get("github_queries", []) if q],
            tavily_queries=[str(q).strip() for q in parsed.get("tavily_queries", []) if q],
            expected_result_type=str(parsed.get("expected_result_type") or "general").strip().lower(),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("LLM retrieval plan parse failed: %s", exc)
        return None


def _fallback_queries(query: str, intent: dict[str, Any] | None) -> list[str]:
    """Generic fallback query expansion that keeps multi-word topic qualifiers intact."""
    primary = _extract_primary_topic(query, intent)
    intent_type = (intent or {}).get("intent", "")
    primary_lower = primary.lower()

    if "agent" in primary_lower:
        # AI agent security queries need concrete technical search terms to
        # surface rich, specific signals fast on GitHub and Tavily.
        security_tail = "mcp-security" if "mcp" in primary_lower else "langchain-security"
        queries: list[str] = [
            primary,
            "ai-agent-security",
            "prompt-injection",
            "agent-guardrails",
            security_tail,
        ]
    elif intent_type in ("Skill Learning", "Career Development"):
        queries = [primary]
        queries.extend([
            f"{primary} tutorial",
            f"{primary} roadmap",
            f"{primary} best practices",
        ])
    elif intent_type == "Technology Evaluation":
        queries = [primary]
        queries.extend([
            f"{primary} comparison",
            f"{primary} vs",
        ])
    else:
        # Market/opportunity expansions: keep the primary phrase as the head.
        queries = [primary]
        query_lower = query.lower()
        is_security = any(term in query_lower for term in ("security", "vulnerab", "attack"))
        extras = [
            f"{primary} tooling",
            f"{primary} market",
            f"{primary} opportunities",
        ]
        if is_security:
            extras.append(f"{primary} vulnerabilities")
        queries.extend(extras)

    # Guard against any single-word query collapsing the topic. Hyphenated
    # technical terms (e.g. "prompt-injection") are kept intact.
    guarded: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = " ".join(q.split())
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        if len(q.split()) < 2 and "-" not in q:
            q = primary
        guarded.append(q)
    return guarded[:5]


def _fallback_tavily_queries(primary: str) -> list[str]:
    """Return Tavily-friendly web search queries that avoid the short/generic terms
    that tend to return zero or time out on Tavily."""
    head = primary.strip() or ""
    return [
        f"{head} tools market trends 2026",
        f"{head} tooling use cases and vendors",
        f"{head} emerging startups and landscape",
    ]


def build_retrieval_plan(query: str, intent: dict[str, Any] | None = None) -> RetrievalPlan:
    """Return a plan for what to search and how strictly to filter."""
    plan = _llm_retrieval_plan(query, intent)
    if plan is None:
        primary = _extract_primary_topic(query, intent)
        fallback = _fallback_queries(query, intent)
        alias_candidates = [primary]
        alias_candidates.extend((intent or {}).get("topics", []))
        alias_candidates.extend((intent or {}).get("search_themes", []))
        plan = RetrievalPlan(
            intent=(intent or {}).get("intent", "Opportunity Discovery"),
            primary=primary,
            aliases=alias_candidates,
            github_queries=fallback,
            tavily_queries=_fallback_tavily_queries(primary),
        )

    # Preserve the original topic phrases for queries; never collapse a
    # multi-word topic like "AI agent security" to a single generic token.
    plan.primary = " ".join(plan.primary.split())
    plan.aliases = _dedupe_preserve([plan.primary] + plan.aliases)
    plan.github_queries = _dedupe_preserve(plan.github_queries) or _fallback_queries(query, intent)
    plan.tavily_queries = _dedupe_preserve(plan.tavily_queries) or _fallback_tavily_queries(plan.primary)
    return plan


def rank_repos(repos: list[dict[str, Any]], plan: RetrievalPlan) -> list[dict[str, Any]]:
    """Filter and rank GitHub repos by combined authority * relevance."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for repo in repos:
        if not is_quality_repo(repo, plan.intent):
            continue
        authority = repo_authority(repo)
        relevance = repo_relevance(repo, plan)
        if relevance < plan.min_relevance or authority < plan.min_authority:
            continue
        repo["_authority"] = authority
        repo["_relevance"] = relevance
        repo["_retrieval_score"] = round(authority * relevance, 3)
        scored.append((repo["_retrieval_score"], repo))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


def rank_articles(articles: list[dict[str, Any]], plan: RetrievalPlan) -> list[dict[str, Any]]:
    """Filter and rank Tavily/web results by combined authority * relevance."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in articles:
        authority = article_authority(item)
        relevance = article_relevance(item, plan)
        if relevance < plan.min_relevance or authority < plan.min_authority:
            continue
        item["_authority"] = authority
        item["_relevance"] = relevance
        item["_retrieval_score"] = round(authority * relevance, 3)
        scored.append((item["_retrieval_score"], item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]
