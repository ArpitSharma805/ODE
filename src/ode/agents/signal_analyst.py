"""Signal Analyst: multi-stage analysis pipeline.

Stages:
  1. Classify raw signals into structured categories.
  2. Cluster classified signals by phenomenon.
  3. Extract high-level themes.
  4. Identify problems from themes.
  5. Generate cross-cutting insights.

The pipeline is LLM-primary: each stage asks the configured model to produce
structured JSON. If the model is unavailable or returns invalid output, the
stage falls back to rule-based heuristics so the pipeline never crashes.

All prompts are loaded from ``src/ode/config/prompts/*.txt`` by
:mod:`ode.analysis_config` so the analysis behaviour can be tuned without code
changes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from ode.analysis_config import AnalysisPipelineConfig, load_prompts
from ode.synthesis import _dedupe_repeated_words
from ode.analysis_models import (
    AnalysisResult,
    ClassifiedSignal,
    Insight,
    Problem,
    SignalCluster,
    Theme,
)
from ode.llm import _ollama_generate, synthesize_trends as llm_synthesize_trends
from ode.research import ResearchDepth, get_research_depth
from ode.signals import _filter_commit_messages
from ode.mcp.sequential_thinking import MarketTrend, SequentialThinkingProvider
from ode.trends import MIN_SIGNALS_FOR_ACTIVE, Trend
# Imports from signal_collector.py
from ode.config.timeouts import MCP_TIMEOUT
from ode.concepts import ConceptRegistry
from ode.mcp_client import call_tool
from ode.mcp.jit_tool import synthesize_and_run_tool
from ode.mcp.research_sources import (
    search_firecrawl,
    search_hackernews,
    search_jobs,
    search_news,
    search_producthunt,
    search_reddit,
)
from ode.mcp.tavily import TavilyProvider
from ode.pipeline_context import PipelineContext
from ode.research import build_research_plan
from ode.retrieval import (
    RetrievalPlan,
    article_authority,
    article_relevance,
    rank_repos,
    repo_authority,
)
from ode.signals import Signal, normalize_signals
from ode.sources import create_source

logger = logging.getLogger(__name__)

# Constants from signal_collector.py
MAX_REPO_DETAIL = 6
SEARCH_PAGE_SIZE = 15
MIN_DETAIL_STARS = 5
REPOS_PER_THEME = 50

EXCLUDED_REPO_KEYWORDS = {
    "job", "jobs", "career", "careers", "recruit", "recruiting", "recruitment",
    "recruiter", "hiring", "interview", "portfolio", "portfolios", "resume",
    "resumes", "cv", "curriculum", "personal", "profile", "profiles", "my-",
    "recruiter", "job-board", "jobboard", "career-path", "careerpath",
}

# Map plan source names to registry source names for comparison
_SOURCE_NAME_MAPPING = {
    "github": "github_mcp",
    "tavily": "tavily_mcp",
    "context7": "context7",
}

_SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "github_mcp": {
        "name": "GitHub MCP",
        "source_type": "github_mcp",
        "trust_tier": 90,
        "endpoint": "mcp://github",
        "owner": "mcp",
    },
    "tavily_mcp": {
        "name": "Tavily MCP",
        "source_type": "tavily_mcp",
        "trust_tier": 85,
        "endpoint": "mcp://tavily",
        "owner": "mcp",
    },
    "hackernews": {
        "name": "Hacker News",
        "source_type": "hackernews",
        "trust_tier": 80,
        "endpoint": "https://hn.algolia.com",
        "owner": "research",
    },
    "reddit": {
        "name": "Reddit",
        "source_type": "reddit",
        "trust_tier": 75,
        "endpoint": "https://oauth.reddit.com",
        "owner": "research",
    },
    "jobs": {
        "name": "Adzuna Jobs",
        "source_type": "jobs",
        "trust_tier": 80,
        "endpoint": "https://api.adzuna.com",
        "owner": "research",
    },
    "producthunt": {
        "name": "Product Hunt",
        "source_type": "producthunt",
        "trust_tier": 80,
        "endpoint": "https://api.producthunt.com",
        "owner": "research",
    },
    "news": {
        "name": "NewsAPI",
        "source_type": "news",
        "trust_tier": 80,
        "endpoint": "https://newsapi.org",
        "owner": "research",
    },
    "firecrawl": {
        "name": "Firecrawl",
        "source_type": "firecrawl",
        "trust_tier": 75,
        "endpoint": "https://api.firecrawl.dev",
        "owner": "research",
    },
    "web": {
        "name": "Web (Playwright)",
        "source_type": "web",
        "trust_tier": 75,
        "endpoint": "mcp://playwright",
        "owner": "mcp",
    },
}

_URL_RE = re.compile(r"\((https?://[^\s)]+)\)")

# DOM markers for signal sanitization (from hybrid_pipeline.py)
_DOM_MARKERS = [
    "- generic",
    "- button",
    "- slider",
    "- img",
    "- link",
    "[ref=",
    "[cursor=",
]


class DeterministicMathEngine:
    """Deterministic math engine for signal sanitization and analysis."""

    @staticmethod
    def sanitize_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitize signals by removing low-quality content."""
        cleaned: list[dict[str, Any]] = []
        for s in signals:
            if not isinstance(s, dict):
                continue
            metric = str(s.get("metric", ""))
            value = s.get("value")
            if metric == "github_commit_messages" and isinstance(value, str):
                filtered = _filter_commit_messages(value)
                if not filtered:
                    continue
                s = {**s, "value": filtered}
            if metric.startswith(("web_page", "docs_page")) and isinstance(value, str):
                marker_count = sum(1 for m in _DOM_MARKERS if m in value)
                if marker_count >= 3:
                    continue
            cleaned.append(s)
        return cleaned

    @staticmethod
    def calculate_bounds(signals: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate quality bounds for signals."""
        qualities: list[float] = []
        sources: set[str] = set()
        for s in signals:
            source = s.get("source") or s.get("source_type") or "unknown"
            sources.add(str(source))
            eq = s.get("evidence_quality")
            q = 0.0
            if isinstance(eq, (int, float)):
                q = float(eq)
            else:
                try:
                    q = float(eq)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    q = 0.0
            if q > 1.0:
                q /= 100.0
            qualities.append(max(0.0, min(1.0, q)))
        return {
            "source_diversity": len(sources),
            "avg_evidence_quality": sum(qualities) / len(qualities) if qualities else 0.0,
            "signal_volume": len(signals),
        }

# Simple LLM response cache to avoid repeated calls for identical inputs
_llm_cache: dict[str, dict[str, Any]] = {}
_cache_hits = 0
_cache_misses = 0


def _cache_key(system: str, user: str) -> str:
    """Generate a cache key from system and user prompts."""
    import hashlib
    combined = f"{system}|||{user}"
    return hashlib.md5(combined.encode()).hexdigest()


def _llm_json_cached(system: str, user: str) -> dict[str, Any] | None:
    """Call the analysis LLM with caching."""
    global _cache_hits, _cache_misses
    key = _cache_key(system, user)

    if key in _llm_cache:
        _cache_hits += 1
        logger.debug("LLM cache hit (hits=%d, misses=%d)", _cache_hits, _cache_misses)
        return _llm_cache[key]

    _cache_misses += 1
    result = _llm_json(system, user)
    if result:
        _llm_cache[key] = result
        logger.debug("LLM cache miss, cached result (hits=%d, misses=%d)", _cache_hits, _cache_misses)
    return result


def _clear_llm_cache() -> None:
    """Clear the LLM cache (useful for testing or when context changes)."""
    global _llm_cache, _cache_hits, _cache_misses
    _llm_cache.clear()
    _cache_hits = 0
    _cache_misses = 0


def _call_analysis_llm(
    system: str,
    user: str,
    temperature: float = 0.2,
) -> str:
    """Call the Ollama analysis LLM and return text, or '' on failure.

    The base ``_ollama_generate`` only accepts a single prompt string and an
    optional ``format`` parameter, so we bake the system prompt into the prompt.
    The ``temperature`` argument is kept for API compatibility but is not
    forwarded because the underlying function does not accept it.
    """
    try:
        prompt = f"{system}\n\n---\n\n{user}"
        return _ollama_generate(prompt, format=None) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analysis LLM call failed: %s", exc)
        return ""


def _update(
    states: dict[str, dict[str, Any]],
    detail: str,
    status: str = "running",
    duration: float | None = None,
) -> dict[str, Any]:
    """Build a status update event for the Signal Analyst UI state."""
    if "Signal Analyst" in states:
        states["Signal Analyst"]["status"] = status
        if duration is not None:
            states["Signal Analyst"]["duration"] = duration
        if detail:
            states["Signal Analyst"]["detail"] = detail
    return {
        "type": "update",
        "status": copy.deepcopy(states),
        "agent": "Signal Analyst",
    }


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _llm_json(system: str, user: str) -> dict[str, Any] | None:
    """Call the analysis LLM and try to parse JSON output."""
    response = _call_analysis_llm(system, user)
    if not response:
        return None
    cleaned = _strip_markdown_fences(response)
    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            logger.warning("LLM JSON response was not a JSON object: %r", parsed)
            return None
        return parsed
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse LLM JSON response: %s", exc)
        return None


def _signal_id(raw: dict[str, Any], idx: int) -> str:
    """Return a stable id for a raw signal."""
    existing = raw.get("signal_id") or raw.get("id")
    if existing:
        return str(existing)
    base = f"{raw.get('entity', '')}:{raw.get('metric', '')}:{raw.get('value', '')}:{idx}"
    return f"sig-{idx}-{hashlib.md5(base.encode()).hexdigest()[:10]}"  # noqa: S324


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _default_signal_type(value: str, metric: str, config: AnalysisPipelineConfig) -> str:
    """Rule-based signal type used when the LLM classifier is unavailable."""
    value_lower = value.lower()
    metric_lower = metric.lower()

    if any(k in metric_lower for k in ("adopt", "support", "add", "launch")):
        return "adoption_indicator"
    if any(k in metric_lower for k in ("security", "vuln", "exploit", "leak", "auth", "permission")):
        return "security_concern"
    if any(k in metric_lower for k in ("pain", "frustrat", "difficult", "hard", "workaround", "manual")):
        return "developer_pain"
    if any(k in metric_lower for k in ("market", "demand", "need", "want")):
        return "market_demand"
    if any(k in metric_lower for k in ("observ", "monitor", "trace", "log", "telemetry")):
        return "observability_need"
    if any(k in metric_lower for k in ("test", "valid", "mock", "regression")):
        return "testing_gap"
    if any(k in metric_lower for k in ("marketplace", "registry", "discover", "hub", "catalog")):
        return "marketplace_need"

    if any(k in value_lower for k in ("adopt", "support", "adds", "integrate", "launch")):
        return "adoption_indicator"
    if any(k in value_lower for k in ("leak", "security", "permission", "access", "vulnerability")):
        return "security_concern"
    if any(k in value_lower for k in ("observability", "monitoring", "tracing", "logs")):
        return "observability_need"
    if any(k in value_lower for k in ("test", "validation", "mock")):
        return "testing_gap"
    if any(k in value_lower for k in ("marketplace", "registry", "discovery", "hub")):
        return "marketplace_need"
    if any(k in value_lower for k in ("pain", "frustrating", "difficult", "hard", "workaround")):
        return "developer_pain"
    if any(k in value_lower for k in ("demand", "need", "want", "market")):
        return "market_demand"

    return config.signal_types[0] if config.signal_types else "adoption_indicator"


def _sentiment(value: str) -> str:
    value_lower = value.lower()
    negative = {
        "frustration", "frustrating", "difficult", "hard", "struggle", "struggling",
        "pain", "painful", "broken", "bug", "leak", "vulnerability", "exploit",
        "breach", "slow", "waste", "annoying", "bad", "fails", "failed",
    }
    positive = {
        "adopt", "support", "launch", "growth", "growing", "success", "popular",
        "trending", "excited", "love", "great", "improve", "better", "easier",
    }
    neg = any(w in value_lower for w in negative)
    pos = any(w in value_lower for w in positive)
    if neg and not pos:
        return "negative"
    if pos and not neg:
        return "positive"
    return "neutral"


def _intensity(evidence_quality: float) -> str:
    if evidence_quality >= 80:
        return "high"
    if evidence_quality >= 50:
        return "medium"
    return "low"


def _maturity_indicator(value: str, metric: str) -> str:
    text = f"{value} {metric}".lower()
    if any(k in text for k in ("experimental", "alpha", "prototype", "research")):
        return "experimental"
    if any(k in text for k in ("early adoption", "early adopters", "newly", "just released")):
        return "early_adoption"
    if any(k in text for k in ("production", "enterprise", "widely used", "mature")):
        return "production_ready"
    if any(k in text for k in ("legacy", "decline", "deprecated", "dying", "abandoned")):
        return "declining"
    return "active_development"


def _temporal_signal(value: str, metric: str) -> str:
    text = f"{value} {metric}".lower()
    if any(k in text for k in ("growing", "growth", "accelerat", "trending", "rapid", "expanding")):
        return "growing"
    if any(k in text for k in ("declin", "falling", "slowing", "less", "abandoned")):
        return "declining"
    if any(k in text for k in ("stable", "steady", "mature", "unchanged")):
        return "stable"
    return "stable"


def _extract_claims(value: str) -> list[str]:
    """Split a signal value into short claims/sentences."""
    value = _normalize_text(value)
    if not value:
        return []
    # Split on sentence-like boundaries, but keep the whole value if it is short.
    if len(value) < 120:
        return [value]
    parts = [p.strip() for p in re.split(r"[.!?]\s+", value) if p.strip()]
    return parts[:3]


def _extract_stakeholders(value: str, signal_type: str) -> list[str]:
    value_lower = value.lower()
    stakeholders: list[str] = []
    if any(k in value_lower for k in ("developer", "engineer", "coder", "programmer")):
        stakeholders.append("developers")
    if any(k in value_lower for k in ("security team", "security", "infosec", "ciso")):
        stakeholders.append("security teams")
    if any(k in value_lower for k in ("ops", "devops", "sre", "platform team")):
        stakeholders.append("operations teams")
    if any(k in value_lower for k in ("product", "pm", "manager")):
        stakeholders.append("product teams")
    if any(k in value_lower for k in ("enterprise", "company", "organization", "business")):
        stakeholders.append("enterprises")
    if not stakeholders:
        if signal_type in ("security_concern",):
            stakeholders.append("security teams")
        elif signal_type in ("developer_pain", "testing_gap"):
            stakeholders.append("developers")
        elif signal_type in ("market_demand",):
            stakeholders.append("buyers")
        else:
            stakeholders.append("engineering teams")
    return stakeholders


def _config() -> AnalysisPipelineConfig:
    return AnalysisPipelineConfig.from_env()


def _prompts(config: AnalysisPipelineConfig | None = None) -> dict[str, str]:
    return load_prompts(config or _config())


def classify_signals(
    raw_signals: list[dict[str, Any]],
    intent: dict[str, Any] | None = None,
    max_signals: int = 20,
) -> list[ClassifiedSignal]:
    """Classify raw signals using the LLM, falling back to rules."""
    config = _config()
    prompts = _prompts(config)
    safe_intent = intent if isinstance(intent, dict) else {}
    query = _normalize_text(
        safe_intent.get("primary_technology")
        or safe_intent.get("topics", ["this topic"])[0]
        if safe_intent
        else "this topic"
    )

    if not raw_signals:
        return []

    # Build the user payload for the LLM.
    limit = max_signals
    truncated = [r for r in raw_signals[:limit] if isinstance(r, dict)]
    signals_json = json.dumps(truncated, ensure_ascii=False, default=str)
    user = f"Query: {query}\n\nInput signals:\n{signals_json}\n\nReturn valid JSON only."

    classified_raw: dict[str, Any] | None = None
    try:
        classified_raw = _llm_json(prompts["classify"], user)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM classification failed: %s", exc)

    classified_list: list[dict[str, Any]] = []
    if classified_raw and isinstance(classified_raw.get("classified"), list):
        classified_list = classified_raw["classified"]

    by_id: dict[str, dict[str, Any]] = {}
    for item in classified_list:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item

    classified: list[ClassifiedSignal] = []
    for idx, raw in enumerate(raw_signals):
        if not isinstance(raw, dict):
            continue
        sid = _signal_id(raw, idx)
        llm_item = by_id.get(sid, {})

        entity = _normalize_text(raw.get("entity") or llm_item.get("entity") or "unknown")
        metric = _normalize_text(raw.get("metric") or llm_item.get("metric") or "signal")
        value = _normalize_text(raw.get("value") or llm_item.get("value") or "")
        source = _normalize_text(raw.get("source_type") or llm_item.get("source") or "unknown")
        source_url = _normalize_text(raw.get("source_url") or llm_item.get("source_url") or "")
        evidence_quality = float(raw.get("evidence_quality") or llm_item.get("evidence_quality") or 0.0)

        if llm_item:
            signal_type = _normalize_text(llm_item.get("signal_type") or "")
            signal_category = _normalize_text(llm_item.get("signal_category") or "")
            sentiment = _normalize_text(llm_item.get("sentiment") or "")
            intensity = _normalize_text(llm_item.get("intensity") or "")
            maturity_indicator = _normalize_text(llm_item.get("maturity_indicator") or "")
            temporal_signal = _normalize_text(llm_item.get("temporal_signal") or "")
            stakeholders = [str(s).strip() for s in llm_item.get("stakeholders") or [] if s]
            extracted_claims = [str(c).strip() for c in llm_item.get("extracted_claims") or [] if c]
            confidence = float(llm_item.get("confidence") or evidence_quality / 100.0 or 0.5)
        else:
            signal_type = ""
            signal_category = ""
            sentiment = ""
            intensity = ""
            maturity_indicator = ""
            temporal_signal = ""
            stakeholders = []
            extracted_claims = []
            confidence = 0.0

        # Rule-based fallback for any missing LLM fields.
        if not signal_type:
            signal_type = _default_signal_type(value, metric, config)
        if not signal_category:
            signal_category = metric
        if not sentiment:
            sentiment = _sentiment(value)
        if not intensity:
            intensity = _intensity(evidence_quality)
        if not maturity_indicator:
            maturity_indicator = _maturity_indicator(value, metric)
        if not temporal_signal:
            temporal_signal = _temporal_signal(value, metric)
        if not stakeholders:
            stakeholders = _extract_stakeholders(value, signal_type)
        if not extracted_claims:
            extracted_claims = _extract_claims(value)
        if not confidence:
            confidence = max(0.1, min(1.0, evidence_quality / 100.0))

        classified.append(
            ClassifiedSignal(
                id=sid,
                entity=entity,
                metric=metric,
                value=value,
                source=source,
                source_url=source_url,
                evidence_quality=evidence_quality,
                signal_type=signal_type,
                signal_category=signal_category,
                sentiment=sentiment,
                intensity=intensity,
                maturity_indicator=maturity_indicator,
                temporal_signal=temporal_signal,
                stakeholders=stakeholders,
                extracted_claims=extracted_claims,
                confidence=confidence,
            )
        )

    return classified


def cluster_signals(
    classified: list[ClassifiedSignal],
) -> list[SignalCluster]:
    """Cluster classified signals using the LLM, falling back to rules."""
    config = _config()
    prompts = _prompts(config)

    if not classified:
        return []

    signals_json = json.dumps(
        [
            {
                "id": s.id,
                "entity": s.entity,
                "metric": s.metric,
                "value": s.value,
                "source": s.source,
                "signal_type": s.signal_type,
                "signal_category": s.signal_category,
                "confidence": s.confidence,
            }
            for s in classified[:60]
        ],
        ensure_ascii=False,
        default=str,
    )
    user = f"Input classified signals:\n{signals_json}\n\nReturn valid JSON only."

    cluster_raw = _llm_json(prompts["cluster"], user)
    cluster_list: list[dict[str, Any]] = []
    if cluster_raw and isinstance(cluster_raw.get("clusters"), list):
        cluster_list = cluster_raw["clusters"]

    by_id = {s.id: s for s in classified}
    clusters: list[SignalCluster] = []
    seen_ids: set[str] = set()

    for raw in cluster_list:
        if not isinstance(raw, dict):
            continue
        label = _normalize_text(raw.get("label") or "Unnamed cluster")
        signal_ids = [str(sid) for sid in raw.get("signal_ids", []) if sid]
        if not signal_ids:
            continue
        sigs = [by_id[sid] for sid in signal_ids if sid in by_id]
        if len(sigs) < config.cluster_min_size:
            continue
        dominant = _normalize_text(raw.get("dominant_signal_type") or sigs[0].signal_type)
        source_diversity = len({s.source for s in sigs})
        avg_confidence = sum(s.confidence for s in sigs) / len(sigs)
        cluster_id = f"cluster-{hashlib.md5(','.join(sorted(signal_ids)).encode()).hexdigest()[:10]}"  # noqa: S324
        if cluster_id in seen_ids:
            continue
        seen_ids.add(cluster_id)
        clusters.append(
            SignalCluster(
                cluster_id=cluster_id,
                label=label,
                signal_ids=signal_ids,
                dominant_signal_type=dominant,
                source_diversity=source_diversity,
                avg_confidence=round(avg_confidence, 2),
                signals=sigs,
            )
        )

    # Rule-based fallback if LLM produced no usable clusters.
    if not clusters:
        buckets: dict[str, list[ClassifiedSignal]] = {}
        for sig in classified:
            key = f"{sig.signal_type}:{sig.signal_category or sig.entity}"
            buckets.setdefault(key, []).append(sig)
        for key, sigs in buckets.items():
            if len(sigs) < config.cluster_min_size:
                continue
            signal_ids = [s.id for s in sigs]
            cluster_id = f"cluster-{hashlib.md5(','.join(sorted(signal_ids)).encode()).hexdigest()[:10]}"  # noqa: S324
            if cluster_id in seen_ids:
                continue
            seen_ids.add(cluster_id)
            # Use the most common noun phrase from values as the label.
            words: Counter[str] = Counter()
            for s in sigs:
                for w in re.findall(r"[A-Za-z0-9]+", s.value.lower()):
                    if len(w) > 3:
                        words[w] += 1
            top_word = words.most_common(1)[0][0] if words else sigs[0].entity
            label = f"{top_word} {sigs[0].signal_type.replace('_', ' ')}".strip()
            source_diversity = len({s.source for s in sigs})
            avg_confidence = sum(s.confidence for s in sigs) / len(sigs)
            clusters.append(
                SignalCluster(
                    cluster_id=cluster_id,
                    label=label,
                    signal_ids=signal_ids,
                    dominant_signal_type=sigs[0].signal_type,
                    source_diversity=source_diversity,
                    avg_confidence=round(avg_confidence, 2),
                    signals=sigs,
                )
            )

    # Always ensure we have at least one cluster by grouping everything.
    if not clusters and classified:
        signal_ids = [s.id for s in classified]
        cluster_id = f"cluster-all-{hashlib.md5(','.join(sorted(signal_ids)).encode()).hexdigest()[:10]}"  # noqa: S324
        source_diversity = len({s.source for s in classified})
        avg_confidence = sum(s.confidence for s in classified) / len(classified)
        clusters.append(
            SignalCluster(
                cluster_id=cluster_id,
                label=f"{classified[0].entity or 'general'} signals",
                signal_ids=signal_ids,
                dominant_signal_type=classified[0].signal_type,
                source_diversity=source_diversity,
                avg_confidence=round(avg_confidence, 2),
                signals=classified,
            )
        )

    clusters.sort(key=lambda c: len(c.signals), reverse=True)
    return clusters[:15]


def classify_and_cluster_signals(
    raw_signals: list[dict[str, Any]],
    intent: dict[str, Any] | None = None,
    max_signals: int = 20,
) -> AnalysisResult:
    """Combined classification and clustering in a single LLM pass."""
    config = _config()
    prompts = _prompts(config)
    safe_intent = intent if isinstance(intent, dict) else {}
    query = _normalize_text(
        safe_intent.get("primary_technology")
        or safe_intent.get("topics", ["this topic"])[0]
        if safe_intent
        else "this topic"
    )

    if not raw_signals:
        return AnalysisResult([], [], [], [], [])

    # Build the user payload for the LLM with combined classification and clustering
    limit = max_signals
    truncated = [r for r in raw_signals[:limit] if isinstance(r, dict)]
    signals_json = json.dumps(truncated, ensure_ascii=False, default=str)
    user = (
        f"Query: {query}\n\n"
        f"Input signals:\n{signals_json}\n\n"
        f"Return valid JSON with keys:\n"
        f'- "classified": list of classified signals with id, entity, metric, value, source, signal_type, signal_category, sentiment, intensity, maturity_indicator, temporal_signal, confidence\n'
        f'- "clusters": list of clusters with label, signal_ids, dominant_signal_type\n'
    )

    combined_raw: dict[str, Any] | None = None
    try:
        combined_raw = _llm_json_cached(prompts["classify"], user)  # Use cached version
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM classify+cluster failed: %s", exc)

    # Process classification results
    classified_list: list[dict[str, Any]] = []
    if combined_raw and isinstance(combined_raw.get("classified"), list):
        classified_list = combined_raw["classified"]

    by_id_llm: dict[str, dict[str, Any]] = {}
    for item in classified_list:
        if isinstance(item, dict) and item.get("id"):
            by_id_llm[str(item["id"])] = item

    classified: list[ClassifiedSignal] = []
    for idx, raw in enumerate(raw_signals):
        if not isinstance(raw, dict):
            continue
        sid = _signal_id(raw, idx)
        llm_item = by_id_llm.get(sid, {})

        entity = _normalize_text(raw.get("entity") or llm_item.get("entity") or "unknown")
        metric = _normalize_text(raw.get("metric") or llm_item.get("metric") or "signal")
        value = _normalize_text(raw.get("value") or llm_item.get("value") or "")
        source = _normalize_text(raw.get("source_type") or llm_item.get("source") or "unknown")
        source_url = _normalize_text(raw.get("source_url") or llm_item.get("source_url") or "")
        evidence_quality = float(raw.get("evidence_quality") or llm_item.get("evidence_quality") or 0.0)

        if llm_item:
            signal_type = _normalize_text(llm_item.get("signal_type") or "")
            signal_category = _normalize_text(llm_item.get("signal_category") or "")
            sentiment = _normalize_text(llm_item.get("sentiment") or "")
            intensity = _normalize_text(llm_item.get("intensity") or "")
            maturity_indicator = _normalize_text(llm_item.get("maturity_indicator") or "")
            temporal_signal = _normalize_text(llm_item.get("temporal_signal") or "")
            confidence = float(llm_item.get("confidence") or 0.7)
            stakeholders = []
            extracted_claims = []
        else:
            # Fallback to rule-based classification
            signal_type = _default_signal_type(value, metric, config)
            signal_category = metric
            sentiment = _sentiment(value)
            intensity = _intensity(evidence_quality)
            maturity_indicator = _maturity_indicator(value, metric)
            temporal_signal = _temporal_signal(value, metric)
            confidence = max(0.1, min(1.0, evidence_quality / 100.0))

        # Always extract stakeholders and claims using rule-based extraction
        stakeholders = _extract_stakeholders(value, signal_type)
        extracted_claims = _extract_claims(value)

        classified.append(
            ClassifiedSignal(
                id=sid,
                entity=entity,
                metric=metric,
                value=value,
                source=source,
                source_url=source_url,
                evidence_quality=evidence_quality,
                signal_type=signal_type,
                signal_category=signal_category,
                sentiment=sentiment,
                intensity=intensity,
                maturity_indicator=maturity_indicator,
                temporal_signal=temporal_signal,
                stakeholders=stakeholders,
                extracted_claims=extracted_claims,
                confidence=confidence,
            )
        )

    # Process clustering results
    cluster_list: list[dict[str, Any]] = []
    if combined_raw and isinstance(combined_raw.get("clusters"), list):
        cluster_list = combined_raw["clusters"]

    by_id: dict[str, ClassifiedSignal] = {s.id: s for s in classified}
    clusters: list[SignalCluster] = []
    seen_ids: set[str] = set()

    for raw in cluster_list:
        if not isinstance(raw, dict):
            continue
        label = _normalize_text(raw.get("label") or "Unnamed cluster")
        signal_ids = [str(sid) for sid in raw.get("signal_ids", []) if sid]
        if not signal_ids:
            continue
        sigs = [by_id[sid] for sid in signal_ids if sid in by_id]
        if len(sigs) < config.cluster_min_size:
            continue
        dominant = _normalize_text(raw.get("dominant_signal_type") or sigs[0].signal_type)
        source_diversity = len({s.source for s in sigs})
        avg_confidence = sum(s.confidence for s in sigs) / len(sigs)
        cluster_id = f"cluster-{hashlib.md5(','.join(sorted(signal_ids)).encode()).hexdigest()[:10]}"  # noqa: S324
        if cluster_id in seen_ids:
            continue
        seen_ids.add(cluster_id)
        clusters.append(
            SignalCluster(
                cluster_id=cluster_id,
                label=label,
                signal_ids=signal_ids,
                dominant_signal_type=dominant,
                source_diversity=source_diversity,
                avg_confidence=round(avg_confidence, 2),
                signals=sigs,
            )
        )

    # Fallback to rule-based clustering if LLM failed
    if not clusters and classified:
        buckets: dict[str, list[ClassifiedSignal]] = {}
        for sig in classified:
            key = f"{sig.signal_type}:{sig.signal_category or sig.entity}"
            buckets.setdefault(key, []).append(sig)
        for key, sigs in buckets.items():
            if len(sigs) < config.cluster_min_size:
                continue
            signal_ids = [s.id for s in sigs]
            cluster_id = f"cluster-{hashlib.md5(','.join(sorted(signal_ids)).encode()).hexdigest()[:10]}"  # noqa: S324
            if cluster_id in seen_ids:
                continue
            seen_ids.add(cluster_id)
            words: Counter[str] = Counter()
            for s in sigs:
                for w in re.findall(r"[A-Za-z0-9]+", s.value.lower()):
                    if len(w) > 3:
                        words[w] += 1
            top_word = words.most_common(1)[0][0] if words else sigs[0].entity
            label = f"{top_word} {sigs[0].signal_type.replace('_', ' ')}".strip()
            source_diversity = len({s.source for s in sigs})
            avg_confidence = sum(s.confidence for s in sigs) / len(sigs)
            clusters.append(
                SignalCluster(
                    cluster_id=cluster_id,
                    label=label,
                    signal_ids=signal_ids,
                    dominant_signal_type=sigs[0].signal_type,
                    source_diversity=source_diversity,
                    avg_confidence=round(avg_confidence, 2),
                    signals=sigs,
                )
            )

    # Ensure at least one cluster
    if not clusters and classified:
        signal_ids = [s.id for s in classified]
        cluster_id = f"cluster-all-{hashlib.md5(','.join(sorted(signal_ids)).encode()).hexdigest()[:10]}"  # noqa: S324
        source_diversity = len({s.source for s in classified})
        avg_confidence = sum(s.confidence for s in classified) / len(classified)
        clusters.append(
            SignalCluster(
                cluster_id=cluster_id,
                label=f"{classified[0].entity or 'general'} signals",
                signal_ids=signal_ids,
                dominant_signal_type=classified[0].signal_type,
                source_diversity=source_diversity,
                avg_confidence=round(avg_confidence, 2),
                signals=classified,
            )
        )

    clusters.sort(key=lambda c: len(c.signals), reverse=True)
    return AnalysisResult(classified, clusters[:15], [], [], [])


def _strength(signal_count: int, avg_confidence: float) -> str:
    if signal_count >= 10 and avg_confidence >= 0.75:
        return "strong"
    if signal_count >= 3 and avg_confidence >= 0.5:
        return "moderate"
    return "weak"


def _severity_from_strength(strength: str) -> str:
    """Map theme strength vocabulary to problem severity vocabulary."""
    mapping = {
        "strong": "high",
        "moderate": "medium",
        "weak": "low",
    }
    return mapping.get(strength, "medium")


def _source_count(signals: list[ClassifiedSignal]) -> int:
    return len({s.source for s in signals})


def extract_themes(
    clusters: list[SignalCluster],
    query: str,
    max_themes: int = 3,
) -> list[Theme]:
    """Extract themes from clusters using the LLM, falling back to rules."""
    config = _config()
    prompts = _prompts(config)

    if not clusters:
        return []

    clusters_json = json.dumps(
        [
            {
                "cluster_id": c.cluster_id,
                "label": c.label,
                "dominant_signal_type": c.dominant_signal_type,
                "source_diversity": c.source_diversity,
                "avg_confidence": c.avg_confidence,
                "signals": [
                    {
                        "id": s.id,
                        "entity": s.entity,
                        "metric": s.metric,
                        "value": s.value,
                        "source": s.source,
                        "signal_type": s.signal_type,
                        "extracted_claims": s.extracted_claims,
                    }
                    for s in c.signals[:10]
                ],
            }
            for c in clusters[:10]
        ],
        ensure_ascii=False,
        default=str,
    )
    user = f"Query: {query}\n\nInput clusters:\n{clusters_json}\n\nReturn valid JSON: {{\"themes\": [...]}}"

    theme_raw = _llm_json(prompts["theme"], user)
    theme_list: list[dict[str, Any]] = []
    if theme_raw and isinstance(theme_raw.get("themes"), list):
        theme_list = theme_raw["themes"]

    themes: list[Theme] = []
    cluster_by_id = {c.cluster_id: c for c in clusters}
    seen_ids: set[str] = set()

    for raw in theme_list:
        if not isinstance(raw, dict):
            continue
        theme_id = _normalize_text(raw.get("theme_id") or f"theme-{uuid.uuid4().hex[:8]}")
        if theme_id in seen_ids:
            continue
        seen_ids.add(theme_id)
        cluster_ids = [str(cid) for cid in raw.get("cluster_ids", []) if cid]
        supporting = [cluster_by_id[cid] for cid in cluster_ids if cid in cluster_by_id]
        if not supporting:
            supporting = [clusters[0]]
        all_signals = [s for c in supporting for s in c.signals]
        theme_name = _dedupe_repeated_words(_normalize_text(raw.get("theme_name") or supporting[0].label))
        what_is_happening = _dedupe_repeated_words(_normalize_text(raw.get("what_is_happening") or _fallback_happening(all_signals)))
        evidence_summary = _normalize_text(raw.get("evidence_summary") or _fallback_evidence(supporting))
        strength = _normalize_text(raw.get("strength") or "weak")
        trajectory = _normalize_text(raw.get("trajectory") or "stable")
        stakeholders = [str(s).strip() for s in raw.get("affected_stakeholders") or [] if s]
        if not stakeholders:
            stakeholders = list({st for c in supporting for s in c.signals for st in s.stakeholders})[:5]
        signal_count = int(raw.get("signal_count") or len(all_signals))
        source_count = int(raw.get("source_count") or _source_count(all_signals))
        themes.append(
            Theme(
                theme_id=theme_id,
                theme_name=theme_name,
                what_is_happening=what_is_happening,
                evidence_summary=evidence_summary,
                strength=strength,
                trajectory=trajectory,
                affected_stakeholders=stakeholders,
                cluster_ids=cluster_ids or [supporting[0].cluster_id],
                signal_count=signal_count,
                source_count=source_count,
            )
        )

    if not themes:
        for cluster in clusters:
            theme_id = f"theme-{cluster.cluster_id}"
            if theme_id in seen_ids:
                continue
            seen_ids.add(theme_id)
            what = _fallback_happening(cluster.signals)
            evidence = _fallback_evidence([cluster])
            stakeholders = list({st for s in cluster.signals for st in s.stakeholders})[:5]
            themes.append(
                Theme(
                    theme_id=theme_id,
                    theme_name=cluster.label,
                    what_is_happening=what,
                    evidence_summary=evidence,
                    strength=_strength(len(cluster.signals), cluster.avg_confidence),
                    trajectory=_majority_temporal(cluster.signals),
                    affected_stakeholders=stakeholders,
                    cluster_ids=[cluster.cluster_id],
                    signal_count=len(cluster.signals),
                    source_count=_source_count(cluster.signals),
                )
            )

    themes.sort(key=lambda t: t.signal_count, reverse=True)
    return themes[:max_themes]


def _fallback_theme_name(clusters: list[SignalCluster]) -> str:
    """Fallback theme name when LLM fails."""
    if not clusters:
        return "General Technology Trends"
    return f"{clusters[0].label} Patterns"

def _fallback_what_is_happening(clusters: list[SignalCluster]) -> str:
    """Fallback what_is_happening when LLM fails."""
    if not clusters:
        return "Market signals indicate ongoing activity"
    return f"Growing activity in {clusters[0].label} across multiple sources"

def _fallback_evidence_summary(clusters: list[SignalCluster]) -> str:
    """Fallback evidence summary when LLM fails."""
    if not clusters:
        return "Multiple sources show consistent patterns"
    return f"Consistent signals across {len(clusters)} sources indicate sustained interest"

def _fallback_affected_stakeholders(clusters: list[SignalCluster]) -> list[str]:
    """Fallback affected stakeholders when LLM fails."""
    if not clusters:
        return ["Engineers", "Developers", "Technical Teams"]
    return ["Engineers", "Developers", "Technical Teams"]

def _trajectory_from_strength(strength: str) -> str:
    """Map strength to trajectory."""
    mapping = {
        "strong": "accelerating",
        "moderate": "stable",
        "weak": "emerging",
    }
    return mapping.get(strength, "emerging")


def _fallback_happening(signals: list[ClassifiedSignal]) -> str:
    if len(signals) <= 2:
        return (
            "Limited live evidence was collected for this query. "
            "Results should be treated as directional."
        )
    claims: list[str] = []
    for s in signals:
        claims.extend(s.extracted_claims[:2])
    claims = [c for c in claims if c and str(c).strip() != "0"]
    claims = list(dict.fromkeys(claims))[:5]
    if not claims:
        return (
            "Limited live evidence was collected for this query. "
            "Results should be treated as directional."
        )
    return " ".join(claims)


def _fallback_evidence(clusters: list[SignalCluster]) -> str:
    sources = sorted({s.source for c in clusters for s in c.signals})
    total = sum(len(c.signals) for c in clusters)
    values = [s.value for c in clusters for s in c.signals[:3]]
    evidence = f"{total} signals across {len(sources)} source type(s) ({', '.join(sources)})."
    if values:
        evidence += " Representative signals: " + "; ".join(values[:3]) + "."
    return evidence


def _majority_temporal(signals: list[ClassifiedSignal]) -> str:
    counts: Counter[str] = Counter(s.temporal_signal for s in signals if s.temporal_signal)
    return counts.most_common(1)[0][0] if counts else "stable"


def extract_problems(
    themes: list[Theme],
    query: str,
) -> list[Problem]:
    """Identify problems from themes using the LLM, falling back to rules."""
    config = _config()
    prompts = _prompts(config)

    if not themes:
        return []

    themes_json = json.dumps(
        [
            {
                "theme_id": t.theme_id,
                "theme_name": t.theme_name,
                "what_is_happening": t.what_is_happening,
                "evidence_summary": t.evidence_summary,
                "strength": t.strength,
                "affected_stakeholders": t.affected_stakeholders,
                "signal_count": t.signal_count,
                "source_count": t.source_count,
            }
            for t in themes[:8]
        ],
        ensure_ascii=False,
        default=str,
    )
    user = f"Query: {query}\n\nInput themes:\n{themes_json}\n\nReturn valid JSON: {{\"problems\": [...]}}"

    problem_raw = _llm_json(prompts["problem"], user)
    problem_list: list[dict[str, Any]] = []
    if problem_raw and isinstance(problem_raw.get("problems"), list):
        problem_list = problem_raw["problems"]

    problems: list[Problem] = []
    theme_by_id = {t.theme_id: t for t in themes}
    seen_ids: set[str] = set()

    for raw in problem_list:
        if not isinstance(raw, dict):
            continue
        problem_id = _normalize_text(raw.get("problem_id") or f"problem-{uuid.uuid4().hex[:8]}")
        if problem_id in seen_ids:
            continue
        seen_ids.add(problem_id)
        theme_ids = [str(tid) for tid in raw.get("theme_ids", []) if tid]
        if not theme_ids and themes:
            theme_ids = [themes[0].theme_id]
        supporting = [theme_by_id[tid] for tid in theme_ids if tid in theme_by_id]
        if not supporting:
            supporting = [themes[0]]
        statement = _dedupe_repeated_words(_normalize_text(raw.get("problem_statement") or _fallback_problem_statement(supporting)))
        who = [str(s).strip() for s in raw.get("who_has_this_problem") or [] if s]
        if not who:
            who = supporting[0].affected_stakeholders
        workarounds = _normalize_text(raw.get("current_workarounds") or _fallback_workarounds(supporting))
        severity = _normalize_text(raw.get("severity") or _severity_from_strength(supporting[0].strength))
        signal_count = sum(t.signal_count for t in supporting)
        problems.append(
            Problem(
                problem_id=problem_id,
                problem_statement=statement,
                who_has_this_problem=who,
                current_workarounds=workarounds,
                severity=severity,
                theme_ids=theme_ids,
                signal_count=signal_count,
            )
        )

    if not problems:
        for theme in themes:
            problem_id = f"problem-{theme.theme_id}"
            if problem_id in seen_ids:
                continue
            seen_ids.add(problem_id)
            problems.append(
                Problem(
                    problem_id=problem_id,
                    problem_statement=_fallback_problem_statement([theme]),
                    who_has_this_problem=theme.affected_stakeholders,
                    current_workarounds=_fallback_workarounds([theme]),
                    severity=_severity_from_strength(theme.strength),
                    theme_ids=[theme.theme_id],
                    signal_count=theme.signal_count,
                )
            )

    problems.sort(key=lambda p: p.signal_count, reverse=True)
    return problems[:10]


def _fallback_workarounds(themes: list[Theme]) -> str:
    """Fallback workarounds when LLM fails."""
    if not themes:
        return "Teams are adopting incremental approaches and leveraging existing tools"
    return "Teams are adopting incremental approaches and leveraging existing tools"

def _fallback_insight_statement(themes: list[Theme], problems: list[Problem]) -> str:
    """Fallback insight statement when LLM fails."""
    if themes:
        return f"The convergence of {len(themes)} themes suggests a broader shift in {themes[0].theme_name}"
    if problems:
        return f"Addressing {problems[0].problem_statement} requires coordinated effort across stakeholders"
    return "Market signals indicate a broader shift in technology adoption patterns"


def _fallback_problem_statement(themes: list[Theme]) -> str:
    if not themes:
        return "No clear problem could be derived from the available evidence."
    summary = themes[0].what_is_happening.rstrip(".")
    return f"The evidence shows {summary}, revealing an unmet need for better tooling or practices."

def _fallback_problem_statement_from_clusters(clusters: list[SignalCluster]) -> str:
    """Fallback problem statement when LLM fails and we only have clusters."""
    if not clusters:
        return "No clear problem could be derived from the available evidence."
    return f"The evidence shows growing activity in {clusters[0].label}, revealing an unmet need for better tooling or practices."

def _fallback_workarounds_from_clusters(clusters: list[SignalCluster]) -> str:
    """Fallback workarounds when LLM fails and we only have clusters."""
    if not clusters:
        return "Teams are adopting incremental approaches and leveraging existing tools"
    return "Teams are adopting incremental approaches and leveraging existing tools"


def generate_insights(
    problems: list[Problem],
    themes: list[Theme],
    query: str,
) -> list[Insight]:
    """Generate insights from problems and themes using the LLM, falling back to rules."""
    config = _config()
    prompts = _prompts(config)

    if not themes:
        return []

    payload_json = json.dumps(
        {
            "query": query,
            "themes": [
                {
                    "theme_id": t.theme_id,
                    "theme_name": t.theme_name,
                    "what_is_happening": t.what_is_happening,
                    "evidence_summary": t.evidence_summary,
                    "strength": t.strength,
                    "trajectory": t.trajectory,
                    "affected_stakeholders": t.affected_stakeholders,
                }
                for t in themes[:6]
            ],
            "problems": [
                {
                    "problem_id": p.problem_id,
                    "problem_statement": p.problem_statement,
                    "who_has_this_problem": p.who_has_this_problem,
                    "severity": p.severity,
                    "theme_ids": p.theme_ids,
                }
                for p in problems[:6]
            ],
        },
        ensure_ascii=False,
        default=str,
    )
    user = f"Input themes and problems:\n{payload_json}\n\nReturn valid JSON: {{\"insights\": [...]}}"

    insight_raw = _llm_json(prompts["insight"], user)
    insight_list: list[dict[str, Any]] = []
    if insight_raw and isinstance(insight_raw.get("insights"), list):
        insight_list = insight_raw["insights"]

    insights: list[Insight] = []
    seen_ids: set[str] = set()

    for raw in insight_list:
        if not isinstance(raw, dict):
            continue
        insight_id = _normalize_text(raw.get("insight_id") or f"insight-{uuid.uuid4().hex[:8]}")
        if insight_id in seen_ids:
            continue
        seen_ids.add(insight_id)
        problem_ids = [str(pid) for pid in raw.get("problem_ids", []) if pid]
        theme_ids = [str(tid) for tid in raw.get("theme_ids", []) if tid]
        insights.append(
            Insight(
                insight_id=insight_id,
                observation=_normalize_text(raw.get("observation") or ""),
                connection=_normalize_text(raw.get("connection") or ""),
                implication=_normalize_text(raw.get("implication") or ""),
                timing=_normalize_text(raw.get("timing") or ""),
                confidence=float(raw.get("confidence") or 0.0),
                problem_ids=problem_ids,
                theme_ids=theme_ids,
            )
        )

    if not insights:
        # Rule-based fallback: combine the top themes into a few synthetic insights.
        for i in range(min(2, len(themes) - 1)):
            a, b = themes[i], themes[i + 1]
            insight_id = f"insight-{a.theme_id}-{b.theme_id}"
            if insight_id in seen_ids:
                continue
            seen_ids.add(insight_id)
            observation = (
                f"Both {a.theme_name} and {b.theme_name} appear in the evidence, "
                f"showing that {a.what_is_happening[:120]} and {b.what_is_happening[:120]}."
            )
            insights.append(
                Insight(
                    insight_id=insight_id,
                    observation=observation,
                    connection=f"{a.theme_name} and {b.theme_name} reinforce each other",
                    implication="The combination creates demand for integrated tooling rather than point solutions.",
                    timing="Evidence is current; the overlap is happening now.",
                    confidence=0.6,
                    problem_ids=[],
                    theme_ids=[a.theme_id, b.theme_id],
                )
            )

    insights.sort(key=lambda i: i.confidence, reverse=True)
    return insights[:8]


def extract_themes_problems_insights(
    clusters: list[SignalCluster],
    query: str,
) -> AnalysisResult:
    """Combined themes, problems, and insights extraction in a single LLM pass."""
    config = _config()
    prompts = _prompts(config)

    if not clusters:
        return AnalysisResult([], [], [], [], [])

    clusters_json = json.dumps(
        [
            {
                "cluster_id": c.cluster_id,
                "label": c.label,
                "dominant_signal_type": c.dominant_signal_type,
                "source_diversity": c.source_diversity,
                "avg_confidence": c.avg_confidence,
                "signals": [
                    {
                        "id": s.id,
                        "entity": s.entity,
                        "metric": s.metric,
                        "value": s.value,
                        "source": s.source,
                        "signal_type": s.signal_type,
                        "extracted_claims": s.extracted_claims,
                    }
                    for s in c.signals[:10]
                ],
            }
            for c in clusters[:10]
        ],
        ensure_ascii=False,
        default=str,
    )
    user = (
        f"Query: {query}\n\n"
        f"Input clusters:\n{clusters_json}\n\n"
        f"Return valid JSON with keys:\n"
        f'- "themes": list of themes with theme_id, theme_name, what_is_happening, evidence_summary, strength, affected_stakeholders, cluster_ids\n'
        f'- "problems": list of problems with problem_id, problem_statement, who_has_this_problem, severity, theme_ids, current_workarounds\n'
        f'- "insights": list of insights with insight_id, observation, connection, implication, timing, confidence, problem_ids, theme_ids\n'
    )

    combined_raw: dict[str, Any] | None = None
    try:
        combined_raw = _llm_json_cached(prompts["theme"], user)  # Use cached version
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM themes+problems+insights failed: %s", exc)

    # Process themes
    theme_list: list[dict[str, Any]] = []
    if combined_raw and isinstance(combined_raw.get("themes"), list):
        theme_list = combined_raw["themes"]

    themes: list[Theme] = []
    cluster_by_id = {c.cluster_id: c for c in clusters}
    seen_ids: set[str] = set()

    for raw in theme_list:
        if not isinstance(raw, dict):
            continue
        theme_id = _normalize_text(raw.get("theme_id") or f"theme-{uuid.uuid4().hex[:8]}")
        if theme_id in seen_ids:
            continue
        seen_ids.add(theme_id)
        cluster_ids = [str(cid) for cid in raw.get("cluster_ids", []) if cid]
        supporting = [cluster_by_id[cid] for cid in cluster_ids if cid in cluster_by_id]
        if not supporting:
            supporting = [clusters[0]]
        all_signals = [s for c in supporting for s in c.signals]
        theme_name = _normalize_text(raw.get("theme_name") or _fallback_theme_name(supporting))
        what_is_happening = _normalize_text(raw.get("what_is_happening") or _fallback_what_is_happening(supporting))
        evidence_summary = _normalize_text(raw.get("evidence_summary") or _fallback_evidence_summary(supporting))
        strength = _normalize_text(raw.get("strength") or _strength(len(all_signals), sum(c.avg_confidence for c in supporting) / len(supporting)))
        affected_stakeholders = [str(s).strip() for s in raw.get("affected_stakeholders") or [] if s]
        if not affected_stakeholders:
            affected_stakeholders = _fallback_affected_stakeholders(supporting)
        trajectory = _normalize_text(raw.get("trajectory") or _trajectory_from_strength(strength))
        themes.append(
            Theme(
                theme_id=theme_id,
                theme_name=theme_name,
                what_is_happening=what_is_happening,
                evidence_summary=evidence_summary,
                strength=strength,
                affected_stakeholders=affected_stakeholders,
                trajectory=trajectory,
                cluster_ids=cluster_ids,
                signal_count=len(all_signals),
                source_count=len({s.source for s in all_signals}),
            )
        )

    # Fallback to rule-based themes if LLM failed
    if not themes and clusters:
        for cluster in clusters[:5]:
            theme_id = f"theme-{uuid.uuid4().hex[:8]}"
            theme_name = _fallback_theme_name([cluster])
            what_is_happening = _fallback_what_is_happening([cluster])
            evidence_summary = _fallback_evidence_summary([cluster])
            strength = _strength(len(cluster.signals), cluster.avg_confidence)
            affected_stakeholders = _fallback_affected_stakeholders([cluster])
            trajectory = _trajectory_from_strength(strength)
            themes.append(
                Theme(
                    theme_id=theme_id,
                    theme_name=theme_name,
                    what_is_happening=what_is_happening,
                    evidence_summary=evidence_summary,
                    strength=strength,
                    affected_stakeholders=affected_stakeholders,
                    trajectory=trajectory,
                    cluster_ids=[cluster.cluster_id],
                    signal_count=len(cluster.signals),
                    source_count=len({s.source for s in cluster.signals}),
                )
            )

    # Process problems
    problem_list: list[dict[str, Any]] = []
    if combined_raw and isinstance(combined_raw.get("problems"), list):
        problem_list = combined_raw["problems"]

    problems: list[Problem] = []
    theme_by_id = {t.theme_id: t for t in themes}
    seen_problem_ids: set[str] = set()

    for raw in problem_list:
        if not isinstance(raw, dict):
            continue
        problem_id = _normalize_text(raw.get("problem_id") or f"problem-{uuid.uuid4().hex[:8]}")
        if problem_id in seen_problem_ids:
            continue
        seen_problem_ids.add(problem_id)
        theme_ids = [str(tid) for tid in raw.get("theme_ids", []) if tid]
        if not theme_ids and themes:
            theme_ids = [themes[0].theme_id]
        supporting_themes: list[Theme] = [theme_by_id[tid] for tid in theme_ids if tid in theme_by_id]
        if not supporting_themes:
            supporting_themes = [themes[0]]
        statement = _dedupe_repeated_words(_normalize_text(raw.get("problem_statement") or _fallback_problem_statement(supporting_themes)))
        who = [str(s).strip() for s in raw.get("who_has_this_problem") or [] if s]
        if not who:
            who = supporting_themes[0].affected_stakeholders
        workarounds = _normalize_text(raw.get("current_workarounds") or _fallback_workarounds(supporting_themes))
        severity = _normalize_text(raw.get("severity") or _severity_from_strength(supporting_themes[0].strength))
        signal_count = sum(t.signal_count for t in supporting_themes)
        problems.append(
            Problem(
                problem_id=problem_id,
                problem_statement=statement,
                who_has_this_problem=who,
                severity=severity,
                theme_ids=theme_ids,
                current_workarounds=workarounds,
                signal_count=signal_count,
            )
        )

    # Fallback to rule-based problems if LLM failed
    if not problems and themes:
        for theme in themes[:3]:
            problem_id = f"problem-{uuid.uuid4().hex[:8]}"
            statement = _fallback_problem_statement([theme])
            who = theme.affected_stakeholders
            workarounds = _fallback_workarounds([theme])
            severity = _severity_from_strength(theme.strength)
            problems.append(
                Problem(
                    problem_id=problem_id,
                    problem_statement=statement,
                    who_has_this_problem=who,
                    severity=severity,
                    theme_ids=[theme.theme_id],
                    current_workarounds=workarounds,
                    signal_count=theme.signal_count,
                )
            )

    # Process insights
    insight_list: list[dict[str, Any]] = []
    if combined_raw and isinstance(combined_raw.get("insights"), list):
        insight_list = combined_raw["insights"]

    insights: list[Insight] = []
    seen_insight_ids: set[str] = set()

    for raw in insight_list:
        if not isinstance(raw, dict):
            continue
        insight_id = _normalize_text(raw.get("insight_id") or f"insight-{uuid.uuid4().hex[:8]}")
        if insight_id in seen_insight_ids:
            continue
        seen_insight_ids.add(insight_id)
        problem_ids = [str(pid) for pid in raw.get("problem_ids", []) if pid]
        theme_ids = [str(tid) for tid in raw.get("theme_ids", []) if tid]
        observation = _normalize_text(raw.get("observation") or "")
        connection = _normalize_text(raw.get("connection") or "")
        implication = _normalize_text(raw.get("implication") or "")
        timing = _normalize_text(raw.get("timing") or "")
        confidence_raw = raw.get("confidence")
        if isinstance(confidence_raw, str):
            # Handle string confidence values like "medium", "high", "low"
            confidence_map = {"low": 0.3, "medium": 0.5, "high": 0.8}
            confidence = confidence_map.get(confidence_raw.lower(), 0.5)
        else:
            confidence = float(confidence_raw or 0.7)
        insights.append(
            Insight(
                insight_id=insight_id,
                observation=observation,
                connection=connection,
                implication=implication,
                timing=timing,
                confidence=confidence,
                problem_ids=problem_ids,
                theme_ids=theme_ids,
            )
        )

    # Fallback to rule-based insights if LLM failed
    if not insights and (themes or problems):
        insight_id = f"insight-{uuid.uuid4().hex[:8]}"
        if themes:
            observation = f"The convergence of {len(themes)} themes suggests a broader shift in {themes[0].theme_name}"
        elif problems:
            observation = f"Addressing {problems[0].problem_statement} requires coordinated effort across stakeholders"
        else:
            observation = "Market signals indicate a broader shift in technology adoption patterns"
        connection = ""
        implication = ""
        timing = ""
        insights.append(
            Insight(
                insight_id=insight_id,
                observation=observation,
                connection=connection,
                implication=implication,
                timing=timing,
                confidence=0.6,
                problem_ids=[p.problem_id for p in problems[:2]],
                theme_ids=[t.theme_id for t in themes[:2]],
            )
        )

    return AnalysisResult([], [], themes, problems, insights)


def analyze_signals(
    raw_signals: list[dict[str, Any]],
    query: str,
    intent: dict[str, Any] | None = None,
    states: dict[str, dict[str, Any]] | None = None,
    research_depth: str = "fast",
) -> Generator[dict[str, Any], None, AnalysisResult]:
    """Run the multi-stage signal analysis pipeline with inline optimization."""
    states = states or {}

    # Get research depth configuration
    depth_config = ResearchDepth.get_config(research_depth)
    max_signals = depth_config["max_signals"]
    max_themes = depth_config["max_themes"]

    if not raw_signals:
        return AnalysisResult([], [], [], [], [])

    # Log input data size for signal analysis
    logger.info("=== SIGNAL ANALYSIS INPUT ===")
    logger.info("Raw signals count: %d", len(raw_signals))
    logger.info("Research depth: %s (max_signals: %d, max_themes: %d)", research_depth, max_signals, max_themes)
    logger.info("Query: %s", query)
    logger.info("Intent: %s", intent)
    logger.info("=== END SIGNAL ANALYSIS INPUT ===")

    yield _update(
        states,
        "HybridAnalysisPipeline: orchestrating Math Engine, Fast SLM, and Strategic Reasoning Engine...",
    )
    start = time.time()

    # Inline optimized analysis (absorbed from HybridAnalysisPipeline.analyze_optimized)
    math_engine = DeterministicMathEngine()
    sanitized = math_engine.sanitize_signals(raw_signals[:max_signals])

    # Pass 1: Classification + Clustering (combined)
    classify_cluster_result = classify_and_cluster_signals(sanitized, intent)
    classified = classify_cluster_result.classified_signals
    clusters = classify_cluster_result.clusters

    logger.info(
        "HybridAnalysisPipeline: Math Engine sanitized %d signals; Fast SLM classify+cluster; "
        "Strategic Reasoning Engine synthesizing",
        len(sanitized),
    )

    # Pass 2: Themes + Problems + Insights (combined)
    themes_problems_insights = extract_themes_problems_insights(clusters, query)
    themes = themes_problems_insights.themes
    problems = themes_problems_insights.problems
    insights = themes_problems_insights.insights

    result = AnalysisResult(
        classified_signals=classified,
        clusters=clusters,
        themes=themes,
        problems=problems,
        insights=insights,
    )

    logger.info(
        "HybridAnalysisPipeline: analyzed %d raw signals into %d themes, %d problems, %d insights in %.2fs",
        len(raw_signals[:max_signals]),
        len(result.themes[:max_themes]),
        len(result.problems),
        len(result.insights),
        time.time() - start,
    )

    # Limit themes to max_themes
    result.themes = result.themes[:max_themes]

    if states and "Signal Analyst" in states:
        states["Signal Analyst"]["analysis"] = {
            "classified_count": len(result.classified_signals),
            "cluster_count": len(result.clusters),
            "theme_count": len(result.themes),
            "theme_names": [t.theme_name for t in result.themes],
            "problem_count": len(result.problems),
            "insight_count": len(result.insights),
        }

    yield _update(
        states,
        f"Analysis complete: {len(result.themes)} themes, "
        f"{len(result.problems)} problems, {len(result.insights)} insights",
        status="completed",
    )

    return result


# ==================== Signal Collector Functions ====================
# Functions merged from signal_collector.py

def _add_source_url(signal: dict[str, Any], url: str) -> dict[str, Any]:
    """Add source_url to a signal dict for proper deduplication."""
    signal["source_url"] = url
    return signal


def _is_excluded_repo(repo: Any) -> bool:
    if not isinstance(repo, dict):
        return False
    text = f"{repo.get('name', '')} {repo.get('description', '')} {repo.get('full_name', '')}".lower()
    return any(k in text for k in EXCLUDED_REPO_KEYWORDS)


def _filter_by_exclusion_terms(signals: list[dict[str, Any]], exclusion_terms: list[str]) -> list[dict[str, Any]]:
    """Filter out signals that contain exclusion terms.

    Args:
        signals: List of signal dictionaries
        exclusion_terms: List of terms to exclude

    Returns:
        Filtered list of signals
    """
    if not exclusion_terms:
        return signals

    filtered = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue

        # Check signal text for exclusion terms
        signal_text = (
            f"{signal.get('entity', '')} "
            f"{signal.get('value', '')} "
            f"{signal.get('metric', '')} "
            f"{signal.get('title', '')} "
            f"{signal.get('description', '')}"
        ).lower()

        logger.info("EXCLUSION CHECK: signal='%s', text_sample='%s'",
                    signal.get('entity', ''), signal_text[:100])

        # Skip if any exclusion term is found
        if any(excl.lower() in signal_text for excl in exclusion_terms):
            logger.info("FILTERED signal containing exclusion term: %s", signal.get('entity', 'unknown'))
            continue

        filtered.append(signal)

    logger.info("Filtered %d signals by exclusion terms (kept %d of %d)",
                len(signals) - len(filtered), len(filtered), len(signals))
    return filtered


def _get_or_create_mcp_source(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT source_id FROM sources WHERE name = ?", ("GitHub MCP",))
    row = cur.fetchone()
    if row:
        return int(row[0])
    return create_source(
        conn,
        name="GitHub MCP",
        source_type="github_mcp",
        trust_tier=90,
        endpoint="mcp://github",
        refresh_frequency="manual",
        owner="mcp",
    )


def _get_or_create_tavily_source(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT source_id FROM sources WHERE name = ?", ("Tavily MCP",))
    row = cur.fetchone()
    if row:
        return int(row[0])
    return create_source(
        conn,
        name="Tavily MCP",
        source_type="tavily_mcp",
        trust_tier=85,
        endpoint="mcp://tavily",
        refresh_frequency="manual",
        owner="mcp",
    )


def _get_or_create_source_by_type(conn: sqlite3.Connection, source_key: str) -> int:
    """Return the source_id for a source key, creating the source row if needed."""
    spec = _SOURCE_REGISTRY.get(source_key)
    if not spec:
        spec = {
            "name": source_key.title(),
            "source_type": source_key,
            "trust_tier": 70,
            "endpoint": "",
            "owner": "research",
        }
    cur = conn.cursor()
    cur.execute("SELECT source_id FROM sources WHERE name = ?", (spec["name"],))
    row = cur.fetchone()
    if row:
        return int(row[0])
    return create_source(
        conn,
        name=spec["name"],
        source_type=spec["source_type"],
        trust_tier=spec["trust_tier"],
        endpoint=spec["endpoint"],
        refresh_frequency="manual",
        owner=spec["owner"],
    )


def _create_ingestion_run(
    conn: sqlite3.Connection,
    source_id: int,
    now: str,
    signals_count: int,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ingestion_runs
            (source_id, start_time, end_time, status, signals_created)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_id, now, now, "completed", signals_count),
    )
    conn.commit()
    if cur.lastrowid is None:
        raise RuntimeError("Failed to create ingestion run")
    return cur.lastrowid


def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-quality signal for each canonical (entity, metric, source_url) triplet.

    When source_url is available, distinct articles/repos with different URLs are preserved.
    When source_url is missing, falls back to (entity, metric) for backward compatibility.
    """
    entities = [str(s.get("entity", "")).strip() for s in signals if isinstance(s, dict) and s.get("entity")]
    registry = ConceptRegistry(use_llm=False)
    registry.register(entities)

    best: dict[tuple[str, ...], dict[str, Any]] = {}
    for s in signals:
        if not isinstance(s, dict):
            continue
        entity = str(s.get("entity", "")).strip()
        metric = str(s.get("metric", "")).strip()
        if not entity or not metric:
            continue
        canonical_entity = registry.canonical(entity)
        s["entity"] = canonical_entity

        # Use source_url in deduplication key when available to preserve distinct articles
        source_url = str(s.get("source_url", "")).strip()
        if source_url:
            key: tuple[str, ...] = (canonical_entity, metric, source_url)
        else:
            key = (canonical_entity, metric)

        existing = best.get(key)
        if existing is None or float(s.get("evidence_quality", 0) or 0) > float(existing.get("evidence_quality", 0) or 0):
            best[key] = s
    return list(best.values())


def _rank_tavily_signals(signals: list[dict[str, Any]], plan: RetrievalPlan) -> list[dict[str, Any]]:
    """Score Tavily signals by authority * relevance and filter using the plan."""
    ranked: list[tuple[float, dict[str, Any]]] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        entity = str(s.get("entity", "")).strip()
        m = _URL_RE.search(entity)
        url = m.group(1) if m else ""
        title = entity.split("(")[0].strip() or entity
        content = str(s.get("value", ""))

        authority = article_authority({"url": url, "score": float(s.get("evidence_quality", 0) or 0) / 100.0})
        relevance = article_relevance({"title": title, "content": content[:500]}, plan)

        s["_authority"] = authority
        s["_relevance"] = relevance
        s["_retrieval_score"] = round(authority * relevance, 3)

        if relevance < plan.min_relevance or authority < plan.min_authority:
            continue
        ranked.append((s["_retrieval_score"], s))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in ranked]


