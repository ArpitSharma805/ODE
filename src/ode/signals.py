"""Signal normalization: canonicalize and classify raw MCP signals."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ode.concepts import ConceptRegistry
from ode.llm import _ollama_generate
from ode.retrieval import RetrievalPlan
from ode.search_noise import contains_noise, expand_acronyms

logger = logging.getLogger(__name__)

TRIVIAL_COMMIT_PATTERNS = [
    r"(?i)^initial commit\b",
    r"(?i)^add files via upload\b",
    r"(?i)^update readme(?:\.md)?$",
    r"(?i)^revise readme\b",
    r"(?i)^clean up\b",
    r"(?i)^bump version\b",
    r"(?i)^merge branch\b",
    r"(?i)^merge pull request\b",
]

SIGNAL_TYPES = {
    "developer_pain",
    "adoption",
    "community_discussion",
    "market_demand",
    "hiring",
    "product_launch",
    "unknown",
}

_TRENDS = {"up", "down", "stable", "unknown"}

# Generic pain indicators.  Phrases with spaces or punctuation are matched
# literally; single words are matched with word boundaries to avoid false
# positives like "debug" matching "bug".
_PAIN_KEYWORDS = [
    "alternative to",
    "broken",
    "bug",
    "buggy",
    "cannot",
    "can't",
    "complicated",
    "confusing",
    "could not",
    "difficult",
    "does not support",
    "doesn't support",
    "error",
    "errors",
    "fail",
    "failed",
    "failure",
    "feature request",
    "frustrated",
    "frustrating",
    "hard to",
    "hate",
    "issue",
    "issues",
    "lack",
    "lacking",
    "limitation",
    "limited",
    "looking for alternative",
    "manual process",
    "manual work",
    "missing",
    "need",
    "needed",
    "needs",
    "not possible",
    "not support",
    "pain",
    "painful",
    "poor",
    "problem",
    "problems",
    "repeated",
    "repetitive",
    "slow",
    "struggling",
    "switch away",
    "tedious",
    "too complex",
    "unable to",
    "unsupported",
    "wish",
    "wished",
    "workaround",
]

# Order matters: more specific patterns come first.
_METRIC_TYPE_RULES = [
    ("github_open_issues", "developer_pain"),
    ("workaround", "developer_pain"),
    ("friction", "developer_pain"),
    ("pain", "developer_pain"),
    ("github_repo_results", "market_demand"),
    ("market_trend", "market_demand"),
    ("tavily_research", "market_demand"),
    ("tavily_search_summary", "market_demand"),
    ("demand", "market_demand"),
    ("trend", "market_demand"),
    ("search", "market_demand"),
    ("github_stars", "adoption"),
    ("github_forks", "adoption"),
    ("stars", "adoption"),
    ("forks", "adoption"),
    ("downloads", "adoption"),
    ("installs", "adoption"),
    ("usage", "adoption"),
    ("adoption", "adoption"),
    ("github_issue_titles", "community_discussion"),
    ("github_commit_messages", "community_discussion"),
    ("github_commits", "community_discussion"),
    ("github_contributors", "community_discussion"),
    ("tavily_search_result", "community_discussion"),
    ("contributor", "community_discussion"),
    ("commit", "community_discussion"),
    ("discussion", "community_discussion"),
    ("forum", "community_discussion"),
    ("mention", "community_discussion"),
    ("hiring", "hiring"),
    ("jobs", "hiring"),
    ("job", "hiring"),
    ("salary", "hiring"),
    ("opening", "hiring"),
    ("launch", "product_launch"),
    ("release", "product_launch"),
    ("announcement", "product_launch"),
    ("product", "product_launch"),
]

_SOURCE_TYPE_RULES = [
    ("tavily_mcp", "market_demand"),
    ("github_mcp", "community_discussion"),
]


@dataclass
class Signal:
    """A normalized, classified technology signal.

    ``signal_type`` must be one of: developer_pain, adoption,
    community_discussion, market_demand, hiring, product_launch, unknown.
    """

    signal_type: str
    source: str
    entity: str
    problem: str = ""
    evidence: str = ""
    frequency: int = 1
    trend: str = "unknown"
    confidence: float = 0.5
    source_url: str = ""
    metric: str = ""
    value: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)


def _compile_pain_patterns() -> list[re.Pattern[str]]:
    """Compile pain keyword regexes for fast matching."""
    patterns: list[re.Pattern[str]] = []
    for kw in _PAIN_KEYWORDS:
        if " " in kw or any(ch in kw for ch in ("'", "-")):
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
        else:
            pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        patterns.append(pattern)
    return patterns


_PAIN_PATTERNS = _compile_pain_patterns()


def _clean_entity_for_registry(text: str) -> str:
    """Remove URLs and empty parentheses so entity names canonicalize cleanly."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip()


