"""FastAPI application exposing ODE data and the agent pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    force=True,
)
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ode.agents.orchestrator import run_copilot
from ode.db import DEFAULT_DB_PATH, get_db_connection, init_database
from ode.investigations import (
    Investigation,
    append_investigation_trace,
    create_investigation,
    get_investigation,
    get_latest_investigation,
    list_investigations,
    update_investigation,
)
from ode.serialization import serialize_report, format_opportunity_for_ui, format_signal_for_ui
from ode.opportunities import list_opportunities
from ode.sources import list_sources
from ode.tech_radar import build_tech_radar
from ode.technology_discovery import get_discovery_feed, cache_discovery_feed, load_cached_discovery_feed, get_trending_technologies, discover_custom_technology
from ode.trends import get_active_trends

logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    query: str
    seed_only: bool = False


class QueryResponse(BaseModel):
    top_opportunity: dict[str, Any] | None
    opportunities: list[dict[str, Any]]
    top_trend: dict[str, Any] | None
    answer: dict[str, Any] | None
    signals: list[dict[str, Any]]
    discovered_repos: list[str]
    radar_query: dict[str, Any] | None


def _persist_pipeline_event(
    conn: sqlite3.Connection,
    investigation_id: int,
    event: dict[str, Any],
) -> None:
    """Persist a single pipeline event to the investigation row."""
    if event.get("type") == "final":
        # Extract pipeline artifacts from the final event (now in state directly)
        pipeline_artifacts = event.get("pipeline_artifacts", {})
        update_investigation(
            conn,
            investigation_id,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            final_state=event,
            pipeline_artifacts=pipeline_artifacts,
        )
    elif event.get("status") is not None:
        append_investigation_trace(conn, investigation_id, event)


def _run_query(query: str, seed_only: bool = False) -> dict[str, Any]:
    """Run the full pipeline synchronously and persist the result."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    investigation_id = create_investigation(conn, query)
    try:
        final: dict[str, Any] | None = None
        for raw in run_copilot(query, conn, seed_only=seed_only):
            event = jsonable_encoder(raw, exclude_none=False)
            _persist_pipeline_event(conn, investigation_id, event)
            if event.get("type") == "final":
                final = event
        if final is None:
            update_investigation(
                conn,
                investigation_id,
                status="failed",
                error="Pipeline returned no final event",
            )
            raise HTTPException(status_code=500, detail="Pipeline returned no final event")
        return final
    except Exception as exc:
        logger.error("Query execution failed: %s", traceback.format_exc())
        update_investigation(
            conn,
            investigation_id,
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
        raise
    finally:
        conn.close()


def _final_to_response(final: dict[str, Any]) -> dict[str, Any]:
    """Convert final pipeline state to API response with proper serialization."""
    # Ensure signals are included from the final state
    if "signals" not in final or not final["signals"]:
        # Try to get signals from pipeline_artifacts if missing
        if "pipeline_artifacts" in final and isinstance(final["pipeline_artifacts"], dict):
            final["signals"] = final["pipeline_artifacts"].get("normalized_signals", [])
            if not final["signals"]:
                final["signals"] = final["pipeline_artifacts"].get("raw_signals", [])

    # Ensure answer is properly extracted from ChatResponse object
    if "answer" in final and final["answer"] is not None:
        if hasattr(final["answer"], 'answer'):
            # Convert ChatResponse to dict
            final["answer"] = {"answer": final["answer"].answer}
        elif not isinstance(final["answer"], dict):
            # Ensure answer is a dict
            final["answer"] = {"answer": str(final["answer"])}

    # Format opportunities to handle any JSON strings
    if "opportunities" in final and isinstance(final["opportunities"], list):
        final["opportunities"] = [
            format_opportunity_for_ui(opp) if isinstance(opp, dict) else opp
            for opp in final["opportunities"]
        ]

    # Format top opportunity if present
    if "top_opportunity" in final and isinstance(final["top_opportunity"], dict):
        final["top_opportunity"] = format_opportunity_for_ui(final["top_opportunity"])

    # Format signals for UI
    if "signals" in final and isinstance(final["signals"], list):
        final["signals"] = [
            format_signal_for_ui(signal) if isinstance(signal, dict) else signal
            for signal in final["signals"]
        ]

    # Serialize the report data to handle JSON strings properly
    serialized = serialize_report(final)
    return jsonable_encoder(serialized, exclude_none=False)


def _serialize_event(event: dict[str, Any]) -> str:
    if event.get("type") == "created":
        payload = {
            "type": "created",
            "investigation_id": event["investigation_id"],
            "query": event.get("query", ""),
        }
    elif event.get("type") == "final":
        payload = {"type": "final", **_final_to_response(event)}
    else:
        payload = {
            "type": "status",
            "status": event.get("status", {}),
            "agent": event.get("agent"),
        }
    return f"data: {json.dumps(payload)}\n\n"


_SENTINEL = object()


def _produce_events(
    query: str,
    seed_only: bool,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    investigation_id: int,
) -> None:
    """Run the pipeline in a thread, persist trace/final state, and feed the SSE queue."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        for raw in run_copilot(query, conn, seed_only=seed_only):
            event = jsonable_encoder(raw, exclude_none=False)
            logger.info("SSE event: %s", event.get("type"))
            _persist_pipeline_event(conn, investigation_id, event)
            loop.call_soon_threadsafe(queue.put_nowait, event)
    except Exception as exc:
        logger.exception("SSE pipeline error: %s", exc)
        update_investigation(
            conn,
            investigation_id,
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(exc)})
    finally:
        try:
            conn.close()
        except Exception as exc:
            logger.warning("SSE conn close error: %s", exc)
        loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)


def _create_investigation(query: str) -> int:
    """Create an investigation row in a fresh connection (executor-friendly)."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        return create_investigation(conn, query)
    finally:
        conn.close()


async def _async_event_generator(
    query: str,
    seed_only: bool = False,
    investigation_id: int | None = None,
):
    """Stream pipeline events and persist trace/final state.

    The producer thread keeps running even if the client disconnects so the
    final result is still written to the investigations table.
    """
    loop = asyncio.get_event_loop()
    if investigation_id is None:
        investigation_id = await loop.run_in_executor(None, _create_investigation, query)
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"type": "created", "investigation_id": investigation_id, "query": query})
    producer = loop.run_in_executor(
        None, _produce_events, query, seed_only, queue, loop, investigation_id
    )

    def _producer_done(fut: asyncio.Future) -> None:
        if not fut.cancelled() and (exc := fut.exception()):
            logger.exception("SSE producer thread error: %s", exc)

    producer.add_done_callback(_producer_done)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.debug("SSE heartbeat")
                yield "data: {\"type\": \"heartbeat\"}\n\n"
                continue
            if event is _SENTINEL:
                break
            if event.get("type") == "error":
                yield f"data: {json.dumps(event)}\n\n"
                break
            yield _serialize_event(event)
            if event.get("type") == "final":
                break
    except asyncio.CancelledError:
        # Client disconnected; the producer continues to persist in the background.
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database(DEFAULT_DB_PATH)
    yield