def _dedupe_tavily_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-retrieval-score signal per canonical URL or title."""
    best: dict[str, dict[str, Any]] = {}
    for s in signals:
        if not isinstance(s, dict):
            continue
        entity = str(s.get("entity", "")).strip()
        m = _URL_RE.search(entity)
        key = m.group(1) if m else entity.lower()
        existing = best.get(key)
        if existing is None or float(s.get("_retrieval_score", 0) or 0) > float(existing.get("_retrieval_score", 0) or 0):
            best[key] = s
    return list(best.values())


def _insert_signals(
    conn: sqlite3.Connection,
    source_id: int,
    run_id: int,
    query: str,
    now: str,
    mcp_calls: list[dict[str, Any]],
    signals: list[dict[str, Any]],
) -> None:
    cur = conn.cursor()
    cur.execute("SELECT source_type FROM sources WHERE source_id = ?", (source_id,))
    row = cur.fetchone()
    source_type = row[0] if row else "github_mcp"
    ingest_date = now[:10]
    default_raw = json.dumps({"query": query, "mcp_calls": [c.get("tool") for c in mcp_calls if isinstance(c, dict)]})
    for s in signals:
        if not isinstance(s, dict):
            continue
        raw_payload = s.get("raw_payload", default_raw)
        if not isinstance(raw_payload, str):
            raw_payload = json.dumps(raw_payload)
        normalized_payload = s.get("normalized_payload") or default_raw
        if not isinstance(normalized_payload, str):
            normalized_payload = json.dumps(normalized_payload)
        evidence_quality = int(s.get("evidence_quality", 0) or 0)
        confidence = float(s.get("confidence", evidence_quality / 100.0) or 0)
        cur.execute(
            """
            INSERT INTO signals
                (source_id, ingestion_run_id, source_type, entity, metric, value,
                 unit, timestamp, ingest_date, raw_payload, normalized_payload,
                 evidence_quality, confidence, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                run_id,
                source_type,
                s["entity"],
                s["metric"],
                s["value"],
                s.get("unit", ""),
                s.get("timestamp", now),
                ingest_date,
                raw_payload,
                normalized_payload,
                evidence_quality,
                confidence,
                s.get("tags", query),
            ),
        )
    conn.commit()