def _contains_pain(text: str) -> bool:
    """Return True if the text contains any pain keyword."""
    return any(p.search(text) for p in _PAIN_PATTERNS)


def _first_pain_match(text: str) -> re.Match[str] | None:
    """Return the earliest pain keyword match in the text, or None."""
    first: re.Match[str] | None = None
    for pattern in _PAIN_PATTERNS:
        match = pattern.search(text)
        if match and (first is None or match.start() < first.start()):
            first = match
    return first


def _extract_source(raw: dict[str, Any]) -> str:
    """Infer a human-readable source name from the raw signal."""
    source = str(raw.get("source", "") or raw.get("source_type", "")).strip()
    if source:
        return source
    metric = str(raw.get("metric", "")).lower()
    if metric.startswith("github_"):
        return "github_mcp"
    if metric.startswith("tavily_"):
        return "tavily_mcp"
    if "web" in metric or "docs" in metric:
        return "web"
    return "unknown"


def _extract_source_url(raw: dict[str, Any], entity: str, source: str) -> str:
    """Return a URL for the signal when one is available."""
    for key in ("source_url", "url", "html_url", "link"):
        value = str(raw.get(key, "")).strip()
        if value:
            return value
    match = re.search(r"https?://[^\s)\"]+", entity)
    if match:
        return match.group(0)
    if source == "github_mcp" and re.match(r"^[\w.-]+/[\w.-]+$", entity):
        return f"https://github.com/{entity}"
    return ""


def _classify_signal(raw: dict[str, Any]) -> str:
    """Determine the signal type using deterministic metric/source rules.

    Pain keywords in the value override everything and mark the signal as
    developer_pain.
    """
    value = str(raw.get("value", ""))
    if _contains_pain(value):
        return "developer_pain"

    source = _extract_source(raw)
    text = f"{raw.get('metric', '')} {source}".lower()

    for pattern, stype in _METRIC_TYPE_RULES:
        if pattern in text:
            return stype
    for pattern, stype in _SOURCE_TYPE_RULES:
        if pattern in text:
            return stype

    return "unknown"


def _extract_problem(text: str) -> str:
    """Extract a short problem phrase from a developer-pain signal value."""
    text = str(text).strip()
    if not text or not _first_pain_match(text):
        return ""

    # Remove URLs and normalize whitespace.
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # Prefer the first clause/sentence that contains a pain indicator.
    clauses = re.split(r"\s*[.!?;]\s*", text)
    for clause in clauses:
        if clause and _contains_pain(clause):
            cleaned = re.sub(r"\s+", " ", clause).strip()
            words = cleaned.split()
            if len(words) > 12:
                cleaned = " ".join(words[:12]) + "..."
            return cleaned

    # Fallback: a snippet around the first pain keyword.
    match = _first_pain_match(text)
    if match:
        start = max(0, match.start() - 15)
        end = min(len(text), match.end() + 80)
        snippet = text[start:end].strip()
        if start > 0 and text[start].isalnum():
            snippet = snippet.split(" ", 1)[1] if " " in snippet else snippet
        if end < len(text) and text[end - 1].isalnum():
            snippet = snippet.rsplit(" ", 1)[0] if " " in snippet else snippet
        return re.sub(r"\s+", " ", snippet).strip()

    return ""


