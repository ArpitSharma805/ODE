"""Evidence synthesis: Signals -> Themes -> Problems -> Insights -> Opportunities -> Narrative.

This module provides a deterministic fallback for turning raw signals into a
structured synthesis.  It can be used as a standalone analysis stage or as the
input to richer report generation.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ode.analysis_models import AnalysisResult

logger = logging.getLogger(__name__)


@dataclass
class Theme:
    """A recurring theme observed across the collected evidence."""

    name: str
    summary: str
    signals: list[dict[str, Any]] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    source_count: int = 0
    evidence_quality: float = 0.0


@dataclass
class Problem:
    """A concrete problem derived from a theme."""

    statement: str
    affected_users: str
    evidence: str
    related_themes: list[str] = field(default_factory=list)


@dataclass
class Insight:
    """A synthesized insight that bridges two or more themes."""

    statement: str
    evidence_themes: list[str] = field(default_factory=list)
    opportunity_titles: list[str] = field(default_factory=list)


@dataclass
class SynthesisOpportunity:
    """An opportunity derived from the synthesis."""

    title: str
    problem: str
    affected_users: str
    why_existing_solutions_fail: str
    why_now: str
    potential_solution: str
    risks: str
    evidence_summary: str
    source_themes: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class Narrative:
    """The high-level narrative that ties the synthesis together."""

    key_patterns: list[str] = field(default_factory=list)
    market_implications: list[str] = field(default_factory=list)
    evidence_summary: str = ""


@dataclass
class Synthesis:
    """The complete synthesis output."""

    themes: list[Theme] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    opportunities: list[SynthesisOpportunity] = field(default_factory=list)
    narrative: Narrative = field(default_factory=Narrative)


# Theme buckets and the keywords that indicate them.
_THEME_KEYWORDS: dict[str, list[str]] = {
    "adoption": [
        "adoption",
        "adopt",
        "adopts",
        "adopting",
        "ecosystem",
        "growth",
        "integrate",
        "integration",
        "support",
        "supports",
        "adds",
        "added",
        "usage",
        "users",
        "downloads",
        "stars",
        "forks",
    ],
    "security": [
        "security",
        "leak",
        "leaks",
        "leaked",
        "permission",
        "permissions",
        "governance",
        "access",
        "vulnerability",
        "vulnerabilities",
        "danger",
        "exposure",
        "breach",
        "compliance",
        "auth",
        "unsafe",
        "protect",
    ],
    "observability": [
        "observability",
        "monitoring",
        "tracing",
        "telemetry",
        "log",
        "logs",
        "logging",
        "metrics",
        "trace",
        "monitor",
    ],
    "testing": [
        "testing",
        "validation",
        "validate",
        "test",
        "tests",
        "quality",
        "bug",
        "bugs",
        "regression",
        "verify",
        "verification",
    ],
    "marketplace": [
        "marketplace",
        "market",
        "registry",
        "registries",
        "discovery",
        "discover",
        "catalog",
        "directory",
        "find",
        "search",
        "store",
        "hub",
        "curate",
        "curation",
    ],
    "operations": [
        "operations",
        "operational",
        "deploy",
        "deployment",
        "run",
        "running",
        "manage",
        "management",
        "provisioning",
        "scale",
        "scaling",
        "ops",
        "production",
        "lifecycle",
    ],
}

# Default theme for metrics that do not otherwise match keyword buckets.
_METRIC_DEFAULT_THEME: dict[str, str] = {
    "adoption": "adoption",
    "developer_pain": "security",
    "community_discussion": "adoption",
    "market_demand": "marketplace",
    "hiring": "operations",
    "product_launch": "adoption",
    "news_mention": "adoption",
    "github_commits": "adoption",
    "github_contributors": "adoption",
    "github_open_issues": "security",
    "github_repo_results": "marketplace",
    "web_page_text": "adoption",
    "docs_page_text": "adoption",
}

_THEME_NAMES: dict[str, str] = {
    "adoption": "{primary} Ecosystem Adoption",
    "security": "{primary} Security & Governance",
    "observability": "{primary} Observability & Monitoring",
    "testing": "{primary} Testing & Validation",
    "marketplace": "{primary} Discovery & Marketplace",
    "operations": "{primary} Operations & Deployment",
}

_THEME_SUMMARIES: dict[str, str] = {
    "adoption": "{primary} adoption is accelerating as more organizations recognize its value",
    "security": "{primary} security and governance frameworks are becoming critical for production adoption",
    "observability": "{primary} observability and monitoring tools are emerging to address production visibility gaps",
    "testing": "{primary} testing and validation frameworks are developing to ensure quality and reliability",
    "marketplace": "{primary} marketplace and discovery platforms are forming to connect tools and users",
    "operations": "{primary} operations and deployment tooling is maturing to support production workloads",
}

_PROBLEM_STATEMENTS: dict[str, str] = {
    "security": "{primary} security and permission management concerns are growing as adoption increases",
    "observability": "{primary} observability and monitoring gaps make debugging and production troubleshooting difficult",
    "testing": "{primary} testing and validation challenges slow down production adoption and quality assurance",
    "marketplace": "{primary} marketplace and discovery remain fragmented, making it hard to find and evaluate tools",
    "operations": "{primary} operations and deployment complexity creates barriers to adoption and scaling",
    "adoption": "{primary} adoption is growing but tooling gaps remain for production use cases",
}

_AFFECTED_USERS: dict[str, str] = {
    "security": "Security teams and platform engineers implementing {primary} in production environments",
    "observability": "DevOps teams and developers monitoring {primary} applications in production",
    "testing": "QA teams and developers validating {primary} implementations before deployment",
    "marketplace": "Developers and organizations looking for {primary} tools and solutions",
    "operations": "Platform teams and DevOps engineers managing {primary} infrastructure",
    "adoption": "Organizations and developers considering {primary} for new projects",
}

_WHY_EXISTING_SOLUTIONS_FAIL: dict[str, str] = {
    "security": "Current {primary} security implementations are fragmented and lack standardization",
    "observability": "Existing {primary} monitoring tools don't provide the visibility needed for production workloads",
    "testing": "{primary} testing frameworks are immature and don't provide comprehensive coverage",
    "marketplace": "No centralized {primary} marketplace exists, making discovery and evaluation difficult",
    "operations": "{primary} deployment and operations tooling is complex and requires specialized expertise",
    "adoption": "{primary} tooling gaps and complexity barriers prevent widespread production adoption",
}

_WHY_NOW: dict[str, str] = {
    "security": "Growing {primary} adoption in production environments makes security and governance critical",
    "observability": "Production {primary} deployments require better observability for troubleshooting and monitoring",
    "testing": "As {primary} moves to production, comprehensive testing and validation become essential",
    "marketplace": "The {primary} ecosystem is maturing and needs better discovery and evaluation mechanisms",
    "operations": "Scaling {primary} workloads requires more sophisticated operations and deployment tooling",
    "adoption": "Market momentum and growing developer interest make this the right time to invest in {primary}",
}

_POTENTIAL_SOLUTIONS: dict[str, str] = {
    "security": "Build standardized {primary} security frameworks and governance patterns",
    "observability": "Develop {primary} observability and monitoring solutions for production environments",
    "testing": "Create comprehensive {primary} testing and validation frameworks",
    "marketplace": "Establish centralized {primary} marketplace and discovery platforms",
    "operations": "Simplify {primary} deployment and operations with better tooling and automation",
    "adoption": "Address {primary} tooling gaps and complexity to enable broader adoption",
}

_RISKS: dict[str, str] = {
    "security": "Security frameworks may introduce complexity and overhead",
    "observability": "Monitoring solutions may add latency and resource consumption",
    "testing": "Testing frameworks may slow development velocity if not well-designed",
    "marketplace": "Marketplace adoption may be slow without critical mass",
    "operations": "Simplified operations may reduce flexibility and control",
    "adoption": "Tooling gaps may persist if ecosystem doesn't mature as expected",
}

_OPPORTUNITY_TITLES: dict[str, str] = {
    "security": "{primary} Security & Governance Framework",
    "observability": "{primary} Observability & Monitoring Platform",
    "testing": "{primary} Testing & Validation Suite",
    "marketplace": "{primary} Discovery & Marketplace",
    "operations": "{primary} Operations & Deployment Toolkit",
    "adoption": "{primary} Adoption Acceleration Platform",
}

# Priority rank for opportunity ordering. Lower numbers surface first.
_OPPORTUNITY_PRIORITY: dict[str, int] = {
    "security": 1,
    "observability": 2,
    "operations": 3,
    "testing": 4,
    "marketplace": 5,
    "adoption": 6,
}

_INSIGHT_PAIRS: list[tuple[str, str, str]] = [
    ("adoption", "security", "As {primary} adoption grows, security and governance concerns grow alongside it."),
    ("adoption", "observability", "As {primary} ecosystems expand, observability and operational visibility become harder to maintain."),
    ("adoption", "marketplace", "As more {primary} servers appear, discovery and trust mechanisms become critical."),
    ("security", "operations", "As enterprises adopt {primary}, operational security and compliance controls become increasingly important."),
    ("testing", "operations", "As {primary} deployments move to production, testing and operational validation must be automated."),
    ("marketplace", "security", "A trusted {primary} marketplace cannot grow without security attestations and access controls."),
]


def short_primary(text: str) -> str:
    """Return a concise primary name for use in theme/opportunity titles."""
    if not text:
        return ""
    # Prefer an acronym in parentheses, e.g. "Model Context Protocol (MCP)" -> "MCP"
    m = re.search(r"\(([A-Z0-9]+)\)", text)
    if m:
        return m.group(1)
    # Return the first meaningful token (preserving its case) so "AI observability"
    # stays qualified as "AI" instead of collapsing to the tail word.
    for w in re.findall(r"[A-Za-z0-9]+", text):
        if len(w) >= 2:
            return w
    return text


def _dedupe_primary(primary: str, rendered: str) -> str:
    """Remove a repeated trailing/leading category word after the primary phrase."""
    primary = primary.strip()
    if not primary:
        return rendered
    words = primary.split()
    last = words[-1] if words else ""
    if not last:
        return rendered
    # Collapse adjacent duplicates of the primary's last word (case-insensitive).
    rendered = re.sub(rf"(?i)\b({re.escape(last)})\b\s+\1\b", r"\1", rendered)
    # Remove a trailing duplicate of that word unless the rendered string ends with the full primary.
    lower_rendered = rendered.rstrip().lower()
    lower_primary = primary.lower()
    if lower_rendered.endswith(last.lower()) and not lower_rendered.endswith(lower_primary):
        rendered = re.sub(rf"(?i)\s+{re.escape(last)}\b$", "", rendered.rstrip())
    return rendered


def _format_primary(primary: str, template: str) -> str:
    """Format a template with the primary phrase and deduplicate repeated category words."""
    return _dedupe_primary(primary, template.format(primary=primary))


def _dedupe_repeated_words(text: str) -> str:
    """Collapse any adjacent duplicate words, ignoring case differences."""
    return re.sub(r"(?i)\b(\w+)\b\s+\1\b", r"\1", text)


def primary(
    signals: list[dict[str, Any]],
    intent: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return (primary_long, primary_short) from intent or from the most common entity."""
    primary_long = ""
    if intent:
        primary_long = str((intent or {}).get("primary_technology", "") or "").strip()
        if not primary_long:
            topics = [str(t) for t in (intent or {}).get("topics", []) if t]
            if topics:
                primary_long = topics[0]
    if not primary_long and signals:
        counter: Counter[str] = Counter()
        for s in signals:
            if not isinstance(s, dict):
                continue
            entity = str(s.get("entity", "")).strip()
            if entity:
                counter[entity] += 1
        if counter:
            primary_long = counter.most_common(1)[0][0]
    if not primary_long:
        primary_long = "this technology"
    return primary_long, short_primary(primary_long)


