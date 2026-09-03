# ODE Project Context for AI Assistants

## Project Overview

**ODE (Opportunity Discovery Engine)** is a multi-agent system that discovers, scores, and explains technology opportunities from automated research and analysis. It ingests data from multiple MCP sources, detects trends, scores opportunities for target personas, and surfaces them through a modern Next.js interface with real-time streaming.

## Tech Stack

### Backend (Python)
- **Framework**: FastAPI with Uvicorn server
- **Agent Orchestration**: LangGraph (multi-agent pipeline system)
- **LLM Integration**: LangChain Core, Ollama (local inference)
- **Data Processing**: Pandas, NumPy, Matplotlib
- **PDF Generation**: fpdf2
- **MCP Integration**: Model Context Protocol for external data sources
- **Database**: SQLite
- **Testing**: Pytest, Playwright
- **Type Checking**: MyPy

### Frontend (Next.js)
- **Framework**: Next.js 16.3.0 (App Router) with React 19.2.8
- **Language**: TypeScript
- **Styling**: Tailwind CSS v4
- **UI Components**: shadcn/ui, Lucide React icons, Framer Motion animations
- **Data Visualization**: Recharts
- **Markdown Rendering**: react-markdown
- **Build Tool**: Turbopack

## Directory Structure

```
ODE/
├── apps/web/                    # Next.js frontend
│   ├── src/
│   │   ├── app/                # App router pages
│   │   │   ├── page.tsx        # Main opportunity discovery page
│   │   │   ├── tech-news/      # Technology discovery page
│   │   │   └── architecture/   # Live architecture dashboard
│   │   ├── components/         # React components
│   │   │   ├── ui/            # shadcn/ui components
│   │   │   ├── nav.tsx        # Navigation with theme toggle
│   │   │   └── pipeline-analysis.tsx
│   │   ├── lib/               # Utilities and types
│   │   └── app/globals.css    # Global styles
│   └── package.json
├── src/ode/                    # Backend Python code
│   ├── agents/                 # 6 Agent implementations
│   │   ├── orchestrator.py    # LangGraph orchestration
│   │   ├── signal_analyst.py  # Signal collection and analysis
│   │   ├── opportunity_analyst.py
│   │   └── report_agent.py
│   ├── api/                   # FastAPI endpoints
│   │   └── main.py
│   ├── mcp/                   # MCP source providers
│   │   ├── tavily.py
│   │   ├── research_sources.py
│   │   └── sequential_thinking.py
│   ├── research.py            # Research planning
│   ├── clarify.py             # Query clarification
│   ├── signals.py             # Signal dataclass
│   ├── evidence.py            # Evidence validation
│   ├── synthesis.py           # Evidence synthesis
│   ├── technology_resolver.py # Technology resolution
│   ├── technology_discovery.py # Technology discovery
│   ├── db.py                  # Database layer
│   └── llm.py                 # LLM integration
├── tests/                      # Test suite
├── pyproject.toml            # Python dependencies
├── AGENTS.md                  # Agent-specific notes
├── ARCHITECTURE.md           # Architecture documentation
└── README.md                 # Project overview
```

## Architecture & Data Flow

### 6-Agent Pipeline (LangGraph)
1. **Intent Analyzer** - Analyzes user query intent and persona
2. **Research Planner** - Selects appropriate MCP sources and creates research plan
3. **Signal Analyst** - Collects signals, classifies them, extracts themes/problems/insights
4. **Trend Analyst** - Synthesizes trends from analysis results
5. **Opportunity Analyst** - Generates business theses, scores opportunities, validates evidence
6. **Report Agent** - Generates final chat response with synthesized evidence

### MCP Data Sources
- **GitHub** - Repository search, repo details, stars, forks, commits
- **Tavily** - Web search, news search
- **Context7** - Context search
- **Playwright** - Web scraping
- **HackerNews** - Discussion tracking
- **Reddit** - Community signals
- **Product Hunt** - Product launches

### API Endpoints
- `POST /api/query/stream` - SSE for live pipeline updates
- `GET /api/tech-news` - Technology radar data
- `GET /api/architecture` - Live architecture stats
- `GET /api/investigations` - Investigation list
- `GET /api/investigations/{id}` - Single investigation details
- `GET /api/investigations/{id}/pipeline` - Pipeline artifacts

## Key Backend Modules

### Core Data Structures
- **Signal** (`src/ode/signals.py`) - Canonical signal format from MCP sources
- **Opportunity** (`src/ode/opportunities.py`) - Business opportunity with scoring
- **Evidence** (`src/ode/evidence.py`) - Multi-source evidence validation
- **TechnologyProfile** (`src/ode/technology_resolver.py`) - Technology metadata

### Agent Responsibilities
- **orchestrator.py** - LangGraph StateGraph orchestration, state management
- **signal_analyst.py** - Multi-stage LLM analysis with deterministic fallbacks
- **opportunity_analyst.py** - Business thesis generation and scoring
- **report_agent.py** - Answer-first report generation

