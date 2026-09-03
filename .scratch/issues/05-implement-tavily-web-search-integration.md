# 05 — Implement Tavily Web Search Integration

**What to build:** Integrate Tavily as a web search source for market intelligence, business signals, and broader context beyond GitHub repositories.

**Blocked by:** 03 — Implement Signal Data Model and Normalization

**Status:** ready-for-agent

- [ ] Create Tavily MCP client in src/ode/mcp/tavily.py
- [ ] Implement web search with query expansion and acronym handling
- [ ] Add market demand and community discussion signal extraction
- [ ] Implement noise filtering for irrelevant search results
- [ ] Add fallback query logic for zero-result scenarios
- [ ] Implement Tavily-specific signal normalization
- [ ] Add explicit error logging for Tavily failures
- [ ] Write tests for Tavily integration and signal quality