def _signal_text(signal: Any) -> str:
    """Return a single lower-case text combining the fields we match against."""
    if not isinstance(signal, dict):
        return ""
    parts = [
        str(signal.get("entity", "")),
        str(signal.get("metric", "")),
        str(signal.get("value", "")),
    ]
    return " ".join(p for p in parts if p).lower()


def _classify_signals(
    signals: list[dict[str, Any]],
    primary_short: str,
) -> dict[str, list[dict[str, Any]]]:
    """Assign each signal to the best-fitting theme bucket."""
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in _THEME_KEYWORDS}
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        text = _signal_text(signal)
        metric = str(signal.get("metric", "")).lower().strip()

        best_bucket: str | None = None
        best_score = 0
        matched_keywords: list[str] = []
        for bucket, keywords in _THEME_KEYWORDS.items():
            score = 0
            hits: list[str] = []
            for kw in keywords:
                count = text.count(kw)
                if count:
                    score += count
                    hits.append(kw)
            # Prefer the longest keyword hit as a tiebreaker, but only if score is nonzero.
            if score > best_score or (score == best_score and score > 0 and len(hits) > len(matched_keywords)):
                best_score = score
                best_bucket = bucket
                matched_keywords = hits

        if best_bucket is None or best_score == 0:
            best_bucket = _METRIC_DEFAULT_THEME.get(metric, "adoption")

        # If a keyword matches the primary short name, ensure it is not mis-classified.
        # The primary name should not itself force the adoption bucket.
        if best_score == 0 and metric in _METRIC_DEFAULT_THEME:
            best_bucket = _METRIC_DEFAULT_THEME[metric]

        buckets[best_bucket].append(signal)

    return buckets


