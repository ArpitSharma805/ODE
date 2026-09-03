"""ODE agent orchestration package."""

from ode.agents.opportunity_analyst import generate_opportunities
from ode.agents.orchestrator import (
    AGENTS,
    EXAMPLE_PROMPTS,
    agent_details_html,
    execution_trace_html,
    render_agent_graph,
    run_copilot,
)
from ode.agents.report_agent import ChatResponse, generate_chat_response
from ode.agents.signal_analyst import (
    SEARCH_PAGE_SIZE,
    analyze_signals,
    classify_signals,
    cluster_signals,
    collect_signals,
    trend_analyst,
    DeterministicMathEngine,
)

__all__ = [
    "AGENTS",
    "EXAMPLE_PROMPTS",
    "run_copilot",
    "agent_details_html",
    "execution_trace_html",
    "render_agent_graph",
    "collect_signals",
    "analyze_signals",
    "classify_signals",
    "cluster_signals",
    "trend_analyst",
    "generate_opportunities",
    "generate_chat_response",
    "ChatResponse",
    "DeterministicMathEngine",
]
