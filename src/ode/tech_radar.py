"""Technology Discovery: discover trending technologies and opportunities."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from ode.config.timeouts import TECH_RADAR_TIMEOUT
from ode.llm import _ollama_generate
from ode.mcp_client import call_tool
from ode.technology_resolver import TECHNOLOGY_REGISTRY

logger = logging.getLogger(__name__)

# Mutex to prevent concurrent tech radar building
_tech_radar_lock = threading.Lock()

CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

_tech_radar_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _fetch_github_repos(query: str = "") -> list[dict[str, Any]]:
    """Fetch trending repositories from GitHub MCP."""
    try:
        result = call_tool("github", "search_repositories", {"query": query or "stars:>1000", "limit": 50})
        if result and isinstance(result, dict) and "repositories" in result:
            return result["repositories"]
    except Exception as e:
        logger.warning("GitHub MCP fetch failed: %s", e)
    return []


def _fetch_tavily_articles(query: str = "") -> list[dict[str, Any]]:
    """Fetch technology articles from Tavily MCP."""
    try:
        result = call_tool("tavily", "search", {"query": query or "emerging technology trends 2024", "max_results": 20})
        if result and isinstance(result, dict) and "results" in result:
            return result["results"]
    except Exception as e:
        logger.warning("Tavily MCP fetch failed: %s", e)
    return []


def _fetch_hacker_news_stories(query: str = "") -> list[dict[str, Any]]:
    """Fetch stories from Hacker News MCP."""
    try:
        result = call_tool("hackernews", "search", {"query": query or "technology programming", "limit": 20})
        if result and isinstance(result, dict) and "stories" in result:
            return result["stories"]
    except Exception as e:
        logger.warning("Hacker News MCP fetch failed: %s", e)
    return []


def _extract_technology_from_name(name: str) -> str | None:
    """Extract technology name from repository name."""
    # Remove owner prefix
    if "/" in name:
        name = name.split("/")[-1]

    # Remove common suffixes
    name = name.replace("-cli", "").replace("-api", "").replace("-sdk", "").replace("-lib", "")
    name = name.replace(".js", "").replace(".py", "").replace(".rs", "").replace(".go", "")

    # Only return if it looks like a technology name (no spaces, reasonable length)
    if len(name) >= 3 and len(name) <= 30 and " " not in name:
        return name.lower()
    return None


def _extract_technology_from_title(title: str) -> list[str]:
    """Extract technology names from article titles."""
    technologies = []

    # Known technology patterns
    tech_patterns = [
        r"\b(MCP|Model Context Protocol)\b",
        r"\b(AI Agent|AI Agents)\b",
        r"\b(LangGraph)\b",
        r"\b(A2A|Agent-to-Agent)\b",
        r"\b(LLM|Large Language Model)\b",
        r"\b(RAG|Retrieval Augmented Generation)\b",
        r"\b(React|Vue|Angular|Svelte)\b",
        r"\b(Next\.js|Nuxt\.js)\b",
        r"\b(Docker|Kubernetes|K8s)\b",
        r"\b(AWS|Azure|GCP)\b",
        r"\b(PostgreSQL|MongoDB|Redis)\b",
        r"\b(GraphQL|gRPC)\b",
        r"\b(Rust|Go|Python|TypeScript)\b",
        r"\b(Observability|Monitoring)\b",
        r"\b(DevOps|Platform Engineering)\b",
        r"\b(Infrastructure as Code|IaC)\b",
        r"\b(Serverless|Edge Computing)\b",
        r"\b(WebAssembly|Wasm)\b",
        r"\b(Blockchain|Web3|DeFi)\b",
    ]

    import re
    for pattern in tech_patterns:
        matches = re.findall(pattern, title, re.IGNORECASE)
        for match in matches:
            tech = match.lower()
            if tech not in technologies:
                technologies.append(tech)

    return technologies


def _get_opportunity_count(db_path: str, technology: str, aliases: list[str] | None = None) -> int:
    """Count opportunities related to a technology."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Build search terms: technology name + aliases
        search_terms = [technology] + (aliases or [])

        # Count opportunities matching any search term
        count = 0
        for term in search_terms:
            cur.execute(
                "SELECT COUNT(*) FROM opportunities WHERE title LIKE ? OR description LIKE ?",
                (f"%{term}%", f"%{term}%")
            )
            count += cur.fetchone()[0]

        conn.close()
        return count
    except Exception as e:
        logger.warning("Failed to get opportunity count for %s: %s", technology, e)
        return 0


