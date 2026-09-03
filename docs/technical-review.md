# ODE — Technical Review

This review assesses the ODE design across scalability, maintainability, security, reliability, observability, cost, model limitations, and performance.

## Executive Summary

The ODE design is sound for an MVP but contains known risks that must be addressed before production. The most critical risks are source reliability, scoring credibility, and LLM hallucination in explanations. The recommended path is to ship a narrow MVP quickly, measure signal quality, and iterate on scoring before scaling.

## Scalability

### MVP ✅ COMPLETED

- SQLite is sufficient for thousands of signals and hundreds of opportunities.
- FastAPI backend with Next.js frontend is acceptable for one user or a small local demo.
- Trend detection over small batches (thousands of rows) runs comfortably in Pandas.
- Real-time SSE streaming provides live pipeline visibility.

### Limitations

- SQLite does not support concurrent writes well.
- Pandas is memory-bound.
- Ollama Qwen 7B on CPU is slow; GPU is recommended for acceptable response times.

### Recommended Growth Path

1. DuckDB for larger-than-memory analytical queries.
2. Postgres for multi-user transactional data.
3. Dask/Ray for distributed signal processing.
4. Separate FastAPI service to decouple UI from backend.

## Maintainability

### Strengths

- Clear domain model with bounded entities.
- Deterministic scoring separated from LLM explanations.
- Six bounded agents with explicit inputs and outputs.
- Immutable Signals make debugging and replay easier.
- Real-time SSE streaming provides pipeline visibility.

### Risks

- **Source normalization drift**: every source has a different schema and rate limit. Normalization logic will accumulate.
- **Scoring formula complexity**: as more signals and personas are added, the score formula may become a "magic formula" that is hard to tune.
- **Report templates**: Markdown templates can become brittle if too many special cases are added.

### Recommendations

- Unit test each agent independently.
- Version the scoring formula and the forecast model.
- Treat normalization as a per-source adapter with a strict contract.
- Keep report templates small and composable.

## Security

### Threats

1. **API key exposure**: Source API keys stored in the database could be leaked if the database is compromised.
2. **PII ingestion**: Public sources may contain personal data (e.g., names in job postings, GitHub profiles). ODE should filter PII at ingestion.
3. **User query retention**: storing queries for 90 days creates a privacy surface.
4. **Local LLM**: running Ollama locally reduces cloud data exposure but the model weights and prompts are on the user's machine.
5. **No auth in MVP**: if the Next.js app is exposed, anyone can access the local instance.

### Recommendations

- Encrypt source API keys at rest and never expose them to the frontend.
- Add PII detection and redaction in the Collector agent.
- Default query retention to 30 days or less; make it configurable.
- Do not deploy the MVP Next.js app to the public internet without authentication.
- Treat ODE as an intelligence platform, not a data-upload platform, in the MVP.

## Reliability

### Strengths

- Circuit breaker model for sources prevents one failing source from blocking ODE.
- Immutable Signals make the pipeline auditable.
- Report snapshots are reproducible.

### Risks

- **Trend detection quality**: noisy or low-quality signals can create false trends.
- **Opportunity threshold sensitivity**: too low produces spam; too high misses real opportunities.
- **LLM explanation inconsistency**: same Opportunity may get different explanations on each run.

### Recommendations

- Add source health metrics and alerting.
- Seed the threshold with empirical data from the first source batches.
- Cache or temperature-control LLM explanations for the same input.
- Implement idempotent scoring for reproducibility.

## Observability

### MVP

- SQLite trace tables on IngestionRun, Signal, Trend, Opportunity, Report.
- Structured logging per agent.
- Simple metrics: signals per run, opportunities created, score distribution.

### Future

- OpenTelemetry traces across agents.
- Langfuse or an open-source equivalent for LLM cost and quality tracking.
- Dashboards for source health, pipeline lag, and opportunity throughput.

## Cost

### MVP Cost

- Development hardware only.
- No cloud API costs if Ollama and public sources are used.
- SQLite is free.

### Production Cost Drivers

- Compute for ingestion and trend detection.
- GPU or cloud inference for LLM.
- Storage for immutable signal history.
- API rate limits may require paid tiers for some sources.

### Cost Optimization

- Use DuckDB for analytical workloads to avoid separate warehouse costs.
- Run Ollama locally for as long as user latency allows.
- Archive raw payloads to cheap object storage after normalization.

## Model Limitations

### Ollama Qwen 7B

- Sufficient for explanation and query translation.
- May struggle with nuanced reasoning over complex trend explanations.
- Output can be inconsistent across runs.
- Local inference requires GPU for acceptable latency.

### Forecasting Model

- Linear trend and rolling average will miss inflection points and non-linear growth.
- Confidence calculation must be validated empirically.
- Seasonal smoothing requires enough historical data.

### Recommendations

- Never let the LLM compute scores or forecasts.
- Always constrain LLM outputs with structured templates.
- Validate the forecast model against historical data before trusting it.

## Performance

### Ingestion

- Public API rate limits dominate ingestion speed.
- Use batch inserts and batched API calls where possible.
- Schedule ingestion during off-peak hours.

### Trend Detection

- Run on recent signal windows (e.g., last 30 days) rather than the entire history.
- Use Polars or DuckDB for windowed aggregations.

### Scoring

- Scoring should be sub-second for a single Opportunity.
- Recompute scores incrementally when new Signals arrive.

### UI

- Next.js provides a modern React-based frontend with server-side rendering.
- Cache heavy queries and investigation results.
- Use React Query or similar for client-side caching.

## Risk Register

### HIGH Risks

1. **Source reliability and rate limits**: public sources can change, break, or throttle without notice.
2. **Scoring credibility**: users will not trust scores they cannot explain; the formula must be transparent and validated.
3. **False positives in trend detection**: noisy signals can create fake opportunities.
4. **LLM hallucination in explanations**: explanations must be grounded in evidence.

### MEDIUM Risks

5. **Forecast accuracy**: simple models may mislead users about future growth.
6. **Evidence quality accuracy**: source authority and diversity may be mis-estimated when few signals are available.
7. **Data retention and privacy**: public source ingestion may accidentally capture PII.
8. **Local LLM performance**: CPU inference is slow and degrades UX.

### LOW Risks

9. **Single-user SQLite concurrency**: not a problem until multiple users access the same database.
10. **Report template maintenance**: a solvable problem as the document structure matures.
11. **SSE streaming reliability**: network issues can disrupt real-time updates.

## Top 10 Risks

1. Public source APIs change or impose restrictive rate limits.
2. Normalization logic becomes unmanageable as sources are added.
3. Scoring formula is perceived as opaque or wrong.
4. Trend Analyst produces false positives in low-volume windows.
5. Opportunity Analyst recommends actions not supported by evidence.
6. Forecasts overstate confidence and mislead users.
7. Evidence validation may over-discount opportunities with sparse but high-quality signals.
8. PII or sensitive content is accidentally ingested and stored.
9. Ollama Qwen 7B is too slow or inconsistent on CPU hardware.
10. SSE streaming reliability issues disrupt real-time pipeline visibility.

## Recommended Mitigations Before Production

1. Source adapters behind a stable interface with health monitoring.
2. Unit tests for scoring components with known input/output fixtures.
3. Human-in-the-loop validation of the first 100 opportunities.
4. Confidence thresholds tuned against historical signal batches.
5. PII filtering and data retention policy enforced in code.
6. LLM outputs constrained by structured schemas and prompt templates.
7. Performance benchmarks for ingestion, trend detection, and scoring.
8. SSE streaming reliability with reconnection logic and fallback mechanisms.
