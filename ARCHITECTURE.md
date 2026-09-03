# ODE Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           WEB FRONTEND (Next.js)                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  / (Home)                    /tech-news        /architecture               │  │
│  │  ├─ Hero/Search               ├─ Trend Cards    ├─ Live Stats                 │  │
│  │  ├─ Live Progress Tracker     ├─ Refresh Button ├─ Pipeline Flow              │  │
│  │  ├─ Opportunity Cards        └─ Theme List     └─ Source Status            │  │
│  │  └─ Report Generation                                                          │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTP/REST API
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         FASTAPI BACKEND (Python)                              │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  API Endpoints                                                              │  │
│  │  ├─ POST /api/query/stream      ← SSE for live updates                    │  │
│  │  ├─ GET  /api/tech-news         ← Tech radar data                      │  │
│  │  ├─ GET  /api/architecture      ← Live architecture stats                │  │
│  │  ├─ GET  /api/investigations    ← Investigation list                  │  │
│  │  └─ GET  /api/investigations/{id} ← Single investigation                │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                    │
│                                    ▼
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  ORCHESTRATOR (LangGraph StateGraph)                                         │  │
│  │                                                                              │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Intent Analyzer → Research Planner → Signal Analyst → Trend Analyst  │  │  │
│  │  │      → Opportunity Analyst → Report Agent                          │  │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                              │  │
│  │  Shared State (ODEState):                                                        │  │
│  │  - query, intent, persona_name                                                │  │
│  │  - signal_output, analysis_result, theme_trends, opportunities         │  │
│  │  - agent_states (status, duration, detail, mcp_calls)                      │  │
│  │  - pipeline_artifacts (themes, problems, insights, opportunities)       │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                    │
│                                    ▼
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  AGENTS (Sequential Pipeline)                                                 │  │
│  │                                                                              │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Research Planner                                                           │  │
│  │  │  ├─ MCP: GitHub (search_repos, get_repo_detail)                             │  │
│  │  │  ├─ MCP: Tavily (search_web, search_news)                                     │  │
│  │  │  ├─ MCP: Context7 (search)                                                   │  │
│  │  │  ├─ MCP: Playwright (scrape_web)                                             │  │
│  │  │  └─ → Normalized Signals → state["signal_output"]                        │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                    │                                        │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Signal Analyst (Multi-stage Pipeline)                                     │  │  │
│  │  │  ├─ DeterministicMathEngine (sanitize, calculate bounds)                 │  │  │
│  │  │  ├─ classify_signals (LLM + rules)                                      │  │  │
│  │  │  ├─ cluster_signals (group by phenomenon)                              │  │  │
│  │  │  ├─ extract themes, problems, insights (LLM + rules)                    │  │  │
│  │  │  └─ → AnalysisResult → state["analysis_result"]                         │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                    │                                        │  │
│  │  │  ← state["signal_output"]                                                   │  │
│  │  │                                        │                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Trend Analyst                                                                │  │  │
│  │  │  ├─ Sequential Thinking MCP (structured reasoning)                          │  │  │
│  │  │  ├─ Ollama (synthesize_trends)                                              │  │
│  │  │  └─ → Trend objects → state["theme_trends"]                                │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                    │                                        │  │
│  │  │  ← state["analysis_result"]                                                │  │
│  │  │                                        │                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Opportunity Analyst                                                          │  │
│  │  │  ├─ Ollama (generate_business_theses)                                       │  │
│  │  ├─ Ollama (critique_opportunities)                                         │  │
│  │  ├─ score_opportunity() (multi-factor scoring)                              │  │
│  │  ├─ validate_evidence() (multi-source gate)                                   │  │
│  │  └─ → Opportunity objects → state["opportunities"]                        │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                    │                                        │  │
│  │  │  ← state["theme_trends"]                                                  │  │
│  │  │                                        │                                    │  │
│  │  │  ← state["opportunities"]                                                │  │
│  │  │                                        │                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Report Agent                                                                │  │
│  │  │  ├─ Ollama (generate_chat_response)                                         │  │
│  │  │  ├─ synthesize() (themes, problems, insights)                    │  │
│  │  │  └─ → ChatResponse → state["answer"]                                       │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  │                                    │                                        │  │
│  │  │  ← state["opportunities"]                                                │  │
│  │  │                                        │                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Pipeline Artifacts Storage                                                │  │
│  │  │  ├─ themes: list[Theme]                                                    │  │
│  │  │  ├─ problems: list[Problem]                                                │  │
│  │  │  ├─ insights: list[Insight]                                                │  │
│  │  │  ├─ opportunities: list[Opportunity]                                      │  │
│  │  │  └─ generation_stats: dict                                                │  │
│  │  └─────────────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         DATABASE (SQLite)                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  Tables                                                                      │  │
│  │  ├─ users                                                                      │  │
│  │  ├─ personas                                                                    │  │
│  │  ├─ domains                                                                    │  │
│  │  ├─ sources                                                                    │  │
│  │  ├─ ingestion_runs                                                             │  │
│  │  ├─ signals                                                                    │  │
│  │  ├─ trends                                                                     │  │
│  │  ├─ trend_signals                                                              │  │
│  │  ├─ opportunities                                                              │  │
│  │  ├─ forecasts                                                                  │  │
│  │  ├─ radars                                                                     │  │
│  │  ├─ reports                                                                    │  │
│  │  ├─ user_opportunity_interactions                                             │  │
│  │  ├─ mcp_cache                                                                  │  │
│  │  ├─ mcp_metrics                                                                │  │
│  │  └─ investigations                                                             │  │
│  │     ├─ query, status, started_at, completed_at                              │  │
│  │     ├─ agent_states (JSON)                                                    │  │
│ │     ├─ final_state (JSON)                                                    │  │
│  │     ├─ pipeline_artifacts (JSON)                                            │  │
│  │     └─ trace_log (JSON)                                                     │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL INTEGRATIONS                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  Ollama (LLM)                                                                │  │
│  │  ├─ model: qwen2.5:7b                                                           │  │
│  │  ├─ timeout: 120s                                                               │  │
│  │  └─ Used by: Signal Analyst, Trend Analyst, Opportunity Analyst, Report Agent│  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  MCP Servers (via MCP Client)                                                │  │
│  │  ├─ GitHub MCP                                                                  │  │
│  │  ├─ Tavily MCP                                                                  │  │
│  │  ├─ Context7 MCP                                                                │  │
│  │  ├─ Playwright MCP                                                              │  │
│  │  └─ Sequential Thinking MCP                                                    │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow Diagram