def _get_project_count(db_path: str, technology: str, aliases: list[str] | None = None) -> int:
    """Count GitHub projects related to a technology from signals."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Build search terms: technology name + aliases
        search_terms = [technology] + (aliases or [])

        # Count distinct GitHub repositories matching any search term
        count = 0
        for term in search_terms:
            cur.execute(
                "SELECT COUNT(DISTINCT entity) FROM signals WHERE source_type LIKE '%github%' AND (entity LIKE ? OR tags LIKE ?)",
                (f"%{term}%", f"%{term}%")
            )
            count += cur.fetchone()[0]

        conn.close()
        return count
    except Exception as e:
        logger.warning("Failed to get project count for %s: %s", technology, e)
        return 0


def _calculate_trend_score(tech: str, sources: list[dict[str, Any]]) -> float:
    """Calculate a trend score based on signal strength."""
    score = 0.0

    for source in sources:
        if isinstance(source, dict):
            # GitHub: stars, forks
            if "stargazers_count" in source:
                stars = source["stargazers_count"]
                if isinstance(stars, (int, float)):
                    score += min(30, stars / 1000 * 30)
            if "forks_count" in source:
                forks = source["forks_count"]
                if isinstance(forks, (int, float)):
                    score += min(15, forks / 500 * 15)

            # Articles/stories: points, comments
            if "points" in source:
                points = source["points"]
                if isinstance(points, (int, float)):
                    score += min(20, points / 10 * 20)
            if "comments" in source:
                comments = source["comments"]
                if isinstance(comments, (int, float)):
                    score += min(15, comments / 5 * 15)

            # Recency bonus
            date_field = source.get("created_at") or source.get("published_at") or source.get("updated_at")
            if date_field:
                try:
                    date = datetime.fromisoformat(str(date_field).replace("Z", "+00:00"))
                    days_ago = (datetime.now(timezone.utc) - date).days
                    if days_ago < 7:
                        score += 20
                    elif days_ago < 30:
                        score += 10
                    elif days_ago < 90:
                        score += 5
                except:
                    pass

    return min(100, score)


def _get_momentum_label(score: float) -> str:
    """Get momentum label from score."""
    if score >= 70:
        return "High"
    elif score >= 40:
        return "Medium"
    return "Low"


def _generate_tech_summary(tech: str, sources: list[dict[str, Any]]) -> str:
    """Generate a summary for a technology using LLM."""
    # Collect relevant information
    repo_info = []
    article_info = []

    for source in sources:
        if isinstance(source, dict):
            if "name" in source and "description" in source:
                repo_info.append(f"{source['name']}: {source['description']}")
            if "title" in source:
                article_info.append(source['title'])

    prompt = f"""
Summarize the technology "{tech}" in 1-2 sentences based on the following information.

Repositories:
{chr(10).join(repo_info[:3]) if repo_info else "None"}

Articles:
{chr(10).join(article_info[:3]) if article_info else "None"}

Focus on what the technology is and what it's used for.
Keep it concise and technical. Maximum 2 sentences.
"""

    try:
        response = _ollama_generate(prompt, format="text")
        if response:
            summary = response.strip()
            # Limit to 2 sentences
            sentences = summary.split(". ")
            if len(sentences) > 2:
                summary = ". ".join(sentences[:2]) + "."
            return summary
    except Exception as e:
        logger.warning("LLM summary generation failed for %s: %s", tech, e)

    return f"{tech} is a technology with growing adoption."


def _extract_projects(tech: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract projects being built for a technology."""
    projects = []

    for source in sources:
        if isinstance(source, dict):
            if "name" in source and "description" in source:
                # Only include if it's a GitHub repo
                if "stargazers_count" in source:
                    projects.append({
                        "name": source["name"],
                        "description": source.get("description", ""),
                        "stars": source.get("stargazers_count", 0),
                        "url": source.get("html_url", source.get("url", "")),
                    })

    # Sort by stars and take top 5
    projects.sort(key=lambda x: x.get("stars", 0), reverse=True)
    return projects[:5]


def _calculate_ecosystem_activity(tech: str, sources: list[dict[str, Any]]) -> dict[str, str]:
    """Calculate ecosystem activity metrics."""
    github_count = len([s for s in sources if isinstance(s, dict) and "stargazers_count" in s])
    hn_count = len([s for s in sources if isinstance(s, dict) and "points" in s])
    tavily_count = len([s for s in sources if isinstance(s, dict) and "url" in s and "tavily" in str(s.get("source", "")).lower()])

    def get_level(count: int) -> str:
        if count == 0:
            return "None"
        elif count <= 3:
            return "Low"
        elif count <= 8:
            return "Moderate"
        elif count <= 15:
            return "High"
        else:
            return "Very High"

    # Calculate source diversity bonus
    source_types = set()
    for s in sources:
        if isinstance(s, dict):
            if "stargazers_count" in s:
                source_types.add("github")
            elif "points" in s:
                source_types.add("hackernews")
            elif "url" in s and "tavily" in str(s.get("source", "")).lower():
                source_types.add("tavily")

    # Boost levels if multiple source types present
    diversity_bonus = len(source_types) - 1  # 0 for single source, 1+ for multiple

    def apply_diversity_bonus(base_level: str, count: int) -> str:
        """Boost activity level if multiple source types present."""
        if diversity_bonus <= 0:
            return base_level

        levels = ["None", "Low", "Moderate", "High", "Very High"]
        current_idx = levels.index(base_level)
        new_idx = min(current_idx + diversity_bonus, len(levels) - 1)
        return levels[new_idx]

    dev_activity = apply_diversity_bonus(get_level(github_count), github_count)
    community_interest = apply_diversity_bonus(get_level(hn_count), hn_count)
    industry_attention = apply_diversity_bonus(get_level(tavily_count), tavily_count)

    return {
        "developer_activity": dev_activity,
        "community_interest": community_interest,
        "industry_attention": industry_attention,
    }


