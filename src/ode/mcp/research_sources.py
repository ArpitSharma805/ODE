"""Research source search helpers that return ODE-compatible signal dicts.

Each public function searches a different public or key-gated source and returns
raw signals with the keys expected by :mod:`ode.agents.signal_analyst`:
``source``, ``entity``, ``metric``, ``value``, ``evidence_quality``,
``timestamp``, and ``source_url``.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ode.config.timeouts import RESEARCH_SOURCE_TIMEOUT

logger = logging.getLogger(__name__)

# Use centralized timeout configuration
_DEFAULT_TIMEOUT = RESEARCH_SOURCE_TIMEOUT
_USER_AGENT = "ode-research-sources/0.1"

__all__ = [
    "search_hackernews",
    "search_reddit",
    "search_jobs",
    "search_producthunt",
    "search_news",
    "search_firecrawl",
]


def _now() -> str:
    """Return an ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str | None, max_len: int = 2000) -> str:
    """Truncate a string to ``max_len`` characters, appending an ellipsis."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _quality_from_points(points: int | float | None, default: int = 55) -> int:
    """Map an engagement count (points/votes/score) to an evidence quality 0-100."""
    try:
        value = max(0.0, float(points or 0))
    except (TypeError, ValueError):
        return default
    if value == 0:
        return default
    return int(min(100.0, 50.0 + math.log1p(value) * 10.0))


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """Fetch ``url`` and return parsed JSON, or ``None`` on failure."""
    request_headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        req = urllib.request.Request(url, headers=request_headers)
        # Use SSL context that handles certificate verification issues in WSL/dev environments
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT, context=context) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        logger.warning("HTTP %s from %s: %s", exc.code, url, body or exc.reason)
        return None
    except urllib.error.URLError as exc:
        logger.warning("Request to %s failed: %s", url, exc.reason)
        return None
    except json.JSONDecodeError:
        logger.warning("Non-JSON response from %s", url)
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning("Unexpected error fetching %s: %s", url, exc)
        return None


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    """POST JSON to ``url`` and return parsed JSON, or ``None`` on failure."""
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method="POST",
        )
        # Use SSL context that handles certificate verification issues in WSL/dev environments
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT, context=context) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        logger.warning("HTTP %s from POST %s: %s", exc.code, url, body or exc.reason)
        return None
    except urllib.error.URLError as exc:
        logger.warning("POST to %s failed: %s", url, exc.reason)
        return None
    except json.JSONDecodeError:
        logger.warning("Non-JSON response from POST %s", url)
        return None
    except Exception as exc:  # pragma: no cover
        logger.warning("Unexpected error POSTing to %s: %s", url, exc)
        return None


def search_hackernews(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search Hacker News stories using the public Algolia API.

    No API key is required.
    """
    params = {
        "query": query,
        "hitsPerPage": max_results,
        "tags": "story",
    }
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(params)
    data = _http_get_json(url)
    if not isinstance(data, dict):
        return []

    hits = data.get("hits") or []
    now = _now()
    signals: list[dict[str, Any]] = []
    for hit in hits:
        object_id = hit.get("objectID")
        title = str(hit.get("title") or hit.get("story_title") or "Hacker News item")
        item_url = hit.get("url") or ""
        if not item_url and object_id:
            item_url = f"https://news.ycombinator.com/item?id={object_id}"

        points = hit.get("points") or 0
        num_comments = hit.get("num_comments") or 0
        author = hit.get("author") or ""

        parts = [title]
        if item_url:
            parts.append(f"URL: {item_url}")
        if points:
            parts.append(f"{points} points")
        if num_comments:
            parts.append(f"{num_comments} comments")
        if author:
            parts.append(f"by {author}")

        signals.append(
            {
                "source": "hackernews",
                "entity": title,
                "metric": "hackernews_result",
                "value": _truncate(" | ".join(parts)),
                "evidence_quality": _quality_from_points(points),
                "timestamp": now,
                "source_url": item_url,
            }
        )
    return signals


