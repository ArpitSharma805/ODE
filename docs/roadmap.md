# ODE — Roadmap

## Phase 1 — MVP ✅ COMPLETED

**Goal**: Prove the end-to-end loop with one Domain, one Persona, multiple sources, and real-time streaming.

### Scope

- Domain: Technology
- Persona: Engineer
- Sources: GitHub, Tavily, Context7, HackerNews, Reddit, Product Hunt, Firecrawl
- LLM: Ollama Qwen 7B
- Database: SQLite
- UI: Next.js with real-time SSE streaming
- Orchestration: LangGraph with 6 agents

### Deliverables ✅

- Intent classification and query clarification
- Technology resolution and profiles
- Signal collection and normalization from MCP sources
- Multi-stage signal analysis (classify → cluster → themes → problems → insights)
- Trend synthesis from analysis results
- Opportunity scoring with business thesis generation
- Report generation for different intent types
- Real-time SSE streaming for live pipeline monitoring
- Investigation history and persistence
- Tech News / Technology Discovery page

### Success Criteria ✅

- The system produces credible, explainable opportunities for technology queries.
- Score components are transparent and reproducible.
- Real-time streaming provides live pipeline visibility.
- Multiple MCP sources are integrated and functional.

## Phase 2 — Trend Intelligence

**Goal**: Add more sources, more domains, and improve trend detection quality.

### Scope

- Add Google Trends exports, research paper feeds, and additional MCP sources.
- Expand Domains to Careers and Startups.
- Add Personas: Founder, Investor, Student.
- Improve Trend Analyst with better clustering and persistence rules.
- Add source health dashboard.
- Introduce DuckDB for analytical queries.

### Deliverables

- Multi-domain Radars.
- Predefined Persona templates.
- Source health and ingestion monitoring.
- Trend detail page with signal provenance.

## Phase 3 — Opportunity Scoring

**Goal**: Harden the scoring model and make it configurable.

### Scope

- Evidence validation and source authority tuning.
- Score component explainability per Opportunity.
- A/B testing of scoring formulas.
- Community feedback loop on Opportunity quality.
- Score history over time.

### Deliverables

- Evidence source authority and confidence tuning UI.
- Score breakdown visualization.
- Opportunity quality feedback (thumbs up/down).
- Score trend chart per Opportunity.

## Phase 4 — Prediction & Forecasting

**Goal**: Add deterministic forecasts to Trends and Opportunities.

### Scope

- Forecast model: linear trend, rolling average, seasonal smoothing.
- Forecast confidence derived from data density and variance.
- Forecast Report section.
- "What if" scenarios for Opportunities.
- Predictive alerting for emerging Opportunities.

### Deliverables

- Forecast entity attached to Trends and Opportunities.
- Forecast charts in UI and Reports.
- Radar alert when a new Opportunity crosses the threshold.

## Phase 5 — Enterprise Intelligence Platform

**Goal**: Scale ODE to multi-user, multi-tenant, and private data support.

### Scope

- Postgres for user and transactional data.
- DuckDB/ClickHouse for analytics.
- FastAPI backend separate from Streamlit UI.
- Multi-frontend support (React web, API consumers).
- Private data connectors (constrained, opt-in).
- Role-based access control.
- Real-time ingestion via message queue.
- Distributed processing (Dask/Ray).

### Deliverables

- Multi-tenant deployment.
- REST/GraphQL API.
- Enterprise connectors (Slack, Notion, private RSS).
- Admin dashboard.
- OpenTelemetry observability.

## Recommended MVP ✅ COMPLETED

The MVP has been completed with the following stack:

- **Domain**: Technology
- **Persona**: Engineer
- **Sources**: GitHub, Tavily, Context7, HackerNews, Reddit, Product Hunt, Firecrawl
- **Stack**: Next.js, LangGraph, Ollama Qwen 7B, SQLite, MCP integration

This MVP proves the core value loop with real-time streaming and multi-source MCP integration.

## Recommended Future Architecture

For Phase 5 and beyond, ODE evolves into a modular platform:

- **Frontend**: Next.js + API consumers
- **Backend**: FastAPI + LangGraph workflows (already in place)
- **OLTP**: Postgres
- **Analytics**: DuckDB or ClickHouse
- **Ingestion**: event-driven with Redis/RabbitMQ
- **Processing**: Dask or Ray
- **LLM**: Ollama/vLLM self-hosted or cloud API at scale
- **Reporting**: Markdown + PDF service
- **Observability**: OpenTelemetry + Langfuse

## Timeline Assumptions

| Phase | Estimated Duration | Focus |
|---|---|---|
| Phase 1 — MVP | ✅ COMPLETED | End-to-end value loop |
| Phase 2 — Trend Intelligence | 4–6 weeks | More sources and domains |
| Phase 3 — Opportunity Scoring | 4–6 weeks | Scoring quality and configurability |
| Phase 4 — Prediction & Forecasting | 4–6 weeks | Forecasts and alerts |
| Phase 5 — Enterprise Platform | 3–6 months | Scale, multi-tenancy, private data |

These durations are rough and depend on team size, source availability, and validation feedback from each phase.