def _extract_resources(tech: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract top resources for a technology."""
    resources = []

    for source in sources:
        if isinstance(source, dict):
            if "url" in source and "title" in source:
                resources.append({
                    "title": source["title"],
                    "url": source["url"],
                    "source": source.get("source", "Unknown"),
                })

    # Take top 5
    return resources[:5]


def build_tech_radar(refresh: bool = False, db_path: str = "/workspaces/ODE/ode.db") -> dict[str, Any]:
    """Build technology discovery data from multiple sources."""
    with _tech_radar_lock:
        cache_key = "tech_radar"
        now = time.time()

        if not refresh and cache_key in _tech_radar_cache:
            cached_time, cached_data = _tech_radar_cache[cache_key]
            if now - cached_time < CACHE_TTL_SECONDS:
                return cached_data

        logger.info("Building technology discovery...")

        # Fetch from multiple sources for trend scoring
        github_repos = _fetch_github_repos()
        tavily_articles = _fetch_tavily_articles()
        hn_stories = _fetch_hacker_news_stories()

        all_sources = github_repos + tavily_articles + hn_stories

        # Extract technology names from sources
        tech_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for source in all_sources:
            if isinstance(source, dict):
                # From GitHub repos
                if "name" in source:
                    tech = _extract_technology_from_name(source["name"])
                    if tech:
                        tech_sources[tech].append(source)

                # From articles/stories
                if "title" in source:
                    techs = _extract_technology_from_title(source["title"])
                    for tech in techs:
                        tech_sources[tech].append(source)

        # Build technology entries from TECHNOLOGY_REGISTRY (primary source of truth)
        technologies = []
        for key, profile in TECHNOLOGY_REGISTRY.items():
            tech_name = profile.canonical_name.lower()
            aliases = [a.lower() for a in profile.aliases] if profile.aliases else []

            # Get real counts from database
            opportunity_count = _get_opportunity_count(db_path, tech_name, aliases)
            project_count = _get_project_count(db_path, tech_name, aliases)

            # Calculate trend score from signals if available
            sources_list = tech_sources.get(tech_name, [])
            if not sources_list:
                # Try matching by aliases
                for alias in aliases:
                    if alias in tech_sources:
                        sources_list = tech_sources[alias]
                        break

            if sources_list:
                score = _calculate_trend_score(tech_name, sources_list)
                summary = _generate_tech_summary(tech_name, sources_list)
                projects = _extract_projects(tech_name, sources_list)
                ecosystem_activity = _calculate_ecosystem_activity(tech_name, sources_list)
                resources = _extract_resources(tech_name, sources_list)
            else:
                # Use default values if no signals
                score = 50 + (opportunity_count * 2) + (project_count * 0.5)  # Base score boosted by real data
                score = min(100, score)
                summary = profile.description
                projects = []
                ecosystem_activity = {
                    "developer_activity": "Low" if project_count < 5 else "Moderate" if project_count < 15 else "High",
                    "community_interest": "Low" if opportunity_count < 3 else "Moderate" if opportunity_count < 8 else "High",
                    "industry_attention": "Low" if opportunity_count < 3 else "Moderate" if opportunity_count < 8 else "High",
                }
                resources = []

            technologies.append({
                "name": profile.canonical_name,
                "trend_score": round(score, 1),
                "momentum": _get_momentum_label(score),
                "opportunity_count": opportunity_count,
                "summary": summary,
                "projects": projects,
                "ecosystem_activity": ecosystem_activity,
                "resources": resources,
            })

        # Sort by trend score
        def get_score(x: dict[str, Any]) -> float:
            val = x.get("trend_score", 0)
            if isinstance(val, (int, float)):
                return float(val)
            return 0.0
        technologies.sort(key=get_score, reverse=True)

        # Take top 15
        technologies = technologies[:15]

        result = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "technologies": technologies,
        }

        _tech_radar_cache[cache_key] = (now, result)
        logger.info("Technology discovery built with %d technologies", len(technologies))

        return result