def _cluster_signals(
    signals: list[dict[str, Any]],
    primary_short: str,
) -> dict[str, list[dict[str, Any]]]:
    """Cluster signals into sub-themes based on content similarity.

    Uses simple keyword-based clustering to group related signals together.
    Returns a dict mapping sub-theme names to their signal lists.
    """
    if len(signals) < 4:
        return {"general": signals}

    # Extract signal texts for clustering
    signal_texts = []
    for signal in signals:
        if isinstance(signal, dict):
            text = _signal_text(signal)
            signal_texts.append(text)
        else:
            signal_texts.append("")

    # Simple clustering based on keyword overlap
    clusters: dict[str, list[dict[str, Any]]] = {}
    used_indices = set()

    # Define clustering keywords for common signal patterns
    _sub_theme_keywords = {
        "security": ["security", "auth", "permission", "vulnerability", "governance", "risk"],
        "adoption": ["adopt", "growth", "ecosystem", "integration", "usage", "users"],
        "operations": ["deploy", "ops", "management", "scale", "production", "lifecycle"],
        "discovery": ["discover", "find", "search", "registry", "marketplace", "catalog"],
        "monitoring": ["monitor", "observability", "trace", "log", "telemetry", "debug"],
    }

    # Cluster signals based on keyword matches
    for cluster_name, keywords in _sub_theme_keywords.items():
        cluster_signals = []
        for idx, signal in enumerate(signals):
            if idx in used_indices:
                continue
            if not isinstance(signal, dict):
                continue

            text = _signal_text(signal).lower()
            if any(kw in text for kw in keywords):
                cluster_signals.append(signal)
                used_indices.add(idx)

        if cluster_signals:
            clusters[cluster_name] = cluster_signals

    # Add remaining signals to a general cluster
    remaining = [s for idx, s in enumerate(signals) if idx not in used_indices and isinstance(s, dict)]
    if remaining:
        clusters["general"] = remaining

    # If clustering failed to produce meaningful groups, return single cluster
    if len(clusters) <= 1:
        return {"general": signals}

    return clusters


