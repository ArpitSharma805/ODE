"""Opportunity Analyst agent: score Active Trends into Opportunities for a Persona."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any, cast

from ode.concepts import ConceptRegistry
from ode.evidence import validate_evidence
from ode.llm import (
    Explanation,
    critique_opportunities,
    generate_business_theses,
    generate_explanation,
)
from ode.mcp_client import call_tool
from ode.opportunities import Opportunity, score_opportunity
from ode.personas import Persona, get_persona_by_name
from ode.synthesis import _dedupe_repeated_words, primary as synthesis_primary, synthesize
from ode.technology_resolver import strip_forced_mcp_prefix
from ode.trends import Trend, get_active_trends
from ode.research import ResearchDepth, get_research_depth
from ode.opportunity_typology import (
    get_opportunity_typology,
    get_opportunity_types_for_category,
    validate_opportunity_category_match,
    suggest_opportunity_titles,
)

logger = logging.getLogger(__name__)


def _validate_opportunity_title(title: str, query: str = "") -> bool:
    """Validate that an opportunity title represents a concrete opportunity.

    Rejects generic titles like "MCP Opportunities" or "AI Opportunities".
    Accepts specific titles like "MCP Security & Governance Platform" or
    "AI Observability Infrastructure".

    Args:
        title: The opportunity title to validate
        query: The original query for context

    Returns:
        True if the title is valid, False otherwise
    """
    title_lower = title.lower().strip()

    # Reject obviously generic single-word or two-word titles
    words = title_lower.split()
    if len(words) <= 2:
        return False

    # Reject titles that end with generic plural nouns
    generic_endings = ("opportunities", "opportunity", "ideas", "tools", "solutions",
                      "platforms", "services", "products", "applications", "systems")
    if title_lower.endswith(generic_endings):
        return False

    # Reject titles that are just the query topic with "for" or "in"
    # e.g., "Tools for MCP" or "Opportunities in AI"
    if any(word in title_lower for word in ["for", "in", "about"]):
        # Allow if there are specific technical terms
        specific_terms = {"security", "governance", "observability", "monitoring",
                         "testing", "validation", "infrastructure", "analytics"}
        if not any(term in title_lower for term in specific_terms):
            return False

    return True

SCORE_THRESHOLD = 50.0
VALID_FOR_DAYS = 7
CONTEXT7_TOP_N = 5
_CONTEXT7_TTL_SECONDS = 24 * 3600
_CONTEXT7_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CONTEXT7_RESOLVE_CACHE: dict[str, tuple[float, str]] = {}
_CONTEXT7_DOCS_CACHE: dict[str, tuple[float, str]] = {}
_CONTEXT7_CACHE_LOCK = threading.Lock()
_MAX_REGENERATION_ATTEMPTS = 3


def _parse_float(text: str, pattern: str) -> float:
    match = re.search(pattern, text)
    try:
        return float(match.group(1)) if match else 0.0
    except (ValueError, AttributeError):
        return 0.0


def _parse_reputation(text: str) -> float:
    match = re.search(r"Source Reputation:\s*(\w+)", text)
    rep = match.group(1) if match else "Medium"
    return {"High": 80.0, "Medium": 50.0, "Low": 20.0}.get(rep, 50.0)


def _context7_cache_get(cache: dict[str, tuple[float, Any]], key: str) -> Any:
    now = time.time()
    with _CONTEXT7_CACHE_LOCK:
        entry = cache.get(key)
        if entry and now - entry[0] < _CONTEXT7_TTL_SECONDS:
            return entry[1]
    return None


def _context7_cache_set(cache: dict[str, tuple[float, Any]], key: str, value: Any) -> None:
    with _CONTEXT7_CACHE_LOCK:
        cache[key] = (time.time(), value)


def _context7_resolve_and_query(library_name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Call Context7 MCP for a single library and return parsed metadata plus MCP calls.

    Caches raw resolve-library-id responses by library name and query-docs responses
    by Context7 library id with a 24-hour TTL.
    """
    mcp_calls: list[dict[str, Any]] = []
    defaults: dict[str, Any] = {
        "maturity": 50.0,
        "documentation_quality": 50.0,
        "adoption": 50.0,
        "summary": "",
    }

    resolve_text = _context7_cache_get(_CONTEXT7_RESOLVE_CACHE, library_name)
    if resolve_text is None:
        resolve = call_tool(
            "context7",
            "resolve-library-id",
            {"libraryName": library_name, "query": library_name},
        )
        mcp_calls.append({
            "server": "context7",
            "tool": "resolve-library-id",
            "success": resolve.success,
            "duration": getattr(resolve, "duration", 1.0),
            "error": resolve.error,
        })
        if not resolve.success or "### Error" in str(resolve.data):
            return defaults, mcp_calls
        resolve_text = str(resolve.data)
        _context7_cache_set(_CONTEXT7_RESOLVE_CACHE, library_name, resolve_text)

    library_match = re.search(r"Context7-compatible library ID:\s*([^\s]+)", resolve_text)
    if not library_match:
        return defaults, mcp_calls
    library_id = library_match.group(1)

    doc_text = _context7_cache_get(_CONTEXT7_DOCS_CACHE, library_id)
    if doc_text is None:
        docs = call_tool(
            "context7",
            "query-docs",
            {"libraryId": library_id, "query": f"{library_name} overview"},
        )
        mcp_calls.append({
            "server": "context7",
            "tool": "query-docs",
            "success": docs.success,
            "duration": getattr(docs, "duration", 1.0),
            "error": docs.error,
        })
        doc_text = str(docs.data) if docs.success else ""
        _context7_cache_set(_CONTEXT7_DOCS_CACHE, library_id, doc_text)

    code_snippets = int(_parse_float(resolve_text, r"Code Snippets:\s*(\d+)"))
    benchmark = _parse_float(resolve_text, r"Benchmark Score:\s*([\d.]+)")
    reputation = _parse_reputation(resolve_text)

    doc_quality = min(100.0, len(doc_text) / 20.0)
    adoption = min(100.0, code_snippets / 20.0)
    maturity = benchmark if benchmark > 0 else reputation

    summary = doc_text[:300].strip() if doc_text else f"Resolved {library_id} with {code_snippets} snippets."
    return {
        "maturity": maturity,
        "documentation_quality": doc_quality,
        "adoption": adoption,
        "summary": summary,
    }, mcp_calls


