# Opportunity Discovery Engine (ODE)

> A multi-agent system for discovering, scoring, and explaining opportunities from automated research and analysis.

ODE ingests data from multiple MCP sources, detects trends, scores opportunities for target personas, and surfaces them through a modern Next.js interface with real-time streaming and comprehensive reporting capabilities.

---

## What It Does

1. **6-Agent Pipeline** — LangGraph-powered pipeline with Intent Analyzer, Research Planner, Signal Analyst, Trend Analyst, Opportunity Analyst, and Report Agent
2. **MCP Sources** — ingest signals from GitHub, Tavily, Context7, HackerNews, Reddit, Product Hunt, and other MCP-integrated sources
3. **Intelligent Analysis** — multi-stage LLM analysis with deterministic fallbacks for signal classification and theme extraction
4. **Query Clarification** — detect ambiguous queries and present clarifying questions for improved research precision
5. **Evidence Validation** — multi-source evidence gate ensures opportunities are supported by diverse, high-quality signals
6. **Opportunity Scoring** — rank opportunities based on market demand, developer pain, and adoption signals
7. **Real-Time Streaming** — SSE-based progress updates for live pipeline monitoring
8. **Tech News** — interactive visualization of technology trends across adoption categories
9. **Investigation History** — persistent storage and replay of research queries and results

---

## Architecture

```text
ODE/
├── apps/web/                 # Next.js frontend
│   ├── src/
│   │   ├── app/             # App router pages
│   │   ├── components/      # React components
│   │   └── lib/             # Utilities and types
│   └── package.json
├── src/ode/                 # Backend Python code
│   ├── agents/              # 6 Agent implementations
│   │   ├── orchestrator.py  # LangGraph orchestration
│   │   ├── signal_analyst.py # Signal collection and multi-stage analysis
│   │   ├── opportunity_analyst.py
│   │   └── report_agent.py
│   ├── api/                 # FastAPI endpoints
│   │   └── main.py
│   ├── mcp/                 # MCP source providers
│   │   ├── tavily.py
│   │   ├── research_sources.py
│   │   ├── sequential_thinking.py
│   │   └── jit_tool.py
│   ├── research.py          # Research planning
│   ├── clarify.py           # Query clarification
│   ├── signals.py           # Signal dataclass
│   ├── evidence.py          # Evidence validation
│   ├── synthesis.py         # Evidence synthesis
│   ├── technology_resolver.py # Technology resolution and profiles
│   ├── technology_discovery.py # Technology discovery and trending
│   ├── db.py                # Database layer
│   └── llm.py               # LLM integration
├── tests/                   # Test suite
├── pyproject.toml          # Python dependencies
└── .devcontainer/          # VS Code Dev Container configuration
```

---

## Tech Stack

- **Frontend**: Next.js, React, TypeScript, Tailwind CSS
- **Backend**: FastAPI, Python, LangGraph
- **LLM**: Ollama (local inference)
- **Database**: SQLite
- **MCP**: Model Context Protocol for source integration (GitHub, Tavily, Context7, Playwright)
- **Testing**: Pytest
- **Dev Container**: VS Code Dev Container with Python and Node.js support

---

## Prerequisites

- Python 3.11+
- Node.js 18+ (for Next.js frontend)
- A virtual environment (`.venv` is already present in the workspace)
- Optional: [Ollama](https://ollama.com) for LLM-powered analysis

---

## Setup

Install Python dependencies:

```bash
python3 -m pip install -e ".[dev]"
```

Install Node.js dependencies for the frontend:

```bash
cd apps/web
npm install
cd ../..
```

### Ollama setup (optional, for LLM-powered analysis)

1. Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

2. Start the Ollama server:

```bash
ollama serve
```

3. Pull the recommended model:

```bash
ollama pull qwen2.5:7b
```

4. Configure environment variables:

```bash
export OLLAMA_URL="http://localhost:11434"
export OLLAMA_MODEL="qwen2.5:7b"
```

When `OLLAMA_URL` is not set, ODE falls back to deterministic rule-based analysis.

---

## Running the Application

ODE runs as two separate services:

**Backend (FastAPI):**
```bash
uvicorn src.ode.api.main:app --reload
```

**Frontend (Next.js):**
```bash
cd apps/web
npm run dev
```

Open the frontend at:
```text
http://localhost:3000
```

The backend API runs on:
```text
http://localhost:8000
```

---

## Workflow Example

1. **Enter a research query** in the search interface (e.g., "What opportunities exist in MCP?")
2. **Review clarification** if the query is ambiguous, then provide an answer
3. **Watch real-time progress** as the multi-agent pipeline collects and analyzes signals
4. **Explore opportunities** with detailed evidence, themes, and insights
5. **View the tech radar** to see technology trends across adoption categories
6. **Access investigation history** to review previous research and results
7. **Monitor pipeline execution** via the live architecture visualization

---

## Development

Run Python tests:

```bash
PYTHONPATH=src OLLAMA_TIMEOUT=0.001 .venv/bin/python -m pytest tests/ -q
```

Run type checks:

```bash
PYTHONPATH=src .venv/bin/python -m mypy src/ode/
```

Run frontend lint and build:

```bash
cd apps/web
npm run lint
npm run build
```

---

## License

MIT