def _build_themes(
    buckets: dict[str, list[dict[str, Any]]],
    primary_short: str,
    primary_long: str,
) -> list[Theme]:
    """Build Theme objects from the classified signal buckets."""
    themes: list[Theme] = []
    total_signals = sum(len(sigs) for sigs in buckets.values())
    logger.info("Building themes from %d total signals across %d buckets", total_signals, len(buckets))

    for bucket, sigs in buckets.items():
        if not sigs:
            continue

        logger.info("Processing bucket '%s' with %d signals", bucket, len(sigs))

        # Split large buckets into sub-themes based on signal clustering
        # to avoid collapsing distinct discussions into a single theme
        if len(sigs) >= 4:
            logger.info("Splitting large bucket '%s' (%d signals) into sub-themes", bucket, len(sigs))
            sub_themes = _cluster_signals(sigs, primary_short)
            logger.info("Cluster produced %d sub-themes: %s", len(sub_themes), list(sub_themes.keys()))
            for sub_name, sub_sigs in sub_themes.items():
                name = _format_primary(primary_short, f"{_THEME_NAMES[bucket]}: {sub_name}")
                summary = _format_primary(primary_short, _THEME_SUMMARIES[bucket])
                source_count = len({str(s.get("source_type", "unknown")) for s in sub_sigs if isinstance(s, dict)})
                eqs = [float(s.get("evidence_quality", 0) or 0) for s in sub_sigs if isinstance(s, dict)]
                avg_eq = sum(eqs) / len(eqs) if eqs else 0.0

                # Top keywords present in this sub-theme's signals.
                text = " ".join(_signal_text(s) for s in sub_sigs)
                keywords = [kw for kw in _THEME_KEYWORDS[bucket] if kw in text]
                keywords = sorted(set(keywords), key=lambda k: text.count(k), reverse=True)[:5]

                logger.info("Created sub-theme '%s' with %d signals, %d source types", name, len(sub_sigs), source_count)
                themes.append(
                    Theme(
                        name=name,
                        summary=summary,
                        signals=sub_sigs,
                        keywords=keywords,
                        source_count=source_count,
                        evidence_quality=round(avg_eq, 2),
                    )
                )
        else:
            name = _format_primary(primary_short, _THEME_NAMES[bucket])
            summary = _format_primary(primary_short, _THEME_SUMMARIES[bucket])
            source_count = len({str(s.get("source_type", "unknown")) for s in sigs if isinstance(s, dict)})
            eqs = [float(s.get("evidence_quality", 0) or 0) for s in sigs if isinstance(s, dict)]
            avg_eq = sum(eqs) / len(eqs) if eqs else 0.0

            # Top keywords present in this bucket's signals.
            text = " ".join(_signal_text(s) for s in sigs)
            keywords = [kw for kw in _THEME_KEYWORDS[bucket] if kw in text]
            keywords = sorted(set(keywords), key=lambda k: text.count(k), reverse=True)[:5]

            logger.info("Created theme '%s' with %d signals, %d source types", name, len(sigs), source_count)
            themes.append(
                Theme(
                    name=name,
                    summary=summary,
                    signals=sigs,
                    keywords=keywords,
                    source_count=source_count,
                    evidence_quality=round(avg_eq, 2),
                )
            )

    # Sort by signal count and evidence quality.
    themes.sort(key=lambda t: (len(t.signals), t.evidence_quality), reverse=True)
    logger.info("Final theme count: %d", len(themes))
    return themes