def _fallback_signals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.source_id, s.entity, s.metric, s.value, s.timestamp, s.evidence_quality
        FROM signals s
        JOIN sources src ON src.source_id = s.source_id
        WHERE src.source_type = 'fixture_csv'
        ORDER BY s.signal_id DESC
        LIMIT 20
        """
    )
    columns = ["source_id", "entity", "metric", "value", "timestamp", "evidence_quality"]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def collect_signals(
    query: str,
    conn: sqlite3.Connection,
    states: dict[str, dict[str, Any]],
    intent: dict[str, Any] | None = None,
    ctx: PipelineContext | None = None,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    """Run Signal Collection: query sources and produce raw, normalized signals.

    Args:
        query: Search query
        conn: Database connection
        states: UI state dictionary
        intent: User intent dictionary
        ctx: PipelineContext with technology profile (optional, for exclusion filtering)
    """
    mcp_calls: list[dict[str, Any]] = []
    mcp_calls_lock = threading.Lock()
    now = datetime.now(timezone.utc).isoformat()

    # Get technology profile for exclusion filtering if context provided
    profile = ctx.profile if ctx else None
    exclusion_terms = profile.exclusion_terms if profile else []
    logger.info("EXCLUSION DEBUG: profile=%s, exclusion_terms=%s",
                profile.canonical_name if profile else "NONE", exclusion_terms)

    def _update_status(status: str, duration: float | None = None, detail: str = "") -> dict[str, Any]:
        states["Signal Analyst"]["status"] = status
        if duration is not None:
            states["Signal Analyst"]["duration"] = duration
        if detail:
            states["Signal Analyst"]["detail"] = detail
        states["Signal Analyst"]["mcp_calls"] = copy.deepcopy(mcp_calls)
        return {
            "type": "update",
            "status": copy.deepcopy(states),
            "agent": "Signal Analyst",
        }

    def _call(server: str, tool: str, arguments: dict[str, Any]) -> Any:
        start = time.time()
        logger.info("Signal Analyst MCP start: %s.%s", server, tool)
        result = call_tool(server, tool, arguments)
        elapsed = result.duration if result.duration is not None else time.time() - start
        data_text = result.data if isinstance(result.data, str) else ""
        # Some MCP servers return a tool-level error inside a successful response.
        tool_ok = result.success and "### Error" not in data_text
        error = result.error
        if not error and "### Error" in data_text:
            error = data_text.split("### Error")[1].split("\n")[0].strip(": ")
        logger.info(
            "Signal Analyst MCP done: %s.%s ok=%s duration=%.3fs error=%s",
            server,
            tool,
            tool_ok,
            elapsed,
            error or "none",
        )
        with mcp_calls_lock:
            mcp_calls.append({
                "server": server,
                "tool": tool,
                "success": tool_ok,
                "duration": elapsed,
                "error": error,
            })
        return result

    start = time.time()
    yield _update_status("running", 0.0, "Initializing signal collection...")

    # Build a query-specific research plan that selects sources, signal types,
    # and query expansion based on the user intent.
    research_plan = build_research_plan(query, intent)
    plan = research_plan.retrieval_plan

    # Inject profile-based search expansion terms if available
    if profile and profile.search_expansion:
        logger.info("Injecting profile search expansion terms: %s", profile.search_expansion)
        existing_tavily = plan.tavily_queries or []
        plan.tavily_queries = profile.search_expansion[:5] + existing_tavily
        plan.tavily_queries = list(dict.fromkeys(plan.tavily_queries))[:8]  # dedupe, limit to 8

        existing_github = plan.github_queries or []
        plan.github_queries = profile.search_expansion[:3] + existing_github
        plan.github_queries = list(dict.fromkeys(plan.github_queries))[:6]
        logger.info("After injection - tavily_queries: %s", plan.tavily_queries)
        logger.info("After injection - github_queries: %s", plan.github_queries)

    expanded_queries = plan.github_queries or [query]
    states.setdefault("Research Planner", {
        "status": "pending",
        "duration": None,
        "detail": "",
        "mcp_calls": [],
        "discovered_repos": [],
        "expanded_queries": [],
    })
    states["Research Planner"]["status"] = "completed"
    states["Research Planner"]["detail"] = f"Plan for {plan.primary}"
    states["Research Planner"]["expanded_queries"] = (
        (plan.github_queries or []) + (plan.tavily_queries or [])
    )
    yield {
        "type": "update",
        "status": copy.deepcopy(states),
        "agent": "Research Planner",
    }
    logger.info(
        "Signal Analyst query=%s intent=%s themes=%s",
        query,
        intent.get("intent") if intent else "none",
        expanded_queries,
    )
    search_terms = list(
        {
            t.lower()
            for term in ([plan.primary] + plan.aliases + expanded_queries)
            for t in re.findall(r"\b\w+\b", term)
            if len(t) > 2
        }
    )
    states["Signal Analyst"]["expanded_queries"] = expanded_queries
    yield _update_status("running", 0.0, f"Searching themes: {', '.join(expanded_queries[:3])}")

    def _clean_playwright_snapshot(text: str) -> str:
        """Strip Playwright accessibility-tree noise from a captured snapshot."""
        # Remove lines that are pure accessibility-tree UI chrome.
        text = re.sub(
            r"^-\s*(generic|button|slider|img|link|textbox|checkbox|radio|"
            r"menuitem|tab|listitem|treeitem|progressbar|scrollbar|"
            r"generic\s*\[active\]|button\s*\[active\]|slider\s*\[active\]|"
            r"img\s*\[active\]|link\s*\[active\]).*$",
            "",
            text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        # Strip inline [ref=e1] / [cursor=pointer] markers.
        text = re.sub(r"\[ref=[^\]]+\]", "", text)
        text = re.sub(r"\[cursor=[^\]]+\]", "", text)
        # Collapse remaining whitespace.
        return re.sub(r"\s+", " ", text).strip()

    def _read_playwright_snapshot(result_text: str | None) -> str:
        if not result_text or "### Error" in result_text:
            return ""
        match = re.search(r"\[Snapshot\]\((\.playwright-mcp/[^)]+)\)", result_text)
        if not match:
            return ""
        snapshot_path = os.path.join(os.getcwd(), match.group(1))
        try:
            with open(snapshot_path, encoding="utf-8") as handle:
                return _clean_playwright_snapshot(handle.read())
        except FileNotFoundError:
            return ""

    def _mention_count(text: str) -> int:
        lowered = text.lower()
        return sum(lowered.count(term) for term in search_terms if term)

    def _as_json(result: Any) -> Any:
        """Return JSON-decoded data from an MCP result, handling pre-parsed data."""
        if isinstance(result, (dict, list)):
            return result
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None

    repo_map: dict[str, dict[str, Any]] = {}
    any_search_failed = False

    with ThreadPoolExecutor(max_workers=5) as executor:
        search_futures = [
            executor.submit(
                _call,
                "github",
                "search_repositories",
                {"query": theme, "per_page": REPOS_PER_THEME},
            )
            for theme in expanded_queries
        ]
        for future in search_futures:
            try:
                result = future.result(timeout=MCP_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                logger.warning("GitHub search timed out or failed: %s", exc)
                any_search_failed = True
                continue
            if not result.success:
                any_search_failed = True
                continue
            data = _as_json(result.data)
            if not isinstance(data, dict):
                any_search_failed = True
                continue
            for repo in data.get("items", []):
                if _is_excluded_repo(repo):
                    continue
                full_name = repo.get("full_name", "")
                if not full_name:
                    continue
                if full_name not in repo_map or repo_authority(repo) > repo_authority(repo_map[full_name]):
                    repo_map[full_name] = repo

    yield _update_status("running")

    # Rank and filter repos by authority * relevance using the retrieval plan.
    items = rank_repos(list(repo_map.values()), plan)

    if not items:
        logger.warning("Signal Analyst: no repos for themes=%s; attempting fallback to primary technology", expanded_queries)
        # Fallback: search GitHub for the bare primary technology
        primary_fallback = plan.primary or (expanded_queries[0] if expanded_queries else query)
        logger.info("Signal Analyst: fallback GitHub search for primary technology: '%s'", primary_fallback)
        try:
            fallback_result = _call(
                "github",
                "search_repositories",
                {"query": primary_fallback, "per_page": REPOS_PER_THEME},
            )
            if fallback_result.success:
                fallback_data = _as_json(fallback_result.data)
                if isinstance(fallback_data, dict):
                    for repo in fallback_data.get("items", []):
                        if _is_excluded_repo(repo):
                            continue
                        full_name = repo.get("full_name", "")
                        if not full_name:
                            continue
                        if full_name not in repo_map or repo_authority(repo) > repo_authority(repo_map[full_name]):
                            repo_map[full_name] = repo
                    items = rank_repos(list(repo_map.values()), plan)
                    logger.info("Signal Analyst: fallback search found %d repos", len(items))
        except Exception as exc:
            logger.error("Signal Analyst: fallback GitHub search failed: %s", exc)

        if not items:
            logger.warning("Signal Analyst: no repos found even with fallback; falling through to Tavily")
            discovered_repos: list[dict[str, Any]] = []
        else:
            discovered_repos = [
                {
                    "full_name": r.get("full_name", ""),
                    "name": r.get("name", ""),
                    "description": r.get("description", "") or "",
                    "language": r.get("language", "") or "",
                    "html_url": r.get("html_url", "") or "",
                }
                for r in items
            ]
    else:
        discovered_repos = [
            {
                "full_name": r.get("full_name", ""),
                "name": r.get("name", ""),
                "description": r.get("description", "") or "",
                "language": r.get("language", "") or "",
                "html_url": r.get("html_url", "") or "",
            }
            for r in items
        ]
    states["Signal Analyst"]["discovered_repos"] = [
        r["full_name"] for r in discovered_repos[:SEARCH_PAGE_SIZE]
    ]

    github_signals: list[dict[str, Any]] = []
    github_source_id = _get_or_create_mcp_source(conn)
    total_count = len(items)
    top_repo = items[0] if items else None

    github_signals.append({
        "source_id": github_source_id,
        "entity": query,
        "metric": "github_repo_results",
        "value": str(total_count),
        "timestamp": now,
        "evidence_quality": 90,
    })

    # Detail work for the top N repos. Skip low-star repos unless we have very few.
    for idx, repo in enumerate(items[:MAX_REPO_DETAIL]):
        full_name = repo.get("full_name", "")
        if "/" not in full_name:
            continue
        owner, _, repo_name = full_name.partition("/")

        stars = repo.get("stargazers_count") or 0
        if int(stars) < MIN_DETAIL_STARS and total_count >= 3:
            logger.info("Skipping detail extraction for %s (stars=%s < %s)", full_name, stars, MIN_DETAIL_STARS)
            continue
        forks = repo.get("forks_count") or 0
        pushed = repo.get("pushed_at") or repo.get("updated_at")
        days_since_push = 0
        if pushed:
            try:
                dt = datetime.fromisoformat(str(pushed).replace("Z", "+00:00"))
                days_since_push = (datetime.now(timezone.utc) - dt).days
            except (ValueError, TypeError):
                days_since_push = 0

        github_signals.append({
            "source_id": github_source_id,
            "entity": full_name,
            "metric": "github_stars",
            "value": str(int(stars)),
            "timestamp": now,
            "evidence_quality": 90,
        })
        _add_source_url(github_signals[-1], repo.get("html_url", ""))
        github_signals.append({
            "source_id": github_source_id,
            "entity": full_name,
            "metric": "github_forks",
            "value": str(int(forks)),
            "timestamp": now,
            "evidence_quality": 90,
        })
        _add_source_url(github_signals[-1], repo.get("html_url", ""))
        github_signals.append({
            "source_id": github_source_id,
            "entity": full_name,
            "metric": "github_recency",
            "value": str(int(days_since_push)),
            "timestamp": now,
            "evidence_quality": 80,
        })
        _add_source_url(github_signals[-1], repo.get("html_url", ""))

        with ThreadPoolExecutor(max_workers=2) as detail_executor:
            commits_future = detail_executor.submit(
                _call,
                "github",
                "list_commits",
                {"owner": owner, "repo": repo_name, "per_page": 5},
            )
            issues_future = detail_executor.submit(
                _call,
                "github",
                "list_issues",
                {"owner": owner, "repo": repo_name, "state": "open", "per_page": 5},
            )
            commits_result = commits_future.result()
            issues_result = issues_future.result()

        yield _update_status("running", 0.0, f"Analyzing {idx + 1}/{MAX_REPO_DETAIL}: {full_name}")

        if commits_result.success:
            commits = _as_json(commits_result.data)
            if isinstance(commits, list):
                authors = {
                    c.get("commit", {}).get("author", {}).get("name")
                    for c in commits
                    if isinstance(c, dict)
                }
                authors.discard(None)
                commit_messages = "; ".join(
                    str(c.get("commit", {}).get("message", "")).split("\n")[0]
                    for c in commits
                    if isinstance(c, dict)
                )[:500]
                github_signals.append({
                    "source_id": github_source_id,
                    "entity": full_name,
                    "metric": "github_commits",
                    "value": str(len(commits)),
                    "timestamp": now,
                    "evidence_quality": 90,
                })
                _add_source_url(github_signals[-1], repo.get("html_url", ""))
                github_signals.append({
                    "source_id": github_source_id,
                    "entity": full_name,
                    "metric": "github_contributors",
                    "value": str(len(authors)),
                    "timestamp": now,
                    "evidence_quality": 80,
                })
                _add_source_url(github_signals[-1], repo.get("html_url", ""))
                if commit_messages:
                    github_signals.append({
                        "source_id": github_source_id,
                        "entity": full_name,
                        "metric": "github_commit_messages",
                        "value": commit_messages,
                        "timestamp": now,
                        "evidence_quality": 80,
                    })
                    _add_source_url(github_signals[-1], repo.get("html_url", ""))

        if issues_result.success:
            issues = _as_json(issues_result.data)
            if isinstance(issues, list):
                github_signals.append({
                    "source_id": github_source_id,
                    "entity": full_name,
                    "metric": "github_open_issues",
                    "value": str(len(issues)),
                    "timestamp": now,
                    "evidence_quality": 80,
                })
                _add_source_url(github_signals[-1], repo.get("html_url", ""))
                issue_titles = "; ".join(
                    str(issue.get("title", "")).strip()
                    for issue in issues
                    if isinstance(issue, dict) and issue.get("title")
                )[:700]
                if issue_titles:
                    github_signals.append({
                        "source_id": github_source_id,
                        "entity": full_name,
                        "metric": "github_issue_titles",
                        "value": issue_titles,
                        "timestamp": now,
                        "evidence_quality": 85,
                    })
                    _add_source_url(github_signals[-1], repo.get("html_url", ""))

    yield _update_status("running", 0.0, "Searching code patterns...")
    code_query = plan.primary or (expanded_queries[0] if expanded_queries else query)
    if code_query:
        _call("github", "search_code", {"query": f"{code_query} in:file"})
    yield _update_status("running", 0.0, "Fetching Tavily market signals...")

    if top_repo and top_repo.get("html_url"):
        yield _update_status("running", 0.0, f"Analyzing documentation: {top_repo['full_name']}")
        page = _call("playwright", "browser_navigate", {"url": top_repo["html_url"]})
        yield _update_status("running", 0.0, "Processing documentation content...")
        page_text = _read_playwright_snapshot(page.data)
        if page_text:
            github_signals.append({
                "source_id": github_source_id,
                "entity": top_repo["full_name"],
                "metric": "web_page_text",
                "value": str(len(page_text)),
                "timestamp": now,
                "evidence_quality": 70,
            })
            github_signals.append({
                "source_id": github_source_id,
                "entity": top_repo["full_name"],
                "metric": "web_page_mentions",
                "value": str(_mention_count(page_text)),
                "timestamp": now,
                "evidence_quality": 70,
            })

    # Tavily MCP: market intelligence and research signals.
    tavily_signals: list[dict[str, Any]] = []
    tavily_queries = plan.tavily_queries or [query]
    try:
        logger.info("Signal Analyst Tavily queries: %s", tavily_queries)
        provider = TavilyProvider()
        # Use the second LLM-generated term as the primary fallback; search it only once.
        fallback_query = tavily_queries[1] if len(tavily_queries) > 1 else ""
        primary_q = tavily_queries[0]
        extra_qs = [q for q in tavily_queries[1:] if q != fallback_query]
        with ThreadPoolExecutor(max_workers=4) as tavily_executor:
            futures = [tavily_executor.submit(provider.search_with_fallback, primary_q, fallback_query, int(MCP_TIMEOUT))]
            futures += [tavily_executor.submit(provider.search_with_fallback, q, "", int(MCP_TIMEOUT)) for q in extra_qs]
            results = [f.result(timeout=MCP_TIMEOUT) for f in futures]

        all_search_calls: list[dict[str, Any]] = []
        raw_tavily_signals: list[dict[str, Any]] = []
        for search_signals, search_calls in results:
            raw_tavily_signals.extend(search_signals)
            all_search_calls.extend(search_calls)

        # Rank and filter Tavily signals by authority * relevance, then dedupe.
        tavily_signals = _rank_tavily_signals(raw_tavily_signals, plan)
        tavily_signals = _dedupe_tavily_signals(tavily_signals)

        logger.info("Signal Analyst Tavily kept %d signals", len(tavily_signals))
        with mcp_calls_lock:
            mcp_calls.extend(all_search_calls)
    except Exception as exc:  # noqa: BLE001
        logger.error("Signal Analyst Tavily search failed for query '%s': %s", primary_q, exc)
        with mcp_calls_lock:
            mcp_calls.append({
                "server": "tavily",
                "tool": "tavily_search",
                "success": False,
                "duration": 0.0,
                "error": str(exc),
            })

    # Playwright MCP: dynamically gather documentation and product-page evidence.
    playwright_signals: list[dict[str, Any]] = []
    targets: list[tuple[str, str]] = []
    for s in tavily_signals:
        entity = str(s.get("entity", ""))
        m = _URL_RE.search(entity)
        if m:
            url = m.group(1)
            label = entity.split("(")[0].strip() or url
            targets.append((label, url))
    for r in discovered_repos[:2]:
        html_url = r.get("html_url")
        if html_url:
            targets.append((r.get("full_name", "repo"), html_url))
    # No generic fallback URLs: Playwright only fetches pages the pipeline itself
    # discovered from Tavily or GitHub so evidence stays tied to the query.
    seen_urls: set[str] = set()
    for label, url in targets[:5]:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            page = _call("playwright", "browser_navigate", {"url": url})
            yield _update_status("running")
            page_text = _read_playwright_snapshot(page.data)
            if page_text:
                topic_text = " ".join(str(t) for t in (intent.get("topics", []) if intent else [query]))
                mentions = sum(page_text.lower().count(t.lower()) for t in topic_text.split() if len(t) > 2)
                playwright_signals.append({
                    "source_id": github_source_id,
                    "entity": f"{label} ({url})",
                    "metric": "web_page_text",
                    "value": page_text[:2000],
                    "timestamp": now,
                    "evidence_quality": 80,
                })
                playwright_signals.append({
                    "source_id": github_source_id,
                    "entity": f"{label} ({url})",
                    "metric": "web_page_mentions",
                    "value": str(mentions),
                    "timestamp": now,
                    "evidence_quality": 80,
                })
        except Exception as exc:  # noqa: BLE001
            logger.info("Playwright navigation failed for %s: %s", url, exc)

    github_signals.extend(playwright_signals)

    # Research-oriented sources from the plan (Hacker News, Reddit, jobs, etc.).
    research_raw_signals: list[dict[str, Any]] = []
    search_terms = list({t for t in (plan.primary, query) if t})
    if not search_terms:
        search_terms = [query]

    def _collect_research_source(source_name: str, term: str) -> list[dict[str, Any]]:
        try:
            if source_name == "hackernews":
                return search_hackernews(term, max_results=5)
            if source_name == "reddit":
                return search_reddit(term, max_results=5)
            if source_name == "jobs":
                return search_jobs(term, max_results=5)
            if source_name == "producthunt":
                return search_producthunt(term, max_results=5)
            if source_name == "news":
                return search_news(term, max_results=5)
            if source_name == "firecrawl":
                return search_firecrawl(term, max_results=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Research source %s failed for %s: %s", source_name, term, exc)
        return []

    selected_sources = set(research_plan.sources)

    # Build set of registry names that should be active
    active_registry_names = set()
    for source in selected_sources:
        registry_name = _SOURCE_NAME_MAPPING.get(source, source)
        active_registry_names.add(registry_name)

    for source_name in _SOURCE_REGISTRY:
        if source_name not in active_registry_names:
            logger.info("Skipping %s for %s intent", source_name, intent.get("intent") if intent else "unknown")

    # Debug: Log which sources will actually be executed
    research_sources = [s for s in research_plan.sources if s not in ("github", "tavily", "context7")]
    logger.info("Selected research sources for execution: %s", research_sources)

    for source_name in research_plan.sources:
        if source_name in ("github", "tavily", "context7"):
            continue
        for term in search_terms[:2]:
            with mcp_calls_lock:
                sigs = _collect_research_source(source_name, term)
            research_raw_signals.extend(sigs)
            if sigs:
                mcp_calls.append({
                    "server": source_name,
                    "tool": "search",
                    "success": True,
                    "duration": 0.0,
                    "error": "",
                })

    # Combine and deduplicate raw signals before normalization.
    all_raw_signals = github_signals + tavily_signals + research_raw_signals
    all_raw_signals = _dedupe_signals(all_raw_signals)

    # JIT fallback when primary sources returned very few signals.
    if len(all_raw_signals) < 3:
        primary_topic = plan.primary or query
        jit_signals, jit_call = synthesize_and_run_tool(query, primary_topic)
        if jit_signals:
            all_raw_signals.extend(jit_signals)
            mcp_calls.append(jit_call)

    all_raw_signals = _dedupe_signals(all_raw_signals)
    yield _update_status("running", 0.0, f"Normalizing {len(all_raw_signals)} raw signals...")
    normalized_signals: list[Signal] = normalize_signals(all_raw_signals, plan, use_llm=False)

    # Apply exclusion term filtering if profile provided
    if exclusion_terms:
        logger.info("Applying exclusion term filtering with %d terms", len(exclusion_terms))
        # Convert Signal objects to dicts for filtering
        signal_dicts_for_filtering = []
        for sig in normalized_signals:
            signal_dicts_for_filtering.append({
                "entity": sig.entity,
                "value": sig.value,
                "metric": sig.metric,
                "title": sig.problem if sig.problem else "",
                "description": sig.evidence if sig.evidence else "",
            })

        filtered_dicts = _filter_by_exclusion_terms(signal_dicts_for_filtering, exclusion_terms)
        filtered_entities = {s["entity"] for s in filtered_dicts}

        # Keep only signals that passed the filter
        normalized_signals = [sig for sig in normalized_signals if sig.entity in filtered_entities]
        logger.info("After exclusion filtering: %d signals remain", len(normalized_signals))

    # Signal saturation detection: if confidence is already high, stop collecting
    max_signals = 20  # default for fast mode
    try:
        from ode.research import ResearchDepth
        research_depth = plan.research_depth if hasattr(plan, 'research_depth') else ResearchDepth.get_default()
        depth_config = ResearchDepth.get_config(research_depth)
        max_signals = depth_config["max_signals"]
    except ImportError:
        pass  # Use default if circular import

    # Check for signal saturation
    if len(normalized_signals) >= max_signals:
        avg_confidence = sum(sig.confidence for sig in normalized_signals) / len(normalized_signals)
        if avg_confidence >= 0.8:  # High confidence threshold
            logger.info("Signal saturation reached: avg confidence %.2f with %d signals, stopping collection", avg_confidence, len(normalized_signals))
            normalized_signals = normalized_signals[:max_signals]

    # Convert normalized Signal objects into insertion-ready dicts.
    signal_dicts: list[dict[str, Any]] = []
    for sig in normalized_signals:
        raw_ts = sig.raw_metadata.get("timestamp") if sig.raw_metadata else None
        ts = raw_ts or now
        value = sig.problem or sig.evidence or sig.value
        payload = {
            "signal_type": sig.signal_type,
            "problem": sig.problem,
            "evidence": sig.evidence,
            "original_metric": sig.metric,
            "source_url": sig.source_url,
            "frequency": sig.frequency,
            "trend": sig.trend,
            "raw_metadata": sig.raw_metadata,
        }
        signal_dicts.append({
            "entity": sig.entity,
            "metric": sig.signal_type if sig.signal_type != "unknown" else sig.metric,
            "value": value,
            "timestamp": ts,
            "evidence_quality": int(sig.confidence * 100),
            "confidence": sig.confidence,
            "source_type": sig.source,
            "source_url": sig.source_url,
            "tags": sig.signal_type,
            "raw_payload": sig.raw_metadata,
            "normalized_payload": payload,
        })

    # Group normalized signals by source, create ingestion runs, and persist.
    by_source: dict[str, list[dict[str, Any]]] = {}
    for s in signal_dicts:
        by_source.setdefault(s["source_type"], []).append(s)

    run_ids: list[int] = []
    for source_key, group in by_source.items():
        source_id = _get_or_create_source_by_type(conn, source_key)
        run_id = _create_ingestion_run(conn, source_id, now, len(group))
        _insert_signals(conn, source_id, run_id, query, now, mcp_calls, group)
        run_ids.append(run_id)

    signal_counts: dict[str, int] = {}
    for s in signal_dicts:
        key = str(s.get("source_type", "unknown")).replace("_mcp", "")
        signal_counts[key] = signal_counts.get(key, 0) + 1
    states["Signal Analyst"]["signal_counts"] = signal_counts

    duration = time.time() - start
    summary_parts = [f"{k} {v}" for k, v in sorted(signal_counts.items(), key=lambda x: -x[1])]
    yield _update_status(
        "completed",
        duration,
        f"{len(signal_dicts)} normalized signals ({', '.join(summary_parts)}) from {len(discovered_repos)} repos",
    )
    return {
        "signals": signal_dicts,
        "raw_signals": signal_dicts,
        "normalized_signals": normalized_signals,
        "discovered_repos": discovered_repos,
        "run_ids": run_ids,
        "github_run_id": run_ids[0] if run_ids else 0,
        "tavily_run_id": run_ids[1] if len(run_ids) > 1 else 0,
        "used_fallback": False,
        "collection_metadata": {
            "signal_counts": signal_counts,
            "sources_queried": list(by_source.keys()),
            "duration": duration,
        },
    }


# ==================== Trend Analyst Functions ====================

def _to_float(value: str) -> float:
    """Convert string to float with error handling."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _fetch_signals(
    conn: sqlite3.Connection,
    repo_lookup: dict[str, dict[str, Any]],
    run_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return the current signals enriched with repository context."""
    cur = conn.cursor()
    if run_ids:
        placeholders = ",".join("?" * len(run_ids))
        cur.execute(
            f"""
            SELECT s.signal_id, s.entity, s.metric, s.value, s.evidence_quality, src.source_type
            FROM signals s
            JOIN sources src ON src.source_id = s.source_id
            WHERE src.source_type != 'fixture_csv'
              AND s.ingestion_run_id IN ({placeholders})
            ORDER BY s.signal_id
            """,
            run_ids,
        )
    else:
        # Without run_ids we cannot distinguish this query's signals from prior runs,
        # so return an empty list rather than bleeding evidence across sessions.
        return []
    signals: list[dict[str, Any]] = []
    for signal_id, entity, metric, value, eq, source_type in cur.fetchall():
        repo = repo_lookup.get(entity) if source_type == "github_mcp" else None
        if repo is not None and not isinstance(repo, dict):
            repo = None
        signals.append(
            {
                "signal_id": int(signal_id),
                "entity": str(entity),
                "metric": str(metric),
                "value": str(value),
                "evidence_quality": float(eq),
                "source_type": str(source_type),
                "repo_name": repo.get("name") if repo else None,
                "repo_description": repo.get("description") if repo else "",
                "repo_language": repo.get("language") if repo else "",
            }
        )
    return signals


def _insert_market_trend(
    conn: sqlite3.Connection,
    trend: MarketTrend,
    signal_ids: list[int],
    now: str,
) -> Trend:
    """Persist a market trend and its contributing signal IDs."""
    cur = conn.cursor()
    forecast = {
        "summary": trend.summary,
        "supporting_signals": trend.supporting_signals,
        "friction": getattr(trend, "friction", ""),
        "gap": getattr(trend, "gap", ""),
    }
    status = "Active" if trend.evidence_count >= MIN_SIGNALS_FOR_ACTIVE else "Candidate"
    momentum = float(trend.confidence)
    cur.execute(
        """
        INSERT INTO trends (
            entity, metric, start_date, last_updated_date, end_date,
            status, momentum, signal_volume, evidence_quality,
            growth_velocity, created_date, forecast
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trend.name,
            trend.metric,
            now,
            now,
            None,
            status,
            momentum,
            trend.evidence_count,
            trend.evidence_quality,
            momentum / 10.0,
            now,
            json.dumps(forecast),
        ),
    )
    trend_id = int(cur.lastrowid) if cur.lastrowid is not None else 0

    for sid in signal_ids:
        cur.execute(
            "INSERT INTO trend_signals (trend_id, signal_id) VALUES (?, ?)",
            (trend_id, sid),
        )

    return Trend(
        trend_id=trend_id,
        entity=trend.name,
        metric=trend.metric,
        start_date=now,
        last_updated_date=now,
        end_date=None,
        status=status,
        momentum=momentum,
        signal_volume=trend.evidence_count,
        evidence_quality=trend.evidence_quality,
        growth_velocity=momentum / 10.0,
        created_date=now,
        forecast=forecast,
        contributing_signal_ids=signal_ids,
    )


