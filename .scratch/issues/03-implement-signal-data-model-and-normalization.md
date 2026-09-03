# 03 — Implement Signal Data Model and Normalization

**What to build:** Create the canonical Signal dataclass and normalization system to convert raw MCP data into a unified schema for analysis.

**Blocked by:** 01 — Setup Project Infrastructure and Development Environment

**Status:** ready-for-agent

- [ ] Define Signal dataclass with entity, metric, value, timestamp, source_url, evidence_quality
- [ ] Implement normalize_signals function to convert raw MCP dicts to Signal objects
- [ ] Add signal ranking and deduplication logic
- [ ] Create signal quality scoring based on source type and data freshness
- [ ] Implement metric-specific normalization (GitHub stars, Tavily market demand, etc.)
- [ ] Add signal validation and error handling for malformed data
- [ ] Write tests for signal normalization across different source types