def _build_problems(
    themes: list[Theme],
    primary_short: str,
) -> list[Problem]:
    """Derive a concrete problem for each theme."""
    problems: list[Problem] = []
    logger.info("Building problems from %d themes", len(themes))
    for theme in themes:
        bucket = _bucket_from_name(theme.name, primary_short)
        statement = _format_primary(primary_short, _PROBLEM_STATEMENTS[bucket])
        affected_users = _format_primary(primary_short, _AFFECTED_USERS[bucket])
        evidence = (
            f"{len(theme.signals)} signals from {theme.source_count} source types "
            f"mention {', '.join(theme.keywords) or 'this area'}."
        )
        logger.info("Created problem for theme '%s': %s", theme.name, statement[:50])
        problems.append(
            Problem(
                statement=statement,
                affected_users=affected_users,
                evidence=evidence,
                related_themes=[theme.name],
            )
        )
    logger.info("Final problem count: %d", len(problems))
    return problems


def _bucket_from_name(name: str, primary_short: str) -> str:
    """Map a theme name back to its internal bucket key."""
    for bucket, template in _THEME_NAMES.items():
        if _format_primary(primary_short, template) == name:
            return bucket
    return "adoption"


def _bucket_from_title(title: str, primary_short: str) -> str:
    """Map an opportunity title back to its internal bucket key."""
    for bucket, template in _OPPORTUNITY_TITLES.items():
        if _format_primary(primary_short, template) == title:
            return bucket
    return "adoption"


def _build_insights(
    themes: list[Theme],
    primary_short: str,
) -> list[Insight]:
    """Generate insights from pairs of active themes."""
    if not themes:
        logger.info("No themes available for insight generation")
        return []

    logger.info("Building insights from %d themes", len(themes))
    theme_by_bucket: dict[str, Theme] = {}
    for theme in themes:
        bucket = _bucket_from_name(theme.name, primary_short)
        theme_by_bucket[bucket] = theme
        logger.info("Mapped theme '%s' to bucket '%s'", theme.name, bucket)

    insights: list[Insight] = []
    for a, b, template in _INSIGHT_PAIRS:
        if a in theme_by_bucket and b in theme_by_bucket:
            statement = _format_primary(primary_short, template)
            logger.info("Created insight between buckets '%s' and '%s'", a, b)
            insights.append(
                Insight(
                    statement=statement,
                    evidence_themes=[theme_by_bucket[a].name, theme_by_bucket[b].name],
                    opportunity_titles=[
                        _format_primary(primary_short, _OPPORTUNITY_TITLES[a]),
                        _format_primary(primary_short, _OPPORTUNITY_TITLES[b]),
                    ],
                )
            )

    logger.info("Final insight count: %d", len(insights))
    # Sort by combined signal volume so the strongest relationships come first.
    def _insight_strength(insight: Insight) -> int:
        count = 0
        for theme in themes:
            if theme.name in insight.evidence_themes:
                count += len(theme.signals)
        return count

    insights.sort(key=_insight_strength, reverse=True)
    return insights