def _get_reddit_auth_headers() -> dict[str, str] | None:
    """Obtain an OAuth app-only token for Reddit, or ``None`` if not configured."""
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.warning(
            "Reddit client credentials not configured; "
            "set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."
        )
        return None

    user_agent = os.getenv("REDDIT_USER_AGENT", _USER_AGENT)
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    token_url = "https://www.reddit.com/api/v1/access_token"

    try:
        payload = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            token_url,
            data=payload,
            headers={
                "Authorization": f"Basic {credentials}",
                "User-Agent": user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            token_data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("Reddit token request failed: %s", exc)
        return None

    access_token = token_data.get("access_token")
    if not access_token:
        logger.warning("Reddit token response missing access_token: %s", token_data)
        return None

    return {"Authorization": f"Bearer {access_token}", "User-Agent": user_agent}


def search_reddit(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search Reddit posts using OAuth app-only authentication.

    Requires ``REDDIT_CLIENT_ID`` and ``REDDIT_CLIENT_SECRET`` environment
    variables. A custom ``REDDIT_USER_AGENT`` may also be supplied.
    """
    headers = _get_reddit_auth_headers()
    if not headers:
        return []

    params = {
        "q": query,
        "limit": max_results,
        "sort": "relevance",
        "type": "link",
    }
    url = "https://oauth.reddit.com/search?" + urllib.parse.urlencode(params)
    data = _http_get_json(url, headers=headers)
    if not isinstance(data, dict):
        return []

    listing = data.get("data") or {}
    children = listing.get("children") or []
    now = _now()
    signals: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        post = child.get("data") or {}
        title = post.get("title") or "Reddit post"
        subreddit = post.get("subreddit") or ""
        score = post.get("score") or 0
        num_comments = post.get("num_comments") or 0
        permalink = post.get("permalink") or ""
        source_url = f"https://www.reddit.com{permalink}" if permalink else (post.get("url") or "")

        parts = [title]
        if subreddit:
            parts.append(f"r/{subreddit}")
        if score:
            parts.append(f"score {score}")
        if num_comments:
            parts.append(f"{num_comments} comments")
        selftext = post.get("selftext") or ""
        if selftext:
            parts.append(_truncate(selftext, 300))

        signals.append(
            {
                "source": "reddit",
                "entity": title,
                "metric": "reddit_post",
                "value": _truncate(" | ".join(parts)),
                "evidence_quality": _quality_from_points(score),
                "timestamp": now,
                "source_url": source_url,
            }
        )
    return signals


def search_jobs(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search job listings via the Adzuna API.

    Requires ``ADZUNA_APP_ID`` and ``ADZUNA_APP_KEY``. The country code can be
    set with ``ADZUNA_COUNTRY`` (default ``us``).
    """
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        logger.warning(
            "Adzuna credentials not configured; set ADZUNA_APP_ID and ADZUNA_APP_KEY."
        )
        return []

    country = os.getenv("ADZUNA_COUNTRY") or "us"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": min(max_results, 50),
        "what": query,
        "content-type": "application/json",
    }
    url = (
        f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?"
        + urllib.parse.urlencode(params)
    )
    data = _http_get_json(url)
    if not isinstance(data, dict):
        return []

    results = data.get("results") or []
    now = _now()
    signals: list[dict[str, Any]] = []
    for job in results:
        title = job.get("title") or "Job"
        company = ""
        company_obj = job.get("company")
        if isinstance(company_obj, dict):
            company = company_obj.get("display_name") or ""
        elif isinstance(company_obj, str):
            company = company_obj

        location = ""
        location_obj = job.get("location")
        if isinstance(location_obj, dict):
            location = location_obj.get("display_name") or ""
        elif isinstance(location_obj, str):
            location = location_obj

        redirect_url = job.get("redirect_url") or ""
        description = job.get("description") or ""
        salary_min = job.get("salary_min")
        salary_max = job.get("salary_max")

        parts = [title]
        if company:
            parts.append(f"at {company}")
        if location:
            parts.append(f"in {location}")
        if salary_min is not None and salary_max is not None:
            parts.append(f"salary {salary_min}-{salary_max}")
        if description:
            parts.append(_truncate(description, 500))

        signals.append(
            {
                "source": "jobs",
                "entity": title,
                "metric": "job_listing",
                "value": _truncate(" | ".join(parts)),
                "evidence_quality": 60,
                "timestamp": now,
                "source_url": redirect_url,
            }
        )
    return signals


def _producthunt_token() -> str | None:
    """Return a Product Hunt access token, or ``None`` if not configured."""
    token = os.getenv("PRODUCT_HUNT_API_TOKEN") or os.getenv("PRODUCT_HUNT_ACCESS_TOKEN")
    if token:
        return token

    client_id = os.getenv("PRODUCT_HUNT_CLIENT_ID")
    client_secret = os.getenv("PRODUCT_HUNT_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.warning(
            "Product Hunt credentials not configured; "
            "set PRODUCT_HUNT_API_TOKEN or PRODUCT_HUNT_CLIENT_ID/CLIENT_SECRET."
        )
        return None

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    data = _http_post_json("https://api.producthunt.com/v2/oauth/token", payload)
    if not isinstance(data, dict):
        return None
    token = data.get("access_token")
    if not token:
        logger.warning("Product Hunt token response missing access_token: %s", data)
    return token


def _escape_graphql(value: str) -> str:
    """Escape a string for safe interpolation into a GraphQL query."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")


def _producthunt_graphql(query: str, token: str) -> Any:
    """Run a Product Hunt GraphQL query and return the ``data`` payload."""
    payload = {"query": query}
    headers = {"Authorization": f"Bearer {token}"}
    data = _http_post_json(
        "https://api.producthunt.com/v2/api/graphql",
        payload,
        headers=headers,
    )
    if not isinstance(data, dict):
        return None
    if data.get("errors"):
        logger.warning("Product Hunt GraphQL error: %s", data["errors"])
        return None
    return data.get("data")


def _producthunt_topic_slugs(query: str, token: str, limit: int = 3) -> list[str]:
    """Return topic slugs matching ``query``."""
    graphql_query = (
        'query { topics(first: %d, query: "%s") { edges { node { slug } } } }'
        % (limit, _escape_graphql(query))
    )
    data = _producthunt_graphql(graphql_query, token)
    if not isinstance(data, dict):
        return []
    edges = (data.get("topics") or {}).get("edges") or []
    slugs: list[str] = []
    for edge in edges:
        node = edge.get("node") or {}
        slug = node.get("slug")
        if slug:
            slugs.append(str(slug))
    return slugs


def _producthunt_posts(topic: str | None, first: int, token: str) -> list[dict[str, Any]]:
    """Fetch Product Hunt posts, optionally filtered by a topic slug."""
    topic_arg = f', topic: "{_escape_graphql(topic)}"' if topic else ""
    graphql_query = (
        "query { posts(first: %d, order: NEWEST%s) { edges { node { "
        "id name tagline votesCount commentsCount url website createdAt "
        "} } } }"
        % (first, topic_arg)
    )
    data = _producthunt_graphql(graphql_query, token)
    if not isinstance(data, dict):
        return []
    edges = (data.get("posts") or {}).get("edges") or []
    return [edge.get("node") or {} for edge in edges if isinstance(edge, dict)]


def search_producthunt(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search Product Hunt posts via the GraphQL API.

    Product Hunt does not expose free-text post search, so the function first
    tries to match a topic and then falls back to keyword filtering over recent
    posts.

    Requires ``PRODUCT_HUNT_API_TOKEN`` or ``PRODUCT_HUNT_CLIENT_ID/CLIENT_SECRET``.
    """
    token = _producthunt_token()
    if not token:
        return []

    query_lower = query.lower()
    nodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add_node(node: dict[str, Any]) -> None:
        name = str(node.get("name") or "").strip()
        tagline = str(node.get("tagline") or "").strip()
        key = (name.lower(), tagline.lower())
        if not name or key in seen:
            return
        seen.add(key)
        nodes.append(node)

    # Try to find topics matching the query and fetch their posts.
    if query.strip():
        try:
            for slug in _producthunt_topic_slugs(query, token, limit=3)[:2]:
                for node in _producthunt_posts(topic=slug, first=max_results, token=token):
                    _add_node(node)
        except Exception as exc:  # pragma: no cover
            logger.warning("Product Hunt topic search failed: %s", exc)

    # Fall back to keyword filtering over recent posts.
    if len(nodes) < max_results:
        first = min(max(50, max_results * 5), 100)
        for node in _producthunt_posts(topic=None, first=first, token=token):
            text = f"{node.get('name', '')} {node.get('tagline', '')}".lower()
            if not query_lower or query_lower in text:
                _add_node(node)
            if len(nodes) >= max_results:
                break

    now = _now()
    signals: list[dict[str, Any]] = []
    for node in nodes[:max_results]:
        name = str(node.get("name") or "Product Hunt post")
        tagline = node.get("tagline") or ""
        votes = node.get("votesCount") or 0
        comments = node.get("commentsCount") or 0
        url = node.get("url") or node.get("website") or ""
        if url and not url.startswith(("http://", "https://")):
            url = "https://www.producthunt.com" + url

        parts = [name]
        if tagline:
            parts.append(tagline)
        if votes:
            parts.append(f"{votes} votes")
        if comments:
            parts.append(f"{comments} comments")

        signals.append(
            {
                "source": "producthunt",
                "entity": name,
                "metric": "producthunt_post",
                "value": _truncate(" | ".join(parts)),
                "evidence_quality": _quality_from_points(votes),
                "timestamp": now,
                "source_url": url,
            }
        )
    return signals


def search_news(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search news articles via NewsAPI.

    Requires ``NEWSAPI_API_KEY``.
    """
    api_key = os.getenv("NEWSAPI_API_KEY")
    if not api_key:
        logger.warning("NewsAPI key not configured; set NEWSAPI_API_KEY.")
        return []

    params = {
        "q": query,
        "apiKey": api_key,
        "pageSize": min(max_results, 100),
        "sortBy": "relevancy",
    }
    url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(params)
    data = _http_get_json(url)
    if not isinstance(data, dict):
        return []
    if data.get("status") != "ok":
        logger.warning("NewsAPI error: %s", data.get("message"))
        return []

    articles = data.get("articles") or []
    now = _now()
    signals: list[dict[str, Any]] = []
    for article in articles:
        title = article.get("title") or "News article"
        url = article.get("url") or ""
        description = article.get("description") or ""
        content = article.get("content") or ""

        source_name = ""
        source_obj = article.get("source")
        if isinstance(source_obj, dict):
            source_name = source_obj.get("name") or ""
        elif isinstance(source_obj, str):
            source_name = source_obj

        snippet = content or description
        parts = [title]
        if source_name:
            parts.append(f"source: {source_name}")
        if snippet:
            parts.append(_truncate(snippet, 500))

        signals.append(
            {
                "source": "news",
                "entity": title,
                "metric": "news_article",
                "value": _truncate(" | ".join(parts)),
                "evidence_quality": 70,
                "timestamp": now,
                "source_url": url,
            }
        )
    return signals


def search_firecrawl(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the web via the Firecrawl API.

    Requires ``FIRECRAWL_API_KEY``.
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logger.warning("Firecrawl API key not configured; set FIRECRAWL_API_KEY.")
        return []

    url = "https://api.firecrawl.dev/v2/search"
    payload = {
        "query": query,
        "limit": max_results,
        "sources": ["web"],
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    data = _http_post_json(url, payload, headers=headers)
    if not isinstance(data, dict):
        return []
    if data.get("success") is False:
        logger.warning("Firecrawl search failed: %s", data.get("error") or data)
        return []

    raw = data.get("data")
    if isinstance(raw, dict):
        results = raw.get("data") or []
    elif isinstance(raw, list):
        results = raw
    else:
        results = []

    now = _now()
    signals: list[dict[str, Any]] = []
    for item in results:
        title = item.get("title") or "Web result"
        item_url = item.get("url") or ""
        description = (
            item.get("description")
            or item.get("markdown")
            or item.get("content")
            or item.get("snippet")
            or ""
        )

        parts = [title]
        if item_url:
            parts.append(f"URL: {item_url}")
        if description:
            parts.append(_truncate(description, 800))

        signals.append(
            {
                "source": "firecrawl",
                "entity": title,
                "metric": "firecrawl_result",
                "value": _truncate(" | ".join(parts)),
                "evidence_quality": 65,
                "timestamp": now,
                "source_url": item_url,
            }
        )
    return signals
