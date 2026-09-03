# 04 — Implement GitHub MCP Source Integration

**What to build:** Integrate GitHub as the primary MCP data source for repository signals including stars, forks, issues, commits, and contributor data.

**Blocked by:** 03 — Implement Signal Data Model and Normalization

**Status:** ready-for-agent

- [ ] Create GitHub MCP client in src/ode/mcp/research_sources.py
- [ ] Implement repository search with query construction
- [ ] Add repository detail retrieval (stars, forks, issues, commits)
- [ ] Implement contributor and activity metrics collection
- [ ] Add GitHub-specific signal normalization
- [ ] Implement rate limiting and error handling for GitHub API
- [ ] Add GitHub to research plan source selection logic
- [ ] Write tests for GitHub MCP integration and signal generation
