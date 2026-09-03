"""Tavily MCP provider for market intelligence and research signals."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from ode.mcp_client import MCPResult, call_tool


from ode.search_noise import contains_noise, expand_acronyms

logger = logging.getLogger(__name__)


def _normalize_response(result: Any) -> Any:
    """Return parsed JSON, a pre-parsed dict, or the original string."""
    if isinstance(result, (dict, list)):
        return result
    if not isinstance(result, str):
        return None
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result


def _parse_text_results(text: str) -> list[dict[str, Any]]:
    """Parse the keyless Tavily text response into result dicts."""
    results: list[dict[str, Any]] = []
    pattern = re.compile(
        r"Title:\s*(.+?)\n(?:ID:\s*.+?\n)?URL:\s*(.+?)\nContent:\s*(.+?)(?=\n\n|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        title = match.group(1).strip()
        url = match.group(2).strip()
        content = match.group(3).strip()
        results.append({"title": title, "url": url, "content": content, "score": 0.8})
    return results


def _to_signals(
    query: str,
    data: Any,
    metric_prefix: str = "tavily_search",
) -> list[dict[str, Any]]:
    """Convert a Tavily response into ODE signals."""
    signals: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    if isinstance(data, str):
        if _api_error_text(data):
            return signals
        # Keyless/textual Tavily response: extract article blocks.
        answer_match = re.search(r"(?:Answer:|Simple Answer:|Detailed Results:)(.*?)(?=Title:|Detailed Results:|$)", data, re.DOTALL)
        answer = answer_match.group(1).strip() if answer_match else data[:1000]
        if answer and not contains_noise(answer):
            signals.append(
                {
                    "entity": query,
                    "metric": f"{metric_prefix}_summary",
                    "value": answer[:2000],
                    "timestamp": now,
                    "evidence_quality": 85,
                }
            )
        results = _parse_text_results(data)
    elif isinstance(data, dict):
        answer = data.get("answer") or ""
        if answer and not contains_noise(answer):
            signals.append(
                {
                    "entity": query,
                    "metric": f"{metric_prefix}_summary",
                    "value": answer[:2000],
                    "timestamp": now,
                    "evidence_quality": 85,
                }
            )
        results = data.get("results") or []
    else:
        return signals
    for item in results:
        title = item.get("title") or item.get("url") or "unknown"
        url = item.get("url") or ""
        content = item.get("content") or item.get("raw_content") or ""
        if contains_noise(f"{title} {content}"):
            continue
        score = float(item.get("score") or 0) or 0.75
        evidence = min(100, max(50, score * 100))
        entity = f"{title} ({url})" if url else title
        signals.append(
            {
                "entity": entity,
                "metric": f"{metric_prefix}_result",
                "value": content[:1000],
                "timestamp": now,
                "evidence_quality": evidence,
                "source_url": url,  # Ensure source_url is set for proper deduplication
            }
        )

    return signals


def _api_error_text(data: Any) -> str:
    """Return an API error string embedded in the response, or empty if OK."""
    text = str(data) if data else ""
    if "Tavily API error" in text:
        return text[:500]
    return ""


def _record_call(
    tool: str,
    result: Any,
    duration: float,
) -> dict[str, Any]:
    """Build an mcp_calls entry from a Tavily MCP result."""
    if isinstance(result, MCPResult):
        api_error = _api_error_text(result.data)
        success = result.success and not api_error
        return {
            "server": "tavily",
            "tool": tool,
            "success": success,
            "duration": result.duration if result.duration is not None else duration,
            "error": api_error or result.error or "",
        }
    return {
        "server": "tavily",
        "tool": tool,
        "success": False,
        "duration": duration,
        "error": str(result) if result else "unknown error",
    }


class TavilyProvider:
    """Call the Tavily MCP for market intelligence signals."""

    def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        start = time.time()
        result = call_tool("tavily", tool, arguments)
        return result, time.time() - start

    def search(self, query: str, max_results: int = 10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run a Tavily web search and return signals plus an mcp_calls entry."""
        query = expand_acronyms(query)
        result, elapsed = self._call(
            "tavily_search",
            {
                "query": query,
                "max_results": max_results,
                "search_depth": "advanced",
            },
        )
        data = _normalize_response(result.data) if result.success else None
        signals = _to_signals(query, data, metric_prefix="tavily_search")

        if not signals:
            logger.warning("Tavily search returned 0 signals for query: '%s'", query)

        return signals, _record_call("search", result, elapsed)

    def search_with_fallback(
        self,
        query: str,
        fallback_query: str = "",
        max_results: int = 10,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Search Tavily, then retry with a second LLM-generated query if fewer than 3 signals are found."""
        signals, call = self.search(query, max_results)
        calls = [call]
        if len(signals) < 3 and fallback_query:
            more, more_call = self.search(fallback_query, max_results)
            signals.extend(more)
            calls.append(more_call)
        return signals, calls

    def research(self, query: str, max_results: int = 5) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Run a Tavily research query and return signals plus an mcp_calls entry."""
        query = expand_acronyms(query)
        result, elapsed = self._call(
            "tavily_research",
            {
                "query": query,
                "max_results": max_results,
            },
        )
        data = _normalize_response(result.data) if result.success else None
        return _to_signals(query, data, metric_prefix="tavily_research"), _record_call("research", result, elapsed)
