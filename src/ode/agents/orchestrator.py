"""Opportunity Copilot agent orchestration and visualization."""

from __future__ import annotations

import copy
import io
import logging
import time
from collections.abc import Generator
from typing import Any, NotRequired, TypedDict, cast

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

import sqlite3

from langgraph.graph import END, START, StateGraph

from ode.agents.opportunity_analyst import generate_opportunities
from ode.agents.report_agent import generate_chat_response
from ode.agents.signal_analyst import SEARCH_PAGE_SIZE, collect_signals, analyze_signals, trend_analyst
from ode.clarify import maybe_clarify
from ode.intent import _disambiguate_entity, classify_intent
from ode.pipeline_context import PipelineContext
from ode.research import get_research_depth
from ode.synthesis import synthesize
from ode.technology_resolver import TechnologyResolver
from ode.trends import get_active_trends

logger = logging.getLogger(__name__)


AGENTS = [
    "Signal Analyst",
    "Trend Analyst",
    "Opportunity Analyst",
    "Report Agent",
]

EXAMPLE_PROMPTS = [
    "What opportunities exist in MCP?",
    "What should AI engineers learn next?",
    "Which ecosystems are growing fastest?",
    "Show emerging trends",
]


def _resolve_persona_name(conn: sqlite3.Connection, query: str) -> str:
    """Map a user query to the most relevant persona name that exists in the DB."""
    q = query.lower().strip()
    cur = conn.cursor()
    cur.execute("SELECT name FROM personas ORDER BY persona_id")
    existing = [str(row[0]) for row in cur.fetchall()]
    if not existing:
        return "Software Engineer"

    # Ideal full-name matches for seeded personas.
    anchors: list[tuple[str, str]] = [
        ("platform", "Platform Engineer"),
        ("data", "Data Engineer"),
        ("product", "Product Manager"),
        ("business", "Business Leader"),
        ("ai", "AI Engineer"),
        ("software", "Software Engineer"),
        ("engineer", "Software Engineer"),
    ]
    existing_lower = {n.lower(): n for n in existing}
    for anchor, ideal in anchors:
        if anchor in q:
            if ideal.lower() in existing_lower:
                return existing_lower[ideal.lower()]
            # Fall back to any existing persona whose name contains the anchor.
            for name in existing:
                if anchor in name.lower():
                    return name
            return existing[0]
    return existing[0]


def _status_color(status: str) -> str:
    return {
        "pending": "#444444",
        "running": "#F1C40F",
        "completed": "#2ECC71",
    }.get(status, "#444444")


def _mcp_call_color(success: bool) -> str:
    return "#2ECC71" if success else "#E74C3C"