def _build_opportunities(
    themes: list[Theme],
    primary_short: str,
) -> list[SynthesisOpportunity]:
    """Generate one opportunity per active theme, using evidence-based titles."""
    opportunities: list[SynthesisOpportunity] = []
    logger.info("Building opportunities from %d themes", len(themes))
    for theme in themes:
        bucket = _bucket_from_name(theme.name, primary_short)

        # Generate specific, evidence-based title from theme content
        # Use the theme name and summary to create a concrete opportunity title
        theme_name = theme.name or ""
        theme_summary = theme.summary or ""

        # Extract meaningful action-oriented terms from theme
        # Look for specific patterns in theme names and summaries
        title = ""

        # Generate title from theme name and summary
        if theme_name:
            title = f"{primary_short} {theme_name}"
        else:
            title = f"{primary_short} Opportunity"

        # Use actual theme summary for problem description
        if theme_summary:
            problem = theme_summary[:200]
        else:
            problem = f"Challenges in {primary_short} ecosystem"

        # Generate meaningful field values from theme content
        affected_users = f"Developers and organizations working with {primary_short}"
        why_existing_solutions_fail = f"Current {primary_short} solutions lack comprehensive tooling"
        why_now = f"Growing adoption of {primary_short} creates urgent need for better solutions"
        potential_solution = f"Build specialized {primary_short} tools and infrastructure"
        risks = f"Integration complexity and ecosystem fragmentation"

        # Build evidence summary that references actual evidence
        signal_sources = sorted({str(s.get("source_type", "unknown")) for s in theme.signals if isinstance(s, dict)})
        evidence_summary = (
            f"{len(theme.signals)} signals from {theme.source_count} source types "
            f"({', '.join(signal_sources)}) support this opportunity. "
            f"Key evidence includes: {', '.join(theme.keywords[:5]) if theme.keywords else 'multiple indicators'}."
        )

        eqs = [float(s.get("evidence_quality", 0) or 0) for s in theme.signals if isinstance(s, dict)]
        score = (sum(eqs) / len(eqs) if eqs else 0.0) + min(20, len(theme.signals) * 2)
        logger.info("Created opportunity '%s' from theme '%s' with score %.2f", title, theme.name, score)
        opportunities.append(
            SynthesisOpportunity(
                title=title,
                problem=problem,
                affected_users=affected_users,
                why_existing_solutions_fail=why_existing_solutions_fail,
                why_now=why_now,
                potential_solution=potential_solution,
                risks=risks,
                evidence_summary=evidence_summary,
                source_themes=[theme.name],
                score=round(score, 2),
            )
        )
    logger.info("Final opportunity count: %d", len(opportunities))
    # Rank by actionable priority, then by signal strength.
    def _rank(opp: SynthesisOpportunity) -> tuple[int, float]:
        # Extract bucket from title by removing the primary_short prefix
        title_without_primary = opp.title.replace(primary_short, "").strip()
        # Try to match against known buckets
        for bucket, template in _OPPORTUNITY_TITLES.items():
            if _format_primary(primary_short, template) == opp.title:
                return (_OPPORTUNITY_PRIORITY.get(bucket, 99), -opp.score)
        return (99, -opp.score)

    opportunities.sort(key=_rank)
    return opportunities