def _infer_trend(value: str) -> str:
    """Infer a directional trend from the value text."""
    lowered = str(value).lower()
    up_words = {
        "gain",
        "gained",
        "grow",
        "growing",
        "growth",
        "increase",
        "increased",
        "increasing",
        "rise",
        "rising",
        "rose",
        "rally",
        "surge",
        "surging",
        "up",
    }
    down_words = {
        "decline",
        "declined",
        "declining",
        "decrease",
        "decreased",
        "decreasing",
        "down",
        "drop",
        "dropped",
        "dropping",
        "fall",
        "falling",
        "fell",
        "lose",
        "losing",
        "lost",
        "shrank",
        "shrink",
        "shrinking",
    }
    stable_words = {
        "constant",
        "flat",
        "plateau",
        "plateaued",
        "stable",
        "steady",
        "unchanged",
    }
    if any(word in lowered for word in up_words):
        return "up"
    if any(word in lowered for word in down_words):
        return "down"
    if any(word in lowered for word in stable_words):
        return "stable"
    return "unknown"


def _extract_frequency(raw: dict[str, Any]) -> int:
    """Return the signal frequency, defaulting to 1."""
    freq = raw.get("frequency")
    if isinstance(freq, int):
        return max(1, freq)
    if isinstance(freq, float) and freq.is_integer():
        return max(1, int(freq))
    try:
        return max(1, int(float(freq)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1


def _normalize_confidence(raw: dict[str, Any]) -> float:
    """Normalize evidence_quality or confidence to a 0-1 score."""
    quality = raw.get("evidence_quality")
    if quality is not None:
        try:
            score = float(quality)
            if score > 1.0:
                score = score / 100.0
            return max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            pass
    confidence = raw.get("confidence")
    if confidence is not None:
        try:
            return max(0.0, min(1.0, float(confidence)))
        except (ValueError, TypeError):
            pass
    return 0.5


def _extract_evidence(raw: dict[str, Any], metric: str) -> str:
    """Pull a textual evidence snippet from the raw signal when available."""
    evidence = str(raw.get("evidence", "")).strip()
    if evidence:
        return evidence[:1000]
    value = str(raw.get("value", "")).strip()
    if value and re.search(r"[a-zA-Z]", value):
        text_metrics = (
            "title",
            "message",
            "text",
            "content",
            "summary",
            "result",
            "issue",
            "discussion",
            "comment",
            "answer",
            "snippet",
            "body",
            "description",
        )
        if any(term in metric.lower() for term in text_metrics):
            return value[:1000]
    return ""


def _plan_context(plan: Any) -> str:
    """Build a context string from the research plan for concept canonicalization."""
    if plan is None:
        return ""
    primary = getattr(plan, "primary", "") or ""
    aliases = getattr(plan, "aliases", []) or []
    alias_parts = [str(a).strip() for a in aliases if a]
    if primary:
        return f"{primary}: {', '.join(alias_parts)}".strip(": ").strip()
    return ", ".join(alias_parts)


def _extract_json(response: str) -> Any:
    """Parse a JSON response, stripping Markdown code fences if present."""
    text = response.strip()
    if text.startswith("```"):
        match = re.match(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    return json.loads(text)


def _build_classification_prompt(chunk: list[dict[str, Any]]) -> str:
    """Build a batch Ollama prompt for signal classification."""
    allowed_types = ", ".join(sorted(SIGNAL_TYPES))
    items = []
    for raw in chunk:
        items.append({
            "source": _extract_source(raw),
            "metric": str(raw.get("metric", ""))[:80],
            "value": str(raw.get("value", ""))[:400],
        })
    return (
        "You are a generic technology signal classifier. "
        "Given the signal entries below, return a JSON object with key 'signals' "
        "containing one object per entry, in the same order. Each object must have: "
        "index (0-based integer), signal_type (one of: "
        f"{allowed_types}), problem (short phrase when signal_type is developer_pain, otherwise ''), "
        "trend (one of: up, down, stable, unknown). "
        "Rules: classify only from the text; prefer developer_pain for problems, workarounds, "
        "missing features, or frustration; adoption for stars/forks/downloads/usage; "
        "community_discussion for commits/contributors/discussions/articles; "
        "market_demand for search/trend/demand signals; hiring for jobs/salaries; "
        "product_launch for releases/announcements. Keep problem phrases under 12 words. "
        "Return only valid JSON.\n\n"
        f"{json.dumps(items, indent=2)}"
    )


def _llm_classify_batch(raw_signals: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Classify a batch of signals with Ollama, returning overrides or None on failure."""
    if not raw_signals:
        return []

    batch_size = 40
    overrides: list[dict[str, Any]] = [{} for _ in raw_signals]

    for start in range(0, len(raw_signals), batch_size):
        chunk = raw_signals[start : start + batch_size]
        prompt = _build_classification_prompt(chunk)
        response = _ollama_generate(prompt, format="json")
        if not response:
            logger.warning("Ollama signal classification returned no response; using deterministic fallback")
            return None

        try:
            parsed = _extract_json(response)
            signals = parsed.get("signals") if isinstance(parsed, dict) else parsed
            if not isinstance(signals, list):
                logger.warning("Ollama signal classification JSON missing 'signals' list")
                return None
            for item in signals:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                if idx is None:
                    continue
                try:
                    idx = int(idx)
                except (ValueError, TypeError):
                    continue
                if idx < 0 or idx >= len(chunk):
                    continue
                actual_idx = start + idx
                overrides[actual_idx] = {
                    "signal_type": str(item.get("signal_type", "")).strip().lower(),
                    "problem": str(item.get("problem", "")).strip(),
                    "trend": str(item.get("trend", "")).strip().lower(),
                }
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to parse Ollama signal classification response: %s", exc)
            return None

    return overrides


def _is_trivial_commit(commit_message: str) -> bool:
    """Return True when a commit message is noise and should be ignored."""
    first_line = str(commit_message).split("\n")[0].strip()
    return any(re.search(pattern, first_line) for pattern in TRIVIAL_COMMIT_PATTERNS)




def _filter_commit_messages(value: str) -> str:
    """Drop trivial commit messages from a '; '-delimited commit summary."""
    messages = [m.strip() for m in value.split("; ") if m.strip()]
    kept = [m for m in messages if not _is_trivial_commit(m)]
    return "; ".join(kept)


# Sources that carry stronger market/demand signal for learning and career queries.
_LEARNING_CAREER_HIGH_SOURCES = {
    "jobs",
    "hackernews",
    "reddit",
    "news",
    "producthunt",
    "tavily_mcp",
}

# Metrics that represent live job or community discussion data.
_LEARNING_CAREER_HIGH_METRICS = {
    "jobs_result",
    "hackernews_result",
    "reddit_post",
    "news_article",
    "producthunt_post",
    "tavily_search_result",
}


def _source_weight_multiplier(source: str, metric: str, intent: str) -> float:
    """Return a confidence multiplier that reflects source relevance per intent."""
    metric = metric.lower()
    source = source.lower()
    is_github_commit = metric in ("github_commits", "github_commit_messages")

    if intent in ("Skill Learning", "Career Development"):
        if is_github_commit:
            return 0.3
        if source in _LEARNING_CAREER_HIGH_SOURCES or metric in _LEARNING_CAREER_HIGH_METRICS:
            return 1.25
        return 1.0

    if intent in ("Opportunity Discovery", "Product Ideas", "Business Opportunities"):
        # Leave repo, issue, PR, Tavily, and research-source signals at full weight.
        if is_github_commit:
            return 0.8
        return 1.0

    return 1.0


def normalize_signals(
    raw_signals: list[dict[str, Any]],
    plan: RetrievalPlan,
    use_llm: bool = True,
) -> list[Signal]:
    """Normalize a list of raw MCP signal dictionaries into canonical Signal objects.

    The function:
    - validates and filters raw signals,
    - canonicalizes entity names with ``ConceptRegistry(use_llm=False)``,
    - classifies each signal into a signal_type with deterministic rules,
    - optionally asks Ollama to refine classifications (with deterministic fallback),
    - extracts short problem phrases for developer_pain signals,
    - infers trend and confidence, and preserves raw metadata.

    Args:
        raw_signals: Raw signal dictionaries from MCP collectors. Expected keys
            include ``source_id``, ``entity``, ``metric``, ``value``,
            ``evidence_quality``, and optional ``source_type`` / ``url``.
        plan: The research plan (primary concept and aliases) used as context
            for entity canonicalization.
        use_llm: Whether to request Ollama batch classification. If ``False`` or
            the LLM call fails, deterministic rules are used exclusively.

    Returns:
        A list of normalized ``Signal`` objects, one per valid raw signal.
    """
    if not raw_signals:
        return []

    valid_raw: list[dict[str, Any]] = []
    for raw in raw_signals:
        if not isinstance(raw, dict):
            logger.warning("Skipping invalid raw signal: %r", raw)
            continue
        entity = str(raw.get("entity", "")).strip()
        value = str(raw.get("value", ""))
        if not entity:
            logger.warning("Skipping invalid raw signal: %r", raw)
            continue
        if contains_noise(f"{entity} {value}"):
            logger.warning("Skipping noisy search signal: %r", raw)
            continue
        # Expand ambiguous acronyms in web/search signals so downstream concept
        # extraction and reports use the full disambiguated term.
        if str(raw.get("metric", "")).startswith("tavily_"):
            entity = expand_acronyms(entity)
            value = expand_acronyms(value)
        raw["entity"] = entity
        raw["value"] = value
        valid_raw.append(raw)

    if not valid_raw:
        return []

    # Canonicalize all entity names in one batch using deterministic clustering.
    entities = [_clean_entity_for_registry(str(raw.get("entity", "")).strip()) for raw in valid_raw]
    registry = ConceptRegistry(use_llm=False)
    registry.register(entities, context=_plan_context(plan))

    # Deterministic classification and trend inference.
    det_types = [_classify_signal(raw) for raw in valid_raw]
    det_trends = [_infer_trend(str(raw.get("value", ""))) for raw in valid_raw]

    signal_types = det_types[:]
    trends = det_trends[:]
    problems: list[str] = ["" for _ in valid_raw]

    # Optional LLM batch refinement.
    if use_llm:
        overrides = _llm_classify_batch(valid_raw)
        if overrides:
            for i, override in enumerate(overrides):
                if i >= len(valid_raw):
                    break
                stype = override.get("signal_type")
                if stype in SIGNAL_TYPES:
                    signal_types[i] = stype
                trend = override.get("trend")
                if trend in _TRENDS:
                    trends[i] = trend
                problem = str(override.get("problem", "")).strip()
                if problem:
                    problems[i] = problem[:120]

    signals: list[Signal] = []
    intent = getattr(plan, "intent", "Opportunity Discovery") or "Opportunity Discovery"
    for raw, signal_type, trend, problem in zip(valid_raw, signal_types, trends, problems):
        entity_raw = str(raw.get("entity", "")).strip()
        entity_clean = _clean_entity_for_registry(entity_raw)
        entity = registry.canonical(entity_clean) or entity_clean
        metric = str(raw.get("metric", "")).strip()
        value = str(raw.get("value", "")).strip()
        source = _extract_source(raw)
        source_url = _extract_source_url(raw, entity_raw, source)
        base_confidence = _normalize_confidence(raw)
        weight = _source_weight_multiplier(source, metric, intent)
        confidence = max(0.0, min(1.0, base_confidence * weight))
        frequency = _extract_frequency(raw)
        evidence = _extract_evidence(raw, metric)

        if metric == "github_commit_messages":
            value = _filter_commit_messages(value)
            if not value:
                continue

        final_problem = problem
        if signal_type == "developer_pain" and not final_problem:
            final_problem = _extract_problem(value)

        # Preserve anything we did not explicitly map into a Signal field.
        raw_metadata = {
            k: v
            for k, v in raw.items()
            if k not in {
                "entity",
                "metric",
                "value",
                "source",
                "source_type",
                "source_url",
                "url",
                "evidence",
                "confidence",
                "frequency",
                "trend",
            }
        }

        signals.append(
            Signal(
                signal_type=signal_type,
                source=source,
                entity=entity,
                problem=final_problem,
                evidence=evidence,
                frequency=frequency,
                trend=trend,
                confidence=confidence,
                source_url=source_url,
                metric=metric,
                value=value,
                raw_metadata=raw_metadata,
            )
        )

    return signals
