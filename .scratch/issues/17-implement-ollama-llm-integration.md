# 17 — Implement Ollama LLM Integration

**What to build:** Integrate Ollama as the local LLM backend for reasoning tasks with fallback to deterministic rules when unavailable.

**Blocked by:** 02 — Implement LangGraph-Based Orchestrator Pipeline

**Status:** ready-for-agent

- [ ] Create Ollama client in src/ode/llm.py
- [ ] Implement LLM query execution with timeout handling
- [ ] Add prompt templates for classification, analysis, and generation
- [ ] Implement OLLAMA_TIMEOUT configuration for fast-fail in tests
- [ ] Create fallback logic for when Ollama is unavailable
- [ ] Add LLM availability monitoring and health checks
- [ ] Implement model selection and configuration
- [ ] Write tests for LLM integration and fallback behavior