def _build_narrative(
    themes: list[Theme],
    insights: list[Insight],
    primary_short: str,
    primary_long: str,
) -> Narrative:
    """Build the high-level narrative: key patterns, market implications, evidence summary."""
    if not themes:
        return Narrative()

    _SUMMARY_PREFIXES = (
        "multiple signals indicate ",
        "multiple sources highlight ",
        "evidence points to a need for ",
        "sources discuss ",
        "signals suggest ",
        "evidence indicates ",
    )

    def _clean_summary(summary: str) -> str:
        text = summary.lower().rstrip(".").strip()
        for prefix in _SUMMARY_PREFIXES:
            if text.startswith(prefix):
                return text[len(prefix):]
        return text

    key_patterns: list[str] = []
    for theme in themes[:5]:
        metric_counts: Counter[str] = Counter()
        for s in theme.signals:
            if isinstance(s, dict):
                metric_counts[str(s.get("metric", "signal"))] += 1
        top_metric = metric_counts.most_common(1)[0][0] if metric_counts else "discussion"
        phrase = _clean_summary(theme.summary)
        pattern = (
            f"Several sources indicate {phrase}, "
            f"primarily through {top_metric.replace('_', ' ')} signals "
            f"({len(theme.signals)} signals, {theme.source_count} source types)."
        )
        key_patterns.append(pattern)

    market_implications: list[str] = []
    for insight in insights[:4]:
        market_implications.append(
            f"If this pattern continues, {insight.statement.lower().rstrip('.')}, "
            f"creating demand for the related infrastructure opportunities."
        )
    if not market_implications and themes:
        market_implications.append(
            f"As {primary_short} matures, tooling gaps in {', '.join(t.name.replace(f'{primary_short} ', '') for t in themes[:3])} "
            f"are likely to become more valuable."
        )

    total_signals = sum(len(t.signals) for t in themes)
    source_types = sorted({
        str(s.get("source_type", "unknown"))
        for t in themes
        for s in t.signals
        if isinstance(s, dict)
    })
    evidence_summary = (
        f"The collected evidence shows that {primary_long} is moving from fragmented experimentation toward "
        f"focused production concerns. Across {total_signals} signals from {len(source_types)} source types, "
        f"the dominant patterns are {', '.join(t.name.replace(f'{primary_short} ', '') for t in themes[:3])}. "
        f"These patterns are not isolated; they form a coherent story of an ecosystem that is growing faster "
        f"than its operational, security, and discovery tooling."
    )
    if len(themes) > 1:
        evidence_summary += (
            f" More specifically, {insights[0].statement.lower().rstrip('.') if insights else 'the strongest signals cluster around the top theme'} "
            f"suggests the highest-leverage opportunities sit at the intersection of these concerns."
        )

    return Narrative(
        key_patterns=key_patterns,
        market_implications=market_implications,
        evidence_summary=evidence_summary,
    )


_FALLBACK_EVIDENCE_MESSAGE = (
    "Limited live evidence was collected for this query. "
    "Results should be treated as directional."
)


def _is_renderable(value: Any) -> bool:
    """Return True for non-empty, non-zero values that can be rendered."""
    if value is None:
        return False
    if isinstance(value, int) and value == 0:
        return False
    text = str(value).strip()
    return bool(text) and text != "0"


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9]+", text) if len(t) > 2]


def _strength_to_quality(strength: str) -> float:
    return {"strong": 0.9, "moderate": 0.7, "weak": 0.5}.get(strength.lower(), 0.6)