def _context7_library_name(entity: str) -> str:
    return (entity.split()[0] if entity else "python").lower().strip(".,;")


def _context7_fetch(
    entity: str,
    allowed_tokens: set[str] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], bool, bool]:
    """Fetch Context7 metadata for an entity, using the in-memory cache.

    Returns (library_name, metadata, mcp_calls, from_cache, skipped).
    """
    library_name = _context7_library_name(entity)
    if allowed_tokens and library_name not in allowed_tokens:
        return (
            library_name,
            cast(dict[str, Any], {}),
            cast(list[dict[str, Any]], []),
            True,
            True,
        )

    meta = _context7_cache_get(_CONTEXT7_META_CACHE, library_name)
    if meta is not None:
        return library_name, meta, [], True, False

    meta, calls = _context7_resolve_and_query(library_name)
    _context7_cache_set(_CONTEXT7_META_CACHE, library_name, meta)
    return library_name, meta, calls, False, False


OPPORTUNITY_INTENTS = {
    "Opportunity Discovery",
    "Product Ideas",
    "Business Opportunities",
}


def _is_relevant_signal(
    signal: dict[str, Any],
    trend: Trend,
    intent: dict[str, Any] | None,
) -> bool:
    """Return True if a signal is relevant to a trend/opportunity."""
    if not isinstance(signal, dict):
        return False
    keywords: set[str] = set()
    entity = (trend.entity or "").lower().strip()
    if entity:
        keywords.add(entity)
    if intent and isinstance(intent, dict):
        for raw in intent.get("topics", []):
            for part in str(raw).lower().split():
                if len(part) > 2:
                    keywords.add(part)
        primary = str(intent.get("primary_technology", "")).lower().strip()
        if primary:
            keywords.add(primary)
    if not keywords:
        return True
    text = f"{signal.get('entity', '')} {signal.get('metric', '')} {signal.get('value', '')}".lower()
    return any(k in text for k in keywords)


def _format_evidence_line(signal: dict[str, Any]) -> str:
    """Format a single signal as an attributed evidence bullet."""
    if not isinstance(signal, dict):
        return ""
    source = str(signal.get("source_type", "signal")).replace("_mcp", "").replace("_", " ").title() or "Signal"
    metric = str(signal.get("metric", "")).strip()
    entity = str(signal.get("entity", "")).strip()
    value = str(signal.get("value", "")).strip()
    eq = float(signal.get("evidence_quality", 0) or 0)

    if "(" in entity and entity.endswith(")"):
        entity = entity.rsplit("(", 1)[0].strip()

    if metric == "github_repo_results":
        return f"- **{source}**: search for `{entity}` returned {value} repositories (quality {eq:.0f})"
    if metric == "github_stars":
        return f"- **{source}**: `{entity}` has {value} stars (quality {eq:.0f})"
    if metric == "github_forks":
        return f"- **{source}**: `{entity}` has {value} forks"
    if metric == "github_commits":
        return f"- **{source}**: `{entity}` has {value} recent commits"
    if metric == "github_contributors":
        return f"- **{source}**: `{entity}` has {value} distinct contributors"
    if metric == "github_open_issues":
        return f"- **{source}**: `{entity}` has {value} open issues"
    if metric == "github_recency":
        return f"- **{source}**: `{entity}` was last pushed {value} days ago"
    if metric == "github_commit_messages":
        return f"- **{source}**: recent commits on `{entity}` include: {value[:90]}"
    if metric == "github_issue_titles":
        return f"- **{source}**: open issues on `{entity}` include: {value[:90]}"
    if metric in ("web_page_text", "docs_page_text"):
        return f"- **{source}**: `{entity}` documentation/page contains relevant content (quality {eq:.0f})"
    if metric in ("web_page_mentions", "docs_page_mentions"):
        return f"- **{source}**: `{entity}` mentions the topic {value} times"
    if metric.startswith("tavily_search_summary"):
        return f"- **{source}**: market summary for `{entity}`: {value[:120]}"
    if metric.startswith("tavily_search_result"):
        return f"- **{source}**: `{entity}` — {value[:120]} (quality {eq:.0f})"
    if metric.startswith("llm_"):
        return f"- **LLM insight** ({metric}): {value[:120]}"
    return f"- **{source}**: `{entity}` — {metric}: {value[:80]} (quality {eq:.0f})"