```
User Query
    │
    ▼
┌─────────────┐
│  Frontend   │
└─────────────┘
    │ POST /api/query/stream
    ▼
┌─────────────┐
│  FastAPI    │
└─────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Orchestrator (LangGraph)                                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  1. Intent Analyzer                                         │  │
│  │     ├─ classify_intent(query)                              │  │
│  │     ├─ maybe_clarify(query, intent)                      │  │
│  │     ├─ TechnologyResolver.resolve()                        │  │
│  │     └─ → state["intent"]                                 │  │
│  └───────────────────────────────────────────────────────────┘  │
│     │
│     ▼
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  2. Research Planner                                       │  │
│  │     ├─ build_research_plan(query, intent)                  │  │
│  │     ├─ MCP: GitHub (search_repos)                         │  │
│  │  │  └─ get_repo_detail()                              │  │
│  │     ├─ MCP: Tavily (search_web)                            │  │
│  │     ├─ MCP: Context7 (search)                             │  │
│  │     ├─ MCP: Playwright (scrape_web)                       │  │
│  │     ├─ normalize_signals()                               │  │
│  │     └─ → state["signal_output"]                         │  │
│  └───────────────────────────────────────────────────────────┘  │
│     │
│     ▼
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  3. Signal Analyst                                         │  │
│  │     ├─ ← state["signal_output"]                         │  │
│  │     ├─ classify_signals (LLM + rules)                   │  │
│  │     ├─ cluster_signals (group by phenomenon)            │  │
│  │     ├─ extract themes, problems, insights (LLM + rules)  │  │
│  │     └─ → state["analysis_result"]                       │  │
│  └───────────────────────────────────────────────────────────  │  │
│     │
│     ▼
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  4. Trend Analyst                                            │  │
│  │     ├─ ← state["analysis_result"]                        │  │
│  │     ├─ Sequential Thinking MCP (reasoning)              │  │
│  │     ├─ Ollama (synthesize_trends)                         │  │
│  │     └─ → state["theme_trends"]                           │  │
│  └───────────────────────────────────────────────────────────  │  │
│     │
│     ▼
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  5. Opportunity Analyst                                     │  │
│  │     ├─ ← state["theme_trends"]                           │  │
│ │     ├─ Ollama (generate_business_theses)                   │  │
│  │     ├─ Ollama (critique_opportunities)                     │
│ │     ├─ score_opportunity() (multi-factor)                 │  │
│ │     ├─ validate_evidence() (multi-source gate)              │  │
│  │     └─ → state["opportunities"]                         │  │
│  └───────────────────────────────────────────────────────────  │  │
│     │
│     ▼
┌───────────────────────────────────────────────────────────┐
│  6. Report Agent                                            │
│     ├─ ← state["opportunities"]                         │
│     ├─ ← state["analysis_result"]                       │
│     ├─ synthesize() (themes, problems, insights)            │
│     ├─ Ollama (generate_chat_response)                   │
│     └─ → state["answer"]                                  │
└─────────────────────────────────────────────────────────── │
     │
     ▼
┌───────────────────────────────────────────────────────────┐
│  Pipeline Artifacts Storage                               │
│     ├─ themes: list[Theme]                                    │
│     ├─ problems: list[Problem]                                  │
│     ├─ insights: list[Insight]                                  │
│     ├─ opportunities: list[Opportunity]                        │
│     └─ generation_stats: dict                                │
└─────────────────────────────────────────────────────────── │
     │
     ▼
┌───────────────────────────────────────────────────────────┐
│  Database Persistence                                      │
│     ├─ investigations table                                    │
│     │  ├─ query, status, started_at, completed_at            │
│     │  ├─ agent_states (JSON)                              │
│     │  ├─ final_state (JSON)                                │
│     │  ├─ pipeline_artifacts (JSON)                          │  │
│     │  └─ trace_log (JSON)                                   │
│     ├─ signals table (from signal_output)                   │
│     ├─ trends table (from theme_trends)                     │
│     └─ opportunities table (from opportunities)             │
└─────────────────────────────────────────────────────────── │
     │
     ▼
┌───────────────────────────────────────────────────────────┐
│  SSE Stream to Frontend                                    │
│     ├─ type: "created" (investigation_id)                │
│     ├─ type: "status" (agent_states)                       │
│     ├─ type: "update" (agent_states)                       │
│     ├─ type: "opportunity" (opportunity object)              │
│     ├─ type: "answer" (answer text)                          │
│     └─ type: "final" (complete result)                       │
└─────────────────────────────────────────────────────────── │
```

