# Agent Notes

## Verification Commands

- Python unit tests:
  ```bash
  PYTHONPATH=src OLLAMA_TIMEOUT=0.001 .venv/bin/python -m pytest tests/test_scoring.py tests/test_db.py tests/test_health.py tests/test_source_normalization.py tests/test_concepts.py tests/eval/test_eval.py tests/test_research_pipeline.py tests/test_synthesis.py tests/test_analysis.py tests/test_issue_fixes.py tests/test_ui_fixes.py tests/test_investigations.py tests/test_architecture.py -q
  ```
- Python type check for changed modules:
  ```bash
  PYTHONPATH=src .venv/bin/python -m mypy src/ode/analysis_models.py src/ode/agents/signal_analyst.py src/ode/agents/opportunity_analyst.py src/ode/agents/orchestrator.py src/ode/agents/report_agent.py src/ode/signals.py src/ode/evidence.py src/ode/research.py src/ode/mcp/research_sources.py src/ode/mcp/tavily.py src/ode/search_noise.py src/ode/clarify.py src/ode/synthesis.py src/ode/llm.py src/ode/technology_discovery.py src/ode/api/main.py src/ode/investigations.py src/ode/db.py
  ```
- Web lint/build:
  ```bash
  cd apps/web && npm run lint
  cd apps/web && npm run build
  ```

## Project Conventions

- Python code lives under `src/ode/`, tests under `tests/`.
- Ranking, filtering, scoring, and confidence calculations remain deterministic; the signal analysis pipeline (`ode.agents.signal_analyst`) is LLM-primary with deterministic rule fallbacks.
- Ollama is used for reasoning; set `OLLAMA_TIMEOUT=0.001` for fast-fail in tests.
- MCP-based sources return raw dicts; `ode.signals.normalize_signals` converts them into a canonical `Signal` schema.
- Opportunities are scored with `ode.opportunities.score_opportunity` and further moderated by `ode.evidence.validate_evidence` (multi-source evidence gate).
- `signal_analyst` runs the multi-stage LLM analysis pipeline including signal collection.
- `orchestrator` runs `ode.clarify.maybe_clarify` before the full pipeline and returns a clarifying question for ambiguous/broad queries.

## Key Modules

- `src/ode/research.py` — `ResearchPlan` and source selection.
- `src/ode/clarify.py` — intent-based query clarification.
- `src/ode/signals.py` — `Signal` dataclass and `normalize_signals`.
- `src/ode/evidence.py` — `validate_evidence` multi-source gate.
- `src/ode/mcp/research_sources.py` — skeleton/fallback source helpers.
- `src/ode/agents/signal_analyst.py` — signal collection and multi-stage LLM analysis pipeline.
- `src/ode/agents/opportunity_analyst.py` — opportunity generation with evidence validation.
- `src/ode/synthesis.py` — evidence synthesis: themes, problems, insights, opportunities, narrative (LLM-primary with rule fallback).
- `src/ode/technology_resolver.py` — technology resolution and TechnologyProfile creation.
- `src/ode/technology_discovery.py` — technology discovery and trending data pipeline.