def _build_evidence_summary(
    trend: Trend,
    signals: list[dict[str, Any]],
    intent: dict[str, Any] | None = None,
    max_items: int = 6,
) -> str:
    """Return a concise, attributed evidence markdown string for a trend.

    Directly matching signals (entity contains the trend name) are surfaced first,
    followed by intent-related signals. This keeps per-skill evidence specific
    while still providing useful attribution when no direct match exists.
    """
    if not signals:
        return "No concrete signals available."

    trend_name = str(trend.entity or "").lower().strip()

    def _signal_text(s: dict[str, Any]) -> str:
        if not isinstance(s, dict):
            return ""
        return f"{s.get('entity', '')} {s.get('metric', '')} {s.get('value', '')}".lower()

    direct_matches = [s for s in signals if isinstance(s, dict) and trend_name and trend_name in _signal_text(s)]
    related = [
        s for s in signals
        if isinstance(s, dict) and s not in direct_matches and _is_relevant_signal(s, trend, intent)
    ]
    relevant = direct_matches + related
    if not relevant:
        return "No concrete signals available."

    direct_ids = {id(s) for s in direct_matches}

    # Prefer direct matches, then higher evidence quality, then shorter signals.
    seen: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for s in sorted(
        relevant,
        key=lambda x: (
            0 if id(x) in direct_ids else 1,
            -float(x.get("evidence_quality", 0) or 0),
        ),
    ):
        if not isinstance(s, dict):
            continue
        metric = str(s.get("metric", ""))
        entity = str(s.get("entity", ""))
        key = f"{entity}:{metric}"
        if metric in ("web_page_text", "docs_page_text", "tavily_search_summary"):
            base = entity
            if base not in seen:
                # only keep the first text signal per entity; favor mention/result signals below
                seen.add(base)
                filtered.append(s)
            continue
        if key not in seen:
            seen.add(key)
            filtered.append(s)

    filtered = filtered[:max_items]
    if not filtered:
        filtered = relevant[:max_items]

    # Double newlines make each bullet render as a distinct markdown list item
    # in the UI, even when the frontend collapses single newlines.
    return "\n\n".join(_format_evidence_line(s) for s in filtered)