def _synthesis_from_analysis(
    analysis: dict[str, Any],
    signals: list[dict[str, Any]],
    intent: dict[str, Any] | None,
) -> Synthesis:
    """Convert an LLM AnalysisResult dict into the synthesis dataclasses."""
    if not isinstance(analysis, dict):
        return Synthesis()
    primary_long, primary_short = primary(signals, intent)

    themes: list[Theme] = []
    for raw_theme in analysis.get("themes", []):
        if not isinstance(raw_theme, dict):
            continue
        name = _normalize_text(raw_theme.get("theme_name"))
        summary = _normalize_text(raw_theme.get("what_is_happening")) or name
        evidence_summary = _normalize_text(raw_theme.get("evidence_summary"))
        strength = _normalize_text(raw_theme.get("strength")).lower()
        stakeholders = [str(s).strip() for s in raw_theme.get("affected_stakeholders", []) if s]
        cluster_ids = [str(c).strip() for c in raw_theme.get("cluster_ids", []) if c]
        keywords = sorted(set(_tokenize(name) + _tokenize(summary) + stakeholders + cluster_ids))[:10]
        themes.append(
            Theme(
                name=name,
                summary=summary,
                signals=[],
                keywords=keywords,
                source_count=int(raw_theme.get("source_count") or 0),
                evidence_quality=round(_strength_to_quality(strength), 2),
            )
        )

    problems: list[Problem] = []
    for raw_problem in analysis.get("problems", []):
        if not isinstance(raw_problem, dict):
            continue
        statement = _normalize_text(raw_problem.get("problem_statement"))
        affected_users_list = [str(s).strip() for s in raw_problem.get("who_has_this_problem", []) if s]
        affected_users = ", ".join(affected_users_list)
        evidence = _normalize_text(raw_problem.get("current_workarounds"))
        related_themes = [str(t).strip() for t in raw_problem.get("theme_ids", []) if t]
        problems.append(
            Problem(
                statement=statement,
                affected_users=affected_users,
                evidence=evidence,
                related_themes=related_themes,
            )
        )

    insights: list[Insight] = []
    for raw_insight in analysis.get("insights", []):
        if not isinstance(raw_insight, dict):
            continue
        observation = _normalize_text(raw_insight.get("observation"))
        connection = _normalize_text(raw_insight.get("connection"))
        implication = _normalize_text(raw_insight.get("implication"))
        timing = _normalize_text(raw_insight.get("timing"))
        parts = [observation, connection, implication, timing]
        statement = " ".join(p for p in parts if p).strip()
        if not statement:
            statement = "Unspecified insight"
        evidence_themes = [str(t).strip() for t in raw_insight.get("theme_ids", []) if t]
        insights.append(
            Insight(
                statement=statement,
                evidence_themes=evidence_themes,
                opportunity_titles=[],
            )
        )

    key_patterns = [
        p
        for p in (theme.summary or theme.name for theme in themes[:6])
        if _is_renderable(p)
    ]

    market_implications: list[str] = []
    for raw_insight in analysis.get("insights", []):
        impl = _normalize_text(raw_insight.get("implication"))
        if _is_renderable(impl):
            market_implications.append(impl)
    if not market_implications:
        for problem in problems[:4]:
            if _is_renderable(problem.statement):
                market_implications.append(
                    f"If this pattern continues, {problem.statement.lower().rstrip('.')}, "
                    f"creating demand for related infrastructure opportunities."
                )
    market_implications = [m for m in market_implications if _is_renderable(m)]

    evidence_parts = [theme.summary for theme in themes if _is_renderable(theme.summary)]
    evidence_summary = " ".join(evidence_parts) if evidence_parts else ""

    if problems:
        problem_text = "; ".join(p.statement for p in problems[:3] if _is_renderable(p.statement))
        if problem_text:
            evidence_summary += f" Key problems identified: {problem_text}."

    if not _is_renderable(evidence_summary) and signals:
        total = len(signals)
        sources = sorted({
            str(s.get("source_type", "unknown"))
            for s in signals
            if isinstance(s, dict)
        })
        evidence_summary = (
            f"The collected evidence contains {total} signals from {len(sources)} source type(s). "
            f"No detailed analyst summary was available."
        )

    if len(signals) <= 2 or not key_patterns:
        evidence_summary = _FALLBACK_EVIDENCE_MESSAGE
        key_patterns = []
        market_implications = []

    narrative = Narrative(
        key_patterns=[p for p in key_patterns if _is_renderable(p)],
        market_implications=[m for m in market_implications if _is_renderable(m)],
        evidence_summary=_FALLBACK_EVIDENCE_MESSAGE
        if not _is_renderable(evidence_summary)
        else evidence_summary.strip(),
    )

    return Synthesis(
        themes=themes,
        problems=problems,
        insights=insights,
        opportunities=[],
        narrative=narrative,
    )


def synthesize(
    signals: list[dict[str, Any]],
    intent: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
) -> Synthesis:
    """Synthesize raw signals into themes, problems, insights, opportunities and a narrative.

    When *analysis* is provided (from the LLM analysis pipeline),
    it is used as the primary source.  The existing rule-based logic
    serves as a fallback when no analysis is available.
    """
    if not signals:
        return Synthesis()

    # Use LLM analysis result when available (dict or AnalysisResult dataclass)
    if isinstance(analysis, AnalysisResult):
        analysis = {
            "themes": [asdict(t) for t in analysis.themes],
            "problems": [asdict(p) for p in analysis.problems],
            "insights": [asdict(i) for i in analysis.insights],
        }
    if isinstance(analysis, dict) and analysis.get("themes"):
        try:
            return _synthesis_from_analysis(analysis, signals, intent)
        except Exception:
            logger.exception("Failed to convert analysis result; falling back to rule-based synthesis")

    primary_long, primary_short = primary(signals, intent)
    buckets = _classify_signals(signals, primary_short)
    themes = _build_themes(buckets, primary_short, primary_long)
    problems = _build_problems(themes, primary_short)
    insights = _build_insights(themes, primary_short)
    opportunities = _build_opportunities(themes, primary_short)
    narrative = _build_narrative(themes, insights, primary_short, primary_long)

    return Synthesis(
        themes=themes,
        problems=problems,
        insights=insights,
        opportunities=opportunities,
        narrative=narrative,
    )