## Component Details

### Frontend (Next.js)
- **Home Page** (`/`): Query input, live progress tracker, opportunity cards, report display
- **Tech News Page** (`/tech-news`): Technology discovery and trending data
- **Architecture Page** (`/architecture`): Live pipeline stats, agent status, source integrations

### Backend (FastAPI)
- **Query Stream Endpoint**: SSE-based real-time updates
- **Tech News Endpoint**: Technology discovery data
- **Architecture Endpoint**: Live system metrics
- **Investigation Endpoints**: CRUD operations on investigations

### Agents (Sequential Pipeline)
1. **Intent Analyzer**: Classify query intent, clarify if needed, resolve technology profiles
2. **Research Planner**: Plan and execute signal collection from MCP sources
3. **Signal Analyst**: Multi-stage analysis (classify → cluster → themes → problems → insights)
4. **Trend Analyst**: Synthesize analysis themes into market trends
5. **Opportunity Analyst**: Score trends into opportunities for personas
6. **Report Agent**: Generate final analyst reports

### Supporting Components
- **DeterministicMathEngine**: Signal sanitization and quality bounds calculation
- **LLM Integration**: Ollama for reasoning tasks
- **MCP Client**: Communication with external MCP servers
- **Signal Normalization**: Canonical signal schema conversion
- **Evidence Validation**: Multi-source evidence quality gate
- **TechnologyResolver**: Technology resolution and profile creation
- **TechnologyDiscovery**: Technology discovery and trending data pipeline

### Database Schema
- **investigations**: Query pipeline runs and results
- **signals**: Normalized signals from various sources
- **trends**: Market-level trends with momentum metrics
- **opportunities**: Scored opportunities for personas
- **mcp_cache**: Cached MCP tool results
- **mcp_metrics**: MCP call performance metrics

### External Integrations
- **Ollama**: LLM for reasoning (qwen2.5:7b model)
- **GitHub MCP**: Repository search and details
- **Tavily MCP**: Web search and news
- **Context7 MCP**: Documentation search
- **Playwright MCP**: Web scraping
- **Sequential Thinking MCP**: Structured reasoning
- **Hacker News API**: Tech news data

## Current Limitations

1. **Fixed Pipeline**: Agents run in fixed sequence, no dynamic routing
2. **No Agent Communication**: Agents don't reason about each other's outputs
3. **Hardcoded State Transitions**: Orchestrator uses fixed LangGraph edges
4. **No Supervisor**: No quality control or guidance system
5. **Sequential Execution**: No parallel agent execution
6. **Shared State Only**: Communication through state object, not artifacts

## Key Design Patterns

- **Pipeline Pattern**: Sequential data processing stages
- **State Graph**: LangGraph for pipeline orchestration
- **SSE Streaming**: Real-time frontend updates
- **Hybrid Analysis**: Deterministic rules + LLM reasoning
- **Multi-Source Ingestion**: Parallel MCP source queries
- **Evidence Validation**: Multi-source quality gate
- **Persona-Based Scoring**: Opportunities scored for specific user personas