def generate_opportunities(
    conn: sqlite3.Connection,
    persona_name: str = "Engineer",
    mcp_calls: list[dict[str, Any]] | None = None,
    trends: list[Any] | None = None,
    intent: dict[str, Any] | None = None,
    query: str = "",
    analysis_result: Any = None,
) -> tuple[list[Opportunity], dict[str, Any]]:
    """Generate up to 3 high-quality, evidence-backed Opportunities for a Persona.

    Uses an Ollama-first business thesis generator; falls back to deterministic
    scoring if the LLM is unavailable or produces no valid opportunities.

    Args:
        conn: Database connection
        persona_name: Target persona for opportunities
        mcp_calls: MCP call tracking list
        trends: List of trends to analyze
        intent: User intent for context
        query: Original query
        analysis_result: AnalysisResult from Signal Analyst with themes, problems, insights

    Returns tuple of (opportunities, generation_stats) where generation_stats contains:
    - generated: total opportunities generated
    - rejected_title: count rejected due to generic title
    - rejected_evidence: count rejected due to insufficient evidence
    - accepted: count of accepted opportunities
    - rejection_reasons: list of specific rejection reasons
    """

    # Track opportunity generation statistics
    generation_stats = {
        "generated": 0,
        "rejected_title": 0,
        "rejected_evidence": 0,
        "accepted": 0,
        "rejection_reasons": [],
        "regeneration_attempts": 0,
    }

    def _fetch_signals(trends_to_fetch: list[Trend]) -> list[dict[str, Any]]:
        signal_ids = {
            sid for t in trends_to_fetch for sid in (t.contributing_signal_ids or [])
        }
        if not signal_ids:
            return []

        placeholders = ",".join("?" * len(signal_ids))
        params: tuple[Any, ...] = tuple(signal_ids)

        # Derive the current ingestion run(s) from the trend's contributing signals.
        # This keeps the fix surgical (no new parameters through the pipeline) while still
        # letting us pull related market intelligence from the same source in this run.
        cur = conn.execute(
            f"SELECT DISTINCT ingestion_run_id, source_id FROM signals WHERE signal_id IN ({placeholders})",
            params,
        )
        run_ids: list[int] = []
        source_ids: list[int] = []
        for row in cur.fetchall():
            run_ids.append(row[0])
            source_ids.append(row[1])
        if not run_ids or not source_ids:
            # Fall back to the exact supporting signals when run metadata is unavailable.
            cur = conn.execute(
                f"""
                SELECT s.signal_id, s.entity, s.metric, s.value, s.evidence_quality, src.source_type
                FROM signals s
                JOIN sources src ON src.source_id = s.source_id
                WHERE s.signal_id IN ({placeholders})
                ORDER BY s.evidence_quality DESC
                """,
                params,
            )
        else:
            run_ph = ",".join("?" * len(run_ids))
            src_ph = ",".join("?" * len(source_ids))
            cur = conn.execute(
                f"""
                SELECT s.signal_id, s.entity, s.metric, s.value, s.evidence_quality, src.source_type
                FROM signals s
                JOIN sources src ON src.source_id = s.source_id
                WHERE s.ingestion_run_id IN ({run_ph})
                  AND s.source_id IN ({src_ph})
                ORDER BY s.evidence_quality DESC
                LIMIT 100
                """,
                tuple(run_ids) + tuple(source_ids),
            )
        return [
            {
                "signal_id": row[0],
                "entity": str(row[1]),
                "metric": str(row[2]),
                "value": str(row[3]),
                "evidence_quality": float(row[4] or 0),
                "source_type": str(row[5]),
            }
            for row in cur.fetchall()
        ]

    def _trend_to_dict(trend: Trend) -> dict[str, Any]:
        forecast = getattr(trend, "forecast", None) or {}
        return {
            "name": trend.entity,
            "summary": forecast.get("summary", ""),
            "friction": forecast.get("friction", ""),
            "gap": forecast.get("gap", ""),
            "confidence": min(100, max(0, int(trend.momentum or 0))),
            "evidence_count": trend.signal_volume or 0,
            "evidence_quality": int(trend.evidence_quality or 0),
            "supporting_signals": forecast.get("supporting_signals", []),
        }

    def _add_if_valid(result: list[Opportunity], opportunity: Opportunity | None, context: str, stats: dict[str, Any]) -> None:
        """Add opportunity to result if valid, log skip if rejected."""
        if opportunity is not None:
            stats["accepted"] += 1
            logger.info("Accepted opportunity #%d from %s: title='%s', score=%.2f",
                        stats["accepted"], context, opportunity.title, opportunity.score)
            result.append(opportunity)
        else:
            logger.info("Skipping rejected opportunity from %s", context)

    def _persist_opportunity(trend: Trend, opp: dict[str, Any], stats: dict[str, Any], persona_param: "Persona", query_param: str, intent_param: dict[str, Any] | None, signals_param: list[dict[str, Any]]) -> Opportunity | None:
        # Handle None intent
        intent_safe = intent_param if isinstance(intent_param, dict) else {}

        stats["generated"] += 1
        logger.info("Persisting opportunity #%d: title='%s'", stats["generated"], opp.get("title", "unknown"))

        # Log the full opportunity object before validation
        logger.info("=== RAW OPPORTUNITY OBJECT #%d ===", stats["generated"])
        logger.info("Title: %s", opp.get("title", "unknown"))
        logger.info("Description: %s", opp.get("core_problem", "unknown"))
        logger.info("Why existing solutions fail: %s", opp.get("why_existing_solutions_fail", "unknown"))
        logger.info("Target users: %s", opp.get("target_users", "unknown"))
        logger.info("Why now: %s", opp.get("why_now", "unknown"))
        logger.info("Recommended action: %s", opp.get("recommended_action", "unknown"))
        logger.info("Business model: %s", opp.get("business_model", "unknown"))
        logger.info("=== END RAW OPPORTUNITY OBJECT #%d ===", stats["generated"])

        assert persona_param is not None  # noqa: S101
        if not isinstance(opp, dict):
            opp = {}
        # Guard against LLMs or templates forcing an 'MCP' prefix on non-MCP queries.
        raw_title = str(opp.get("title", ""))
        opp["title"] = _dedupe_repeated_words(strip_forced_mcp_prefix(raw_title, query_param, intent_safe))

        # Validate that the opportunity title represents a concrete opportunity
        if not _validate_opportunity_title(opp["title"], query_param):
            stats["rejected_title"] += 1
            stats["rejection_reasons"].append(f"Generic title: {opp['title']}")
            logger.warning("Rejected generic opportunity title: %s - skipping entirely", opp["title"])
            logger.info("=== REJECTION DETAILS ===")
            logger.info("Rejection reason: Generic title")
            logger.info("Title: %s", opp["title"])
            logger.info("Query: %s", query_param)
            logger.info("=== END REJECTION DETAILS ===")
            # Return None to indicate this opportunity should not be included
            return None

        # Validate that the opportunity matches the expected patterns for its category
        category = intent_safe.get("domain", "general")
        is_category_match, category_reason = validate_opportunity_category_match(opp["title"], category)
        if not is_category_match:
            stats["rejected_title"] += 1
            stats["rejection_reasons"].append(f"Category mismatch: {category_reason}")
            logger.warning("Rejected opportunity '%s' due to category mismatch: %s", opp["title"], category_reason)
            logger.info("=== REJECTION DETAILS ===")
            logger.info("Rejection reason: Category mismatch")
            logger.info("Title: %s", opp["title"])
            logger.info("Category: %s", category)
            logger.info("Reason: %s", category_reason)
            logger.info("=== END REJECTION DETAILS ===")
            return None

        # Compute the five-component score from the underlying trend data so the
        # scorecard is internally consistent and explainable.
        components = score_opportunity(trend, intent=intent_safe)

        # Require multi-source, independent evidence before accepting a high score.
        relevant_signals = [s for s in signals_param if _is_relevant_signal(s, trend, intent_safe)]
        logger.info("Opportunity '%s': relevant_signals=%d", opp["title"], len(relevant_signals))
        evidence_assessment = validate_evidence(relevant_signals, min_source_types=2, min_signals=3)
        logger.info("Opportunity '%s': evidence validated=%s, sources=%d, signals=%d, confidence=%.3f",
                     opp["title"], evidence_assessment.validated, evidence_assessment.source_count,
                     evidence_assessment.signal_count, evidence_assessment.confidence)
        if not evidence_assessment.validated:
            stats["rejected_evidence"] += 1
            stats["rejection_reasons"].append(f"Insufficient evidence: {evidence_assessment.rationale}")
            logger.warning("Rejected opportunity '%s' due to insufficient evidence: %s", opp["title"], evidence_assessment.rationale)
            components["total"] = round(components["total"] * evidence_assessment.confidence, 2)
        score = components["total"]

        # The five-component score is the source of truth. Preserve any LLM
        # confidence value only as a diagnostic reference.
        score_components = dict(components)
        score_components["confidence_score"] = float(opp.get("confidence_score", 0) or 0)
        score_components["signal_volume"] = float(trend.signal_volume or 0)
        score_components["growth_velocity"] = float(trend.growth_velocity or 0)
        score_components["evidence_validation"] = evidence_assessment.confidence
        score_components["evidence_rationale"] = evidence_assessment.rationale

        # Always attach concrete, attributed evidence so the supporting_evidence
        # field explains *why* this opportunity exists rather than restating it.
        evidence_summary = _build_evidence_summary(trend, signals_param, intent_safe)
        user_evidence = str(opp.get("supporting_evidence", "")).strip()
        if user_evidence and user_evidence not in evidence_summary:
            opp["supporting_evidence"] = f"{evidence_summary}\n\n{user_evidence}"
        else:
            opp["supporting_evidence"] = evidence_summary

        cur = conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, lifecycle_state
            FROM opportunities
            WHERE trend_id = ? AND persona_id = ?
            """,
            (trend.trend_id, persona_param.persona_id),
        )
        row = cur.fetchone()
        if row is not None:
            opportunity_id = row[0]
            lifecycle_state = str(row[1])
            cur.execute(
                """
                UPDATE opportunities
                SET title = ?, category = ?, description = ?, why_now = ?,
                    who_benefits = ?, recommended_action = ?, supporting_evidence = ?,
                    why_existing_solutions_fail = ?, business_model = ?, risk_assessment = ?,
                    score = ?, score_components = ?, lifecycle_state = ?, last_score_date = ?, valid_until = ?
                WHERE opportunity_id = ?
                """,
                (
                    opp["title"],
                    opp.get("category", "Product"),
                    opp.get("core_problem", ""),
                    opp.get("why_now", ""),
                    opp.get("target_users", ""),
                    opp.get("recommended_action", ""),
                    opp.get("supporting_evidence", ""),
                    opp.get("why_existing_solutions_fail", ""),
                    opp.get("business_model", ""),
                    opp.get("risk_assessment", ""),
                    score,
                    json.dumps(score_components),
                    lifecycle_state,
                    now,
                    valid_until,
                    opportunity_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO opportunities (
                    trend_id, persona_id, title, category, description, why_now,
                    who_benefits, recommended_action, supporting_evidence,
                    why_existing_solutions_fail, business_model, risk_assessment,
                    score, score_components, lifecycle_state, emerged_date,
                    valid_until, last_score_date, execution_roadmap
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trend.trend_id,
                    persona_param.persona_id,
                    opp["title"],
                    opp.get("category", "Product"),
                    opp.get("core_problem", ""),
                    opp.get("why_now", ""),
                    opp.get("target_users", ""),
                    opp.get("recommended_action", ""),
                    opp.get("supporting_evidence", ""),
                    opp.get("why_existing_solutions_fail", ""),
                    opp.get("business_model", ""),
                    opp.get("risk_assessment", ""),
                    score,
                    json.dumps(score_components),
                    "Emerging",
                    now,
                    valid_until,
                    now,
                    json.dumps(opp.get("execution_roadmap", {})),
                ),
            )
            opportunity_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
            lifecycle_state = "Emerging"

        return Opportunity(
            opportunity_id=opportunity_id,
            trend_id=trend.trend_id,
            persona_id=persona_param.persona_id,
            title=opp["title"],
            category=opp.get("category", "Product"),
            description=opp.get("core_problem", ""),
            why_now=opp.get("why_now", ""),
            who_benefits=opp.get("target_users", ""),
            recommended_action=opp.get("recommended_action", ""),
            supporting_evidence=opp.get("supporting_evidence", ""),
            why_existing_solutions_fail=opp.get("why_existing_solutions_fail", ""),
            business_model=opp.get("business_model", ""),
            risk_assessment=opp.get("risk_assessment", ""),
            score=score,
            score_components=score_components,
            lifecycle_state=lifecycle_state,
            emerged_date=now,
            valid_until=valid_until,
            last_score_date=now,
            execution_roadmap=opp.get("execution_roadmap"),
        )

    def _synthesized_opportunities(
        active_trends: list[Trend],
        signals: list[dict[str, Any]],
    ) -> list[Opportunity]:
        """Use the deterministic evidence synthesis pipeline to derive detailed opportunities.

        Framework: Signals -> Themes -> Problems -> Insights -> Opportunities -> Narrative.
        """
        assert persona is not None  # noqa: S101
        logger.info("Synthesized opportunities: active_trends=%d, signals=%d", len(active_trends), len(signals))

        if not signals:
            logger.warning("No signals available for synthesis, using fallback")
            return _fallback_opportunities(active_trends)

        synth = synthesize(signals, intent)
        if not synth:
            logger.warning("Synthesis returned None, using fallback")
            return _fallback_opportunities(active_trends)

        logger.info("Synthesis: themes=%d, problems=%d, insights=%d, opportunities=%d",
                     len(synth.themes), len(synth.problems), len(synth.insights),
                     len(synth.opportunities) if synth.opportunities else 0)

        if not synth.opportunities:
            logger.warning("No opportunities from synthesis, using fallback")
            return _fallback_opportunities(active_trends)

        # Determine a primary entity for the synthetic trend. Shorten it so
        # relevance matching and evidence attribution find the right signals.
        primary_long, primary_short = synthesis_primary(signals, intent)

        # The trend entity must itself be a searchable token, not a long phrase,
        # otherwise _is_relevant_signal treats the whole phrase as a single
        # substring and misses most signals.
        trend_entity = primary_short or primary_long

        theme_by_name = {t.name: t for t in synth.themes}
        result: list[Opportunity] = []
        for idx, sop in enumerate(synth.opportunities[:5]):
            theme = next(
                (theme_by_name[name] for name in sop.source_themes if name in theme_by_name),
                None,
            )
            theme_signals = [
                s for s in (theme.signals if theme else signals)
                if isinstance(s, dict)
            ]
            eqs = [float(s.get("evidence_quality", 0) or 0) for s in theme_signals]
            avg_eq = sum(eqs) / len(eqs) if eqs else 0.0

            trend = Trend(
                trend_id=-(idx + 1),
                entity=trend_entity,
                metric="market_trend",
                start_date=now,
                last_updated_date=now,
                end_date=None,
                status="Active",
                momentum=round(avg_eq, 2),
                signal_volume=len(theme_signals),
                evidence_quality=round(avg_eq, 2),
                growth_velocity=0.0,
                created_date=now,
                forecast={
                    "summary": sop.problem,
                    "supporting_signals": [
                        str(s.get("value", "")).strip()[:120]
                        for s in theme_signals[:6]
                    ],
                },
                contributing_signal_ids=[int(s.get("signal_id", 0) or 0) for s in theme_signals],
            )

            opp = {
                "title": sop.title,
                "category": "Product",
                "core_problem": sop.problem,
                "why_existing_solutions_fail": sop.why_existing_solutions_fail,
                "target_users": sop.affected_users,
                "why_now": sop.why_now,
                "supporting_evidence": sop.evidence_summary,
                "recommended_action": sop.potential_solution,
                "business_model": "",
                "risk_assessment": sop.risks,
                "confidence_score": sop.score,
            }
            opportunity = _persist_opportunity(trend, opp, generation_stats, persona, query, intent, signals)
            _add_if_valid(result, opportunity, f"theme '{theme.name if theme else 'unknown'}'", generation_stats)
        conn.commit()
        return result

    def _fallback_opportunities(
        active_trends: list[Trend],
    ) -> list[Opportunity]:
        """Deterministic fallback when Ollama is unavailable."""
        assert persona is not None  # noqa: S101
        logger.info("Fallback opportunities: active_trends=%d", len(active_trends))
        result: list[Opportunity] = []
        for trend in active_trends[:3]:
            logger.info("Processing fallback trend: %s", trend.entity)
            components = score_opportunity(trend, intent=intent)
            safe_intent = intent if isinstance(intent, dict) else {}
            explanation = generate_explanation(
                trend.entity,
                trend.metric,
                components["total"],
                components,
                persona.name,
                persona=persona,
                intent=intent,
                supporting_signals=(trend.forecast or {}).get("supporting_signals", []),
                topics=safe_intent.get("topics"),
            )
            opp = _persist_opportunity(
                trend,
                {
                    "title": explanation.title,
                    "category": explanation.category,
                    "core_problem": explanation.summary,
                    "why_existing_solutions_fail": "Existing tools are fragmented and do not address the specific workflow friction identified.",
                    "target_users": explanation.who_benefits,
                    "why_now": explanation.why_now,
                    "supporting_evidence": explanation.supporting_evidence,
                    "recommended_action": explanation.recommended_action,
                    "business_model": "SaaS / developer-tools licensing or managed service",
                    "risk_assessment": "Execution risk and ecosystem adoption timing.",
                    "confidence_score": components["total"],
                },
                generation_stats,
                persona,
                query,
                intent,
                signals,
            )
            _add_if_valid(result, opp, f"trend '{trend.entity}'", generation_stats)
        conn.commit()
        logger.info("Fallback returned %d opportunities", len(result))
        return result

    def _skill_candidates(
        active_trends: list[Trend],
        signals: list[dict[str, Any]],
    ) -> list[Opportunity]:
        """Build skill/technology candidates for learning, career, and comparison queries.

        Instead of forcing market trends into product opportunities, derive one
        concrete skill/technology candidate per intent topic and attach the
        concrete signals that support it.
        """
        assert persona is not None  # noqa: S101
        registry = ConceptRegistry(use_llm=False)
        safe_intent = intent if isinstance(intent, dict) else {}
        topics = registry.dedupe([t for t in safe_intent.get("topics", []) if t])[:5]
        if not topics:
            return _fallback_opportunities(active_trends)

        result: list[Opportunity] = []
        used_trend_ids: set[int] = set()

        for idx, topic in enumerate(topics):
            topic_str = registry.canonical(topic).strip()
            topic_lower = topic_str.lower()
            topic_aliases = {a.lower() for a in registry.aliases_for(topic)}

            # Prefer a trend whose name or summary mentions any alias of the topic.
            trend = None
            for t in active_trends:
                if t.trend_id in used_trend_ids:
                    continue
                forecast_summary = t.forecast.get("summary", "") if isinstance(t.forecast, dict) else ""
                text = f"{t.entity} {forecast_summary}".lower() if t.forecast else t.entity.lower()
                if any(alias in text for alias in topic_aliases):
                    trend = t
                    break
            if trend is None:
                trend = next((t for t in active_trends if t.trend_id not in used_trend_ids), None)
            if trend is not None and trend.trend_id != 0:
                used_trend_ids.add(trend.trend_id)

            # Gather signals specific to this topic to compute a meaningful score.
            topic_signals = [
                s
                for s in signals
                if isinstance(s, dict)
                and any(
                    alias in f"{s.get('entity', '')} {s.get('metric', '')} {s.get('value', '')}".lower()
                    for alias in topic_aliases
                )
            ]
            if topic_signals:
                volume = len(topic_signals)
                eq = sum(float(s.get("evidence_quality", 0) or 0) for s in topic_signals) / volume
            else:
                volume = 0
                eq = 0.0

            if trend is None:
                # No real trend available: build a synthetic trend for scoring.
                trend = Trend(
                    trend_id=0,
                    entity=topic_str,
                    metric="market_trend",
                    start_date=now,
                    last_updated_date=now,
                    end_date=None,
                    status="Active",
                    momentum=eq * 0.8,
                    signal_volume=volume,
                    evidence_quality=eq,
                    growth_velocity=0.0,
                    created_date=now,
                    forecast=None,
                )
            else:
                # Augment the matched trend with topic-specific signal counts.
                trend = Trend(
                    trend_id=trend.trend_id,
                    entity=topic_str,
                    metric=trend.metric,
                    start_date=trend.start_date,
                    last_updated_date=trend.last_updated_date,
                    end_date=trend.end_date,
                    status=trend.status,
                    momentum=max(float(trend.momentum or 0), eq * 0.8),
                    signal_volume=max(int(trend.signal_volume or 0), volume),
                    evidence_quality=max(float(trend.evidence_quality or 0), eq),
                    growth_velocity=float(trend.growth_velocity or 0),
                    created_date=trend.created_date,
                    forecast=trend.forecast,
                )

            components = score_opportunity(trend, intent=intent)
            evidence_assessment = validate_evidence(topic_signals, min_source_types=2, min_signals=3)
            if not evidence_assessment.validated:
                components["total"] = round(components["total"] * evidence_assessment.confidence, 2)
            evidence_summary = _build_evidence_summary(trend, signals, intent)
            explanation = generate_explanation(
                trend.entity,
                trend.metric,
                components["total"],
                components,
                persona.name,
                persona=persona,
                intent=intent,
                supporting_signals=(trend.forecast.get("supporting_signals", []) if isinstance(trend.forecast, dict) else []),
                topics=[topic_str],
            )

            opportunity = Opportunity(
                opportunity_id=-(idx + 1),
                trend_id=trend.trend_id,
                persona_id=persona.persona_id,
                title=topic_str.title(),
                category="Skill",
                description=explanation.summary,
                why_now=explanation.why_now,
                who_benefits=explanation.who_benefits,
                recommended_action=explanation.recommended_action,
                supporting_evidence=evidence_summary,
                why_existing_solutions_fail="",
                business_model="",
                risk_assessment="Market demand may shift; validate with local job postings and team needs.",
                score=components["total"],
                score_components={
                    **components,
                    "confidence_score": components["total"],
                    "signal_volume": float(trend.signal_volume or 0),
                    "growth_velocity": float(trend.growth_velocity or 0),
                    "evidence_validation": evidence_assessment.confidence,
                    "evidence_rationale": evidence_assessment.rationale,
                },
                lifecycle_state="Emerging",
                emerged_date=now,
                valid_until=valid_until,
                last_score_date=now,
            )
            result.append(opportunity)
        return result

    persona = get_persona_by_name(conn, persona_name)
    if persona is None:
        persona = get_persona_by_name(conn, "Engineer")
    if persona is None:
        raise ValueError(f"Persona '{persona_name}' not found")

    # Use in-memory trends if provided, otherwise query database
    active_trends = trends if trends is not None else get_active_trends(conn)
    active_trends = sorted(
        active_trends,
        key=lambda t: float(t.momentum or 0),
        reverse=True,
    )[:3]

    allowed_tokens: set[str] = set()
    if intent:
        allowed_tokens = {
            str(t).lower().strip()
            for t in intent.get("topics", [])
        } | {str(intent.get("primary_technology", "")).lower().strip()}
        allowed_tokens.discard("")

    now = datetime.now(timezone.utc).isoformat()
    valid_until = (
        datetime.now(timezone.utc) + timedelta(days=VALID_FOR_DAYS)
    ).isoformat()

    if not active_trends:
        return [], generation_stats

    signals = _fetch_signals(active_trends)

    ctx7_map: dict[str, dict[str, Any]] = {}
    context7_summaries: dict[str, str] = {}
    if mcp_calls is not None:
        cache_stats = {"hits": 0, "misses": 0, "skipped": 0}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_context7_fetch, trend.entity, allowed_tokens): trend
                for trend in active_trends
            }
            for future in futures:
                library_name, meta, calls, cached, skipped = future.result()
                if skipped:
                    cache_stats["skipped"] += 1
                elif cached:
                    cache_stats["hits"] += 1
                else:
                    cache_stats["misses"] += 1
                ctx7_map[library_name] = meta
                if meta.get("summary"):
                    context7_summaries[library_name] = meta["summary"]
                mcp_calls.extend(calls)
        mcp_calls.append({
            "server": "context7",
            "tool": "cache_metrics",
            "success": True,
            "duration": 0.0,
            "error": "",
            "cache_hits": cache_stats["hits"],
            "cache_misses": cache_stats["misses"],
            "cache_skipped": cache_stats["skipped"],
        })

    safe_intent = intent if isinstance(intent, dict) else {}
    intent_type = safe_intent.get("intent", "Opportunity Discovery")

    logger.info(
        "Opportunity generation started: intent_type=%s, active_trends=%d, signals=%d",
        intent_type,
        len(active_trends),
        len(signals),
    )

    # Do not force learning/career/comparison queries into a product-opportunity mold.
    if intent_type in OPPORTUNITY_INTENTS:
        # Determine research depth for opportunity generation
        research_depth = get_research_depth(intent)
        depth_config = ResearchDepth.get_config(research_depth)
        max_opportunities = depth_config["max_opportunities"]
        max_trends = depth_config["max_trends"]

        # Limit trends to max_trends
        active_trends = active_trends[:max_trends]

        for attempt in range(_MAX_REGENERATION_ATTEMPTS):
            trend_dicts = [_trend_to_dict(t) for t in active_trends]
            logger.info("Generation attempt %d/%d (max_opportunities: %d)", attempt + 1, _MAX_REGENERATION_ATTEMPTS, max_opportunities)

            # Add analysis_result context to improve opportunity generation
            analysis_context = {}
            if analysis_result:
                analysis_context = {
                    "themes": [
                        {
                            "theme_name": t.theme_name,
                            "what_is_happening": t.what_is_happening,
                            "strength": t.strength,
                        }
                        for t in analysis_result.themes[:3]  # Limit to top 3 themes
                    ],
                    "problems": [
                        {
                            "problem_statement": p.problem_statement,
                            "severity": p.severity,
                        }
                        for p in analysis_result.problems[:2]  # Limit to top 2 problems
                    ],
                    "insights": [
                        {
                            "observation": i.observation,
                            "connection": i.connection,
                            "implication": i.implication,
                            "confidence": i.confidence,
                        }
                        for i in analysis_result.insights[:2]  # Limit to top 2 insights
                    ],
                }
                logger.info("Using analysis_result context: %d themes, %d problems, %d insights",
                            len(analysis_context["themes"]), len(analysis_context["problems"]), len(analysis_context["insights"]))

            raw_opportunities = generate_business_theses(
                signals, trend_dicts, persona, intent=intent, context7_summaries=context7_summaries
            )

            logger.info("Generated %d raw business theses", len(raw_opportunities))

            # Limit raw opportunities to max_opportunities
            raw_opportunities = raw_opportunities[:max_opportunities]

            critiqued: list[dict[str, Any]] = []
            if raw_opportunities:
                logger.info("Critiquing %d raw opportunities", len(raw_opportunities))
                critiqued = critique_opportunities(raw_opportunities, query, intent)
                logger.info("Critiqued %d opportunities", len(critiqued))
            else:
                logger.warning("No raw opportunities generated, falling back to synthesis")

            # Process critiqued opportunities
            opportunities: list[Opportunity] = []
            used_trend_ids: set[int] = set()
            for opp in critiqued:
                if not isinstance(opp, dict):
                    continue
                target = opp.get("target_trend", "").lower()
                title = opp.get("title", "").lower()
                trend = None
                for t in active_trends:
                    if t.trend_id in used_trend_ids:
                        continue
                    te = t.entity.lower()
                    if target and (target == te or target in te or te in target):
                        trend = t
                        break
                    if title and te in title:
                        trend = t
                        break
                if trend is None:
                    for t in active_trends:
                        if t.trend_id not in used_trend_ids:
                            trend = t
                            break
                if trend is None:
                    continue
                used_trend_ids.add(t.trend_id)

                if not opp.get("confidence_score"):
                    opp["confidence_score"] = int(trend.momentum or 0)

                opportunity = _persist_opportunity(trend, opp, generation_stats, persona, query, intent, signals)
                _add_if_valid(opportunities, opportunity, f"critiqued opportunity '{opp.get('title', 'unknown')}'", generation_stats)

            conn.commit()
            opportunities.sort(key=lambda o: o.score, reverse=True)

            # Log generation statistics for this attempt
            logger.info(
                "Generation attempt %d stats: Generated=%d, Rejected Title=%d, Rejected Evidence=%d, Accepted=%d",
                attempt + 1,
                generation_stats["generated"],
                generation_stats["rejected_title"],
                generation_stats["rejected_evidence"],
                generation_stats["accepted"]
            )

            # If we got accepted opportunities, return them
            accepted_count = int(generation_stats["accepted"]) if isinstance(generation_stats["accepted"], (int, str)) else 0
            if accepted_count > 0:
                logger.info("Generation attempt %d succeeded with %d accepted opportunities", attempt + 1, accepted_count)
                # Log final generation statistics
                logger.info(
                    "Final generation stats: Generated=%d, Rejected Title=%d, Rejected Evidence=%d, Accepted=%d, Regeneration Attempts=%d",
                    int(generation_stats["generated"]) if isinstance(generation_stats["generated"], (int, str)) else 0,
                    int(generation_stats["rejected_title"]) if isinstance(generation_stats["rejected_title"], (int, str)) else 0,
                    int(generation_stats["rejected_evidence"]) if isinstance(generation_stats["rejected_evidence"], (int, str)) else 0,
                    accepted_count,
                    int(generation_stats["regeneration_attempts"]) if isinstance(generation_stats["regeneration_attempts"], (int, str)) else 0
                )
                if generation_stats["rejection_reasons"]:
                    reasons = generation_stats["rejection_reasons"]
                    if isinstance(reasons, list):
                        logger.info("Rejection reasons: %s", "; ".join([str(r) for r in reasons if r]))
                return opportunities, generation_stats
            else:
                current_attempts = int(generation_stats["regeneration_attempts"]) if isinstance(generation_stats["regeneration_attempts"], (int, str)) else 0
                generation_stats["regeneration_attempts"] = current_attempts + 1
                logger.warning("Generation attempt %d failed with 0 accepted opportunities, trying fallback", attempt + 1)
                if attempt < _MAX_REGENERATION_ATTEMPTS - 1:
                    continue
                else:
                    logger.warning("All generation attempts failed, using fallback")
                    break

        # If no opportunities accepted after all attempts, use fallback
        accepted_count = int(generation_stats["accepted"]) if isinstance(generation_stats["accepted"], (int, str)) else 0
        if accepted_count == 0:
            logger.info("No opportunities accepted after %d attempts, using synthesized opportunities fallback", _MAX_REGENERATION_ATTEMPTS)
            # Log final generation statistics
            logger.info(
                "Final generation stats: Generated=%d, Rejected Title=%d, Rejected Evidence=%d, Accepted=%d, Regeneration Attempts=%d",
                int(generation_stats["generated"]) if isinstance(generation_stats["generated"], (int, str)) else 0,
                int(generation_stats["rejected_title"]) if isinstance(generation_stats["rejected_title"], (int, str)) else 0,
                int(generation_stats["rejected_evidence"]) if isinstance(generation_stats["rejected_evidence"], (int, str)) else 0,
                accepted_count,
                int(generation_stats["regeneration_attempts"]) if isinstance(generation_stats["regeneration_attempts"], (int, str)) else 0
            )
            if generation_stats["rejection_reasons"]:
                reasons = generation_stats["rejection_reasons"]
                if isinstance(reasons, list):
                    logger.info("Rejection reasons: %s", "; ".join([str(r) for r in reasons if r]))
            result = _synthesized_opportunities(active_trends, signals)
            return result, generation_stats
    else:
        logger.info("Intent type '%s' not in OPPORTUNITY_INTENTS, using skill candidates", intent_type)
        result = _skill_candidates(active_trends, signals)
        return result, generation_stats

    # This should never be reached, but return empty list as safety
    return [], generation_stats