def trend_analyst(
    conn: sqlite3.Connection,
    signal_output: dict[str, Any] | None,
    states: dict[str, dict[str, Any]],
    intent: dict[str, Any] | None = None,
    analysis_result: AnalysisResult | None = None,
) -> Generator[dict[str, Any], None, list[Trend]]:
    """Synthesize repository signals into market-level trends using Sequential Thinking.

    Args:
        conn: Database connection
        signal_output: Legacy signal output (deprecated, use analysis_result instead)
        states: Agent states for UI updates
        intent: User intent for context
        analysis_result: AnalysisResult from Signal Analyst (preferred over signal_output)
    """

    def _update_status(status: str, detail: str = "") -> dict[str, Any]:
        states["Trend Analyst"]["status"] = status
        if detail:
            states["Trend Analyst"]["detail"] = detail
        return {
            "type": "update",
            "status": copy.deepcopy(states),
            "agent": "Trend Analyst",
        }

    start = time.time()
    yield _update_status("running", "Initializing trend synthesis...")

    now = datetime.now(timezone.utc).isoformat()

    # Prefer analysis_result over signal_output
    if analysis_result:
        discovered: list[dict[str, Any]] = []
        repo_lookup: dict[str, dict[str, Any]] = {}
        signals: list[dict[str, Any]] = []

        # Extract signals from analysis_result instead of re-fetching from database
        if analysis_result.classified_signals:
            signals = [
                {
                    "signal_id": s.id,
                    "entity": s.entity,
                    "metric": s.metric,
                    "value": s.value,
                    "evidence_quality": s.evidence_quality,
                    "source_type": s.source,
                }
                for s in analysis_result.classified_signals
            ]
            # When using analysis_result, we need to fetch actual signal IDs from database
            # since ClassifiedSignal.id is a synthetic string, not a database ID
            logger.info("Fetching actual signal IDs from database for %d classified signals", len(signals))
            entities = [s["entity"] for s in signals]
            if entities:
                placeholders = ",".join("?" * len(entities))
                cur = conn.execute(
                    f"""
                    SELECT s.signal_id, s.entity, s.metric, s.value, s.evidence_quality, src.source_type as source_type
                    FROM signals s
                    JOIN sources src ON src.source_id = s.source_id
                    WHERE s.entity IN ({placeholders})
                    ORDER BY s.evidence_quality DESC
                    LIMIT 50
                    """,
                    tuple(entities),
                )
                signals = [
                    {
                        "signal_id": row[0],
                        "entity": row[1],
                        "metric": row[2],
                        "value": row[3],
                        "evidence_quality": row[4],
                        "source_type": row[5],
                    }
                    for row in cur.fetchall()
                ]

        logger.info("=== TREND ANALYSIS INPUT (using analysis_result) ===")
        logger.info("Classified signals: %d", len(analysis_result.classified_signals))
        logger.info("Clusters: %d", len(analysis_result.clusters))
        logger.info("Themes: %d", len(analysis_result.themes))
        logger.info("Intent: %s", intent)
        logger.info("=== END TREND ANALYSIS INPUT ===")

        # Clean up database for fresh trend analysis
        cur = conn.cursor()
        cur.execute("DELETE FROM trend_signals")
        cur.execute("DELETE FROM trends")
    else:
        # Fallback to legacy signal_output
        discovered = signal_output.get("discovered_repos", []) if isinstance(signal_output, dict) else []
        repo_lookup = {r["full_name"]: r for r in discovered if isinstance(r, dict)}

        # Log input data size for trend analysis
        logger.info("=== TREND ANALYSIS INPUT (legacy signal_output) ===")
        logger.info("Signal output keys: %s", list(signal_output.keys()) if isinstance(signal_output, dict) else "not a dict")
        logger.info("Discovered repos: %d", len(discovered))
        logger.info("Intent: %s", intent)
        logger.info("=== END TREND ANALYSIS INPUT ===")

        cur = conn.cursor()
        cur.execute("DELETE FROM trend_signals")
        cur.execute("DELETE FROM trends")

        if isinstance(signal_output, dict):
            run_ids = tuple(int(rid) for rid in (signal_output.get("run_ids") or []))
            if not run_ids:
                run_ids = (
                    int(signal_output.get("github_run_id") or 0),
                    int(signal_output.get("tavily_run_id") or 0),
                )
        else:
            run_ids = ()
        run_ids = tuple(rid for rid in run_ids if rid)
        signals = _fetch_signals(conn, repo_lookup, run_ids)
    yield _update_status("running", f"Analyzing {len(signals)} signals...")

    # Log actual signal count being processed
    logger.info("Trend Analyst: Processing %d signals", len(signals))

    # Direct derivation from analysis_result themes if available (avoid duplicate LLM call)
    market_trends: list[MarketTrend] = []
    cluster_counts: dict[str, int] = {}
    mcp_calls: list[dict[str, Any]] = []
    source = ""

    if analysis_result and analysis_result.themes:
        yield _update_status("running", "Deriving market trends from signal analysis themes...")
        source = "direct derivation from themes"

        for theme in analysis_result.themes:
            # Extract supporting entities from clusters that belong to this theme
            supporting_entities = []
            for cluster in analysis_result.clusters:
                if cluster.cluster_id in theme.cluster_ids:
                    supporting_entities.extend([s.entity for s in cluster.signals])

            # Create MarketTrend directly from theme data
            mt = MarketTrend(
                name=theme.theme_name,
                confidence=int(85 if theme.strength == "strong" else 65),
                summary=theme.what_is_happening,
                supporting_signals=supporting_entities[:10],
                evidence_count=theme.signal_count,
                evidence_quality=80.0 if theme.strength == "strong" else 60.0,
                metric="market_trend",
            )
            market_trends.append(mt)
            cluster_counts[theme.theme_name] = theme.signal_count

        logger.info("Trend Analyst: Derived %d market trends from %d themes (no LLM call)",
                    len(market_trends), len(analysis_result.themes))
    else:
        # Fallback to LLM synthesis when no themes available
        yield _update_status("running", "Synthesizing market trends with LLM...")
        ollama_trend_dicts = llm_synthesize_trends(signals, intent)
        source = "Ollama"

        if ollama_trend_dicts:
            for t in ollama_trend_dicts:
                if not isinstance(t, dict):
                    continue
                mt = MarketTrend(
                    name=t["name"],
                    confidence=t["confidence"],
                    summary=t["summary"],
                    supporting_signals=t.get("supporting_signals", []),
                    evidence_count=t.get("evidence_count", 0),
                    evidence_quality=t.get("evidence_quality", 0.0),
                    metric="market_trend",
                )
                market_trends.append(mt)
                cluster_counts[t["name"]] = t.get("evidence_count", 0)
        else:
            # Fallback to Sequential Thinking MCP + rule-based derivation.
            provider = SequentialThinkingProvider()
            yield _update_status("running", "Running Sequential Thinking MCP fallback...")
            market_trends, cluster_counts, mcp_calls = provider.synthesize_trends(signals, intent)
            source = "Sequential Thinking MCP" if provider.available else "rule-based fallback"

    yield _update_status("running", f"Identified {len(market_trends)} market trends...")

    # Map each trend back to the specific signals that support it.
    theme_trends: list[Trend] = []
    for mt in market_trends:
        signal_ids = [
            s["signal_id"]
            for s in signals
            if str(s.get("entity", "")) in mt.supporting_signals
        ]
        # If no direct match, include all signals as fallback evidence.
        if not signal_ids:
            signal_ids = [s["signal_id"] for s in signals]
        theme_trends.append(_insert_market_trend(conn, mt, signal_ids, now))

    states["Trend Analyst"]["mcp_calls"] = mcp_calls
    states["Trend Analyst"]["trend_count"] = len(theme_trends)
    states["Trend Analyst"]["signal_clusters"] = cluster_counts

    duration = time.time() - start
    detail = f"{len(theme_trends)} market trends via {source}"
    yield _update_status("completed", detail)
    return theme_trends
