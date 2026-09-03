# 07 — Implement Research Planning and Source Selection

**What to build:** Create the research planning system that expands user queries into targeted search terms and selects appropriate MCP sources based on intent.

**Blocked by:** 02 — Implement LangGraph-Based Orchestrator Pipeline

**Status:** ready-for-agent

- [ ] Define ResearchPlan dataclass with queries, sources, signal types, recency
- [ ] Implement build_research_plan function for query expansion
- [ ] Add GitHub-specific query construction (filter job/career terms)
- [ ] Implement Tavily business and market query generation
- [ ] Add intent-based source selection logic
- [ ] Implement LLM-assisted query expansion when available
- [ ] Add category-specific research plan variants
- [ ] Write tests for research plan generation and source selection