app = FastAPI(
    title="ODE API",
    description="Opportunity Discovery Engine API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.on_event("startup")
def _initialize_database() -> None:
    """Ensure the ODE database schema is present before accepting requests."""
    init_database(DEFAULT_DB_PATH)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    detail = str(exc)
    message = "An unexpected error occurred. Please try again later."
    if "no such table" in detail.lower():
        message = "Database is not initialized."
    elif "disk i/o error" in detail.lower():
        message = "Database is temporarily unavailable. Try restarting the API."
    return JSONResponse(
        status_code=500,
        content={"error": message, "detail": detail},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM opportunities")
        opp_count = cur.fetchone()[0]
        return {"status": "ok", "opportunities": opp_count}
    finally:
        conn.close()


@app.get("/api/personas")
def personas() -> list[dict[str, Any]]:
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT persona_id, name, goals, interests, skill_profile FROM personas")
        return [
            {
                "persona_id": row[0],
                "name": row[1],
                "goals": json.loads(row[2] or "[]"),
                "interests": json.loads(row[3] or "[]"),
                "skill_profile": json.loads(row[4] or "[]"),
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


@app.get("/api/sources")
def sources() -> list[dict[str, Any]]:
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        return jsonable_encoder(list_sources(conn))
    finally:
        conn.close()


@app.get("/api/trends")
def trends() -> list[dict[str, Any]]:
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        return jsonable_encoder(get_active_trends(conn))
    finally:
        conn.close()


@app.get("/api/opportunities")
def opportunities(persona_name: str | None = Query(None)) -> list[dict[str, Any]]:
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        return jsonable_encoder(list_opportunities(conn, persona_name=persona_name))
    finally:
        conn.close()


@app.post("/api/query")
def query(req: QueryRequest) -> dict[str, Any]:
    final = _run_query(req.query, seed_only=req.seed_only)
    return _final_to_response(final)


@app.get("/api/query/stream")
def query_stream(query: str = Query(...), seed_only: bool = Query(False)) -> StreamingResponse:
    logger.info("[API] Received query_stream request: query='%s' seed_only=%s", query, seed_only)
    logger.info("[API] Query repr: %r", query)
    return StreamingResponse(
        _async_event_generator(query, seed_only=seed_only),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/trace")
def trace(query: str = Query("What are the top opportunities?"), seed_only: bool = Query(False)) -> dict[str, Any]:
    final = _run_query(query, seed_only=seed_only)
    return {
        "agents": final.get("status", {}),
        "signals": len(final.get("signals", [])),
        "opportunities": len(final.get("opportunities", [])),
    }


_PIPELINE_NODES = [
    "Query",
    "Intent Analyzer",
    "Research Planner",
    "Signal Analyst",
    "Trend Analyst",
    "Opportunity Analyst",
    "Report Agent",
]


def _investigation_to_response(inv: Investigation) -> dict[str, Any]:
    """Convert an Investigation model to a JSON-serializable response."""
    # Process final_state to ensure answer is properly formatted
    final_state = inv.final_state
    if final_state and isinstance(final_state, dict):
        # Ensure answer field is properly formatted for frontend consumption
        if "answer" in final_state and final_state["answer"]:
            if hasattr(final_state["answer"], 'answer'):
                final_state["answer"] = {"answer": final_state["answer"].answer}
            elif not isinstance(final_state["answer"], dict):
                final_state["answer"] = {"answer": str(final_state["answer"])}

    return jsonable_encoder(
        {
            "investigation_id": inv.investigation_id,
            "query": inv.query,
            "status": inv.status,
            "started_at": inv.started_at,
            "completed_at": inv.completed_at,
            "final_state": final_state,
            "agent_states": inv.agent_states,
            "trace_log": inv.trace_log,
            "pipeline_artifacts": inv.pipeline_artifacts,
            "error": inv.error,
        },
        exclude_none=False,
    )


def _build_pipeline_status(agent_states: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    """Map the canonical agent-state snapshot to the display pipeline."""
    query_status = "completed" if query else "pending"
    states: dict[str, Any] = agent_states or {}
    result: list[dict[str, Any]] = []
    for name in _PIPELINE_NODES:
        if name == "Query":
            result.append({"name": name, "status": query_status, "detail": query or "Waiting for input"})
            continue
        info = states.get(name, {})
        result.append({
            "name": name,
            "status": info.get("status", "pending"),
            "detail": info.get("detail", ""),
            "duration": info.get("duration"),
        })
    return result


def _source_call_counts(agent_states: dict[str, Any]) -> dict[str, int]:
    """Sum MCP calls across all agents by normalized server name."""
    counts: dict[str, int] = {}
    for info in (agent_states or {}).values():
        if not isinstance(info, dict):
            continue
        for call in info.get("mcp_calls", []):
            server = str(call.get("server", "unknown")).lower()
            if not server:
                server = "unknown"
            counts[server] = counts.get(server, 0) + 1
    return counts


def _calls_for_source(source: dict[str, Any], call_counts: dict[str, int]) -> int:
    """Map MCP server call counts to a source record by name or type."""
    name = str(source.get("name", "")).lower()
    source_type = str(source.get("source_type", "")).lower()
    total = 0
    for server, count in call_counts.items():
        if (
            server in name
            or name in server
            or server in source_type
            or source_type in server
        ):
            total += count
    return total


def _architecture_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Assemble the live architecture view from the latest investigation and DB counts.

    Metric sources:
    - signals: COUNT(*) FROM signals (aggregate across all investigations)
    - trends: COUNT(*) FROM trends (aggregate across all investigations)
    - opportunities: COUNT(*) FROM opportunities (aggregate across all investigations)
    - investigations: COUNT(*) FROM investigations (aggregate across all investigations)
    - themes: pipeline_artifacts["themes"] from latest investigation only

    Note: Themes are stored in pipeline_artifacts during the report agent execution,
    not in a separate database table, so they only reflect the latest investigation.
    """
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM signals")
    signal_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM trends")
    trend_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM opportunities")
    opportunity_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM investigations")
    investigation_count = cur.fetchone()[0]

    latest = get_latest_investigation(conn)
    agent_states = latest.agent_states if latest else {}
    query = latest.query if latest else ""
    sources = jsonable_encoder(list_sources(conn))
    call_counts = _source_call_counts(agent_states)
    for source in sources:
        source["calls"] = _calls_for_source(source, call_counts)

    # Extract themes from pipeline_artifacts (not final_state.synthesis)
    # Themes are stored in pipeline_artifacts during the report agent
    pipeline_artifacts = latest.pipeline_artifacts if latest else {}
    if not isinstance(pipeline_artifacts, dict):
        pipeline_artifacts = {}

    themes_list = pipeline_artifacts.get("themes", [])
    if not isinstance(themes_list, list):
        themes_list = []

    theme_names = []
    for t in themes_list:
        if isinstance(t, dict):
            theme_names.append(t.get("name") or t.get("theme_name", ""))
        elif isinstance(t, str):
            theme_names.append(t)

    return {
        "pipeline": _build_pipeline_status(agent_states, query),
        "sources": sources,
        "counts": {
            "signals": signal_count,
            "trends": trend_count,
            "opportunities": opportunity_count,
            "investigations": investigation_count,
            "themes": len(theme_names),
        },
        "themes": theme_names,
        "latest": _investigation_to_response(latest) if latest else None,
    }


@app.get("/api/architecture")
def architecture() -> dict[str, Any]:
    """Return the live architecture snapshot: pipeline, sources, and counts."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        return _architecture_snapshot(conn)
    finally:
        conn.close()


@app.get("/api/investigations")
def list_investigations_endpoint(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    """Return recent investigations, newest first."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        return [_investigation_to_response(inv) for inv in list_investigations(conn, limit=limit)]
    finally:
        conn.close()


@app.get("/api/investigations/latest")
def get_latest_investigation_endpoint() -> dict[str, Any] | None:
    """Return the most recent investigation, or null if none exists."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        latest = get_latest_investigation(conn)
        return _investigation_to_response(latest) if latest else None
    finally:
        conn.close()


@app.get("/api/investigations/{investigation_id}")
def get_investigation_endpoint(investigation_id: int) -> dict[str, Any]:
    """Return a single investigation by id."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        inv = get_investigation(conn, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="Investigation not found")
        return _investigation_to_response(inv)
    finally:
        conn.close()


@app.get("/api/investigations/{investigation_id}/pipeline")
def get_investigation_pipeline(investigation_id: int) -> dict[str, Any]:
    """Return pipeline artifacts for a single investigation by id."""
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        inv = get_investigation(conn, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="Investigation not found")
        return {
            "investigation_id": inv.investigation_id,
            "query": inv.query,
            "pipeline_artifacts": inv.pipeline_artifacts,
        }
    finally:
        conn.close()


@app.get("/api/investigations/{investigation_id}/synthesis")
def get_investigation_synthesis(investigation_id: int) -> dict[str, Any]:
    """Return detailed synthesis artifacts for a single investigation by id.

    Includes themes, problems, insights, and generated opportunities with rejection details.
    """
    conn = get_db_connection(DEFAULT_DB_PATH)
    try:
        inv = get_investigation(conn, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="Investigation not found")

        artifacts = inv.pipeline_artifacts or {}

        # Extract synthesis artifacts if available
        synthesis_data = {
            "investigation_id": inv.investigation_id,
            "query": inv.query,
            "themes": artifacts.get("themes", []),
            "problems": artifacts.get("problems", []),
            "insights": artifacts.get("insights", []),
            "opportunities": artifacts.get("opportunities", []),
            "generation_stats": artifacts.get("generation_stats", {}),
        }

        return synthesis_data
    finally:
        conn.close()


@app.get("/api/tech-news")
def tech_news(refresh: bool = Query(False)) -> dict[str, Any]:
    """Return technology discovery data from multiple sources."""
    logger.info("Technology Discovery endpoint called with refresh=%s", refresh)
    start_time = time.time()

    try:
        result = build_tech_radar(refresh=refresh, db_path=DEFAULT_DB_PATH)
        duration = time.time() - start_time
        logger.info("Technology Discovery endpoint completed in %.2fs, technologies=%d", duration, len(result.get("technologies", [])))
        return result
    except Exception as exc:
        duration = time.time() - start_time
        logger.exception("Technology Discovery failed after %.2fs: %s", duration, exc)
        return {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "technologies": [],
            "error": str(exc),
        }


@app.get("/api/technology-discovery")
def technology_discovery(refresh: bool = Query(False)) -> dict[str, Any]:
    """Return technology discovery feed with trend scores and momentum.

    This endpoint provides the data for the new Technology Discovery page,
    replacing the older tech-news endpoint with more sophisticated metrics.

    Args:
        refresh: Force refresh of the discovery feed (ignore cache)

    Returns:
        Discovery feed with trending, established, and emerging technologies
    """
    logger.info("Technology Discovery Feed endpoint called with refresh=%s", refresh)
    start_time = time.time()

    try:
        if not refresh:
            # Try to load from cache first
            cached_feed = load_cached_discovery_feed()
            if cached_feed:
                duration = time.time() - start_time
                logger.info("Technology Discovery Feed returned from cache in %.2fs", duration)
                return {
                    "trending": [
                        {
                            "technology": entry.technology,
                            "category": entry.category,
                            "trend_score": entry.trend_score,
                            "momentum": entry.momentum,
                            "momentum_delta": entry.momentum_delta,
                            "signal_count_7d": entry.signal_count_7d,
                            "signal_count_30d": entry.signal_count_30d,
                            "opportunity_count": entry.opportunity_count,
                            "top_opportunity": entry.top_opportunity,
                            "ecosystem_health": entry.ecosystem_health,
                            "last_updated": entry.last_updated.isoformat(),
                        }
                        for entry in cached_feed.trending
                    ],
                    "established": [
                        {
                            "technology": entry.technology,
                            "category": entry.category,
                            "trend_score": entry.trend_score,
                            "momentum": entry.momentum,
                            "momentum_delta": entry.momentum_delta,
                            "signal_count_7d": entry.signal_count_7d,
                            "signal_count_30d": entry.signal_count_30d,
                            "opportunity_count": entry.opportunity_count,
                            "top_opportunity": entry.top_opportunity,
                            "ecosystem_health": entry.ecosystem_health,
                            "last_updated": entry.last_updated.isoformat(),
                        }
                        for entry in cached_feed.established
                    ],
                    "emerging": [
                        {
                            "technology": entry.technology,
                            "category": entry.category,
                            "trend_score": entry.trend_score,
                            "momentum": entry.momentum,
                            "momentum_delta": entry.momentum_delta,
                            "signal_count_7d": entry.signal_count_7d,
                            "signal_count_30d": entry.signal_count_30d,
                            "opportunity_count": entry.opportunity_count,
                            "top_opportunity": entry.top_opportunity,
                            "ecosystem_health": entry.ecosystem_health,
                            "last_updated": entry.last_updated.isoformat(),
                        }
                        for entry in cached_feed.emerging
                    ],
                    "updated_at": cached_feed.updated_at.isoformat(),
                    "cached": True,
                }

        # Compute fresh discovery feed
        feed = get_discovery_feed(DEFAULT_DB_PATH)

        # Cache the feed for future requests
        cache_discovery_feed(feed)

        duration = time.time() - start_time
        logger.info("Technology Discovery Feed computed in %.2fs, trending=%d, established=%d, emerging=%d",
                   duration, len(feed.trending), len(feed.established), len(feed.emerging))

        return {
            "trending": [
                {
                    "technology": entry.technology,
                    "category": entry.category,
                    "trend_score": entry.trend_score,
                    "momentum": entry.momentum,
                    "momentum_delta": entry.momentum_delta,
                    "signal_count_7d": entry.signal_count_7d,
                    "signal_count_30d": entry.signal_count_30d,
                    "opportunity_count": entry.opportunity_count,
                    "top_opportunity": entry.top_opportunity,
                    "ecosystem_health": entry.ecosystem_health,
                    "last_updated": entry.last_updated.isoformat(),
                }
                for entry in feed.trending
            ],
            "established": [
                {
                    "technology": entry.technology,
                    "category": entry.category,
                    "trend_score": entry.trend_score,
                    "momentum": entry.momentum,
                    "momentum_delta": entry.momentum_delta,
                    "signal_count_7d": entry.signal_count_7d,
                    "signal_count_30d": entry.signal_count_30d,
                    "opportunity_count": entry.opportunity_count,
                    "top_opportunity": entry.top_opportunity,
                    "ecosystem_health": entry.ecosystem_health,
                    "last_updated": entry.last_updated.isoformat(),
                }
                for entry in feed.established
            ],
            "emerging": [
                {
                    "technology": entry.technology,
                    "category": entry.category,
                    "trend_score": entry.trend_score,
                    "momentum": entry.momentum,
                    "momentum_delta": entry.momentum_delta,
                    "signal_count_7d": entry.signal_count_7d,
                    "signal_count_30d": entry.signal_count_30d,
                    "opportunity_count": entry.opportunity_count,
                    "top_opportunity": entry.top_opportunity,
                    "ecosystem_health": entry.ecosystem_health,
                    "last_updated": entry.last_updated.isoformat(),
                }
                for entry in feed.emerging
            ],
            "updated_at": feed.updated_at.isoformat(),
            "cached": False,
        }
    except Exception as exc:
        duration = time.time() - start_time
        logger.exception("Technology Discovery Feed failed after %.2fs: %s", duration, exc)
        return {
            "trending": [],
            "established": [],
            "emerging": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "error": str(exc),
        }


@app.get("/api/discovery")
async def get_discovery_api(refresh: bool = Query(False)) -> dict[str, Any]:
    """Get trending technologies with live or cached metrics.

    Args:
        refresh: Force refresh of technology metrics (ignore cache)

    Returns:
        List of trending technologies with real project counts, trend scores, and momentum
    """
    logger.info("Discovery API endpoint called with refresh=%s", refresh)
    start_time = time.time()

    try:
        conn = get_db_connection()
        try:
            technologies = get_trending_technologies(conn, force_refresh=refresh)
            duration = time.time() - start_time
            logger.info("Discovery API returned %d technologies in %.2fs", len(technologies), duration)

            return {
                "technologies": technologies,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "cached": not refresh
            }
        finally:
            conn.close()
    except Exception as exc:
        duration = time.time() - start_time
        logger.exception("Discovery API failed after %.2fs: %s", duration, exc)
        return {
            "technologies": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "error": str(exc)
        }


@app.get("/api/discovery/search")
async def search_discovery_api(q: str = Query(..., description="Technology name to search for")) -> dict[str, Any]:
    """Search for a technology, discovering it on-demand if not found.

    Args:
        q: Technology name to search for (e.g., "Polars", "Vite")

    Returns:
        Technology discovery object with live metrics, or error if not found
    """
    logger.info("Discovery search endpoint called with query=%s", q)
    start_time = time.time()

    try:
        conn = get_db_connection()
        try:
            # Try to discover the technology (checks existing registry/db first)
            tech_data = discover_custom_technology(q, conn)

            if tech_data:
                duration = time.time() - start_time
                logger.info("Discovery search found technology %s in %.2fs", q, duration)
                return {
                    "technology": tech_data,
                    "found": True,
                    "cached": "top_projects" in tech_data and len(tech_data["top_projects"]) > 0
                }
            else:
                duration = time.time() - start_time
                logger.warning("Discovery search failed for %s after %.2fs", q, duration)
                return {
                    "technology": None,
                    "found": False,
                    "error": f"Could not discover technology: {q}"
                }
        finally:
            conn.close()
    except Exception as exc:
        duration = time.time() - start_time
        logger.exception("Discovery search failed after %.2fs: %s", duration, exc)
        return {
            "technology": None,
            "found": False,
            "error": str(exc)
        }


@app.post("/api/discovery/refresh")
async def refresh_discovery_api() -> dict[str, Any]:
    """Trigger a refresh of all technology metrics.

    Returns:
        Updated list of trending technologies with fresh metrics
    """
    logger.info("Discovery refresh endpoint called")
    start_time = time.time()

    try:
        conn = get_db_connection()
        try:
            technologies = get_trending_technologies(conn, force_refresh=True)
            duration = time.time() - start_time
            logger.info("Discovery refresh completed in %.2fs, returned %d technologies", duration, len(technologies))

            return {
                "technologies": technologies,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "refreshed": True
            }
        finally:
            conn.close()
    except Exception as exc:
        duration = time.time() - start_time
        logger.exception("Discovery refresh failed after %.2fs: %s", duration, exc)
        return {
            "technologies": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "refreshed": False,
            "error": str(exc)
        }