def render_agent_graph(states: dict[str, dict[str, Any]]) -> bytes:
    """Render a vertical DAG of the agent pipeline, including MCP sub-calls."""
    entries: list[dict[str, Any]] = [{"kind": "agent", "name": "User Query"}]
    for agent in AGENTS:
        entries.append({"kind": "agent", "name": agent})
        expanded = states.get(agent, {}).get("expanded_queries", [])
        if expanded:
            entries.append({"kind": "expanded", "queries": expanded})
        repos = states.get(agent, {}).get("discovered_repos", [])
        if repos:
            entries.append({"kind": "discovered", "repos": repos})
        # Aggregate MCP calls per tool for the graph to avoid excessive height.
        mcp_calls = states.get(agent, {}).get("mcp_calls", [])
        if mcp_calls:
            summary: dict[str, list[int | float]] = {}
            for call in mcp_calls:
                if not isinstance(call, dict):
                    continue
                key = f"{call.get('server', 'mcp')}/{call.get('tool', 'unknown')}"
                bucket = summary.setdefault(key, [0, 0, 0.0])
                bucket[0] += 1 if call.get("success") else 0  # type: ignore[operator]
                bucket[1] += 1  # type: ignore[operator]
                bucket[2] += call.get("duration", 0.0)  # type: ignore[operator]
            for key, (success_count, total_count, total_dur) in summary.items():
                ok = success_count == total_count
                mark = "✅" if ok else f"({success_count}/{total_count})"
                entries.append({
                    "kind": "mcp",
                    "tool": key,
                    "success": ok,
                    "duration": float(total_dur),
                    "label": f"{key} {mark} ({float(total_dur):.1f}s)",
                })
    entries.append({"kind": "agent", "name": "Final Recommendation"})

    node_status: dict[str, str] = {"User Query": "completed"}
    for agent in AGENTS:
        node_status[agent] = states.get(agent, {}).get("status", "pending")
    all_completed = all(node_status[a] == "completed" for a in AGENTS)
    node_status["Final Recommendation"] = "completed" if all_completed else "pending"

    positions: list[tuple[float, float]] = []
    y = 0.5
    for entry in entries:
        if entry["kind"] == "agent":
            height = 0.85
        elif entry["kind"] == "expanded":
            height = min(2.0, 0.22 * len(entry["queries"]))
        elif entry["kind"] == "discovered":
            height = min(2.0, 0.22 * len(entry["repos"]))
        else:
            height = 0.42
        positions.append((y, height))
        y += height + 0.12

    fig, ax = plt.subplots(figsize=(8, max(6, y)))
    fig.patch.set_facecolor("#0B0C10")
    ax.set_facecolor("#0B0C10")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, y)
    ax.axis("off")

    for i, entry in enumerate(entries):
        y_pos, height = positions[i]
        if entry["kind"] == "agent":
            name = entry["name"]
            status = node_status.get(name, "pending")
            color = _status_color(status)
            x_start = 0.5
            width = 9.0
            fontsize = 11
            duration = ""
            if name in states:
                dur = states[name].get("duration")
                if dur is not None:
                    duration = f"  ({dur:.2f}s)"
            label = f"{name}{duration}"
        elif entry["kind"] == "expanded":
            color = "#E67E22"
            x_start = 1.0
            width = 8.0
            fontsize = 8
            label = "\n".join(entry["queries"])
        elif entry["kind"] == "discovered":
            color = "#3498DB"
            x_start = 1.0
            width = 8.0
            fontsize = 8
            label = "\n".join(entry["repos"])
        else:
            color = _mcp_call_color(entry["success"])
            x_start = 1.25
            width = 7.5
            fontsize = 9
            label = entry["label"]

        rect = patches.FancyBboxPatch(
            (x_start, y_pos),
            width,
            height,
            boxstyle="round,pad=0.04,rounding_size=0.15",
            facecolor=color,
            edgecolor="#888888",
            linewidth=1.2,
        )
        ax.add_patch(rect)
        ax.text(
            5.0,
            y_pos + height / 2,
            label,
            ha="center",
            va="center",
            color="white",
            fontsize=fontsize,
            weight="bold" if entry["kind"] == "agent" else "normal",
        )

        if i < len(entries) - 1:
            y_next, _ = positions[i + 1]
            ax.annotate(
                "",
                xy=(5.0, y_next + 0.05),
                xytext=(5.0, y_pos + height - 0.05),
                arrowprops=dict(arrowstyle="->", color="#888888", lw=1.5),
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
    plt.close(fig)
    return buf.getvalue()


def agent_details_html(states: dict[str, dict[str, Any]]) -> str:
    """Return an HTML status panel for each agent, including repos and MCP calls."""
    status_emoji = {
        "pending": "⚪",
        "running": "🟡",
        "completed": "🟢",
    }
    lines = []
    for agent in AGENTS:
        info = states.get(agent, {})
        status = info.get("status", "pending")
        emoji = status_emoji.get(status, "⚪")
        duration = info.get("duration")
        detail = info.get("detail", "")
        dur_text = f" · {duration:.2f}s" if duration is not None else ""
        lines.append(f"{emoji} **{agent}**{dur_text} — {detail}")
        expanded = info.get("expanded_queries", [])
        if expanded:
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;🔍 {len(expanded)} themes: {', '.join(expanded[:10])}")
        repos = info.get("discovered_repos", [])
        if repos:
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;📦 {len(repos)} repos: {', '.join(repos[:15])}")
        for call in info.get("mcp_calls", []):
            if not isinstance(call, dict):
                continue
            if call.get("tool") == "cache_metrics":
                hits = call.get("cache_hits", 0)
                misses = call.get("cache_misses", 0)
                lines.append(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;🗂️ Context7 cache: {hits} hits / {misses} misses"
                )
                continue
            mark = "✅" if call.get("success") else "❌"
            err = f" — {call.get('error')}" if call.get("error") else ""
            tool = call.get("tool", "unknown")
            duration = call.get("duration")
            dur_text = f"{float(duration):.2f}s" if duration is not None else "0.00s"
            lines.append(
                f"&nbsp;&nbsp;&nbsp;&nbsp;{mark} {call.get('server', 'mcp')}: `{tool}` ({dur_text}){err}"
            )
    return "\n\n".join(lines)


def execution_trace_html(states: dict[str, dict[str, Any]]) -> str:
    """Render a compact horizontal execution trace for the agent workflow."""
    steps: list[str] = []
    for agent in AGENTS:
        info = states.get(agent, {})
        status = info.get("status", "pending")
        duration = info.get("duration")
        mcp_count = len(info.get("mcp_calls", []))
        dur_text = f"{duration:.2f}s" if duration is not None else ""
        mcp_text = f"{mcp_count} MCP{'s' if mcp_count != 1 else ''}" if mcp_count else ""
        meta = " · ".join(p for p in [dur_text, mcp_text] if p)
        label = {"pending": "Pending", "running": "Running", "completed": "Complete"}.get(status, status)
        steps.append(
            f"""
            <div class="ode-trace-step {status}">
              <div class="ode-trace-dot"></div>
              <div class="ode-trace-content">
                <div class="ode-trace-name">{agent}</div>
                <div class="ode-trace-meta">{label} {f'• {meta}' if meta else ''}</div>
              </div>
            </div>
            """
        )
    return '<div class="ode-trace">' + "\n".join(
        ('<div class="ode-trace-arrow" aria-hidden="true"></div>' if i > 0 else '') + s for i, s in enumerate(steps)
    ) + "</div>"


# ---------------------------------------------------------------------------
# LangGraph-backed pipeline
# ---------------------------------------------------------------------------


class ODEState(TypedDict):
    """Pipeline state passed between LangGraph nodes.

    The schema mirrors the data previously carried by the hand-rolled
    ``run_copilot`` generator so that UI consumers of ``run_copilot`` see the
    same keys and values.
    """

    query: str
    seed_only: bool
    conn: Any
    intent: dict[str, Any]
    persona_name: NotRequired[str]
    entity_disambiguation: NotRequired[dict[str, Any]]
    signal_output: NotRequired[dict[str, Any]]
    analysis_result: NotRequired[Any]
    theme_trends: NotRequired[list[Any]]
    opportunities: NotRequired[list[Any]]
    top_opportunity: NotRequired[Any]
    top_trend: NotRequired[Any]
    answer: NotRequired[Any]
    states: dict[str, Any]
    ui_update_log: NotRequired[list[dict[str, Any]]]
    generation_stats: NotRequired[dict[str, Any]]
    pipeline_artifacts: NotRequired[dict[str, Any]]
    context: NotRequired[Any]  # PipelineContext with TechnologyProfile


_NODE_TO_AGENT = {
    "classify": "Signal Analyst",
    "collector": "Signal Analyst",
    "analysis": "Signal Analyst",
    "trend": "Trend Analyst",
    "opportunity": "Opportunity Analyst",
    "report": "Report Agent",
}


def _build_initial_state(
    query: str,
    conn: sqlite3.Connection,
    seed_only: bool,
) -> ODEState:
    # Pipeline-specific statuses are tracked alongside the five core agents so
    # the live architecture view can surface Query -> Intent Analyzer -> Research Planner.
    pipeline_agents = ["Intent Analyzer", "Research Planner"] + AGENTS
    return {
        "query": query,
        "seed_only": seed_only,
        "conn": conn,
        "intent": {},
        "persona_name": "",
        "states": {
            agent: {
                "status": "pending",
                "duration": None,
                "detail": "",
                "mcp_calls": [],
                "discovered_repos": [],
                "expanded_queries": [],
            }
            for agent in pipeline_agents
        },
        "ui_update_log": [],
    }


def _consume_generator(gen: Generator[Any, None, Any], state: ODEState) -> Any:
    """Consume a generator that yields UI updates and returns a final value."""
    ui_log = state.setdefault("ui_update_log", [])
    while True:
        try:
            update = next(gen)
        except StopIteration as exc:
            return exc.value
        if isinstance(update, dict):
            ui_log.append(update)


def _update_state(
    state: ODEState,
    agent: str,
    status: str,
    duration: float | None = None,
    detail: str = "",
    llm_calls: int = 0,
    signals_collected: int = 0,
) -> None:
    """Mutate agent status and append a UI update event to the log."""
    states = state["states"]
    if agent not in states:
        states[agent] = {}
    states[agent]["status"] = status
    if duration is not None:
        states[agent]["duration"] = duration
    if detail:
        states[agent]["detail"] = detail
    if llm_calls:
        states[agent]["llm_calls"] = states[agent].get("llm_calls", 0) + llm_calls
    if signals_collected:
        states[agent]["signals_collected"] = states[agent].get("signals_collected", 0) + signals_collected
    logger.info("State update: agent=%s, status=%s, duration=%s, detail=%s, llm_calls=%d, signals=%d",
                agent, status, f"{duration:.2f}s" if duration is not None else "N/A", detail, llm_calls, signals_collected)
    ui_log = state.setdefault("ui_update_log", [])
    ui_log.append({
        "type": "update",
        "status": copy.deepcopy(states),
        "agent": agent,
    })


def intent_node(state: ODEState) -> ODEState:
    """Classify intent, optionally clarify, and disambiguate the entity."""
    query = state["query"]
    conn = state["conn"]
    seed_only = state["seed_only"]
    states = state["states"]

    _update_state(state, "Intent Analyzer", "running", 0.0, "Classifying intent...")
    logger.info("[START] Pipeline query=%s", query)
    stage_start = time.time()

    intent_start = time.time()
    intent = classify_intent(query)
    intent_duration = time.time() - intent_start
    logger.info("[END] classify_intent duration=%.2fs", intent_duration)

    clarify_start = time.time()
    intent = maybe_clarify(query, intent)
    clarify_duration = time.time() - clarify_start
    logger.info("[END] maybe_clarify duration=%.2fs", clarify_duration)

    # Technology Resolution (NEW: create TechnologyProfile)
    tech_resolve_start = time.time()
    resolver = TechnologyResolver()
    resolved = resolver.resolve(query, intent)
    tech_resolve_duration = time.time() - tech_resolve_start
    logger.info("[END] resolve_technology duration=%.2fs, profile=%s", tech_resolve_duration, resolved.primary_profile.canonical_name if resolved.primary_profile else "None")

    # Create PipelineContext
    research_depth_obj = get_research_depth(intent)
    ctx = PipelineContext(
        query=query,
        intent=intent,
        resolved=resolved,
    )
    state["context"] = ctx

    duration = time.time() - stage_start
    logger.info("[END] Intent Analyzer total=%.2fs (classify=%.2fs, clarify=%.2fs, tech_resolve=%.2fs)",
                duration, intent_duration, clarify_duration, tech_resolve_duration)
    _update_state(
        state,
        "Intent Analyzer",
        "completed",
        duration,
        f"Intent: {intent.get('intent', 'unknown')}, Tech: {resolved.primary_profile.canonical_name if resolved.primary_profile else 'Unknown'}",
        llm_calls=1 if intent_duration > 0.1 else 0,  # Estimate LLM calls based on duration
    )

    state["intent"] = intent
    states["intent"] = intent

    if intent.get("needs_clarification"):
        state["persona_name"] = intent.get("persona_name") or "Engineer"
        states["persona"] = state["persona_name"]
        _update_state(state, "Signal Analyst", "completed", 0.0, "Awaiting clarification")
        answer = generate_chat_response(query, [], context={"intent": intent})
        logger.info("[CLARIFICATION] query=%s", query)
        state["answer"] = answer
        state["opportunities"] = []
        state["theme_trends"] = []
        state["top_opportunity"] = None
        state["top_trend"] = None
        state["signal_output"] = {
            "signals": [],
            "raw_signals": [],
            "discovered_repos": [],
            "run_id": 0,
            "used_fallback": True,
        }
        return cast(ODEState, dict(state))

    persona_name = intent.get("persona_name") or _resolve_persona_name(conn, query)
    state["persona_name"] = persona_name
    states["persona"] = persona_name

    primary_technology = intent.get("primary_technology", "")
    if primary_technology and not seed_only:
        _update_state(state, "Signal Analyst", "running", 0.0, "Disambiguating entity...")
        stage_start = time.time()
        disambiguated_entity, entity_confidence, entity_signals = _disambiguate_entity(
            primary_technology, query, intent.get("topics", [])
        )
        logger.info("[END] _disambiguate_entity duration=%.2fs", time.time() - stage_start)
        state["entity_disambiguation"] = {
            "original_entity": primary_technology,
            "disambiguated_entity": disambiguated_entity,
            "confidence": entity_confidence,
            "supporting_signals": entity_signals,
        }
        if entity_confidence >= 0.6 and disambiguated_entity != primary_technology:
            intent["primary_technology"] = disambiguated_entity
            intent["topics"] = [disambiguated_entity] + [
                t for t in intent.get("topics", []) if t.lower() != primary_technology.lower()
            ]
            logger.info(
                "Entity disambiguated: %s -> %s (confidence: %.2f)",
                primary_technology,
                disambiguated_entity,
                entity_confidence,
            )
    states["intent"] = intent
    states["entity_disambiguation"] = state.get("entity_disambiguation")

    logger.info(
        "Pipeline raw_query=%s intent=%s topics=%s search_themes=%s persona=%s",
        query,
        intent["intent"],
        intent.get("topics", []),
        intent.get("search_themes", []),
        persona_name,
    )

    _update_state(state, "Signal Analyst", "running", 0.0, "Initializing signal collection...")
    return cast(ODEState, dict(state))


def collector_node(state: ODEState) -> ODEState:
    """Collect raw signals from configured sources using TechnologyProfile for filtering."""
    query = state["query"]
    conn = state["conn"]
    seed_only = state["seed_only"]
    intent = state["intent"]
    states = state["states"]
    ctx = state.get("context")  # Get PipelineContext if available

    logger.info("=== COLLECTOR NODE START ===")
    logger.info("Query: %s", query)
    logger.info("[START] Signal Collection node, seed_only=%s", seed_only)

    if seed_only:
        _update_state(state, "Research Planner", "completed", 0.0, "seed mode")
        _update_state(state, "Signal Analyst", "completed", 0.0, "seed mode")
        signal_output: dict[str, Any] = {
            "signals": [],
            "raw_signals": [],
            "discovered_repos": [],
            "run_id": 0,
            "used_fallback": True,
        }
        logger.info("[END] Signal Collection (seed mode): 0 signals")
    else:
        _update_state(state, "Signal Analyst", "running", 0.0, "Initializing signal collection...")
        stage_start = time.time()
        logger.info("[START] collect_signals generator")

        # Use profile-based search expansion if context is available
        if ctx and isinstance(ctx, PipelineContext):
            profile = ctx.profile
            logger.info("Technology Profile: %s", profile.canonical_name if profile else "NOT SET")
            if profile:
                logger.info("Category: %s", profile.category)
                logger.info("Search expansion terms: %s", profile.search_expansion)
                logger.info("Exclusion terms: %s", profile.exclusion_terms)
        else:
            profile = None
            logger.info("Technology Profile: NOT SET (ctx not PipelineContext or None)")

        gen = collect_signals(query, conn, states, intent, ctx)
        signal_output = _consume_generator(gen, state)

        duration = time.time() - stage_start
        signal_count = len(signal_output.get('signals', []))
        logger.info("[END] Signal Collection duration=%.2fs, signals=%d", duration, signal_count)
        _update_state(state, "Signal Analyst", "completed", duration, f"{signal_count} signals collected", signals_collected=signal_count)

        # Add signals to PipelineContext
        if ctx and isinstance(ctx, PipelineContext):
            ctx.raw_signals = signal_output.get('raw_signals', [])
            ctx.filtered_signals = signal_output.get('signals', [])

    if not isinstance(signal_output, dict):
        logger.warning("Signal collection returned non-dict value %r, using fallback", type(signal_output))
        signal_output = {
            "signals": signal_output if isinstance(signal_output, list) else [],
            "raw_signals": [],
            "discovered_repos": [],
            "run_id": 0,
            "used_fallback": True,
        }
    states["Signal Analyst"]["used_fallback"] = signal_output.get("used_fallback", False)
    state["signal_output"] = signal_output
    return cast(ODEState, dict(state))


def analysis_node(state: ODEState) -> ODEState:
    """Run the multi-stage LLM analysis pipeline over collected signals."""
    query = state["query"]
    intent = state["intent"]
    states = state["states"]
    signal_output = state.get("signal_output", {})
    if not isinstance(signal_output, dict):
        signal_output = {}

    logger.info("[START] Signal Analysis node, signal_count=%d", len(signal_output.get('signals', [])))

    raw_signals = cast(
        list[dict[str, Any]],
        signal_output.get("raw_signals") or signal_output.get("signals", []),
    )
    analysis_result: Any = None
    if raw_signals:
        # Clear LLM cache at start of each query to avoid cross-query contamination
        from ode.agents.signal_analyst import _clear_llm_cache
        _clear_llm_cache()

        _update_state(state, "Signal Analyst", "running", states["Signal Analyst"].get("duration", 0.0), "Analyzing signals...")
        analysis_start = time.time()

        # Determine research depth from intent
        research_depth = get_research_depth(intent)
        logger.info("Signal Analysis: research_depth=%s, raw_signals=%d", research_depth, len(raw_signals))

        gen = analyze_signals(raw_signals, query, intent, states, research_depth)
        analysis_result = _consume_generator(gen, state)
        analysis_duration = time.time() - analysis_start
        logger.info(
            "[END] Signal Analysis duration=%.2fs themes=%d problems=%d insights=%d",
            analysis_duration,
            len(analysis_result.themes),
            len(analysis_result.problems),
            len(analysis_result.insights),
        )
        # Accumulate duration: collection + analysis
        total_duration = states["Signal Analyst"].get("duration", 0.0) + analysis_duration
        _update_state(state, "Signal Analyst", "completed", total_duration, f"{len(raw_signals)} signals analyzed", llm_calls=3)  # Estimate 3 LLM calls for analysis
    else:
        logger.warning("No signals to analyze, skipping analysis")
        _update_state(state, "Signal Analyst", "completed", states["Signal Analyst"].get("duration", 0.0), "No signals to analyze")

    state["analysis_result"] = analysis_result
    states["analysis_result"] = analysis_result  # Store in states for downstream stages
    logger.info("[END] Signal Analysis node, themes=%d", len(analysis_result.themes) if analysis_result else 0)
    return cast(ODEState, dict(state))


def trend_node(state: ODEState) -> ODEState:
    """Synthesize repository signals into market-level trends."""
    conn = state["conn"]
    seed_only = state["seed_only"]
    intent = state["intent"]
    states = state["states"]
    signal_output = state.get("signal_output", {})
    if not isinstance(signal_output, dict):
        signal_output = {}

    logger.info("[START] Trend Analysis node, seed_only=%s", seed_only)

    if seed_only:
        _update_state(state, "Trend Analyst", "running")
        theme_trends = get_active_trends(conn)
        _update_state(state, "Trend Analyst", "completed", 0.0, f"{len(theme_trends)} seeded trends")
        logger.info("[END] Trend Analysis (seed mode): %d trends", len(theme_trends))
    else:
        _update_state(state, "Trend Analyst", "running", 0.0, "Analyzing trends...")
        stage_start = time.time()
        # Pass analysis_result to Trend Analyst instead of signal_output
        analysis_result = state.get("analysis_result")
        logger.info("Trend Analysis: analysis_result=%s, signal_output_keys=%s",
                    "present" if analysis_result else "missing", list(signal_output.keys())[:5])
        gen = trend_analyst(conn, signal_output, states, intent, analysis_result)
        theme_trends = _consume_generator(gen, state)
        duration = time.time() - stage_start
        logger.info("[END] Trend Analysis duration=%.2fs, trends=%d", duration, len(theme_trends))
        _update_state(state, "Trend Analyst", "completed", duration, f"{len(theme_trends)} trends identified", llm_calls=1)

    state["theme_trends"] = theme_trends
    state["top_trend"] = max(theme_trends, key=lambda t: t.momentum) if theme_trends else None
    logger.info("[END] Trend Analysis node, trends=%d", len(theme_trends))
    return cast(ODEState, dict(state))


def opportunity_node(state: ODEState) -> ODEState:
    """Score active trends into persona-matched opportunities."""
    conn = state["conn"]
    seed_only = state["seed_only"]
    query = state["query"]
    intent = state["intent"]
    states = state["states"]
    theme_trends = state.get("theme_trends", [])

    logger.info("[START] Opportunity Analysis node, seed_only=%s, trends=%d", seed_only, len(theme_trends))

    _update_state(state, "Opportunity Analyst", "running", 0.0, "Initializing opportunity scoring...")
    start = time.time()
    logger.info("[START] Opportunity Analyst")
    persona_name = intent.get("persona_name") or _resolve_persona_name(conn, query)
    state["persona_name"] = persona_name

    opp_mcp_calls = states["Opportunity Analyst"]["mcp_calls"] if not seed_only else None
    _update_state(
        state,
        "Opportunity Analyst",
        "running",
        0.0,
        f"Scoring {len(theme_trends)} trends for {persona_name}...",
    )
    # Pass analysis_result to Opportunity Analyst for better context
    analysis_result = state.get("analysis_result")
    logger.info("Opportunity Analysis: analysis_result=%s, trends=%d, persona=%s",
                "present" if analysis_result else "missing", len(theme_trends), persona_name)
    opportunities, generation_stats = generate_opportunities(
        conn,
        persona_name=persona_name,
        mcp_calls=opp_mcp_calls,
        trends=theme_trends,
        intent=intent,
        query=query,
        analysis_result=analysis_result,
    )
    duration = time.time() - start
    logger.info("[END] Opportunity Analysis duration=%.2fs, opportunities=%d", duration, len(opportunities))

    top_opportunity = max(opportunities, key=lambda o: o.score) if opportunities else None
    cache_metric = (
        next((c for c in opp_mcp_calls if c.get("tool") == "cache_metrics"), None)
        if opp_mcp_calls
        else None
    )
    states["Opportunity Analyst"]["used_seed"] = seed_only
    states["Opportunity Analyst"]["source"] = "seed" if seed_only else "generated"
    states["Opportunity Analyst"]["context7_cache"] = cache_metric or {}

    cache_text = ""
    if cache_metric:
        cache_text = (
            f" (Context7 {cache_metric.get('cache_hits', 0)} hits / "
            f"{cache_metric.get('cache_misses', 0)} misses)"
        )

    signal_output = state.get("signal_output", {})
    if not isinstance(signal_output, dict):
        signal_output = {}
    opp_detail = (
        f"{len(opportunities)} opportunities for {persona_name}{cache_text}"
        + (f"; top: {top_opportunity.title}" if top_opportunity else "")
    )
    logger.info(
        "Pipeline counts: signals=%d trends=%d persona=%s opportunities=%d top=%s",
        len(cast(list[Any], signal_output.get("signals", []))),
        len(cast(list[Any], theme_trends)),
        persona_name,
        len(opportunities),
        top_opportunity.title if top_opportunity else "none",
    )
    logger.info(
        "Opportunity generation: Generated=%d, Rejected Title=%d, Rejected Evidence=%d, Accepted=%d",
        generation_stats.get("generated", 0),
        generation_stats.get("rejected_title", 0),
        generation_stats.get("rejected_evidence", 0),
        generation_stats.get("accepted", 0),
    )
    if generation_stats.get("rejection_reasons"):
        logger.info("Rejection reasons: %s", "; ".join(generation_stats["rejection_reasons"]))
    logger.info("[END] Opportunity Analyst duration=%.2fs", duration)
    _update_state(state, "Opportunity Analyst", "completed", duration, opp_detail, llm_calls=len(theme_trends))  # One LLM call per trend

    state["opportunities"] = opportunities
    state["top_opportunity"] = top_opportunity
    state["generation_stats"] = generation_stats
    logger.info("[END] Opportunity Analysis node, opportunities=%d", len(opportunities))
    return cast(ODEState, dict(state))


def report_node(state: ODEState) -> ODEState:
    """Synthesize evidence and generate the final recommendation."""
    query = state["query"]
    intent = state["intent"]
    states = state["states"]
    signal_output = state.get("signal_output", {})
    if not isinstance(signal_output, dict):
        signal_output = {}
    analysis_result = state.get("analysis_result")
    theme_trends = state.get("theme_trends", [])
    opportunities = state.get("opportunities", [])
    persona_name = state.get("persona_name", "Engineer")

    logger.info("[START] Report Generation node, signals=%d, themes=%d, trends=%d, opportunities=%d",
                len(signal_output.get('signals', [])),
                len(analysis_result.themes) if analysis_result else 0,
                len(theme_trends),
                len(opportunities))

    _update_state(state, "Report Agent", "running", 0.0, "Initializing report generation...")
    start = time.time()
    logger.info("[START] Report Agent")

    signals = cast(list[Any], signal_output.get("signals", []))
    github_count = sum(
        1 for s in signals if isinstance(s, dict) and str(s.get("source_type", "")).startswith("github")
    )
    tavily_count = sum(
        1 for s in signals if isinstance(s, dict) and str(s.get("source_type", "")).startswith("tavily")
    )
    playwright_count = sum(
        1
        for s in signals
        if isinstance(s, dict)
        and (
            str(s.get("source_type", "")) == "web"
            or str(s.get("metric", "")).startswith(("web_page", "docs_page"))
        )
    )
    playwright_examples = sorted(
        {
            str(s.get("entity", ""))
            for s in signals
            if isinstance(s, dict)
            and (
                str(s.get("source_type", "")) == "web"
                or str(s.get("metric", "")).startswith(("web_page", "docs_page"))
            )
        }
    )[:6]
    mcp_sources = sorted(
        {
            str(call.get("server", "mcp"))
            for agent in AGENTS
            for call in cast(dict[str, Any], states[agent]).get("mcp_calls", [])
            if isinstance(call, dict)
        }
    )
    opp_mcp_calls = states["Opportunity Analyst"].get("mcp_calls", [])
    context7_count = sum(
        1
        for c in opp_mcp_calls
        if isinstance(c, dict) and c.get("server") == "context7" and c.get("tool") != "cache_metrics"
    )
    _update_state(
        state,
        "Report Agent",
        "running",
        0.0,
        f"Processing {len(signals)} signals from {len(mcp_sources)} MCP sources...",
    )

    synthesis = synthesize(
        signals,
        intent,
        analysis=analysis_result.to_dict() if analysis_result else None,
    )
    logger.info("Synthesis completed: themes=%d, problems=%d, insights=%d, opportunities=%d, narrative=%s",
                len(synthesis.themes), len(synthesis.problems), len(synthesis.insights),
                len(synthesis.opportunities), "present" if synthesis.narrative else "missing")

    # Get generation stats from state (set by opportunity_analyst)
    generation_stats = state.get("generation_stats", {})

    # Capture pipeline artifacts for debugging - do this BEFORE any failure checks
    # so artifacts are always available even if the report fails
    pipeline_artifacts = {
        "raw_signals": signals,
        "normalized_signals": [s.to_dict() if hasattr(s, 'to_dict') else s for s in signals],
        "themes": [t.__dict__ for t in synthesis.themes],
        "problems": [p.__dict__ for p in synthesis.problems],
        "insights": [i.__dict__ for i in synthesis.insights],
        "opportunities": [o.__dict__ for o in synthesis.opportunities],
        "narrative": synthesis.narrative.__dict__ if synthesis.narrative else None,
        "generation_stats": generation_stats,
    }

    # Log pipeline artifacts for debugging
    themes_list = pipeline_artifacts.get("themes")
    themes_count = len(themes_list) if isinstance(themes_list, list) else 0
    problems_list = pipeline_artifacts.get("problems")
    problems_count = len(problems_list) if isinstance(problems_list, list) else 0
    insights_list = pipeline_artifacts.get("insights")
    insights_count = len(insights_list) if isinstance(insights_list, list) else 0
    opportunities_list = pipeline_artifacts.get("opportunities")
    opportunities_count = len(opportunities_list) if isinstance(opportunities_list, list) else 0
    gen_stats = pipeline_artifacts.get("generation_stats")
    gen_stats_keys = list(gen_stats.keys()) if isinstance(gen_stats, dict) else "empty"

    logger.info(
        "Pipeline artifacts created: themes=%d, problems=%d, insights=%d, opportunities=%d, generation_stats_keys=%s",
        themes_count,
        problems_count,
        insights_count,
        opportunities_count,
        gen_stats_keys,
    )

    # Store pipeline_artifacts in state immediately so they're persisted even if report fails
    state["pipeline_artifacts"] = pipeline_artifacts

    report_context = {
        "persona_name": persona_name,
        "signal_count": len(signals),
        "github_count": github_count,
        "tavily_count": tavily_count,
        "playwright_count": playwright_count,
        "playwright_examples": playwright_examples,
        "context7_count": context7_count,
        "repo_count": len(cast(list[Any], signal_output.get("discovered_repos", []))),
        "trend_count": len(cast(list[Any], theme_trends)),
        "opportunity_count": len(opportunities),
        "mcp_sources": mcp_sources,
        "signals": cast(list[Any], signal_output.get("signals", [])),
        "discovered_repos": cast(list[Any], signal_output.get("discovered_repos", [])),
        "intent": intent,
        "agent_states": copy.deepcopy(states),
        "trends": theme_trends,
        "synthesis": synthesis,
        "analysis": analysis_result.to_dict() if analysis_result else None,
        "pipeline_artifacts": pipeline_artifacts,
        "context": state.get("context"),  # Add PipelineContext for report generation
    }
    ui_opportunities = opportunities
    top_opportunity = ui_opportunities[0] if ui_opportunities else None
    _update_state(
        state,
        "Report Agent",
        "running",
        0.0,
        f"Generating analysis for {len(ui_opportunities)} opportunities...",
    )
    logger.info("Generating chat response: query=%s, opportunities=%d, context_keys=%s",
                query, len(ui_opportunities), list(report_context.keys()))
    answer = generate_chat_response(query, ui_opportunities, context=report_context)
    logger.info(
        "Final UI: %d opportunities to display, answer_length=%d",
        len(ui_opportunities),
        len(answer.answer) if answer else 0,
    )
    report_duration = time.time() - start
    logger.info("[END] Report Agent duration=%.2fs", report_duration)
    if not ui_opportunities:
        _update_state(state, "Report Agent", "failed", report_duration, "Insufficient evidence", llm_calls=2)
    else:
        _update_state(
            state,
            "Report Agent",
            "completed",
            report_duration,
            f"{len(ui_opportunities)} opportunities displayed",
            llm_calls=2,  # Estimate 2 LLM calls (synthesis + response)
        )

    state["answer"] = answer
    state["opportunities"] = ui_opportunities
    state["top_opportunity"] = top_opportunity
    logger.info("[END] Report Generation node, answer_length=%d", len(answer.answer) if answer else 0)
    return cast(ODEState, dict(state))


def route_clarification(state: ODEState) -> str:
    """Route to END when the query needs clarification, otherwise continue."""
    if state["intent"].get("needs_clarification"):
        return "clarification_end"
    return "collector"


_pipeline_builder = StateGraph(ODEState)
_pipeline_builder.add_node("classify", intent_node)
_pipeline_builder.add_node("collector", collector_node)
_pipeline_builder.add_node("analysis", analysis_node)
_pipeline_builder.add_node("trend", trend_node)
_pipeline_builder.add_node("opportunity", opportunity_node)
_pipeline_builder.add_node("report", report_node)

_pipeline_builder.add_edge(START, "classify")
_pipeline_builder.add_conditional_edges(
    "classify",
    route_clarification,
    {
        "clarification_end": END,
        "collector": "collector",
    },
)
_pipeline_builder.add_edge("collector", "analysis")
_pipeline_builder.add_edge("analysis", "trend")
_pipeline_builder.add_edge("trend", "opportunity")
_pipeline_builder.add_edge("opportunity", "report")
_pipeline_builder.add_edge("report", END)

pipeline_graph = _pipeline_builder.compile()


def _replay_updates(
    state: dict[str, Any],
    emitted: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return new UI update events from state and the updated emitted count."""
    ui_log: list[dict[str, Any]] = state.get("ui_update_log") or []
    new_events = ui_log[emitted:]
    return new_events, emitted + len(new_events)


def run_copilot(
    query: str,
    conn: sqlite3.Connection,
    seed_only: bool = False,
) -> Generator[dict[str, Any], None, None]:
    """Execute the LangGraph pipeline and yield status updates plus the final result."""
    initial_state = _build_initial_state(query, conn, seed_only)
    current_state: dict[str, Any] | None = None
    emitted = 0

    for event in pipeline_graph.stream(initial_state, stream_mode="updates"):
        node_state_update = next(iter(event.values()))
        current_state = cast(dict[str, Any], node_state_update)
        new_events, emitted = _replay_updates(current_state, emitted)
        yield from new_events

    if current_state is None:
        current_state = cast(dict[str, Any], pipeline_graph.invoke(initial_state))

    new_events, emitted = _replay_updates(current_state, emitted)
    yield from new_events

    signal_output = current_state.get("signal_output") or {"signals": [], "discovered_repos": []}
    if not isinstance(signal_output, dict):
        signal_output = {"signals": [], "discovered_repos": []}
    opportunities = current_state.get("opportunities", [])
    top_opportunity = current_state.get("top_opportunity")
    top_trend = current_state.get("top_trend")
    answer = current_state.get("answer")

    # Convert ChatResponse to dict for serialization
    answer_dict = None
    if answer and hasattr(answer, 'answer'):
        answer_dict = {"answer": answer.answer}
    elif isinstance(answer, dict):
        answer_dict = answer

    logger.info("[FINAL] Pipeline complete: signals=%d, opportunities=%d, answer=%s, answer_length=%d",
                len(signal_output.get('signals', [])), len(opportunities),
                "present" if answer_dict else "missing",
                len(answer_dict.get('answer', '')) if answer_dict else 0)

    synthesis_obj = current_state.get("synthesis")
    logger.info("FINAL YIELD: synthesis=%s, has_themes=%s, themes_count=%d",
                "present" if synthesis_obj else "None",
                "yes" if synthesis_obj and hasattr(synthesis_obj, 'themes') else "no",
                len(synthesis_obj.themes) if synthesis_obj and hasattr(synthesis_obj, 'themes') else 0)

    # Convert signals to frontend-ready format
    raw_signals_list = signal_output.get("signals", []) or current_state.get("signals", [])
    formatted_signals = []
    for s in raw_signals_list:
        if isinstance(s, dict):
            formatted_signals.append(s)
        elif hasattr(s, 'to_dict'):
            formatted_signals.append(s.to_dict())
        elif hasattr(s, '__dict__'):
            formatted_signals.append(s.__dict__)

    yield {
        "type": "final",
        "status": copy.deepcopy(current_state["states"]),
        "answer": answer_dict,
        "top_opportunity": top_opportunity,
        "top_trend": top_trend,
        "opportunities": opportunities,
        "signals": formatted_signals,             # Ensure this is non-empty list of dicts
        "latest_signals": formatted_signals[:10],
        "discovered_repos": [
            str(r.get("full_name"))
            for r in signal_output.get("discovered_repos", [])[:SEARCH_PAGE_SIZE]
            if isinstance(r, dict)
        ],
        "synthesis": synthesis_obj,
        "intent": current_state["intent"],
        "persona_name": current_state.get("persona_name", "Engineer"),
        "pipeline_artifacts": current_state.get("pipeline_artifacts"),
    }