### Key Functions
- `normalize_signals()` - Converts MCP raw dicts to canonical Signal format
- `score_opportunity()` - Multi-factor opportunity scoring
- `validate_evidence()` - Multi-source evidence gate
- `maybe_clarify()` - Query clarification for ambiguous queries
- `synthesize()` - Evidence synthesis for themes, problems, insights

## Frontend Architecture

### Pages
- **Main Page** (`/`) - Hero search, live progress tracker, opportunity cards, report generation
- **Tech News** (`/tech-news`) - Technology discovery with trend cards and refresh
- **Architecture** (`/architecture`) - Live pipeline stats, agent status, source integrations

### Key Components
- **Nav** - Navigation with theme toggle (light/dark/system)
- **PipelineAnalysis** - Collapsible pipeline artifact viewer
- **StatusIcon** - Agent status indicators

### State Management
- Currently using React hooks (useState, useEffect, useCallback, useMemo)
- No global state management (context/redux)
- LocalStorage for theme and investigation persistence
- SSE for real-time pipeline updates

## Development Conventions

### Python Code
- Location: `src/ode/`
- Tests: `tests/`
- Deterministic calculations for ranking, filtering, scoring
- LLM-primary analysis with deterministic rule fallbacks
- Ollama for reasoning (set `OLLAMA_TIMEOUT=0.001` for fast-fail tests)
- MCP sources return raw dicts → `normalize_signals()` converts to canonical format

### Frontend Code
- TypeScript with strict typing
- Tailwind CSS for styling with custom `.card-base` class
- Framer Motion for animations
- React hooks for state management
- Server-Sent Events (SSE) for real-time updates

### Code Style
- Compact code with minimal comments (unless asked)
- Follow existing patterns and abstractions
- Use existing libraries and utilities
- Security-first (no secrets in code, proper validation)

## Verification Commands

### Python Tests
```bash
PYTHONPATH=src OLLAMA_TIMEOUT=0.001 .venv/bin/python -m pytest tests/test_scoring.py tests/test_db.py tests/test_health.py tests/test_source_normalization.py tests/test_concepts.py tests/eval/test_eval.py tests/test_research_pipeline.py tests/test_synthesis.py tests/test_analysis.py tests/test_issue_fixes.py tests/test_ui_fixes.py tests/test_investigations.py tests/test_architecture.py -q
```

### Type Checking
```bash
PYTHONPATH=src .venv/bin/python -m mypy src/ode/analysis_models.py src/ode/agents/signal_analyst.py src/ode/agents/opportunity_analyst.py src/ode/agents/orchestrator.py src/ode/agents/report_agent.py src/ode/signals.py src/ode/evidence.py src/ode/research.py src/ode/mcp/research_sources.py src/ode/mcp/tavily.py src/ode/search_noise.py src/ode/clarify.py src/ode/synthesis.py src/ode/llm.py src/ode/technology_discovery.py src/ode/api/main.py src/ode/investigations.py src/ode/db.py
```

### Frontend Lint/Build
```bash
cd apps/web && npm run lint
cd apps/web && npm run build
```

## Running the Application

### Backend
```bash
.venv/bin/python -m uvicorn src.ode.api.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd apps/web && npm run dev
```

### Access Points
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

## Environment Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- Virtual environment (`.venv` already present)
- Optional: Ollama for LLM-powered analysis

### Python Dependencies
```bash
python3 -m pip install -e ".[dev]"
```

### Node Dependencies
```bash
cd apps/web && npm install
```

### Ollama Setup (Optional)
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve
ollama pull qwen2.5:7b
export OLLAMA_URL="http://localhost:11434"
export OLLAMA_MODEL="qwen2.5:7b"
```

## Current Challenges & Improvement Areas

### Frontend
- Main component has 15+ state variables (needs custom hooks)
- No global state management (theme, user preferences)
- Repeated data fetching patterns across components
- Complex EventSource management in main page

### Backend
- Agent pipeline could benefit from better error handling
- MCP source integration could be more modular
- Limited caching strategies for expensive operations

### Testing
- Frontend has limited test coverage
- Integration tests could be expanded
- Performance testing for large datasets

## Important Notes for AI Assistants

1. **Always check existing patterns** before introducing new libraries or approaches
2. **Security is paramount** - never expose secrets, validate all inputs
3. **Follow the agent-first architecture** - changes should respect the 6-agent pipeline
4. **MCP integration is key** - data source changes affect multiple agents
5. **Deterministic fallbacks** - LLM failures should have rule-based alternatives
6. **Type safety matters** - both Python (MyPy) and TypeScript (strict mode)
7. **Performance considerations** - SSE streaming, caching, and lazy loading
8. **User experience focus** - real-time feedback, loading states, error handling

## Recent Changes

- Removed "Supporting Evidence" section from reports (backend + frontend)
- Added high-contrast card borders for better UI visibility
- Made "Connected Technologies" badges static (non-clickable) in tech discovery
- Updated card styling across all pages for consistency

## Contact & Support

For project-specific questions or issues, refer to:
- `AGENTS.md` - Agent-specific implementation details
- `ARCHITECTURE.md` - Detailed architecture diagrams
- `README.md` - Project overview and setup instructions
