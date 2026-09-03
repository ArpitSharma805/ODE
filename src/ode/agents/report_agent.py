"""Report Agent: synthesize answer-first, evidence-grounded analyst reports."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, cast

from ode.technology_resolver import TechnologyProfile
from ode.pipeline_context import PipelineContext
from ode.opportunities import Opportunity
from ode.synthesis import Synthesis, _FALLBACK_EVIDENCE_MESSAGE, _is_renderable
from ode.llm import _ollama_generate
from ode.evidence import Evidence, create_evidence_from_signal
from ode.opportunity_typology import build_maturity_prompt_additions
from ode.confidence import compute_confidence_score
from ode.validation import validate_technology_specificity

logger = logging.getLogger(__name__)


def _safe_intent(ctx: dict[str, Any]) -> dict[str, Any]:
    """Return the intent dict from a context, guarding against non-dict values."""
    intent = ctx.get("intent", {}) or {}
    return intent if isinstance(intent, dict) else {}


def _format_evidence_text(text: str) -> str:
    """Reformat pipe-delimited source strings into readable markdown bullets."""
    if "URL:" not in text:
        return text
    tokens = [t.strip() for t in text.split(" | ") if t.strip()]
    groups: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for tok in tokens:
        url = re.match(r"^URL:\s*(.+)$", tok, re.IGNORECASE)
        points = re.match(r"^(\d+)\s*points?$", tok, re.IGNORECASE)
        comments = re.match(r"^(\d+)\s*comments?$", tok, re.IGNORECASE)
        author = re.match(r"^by\s+(.+)$", tok, re.IGNORECASE)
        if not any((url, points, comments, author)):
            current = {"title": tok, "url": "", "points": "", "comments": "", "author": ""}
            groups.append(current)
        elif current is not None:
            if url:
                current["url"] = url.group(1).strip()
            elif points:
                current["points"] = points.group(1)
            elif comments:
                current["comments"] = comments.group(1)
            elif author:
                current["author"] = author.group(1).strip()
    if not groups:
        return text
    bullets: list[str] = []
    prose: list[str] = []
    for g in groups:
        if not g["url"]:
            prose.append(g["title"])
            continue
        parts = [f"**{g['title']}**"]
        if g["url"]:
            parts.append(f"URL: {g['url']}")
        if g["points"]:
            parts.append(f"{g['points']} points")
        if g["comments"]:
            parts.append(f"{g['comments']} comments")
        if g["author"]:
            parts.append(f"by {g['author']}")
        bullets.append("- " + " · ".join(parts))
    result = "\n".join(bullets)
    if prose:
        result += "\n\n" + " ".join(prose)
    return result


@dataclass
class ChatResponse:
    answer: str
    suggested_questions: list[str]


_COMMERCIAL_COURSE_PLATFORMS = re.compile(
    r"[^.!?\n]*?"
    r"\b(?:Udemy|Coursera|Pluralsight|LinkedIn Learning|Skillshare|Udacity|EdX|FutureLearn|MasterClass)\b"
    r"[^.!?\n]*?[.!?]",
    re.IGNORECASE,
)

# Matches a multi-word capitalized name (potential individual instructor) near a
# course keyword, optionally with a preceding "by / from / taught by / recommended by"
# or a trailing "by <Name>".
_NAMED_COURSE_RE = re.compile(
    r"[^.!?\n]*?"
    r"(?:\b(?:by|from|taught by|recommended by)\s+)?"
    r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+'?s?)\s+"
    r"(?:verified\s+|premium\s+|paid\s+|recommended\s+|official\s+)?"
    r"(?:course|class|bootcamp|program|tutorial|certification)\b"
    r"[^.!?\n]*?[.!?]",
    re.IGNORECASE,
)

_GENERIC_LEARNING_GUIDANCE = (
    "Use vendor-neutral, official documentation and open community learning paths."
)


def _sanitize_vendor_mentions(answer: str) -> str:
    """Remove or neutralize specific paid-course and individual-instructor mentions.

    The sanitizer is a guard rail: the prompts are already instructed to avoid
    recommending paid instructors or single commercial courses, but this ensures
    vendor-neutral, official-documentation-first guidance in the final answer.
    """
    if not answer:
        return answer
    text = _COMMERCIAL_COURSE_PLATFORMS.sub(_GENERIC_LEARNING_GUIDANCE, answer)
    text = _NAMED_COURSE_RE.sub(_GENERIC_LEARNING_GUIDANCE, text)
    # Clean up double spaces left by replacements.
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _clean_section_heading(text: str, section_name: str) -> str:
    """Strip leading markdown header markers and redundant section name labels."""
    if not text:
        return ""

    # Remove leading **Section Name:** or **Section Name** variations
    section_names = [
        section_name,
        "Opportunity Snapshot",
        "Trend Summary",
        "Market Signals",
        "Execution Roadmap",
        "Recommendation"
    ]
    # Build pattern to match any of the section names with optional bold markers
    section_pattern = "|".join(re.escape(name) for name in section_names if name)
    if section_pattern:
        # Remove **Section Name:**, **Section Name**, ### Section Name:, etc.
        cleaned = re.sub(
            rf"^(\*{{0,2}}{section_pattern}\*{{0,2}}[:\s]*)|#{{1,6}}\s*(?:{section_pattern})?[:\s\-]*",
            "",
            text.strip(),
            flags=re.IGNORECASE
        )
    else:
        # Fallback: remove any leading markdown headers
        cleaned = re.sub(rf"^#{{1,6}}\s*[:\s\-]*", "", text.strip(), flags=re.IGNORECASE)

    # Remove trailing ###, ---, or loose asterisks
    cleaned = re.sub(r"[\s#\-*]+$", "", cleaned).strip()
    return cleaned


def _linkify_cited_signals(text: str, signals: list[dict[str, Any]]) -> str:
    """Replace exact or near-match signal entity names in text with markdown links."""
    if not text or not signals:
        return text

    for s in signals:
        if not isinstance(s, dict):
            continue
        entity = str(s.get("entity", "")).strip()
        url = str(s.get("source_url", "")).strip()

        # Extract title if entity is formatted like "Title (https://...)"
        clean_title = entity.split("(")[0].strip() if "(" in entity else entity

        if url and clean_title and len(clean_title) > 8:
            # Escape regex special characters in title
            pattern = re.escape(clean_title)
            # Only replace if not already inside a markdown link
            if not re.search(rf"\[[^\]]*{pattern}[^\]]*\]", text):
                text = re.sub(
                    rf"(?<!\[)\b{pattern}\b(?!\])",
                    f"[{clean_title}]({url})",
                    text,
                    count=1,
                    flags=re.IGNORECASE
                )
    return text


def _clean_supporting_factors(text: str) -> str:
    """Strip leading bullet characters from supporting factors to prevent double-rendering."""
    if not text:
        return text

    # Remove leading •, *, or - from each line
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Remove leading bullet characters and whitespace
        cleaned = re.sub(r'^[\s\*\-•]+', '', line.strip())
        if cleaned:
            cleaned_lines.append(cleaned)

    return '\n'.join(cleaned_lines)


def _normalize_roadmap_phase(text: str) -> str:
    """Ensure each step and timeframe is on its own separate line with proper markdown bullets."""
    if not text:
        return ""
    # Replace inline step dashes with clean newlines
    normalized = re.sub(r"\s*-\s*\*\*Step\s*(\d+):", r"\n\n- **Step \1:", text)
    # Ensure sub-bullets or secondary sentences inside a step are indented or spaced
    normalized = re.sub(r"\s*-\s*\*\*Timeframe:\*\*", r"\n\n**Timeframe:**", normalized, flags=re.IGNORECASE)
    # Ensure proper spacing after "Phase X:" in headers
    normalized = re.sub(r"Phase\s*(\d+):([^\s])", r"Phase \1: \2", normalized)
    # Strip leading newlines and clean up
    return normalized.strip()


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9]+", text) if len(t) > 3]


def _opportunity_token_set(opportunities: list[Opportunity]) -> set[str]:
    tokens: set[str] = set()
    for opp in opportunities:
        for field in (opp.title, opp.category, opp.description, opp.why_now):
            tokens.update(_tokenize(str(field or "")))
    return tokens


def _answer_grounded(answer: str, opportunities: list[Opportunity]) -> bool:
    """Check that a reasonable number of answer words appear in opportunity tokens."""
    if not opportunities:
        return False
    token_set = _opportunity_token_set(opportunities)
    answer_words = _tokenize(answer)
    if not answer_words:
        return False
    matched = [w for w in answer_words if w in token_set]
    threshold = max(1, len(answer_words) // 5)
    return len(matched) >= threshold


def _display(entity: str) -> str:
    return entity.split("(")[0].strip() or entity


def _source_type_label(source_type: str | None) -> str:
    if not source_type:
        return "Signal"
    return str(source_type).replace("_mcp", "").replace("_", " ").title()


def _signal_evidence_bullets(signals: list[dict[str, Any]], max_items: int = 8) -> list[str]:
    """Convert raw signals into attributed, readable markdown bullets."""
    lines: list[str] = []
    seen: set[str] = set()
    for s in sorted(
        (sig for sig in signals if isinstance(sig, dict)),
        key=lambda x: float(x.get("evidence_quality", 0) or 0),
        reverse=True,
    ):
        metric = str(s.get("metric", ""))
        entity = _display(str(s.get("entity", "")))
        value = str(s.get("value", "")).strip()
        eq = float(s.get("evidence_quality", 0) or 0)
        source = _source_type_label(s.get("source_type"))

        if not _is_renderable(value):
            continue
        if metric in ("web_page_text", "docs_page_text"):
            continue
        if metric in ("github_commit_messages", "github_issue_titles") and len(value) > 120:
            value = value[:120] + "..."

        key = f"{entity}:{metric}:{value[:40]}"
        if key in seen:
            continue
        seen.add(key)

        if metric == "github_repo_results":
            line = f"- **{source}**: `{entity}` search returned {value} repositories (quality {eq:.0f})"
        elif metric == "github_stars":
            line = f"- **{source}**: `{entity}` — {value} stars"
        elif metric == "github_forks":
            line = f"- **{source}**: `{entity}` — {value} forks"
        elif metric == "github_commits":
            line = f"- **{source}**: `{entity}` — {value} recent commits"
        elif metric == "github_contributors":
            line = f"- **{source}**: `{entity}` — {value} contributors"
        elif metric == "github_open_issues":
            line = f"- **{source}**: `{entity}` — {value} open issues"
        elif metric in ("web_page_mentions", "docs_page_mentions"):
            line = f"- **{source}**: `{entity}` page mentions topic {value} times"
        elif metric.startswith("tavily_search_result"):
            line = f"- **{source}**: `{entity}` — {value[:120]}"
        elif metric.startswith("llm_"):
            line = f"- **LLM insight** ({metric}): {value[:120]}"
        else:
            line = f"- **{source}**: `{entity}` — {metric}: {value[:80]}"
        lines.append(line)
        if len(lines) >= max_items:
            break
    return lines


def _count_sources(signals: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = Counter()
    for s in signals:
        if isinstance(s, dict):
            counts[_source_type_label(s.get("source_type"))] += 1
    return dict(counts)


def _opportunity_evidence_bullets(
    opportunities: list[Opportunity],
    max_items: int = 10,
) -> list[str]:
    """Pull attributed evidence bullets from opportunities when raw signals are absent."""
    lines: list[str] = []
    for opp in opportunities:
        evidence = str(opp.supporting_evidence or "").strip()
        if not _is_renderable(evidence):
            continue
        for para in evidence.split("\n"):
            para = para.strip()
            if not _is_renderable(para):
                continue
            if para.startswith("-"):
                lines.append(para)
            else:
                lines.append(f"- {para}")
            if len(lines) >= max_items:
                return lines
    return lines


def _append_evidence_bullets(
    lines: list[str],
    evidence_bullets: list[str],
    *,
    max_items: int | None = None,
    fallback: str = "- No concrete signals were available to attribute.",
) -> None:
    """Append attributed evidence bullets separated by blank lines, or a fallback line."""
    if evidence_bullets:
        sliced = evidence_bullets[:max_items] if max_items is not None else evidence_bullets
        # Double newlines keep each attributed evidence item on its own line in Markdown.
        lines.append("\n\n".join(sliced))
    elif fallback:
        lines.append(fallback)


def _evidence_lines(
    signals: list[dict[str, Any]],
    opportunities: list[Opportunity],
    synthesis: Synthesis | None = None,
    max_items: int = 10,
) -> list[str]:
    """Return evidence lines, preferring a synthesized narrative over raw signals."""
    if synthesis and _is_renderable(synthesis.narrative.evidence_summary):
        lines: list[str] = []
        for para in re.split(r"(?<=[.!?])\s+", synthesis.narrative.evidence_summary.strip()):
            para = para.strip()
            if not _is_renderable(para):
                continue
            if para.startswith("-"):
                lines.append(para)
            else:
                lines.append(f"- {para}")
            if len(lines) >= max_items:
                break
        if lines:
            return [line for line in lines if _is_renderable(line)]
    bullets = _signal_evidence_bullets(signals, max_items=max_items)
    if bullets:
        return [line for line in bullets if _is_renderable(line)]
    return _opportunity_evidence_bullets(opportunities, max_items=max_items)


def _build_state_summary(context: dict[str, Any] | None) -> str:
    """Summarize the full LangGraph state for the LLM prompt."""
    ctx = context or {}
    agent_states = ctx.get("agent_states", {}) or {}
    if not isinstance(agent_states, dict):
        agent_states = {}
    trends = ctx.get("trends", []) or []
    signals = cast(list[dict[str, Any]], ctx.get("signals", []))
    intent = _safe_intent(ctx)
    if not isinstance(intent, dict):
        intent = {}
    topics = [str(t) for t in intent.get("topics", []) if t]
    query = ctx.get("query", "") or (topics[0] if topics else "")

    # Use canonical_name from TechnologyProfile if available
    from ode.technology_resolver import TechnologyResolver
    resolver = TechnologyResolver()
    resolved = resolver.resolve(query, intent)
    canonical_name = resolved.primary_profile.canonical_name if resolved.primary_profile else None
    primary = canonical_name or intent.get("primary_technology") or (topics[0] if topics else "the topic")

    intent_type = intent.get("intent", "Opportunity Discovery")

    mcp_lines = []
    for agent in ("Signal Analyst", "Trend Analyst", "Opportunity Analyst", "Report Agent"):
        calls = agent_states.get(agent, {}).get("mcp_calls", []) or []
        if not calls:
            continue
        counts = Counter(str(c.get("server", "unknown")) for c in calls if isinstance(c, dict))
        mcp_lines.append(
            f"- {agent}: " + ", ".join(f"{server} ({count})" for server, count in counts.most_common())
        )
    mcp_text = "\n".join(mcp_lines) or "No MCP calls recorded."

    cluster_counts = agent_states.get("Trend Analyst", {}).get("signal_clusters", {}) or {}
    top_clusters = sorted(cluster_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    cluster_text = "\n".join(f"- {k}: {v} signals" for k, v in top_clusters) or "- No clusters recorded."

    trend_lines = []
    for t in trends[:5]:
        entity = getattr(t, "entity", "")
        metric = getattr(t, "metric", "")
        volume = getattr(t, "signal_volume", 0)
        quality = getattr(t, "evidence_quality", 0.0)
        momentum = getattr(t, "momentum", 0.0)
        forecast = getattr(t, "forecast", {}) or {}
        summary = forecast.get("summary", "") if isinstance(forecast, dict) else ""
        line = f"- {entity} ({metric}): volume={volume}, quality={quality:.1f}, momentum={momentum:.1f}"
        if summary:
            line += f" — {summary}"
        trend_lines.append(line)
    trend_text = "\n".join(trend_lines) or "- No trends recorded."

    github_entities = [
        str(s.get("entity", ""))
        for s in signals
        if isinstance(s, dict) and str(s.get("metric", "")).startswith("github_")
    ][:8]
    tavily_entities = [
        str(s.get("entity", ""))
        for s in signals
        if isinstance(s, dict) and str(s.get("metric", "")).startswith("tavily_")
    ][:8]
    web_entities = [
        str(s.get("entity", ""))
        for s in signals
        if isinstance(s, dict) and str(s.get("metric", "")).startswith(("web_page", "docs_page"))
    ][:6]

    return (
        f"Intent: {intent_type}\n"
        f"Primary topic: {primary}\n"
        f"Topics: {', '.join(topics) if topics else 'N/A'}\n\n"
        f"MCP calls by analyst:\n{mcp_text}\n\n"
        f"Signal clusters:\n{cluster_text}\n\n"
        f"Top market trends:\n{trend_text}\n\n"
        f"GitHub examples: {', '.join(github_entities)}\n"
        f"Tavily examples: {', '.join(tavily_entities)}\n"
        f"Web/Playwright examples: {', '.join(web_entities)}"
    )


def _format_opportunity_report(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build an answer-first, evidence-grounded investment/opportunity report."""
    ctx = context or {}

    if not opportunities:
        return (
            "Status: FAILED\n"
            "Reason: Insufficient evidence\n\n"
            "No MCP evidence was strong enough to answer the question."
        )

    intent = _safe_intent(ctx)
    persona_name = ctx.get("persona_name", "Engineer")
    topics = [str(t).strip() for t in intent.get("topics", []) if t]
    signals = cast(list[dict[str, Any]], ctx.get("signals", []))
    repos = cast(list[dict[str, Any]], ctx.get("discovered_repos", []))
    trends = ctx.get("trends", []) or []

    primary = _get_canonical_technology_name(query, intent, ctx)
    trend_names = [getattr(t, "entity", "") for t in trends[:4]]
    trend_phrase = ", ".join(trend_names)

    source_counts = _count_sources(signals)
    synth = cast(Synthesis | None, ctx.get("synthesis"))
    evidence_bullets = _evidence_lines(signals, opportunities, synthesis=synth, max_items=8)
    top = opportunities[0]

    # When the pipeline runs in seed/fallback mode, raw signals are not replayed;
    # count the evidence bullets instead so the summary is not misleading.
    evidence_count = len(signals) or len(evidence_bullets)
    source_type_count = len(source_counts) or (
        len({b.split("**")[1].split("**")[0] for b in evidence_bullets if b.startswith("- **")}) or 1
    )

    lines: list[str] = []

    lines.append("## Executive Recommendation\n")
    core = (top.description or "").strip()
    snippet = core[:120] if core else "a concrete workflow gap"
    lines.append(
        f"The strongest opportunity is **{top.title}**. "
        f"It addresses {snippet}{'...' if len(core) > 120 else ''} "
        f"and is supported by {evidence_count} evidence items from {source_type_count} source types. "
        f"Priority: act now while the ecosystem is forming.\n"
    )

    lines.append("## Analysis\n")
    lines.append(
        f"{primary} is creating a window for new products because current tools do not solve "
        f"the friction described in the opportunities below. The signal is concentrated in "
        f"{trend_phrase or primary}. Each opportunity ties a specific gap to a target user and business model.\n"
    )

    lines.append("## Top Opportunities\n")
    for opp in opportunities[:3]:
        lines.append(f"### {opp.title} (Score: {opp.score:.0f}/100)")
        fields = [
            ("1. Problem Description", opp.description),
            ("2. Gap in Existing Solutions", opp.why_existing_solutions_fail),
            ("3. Who Benefits", opp.who_benefits),
            ("4. Timing Factors", opp.why_now),
            ("5. Recommended Action", opp.recommended_action),
            ("6. Business Model", opp.business_model),
            ("7. Risk Assessment", opp.risk_assessment),
        ]
        for label, value in fields:
            if value:
                lines.append(f"**{label}**")
                # Preserve paragraph/blank-line breaks in multi-line values
                for para in str(value).split("\n\n"):
                    para = para.strip()
                    if para:
                        lines.append(para)
                lines.append("")
        lines.append("")

    lines.append("## Recommendation\n")
    lines.append(
        f"Act on **{top.title}** first. It combines the clearest gap, strongest timing, and most "
        f"actionable product concept in {primary}.\n"
    )

    return "\n".join(lines)


def _format_learning_report(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a learning roadmap report: Top Skills, Why, Evidence, Sequence, Projects."""
    ctx = context or {}
    intent = _safe_intent(ctx)
    persona_name = ctx.get("persona_name", "Engineer")
    topics = [str(t).strip() for t in intent.get("topics", []) if t]
    primary = _get_canonical_technology_name(query, intent, ctx)
    signals = cast(list[dict[str, Any]], ctx.get("signals", []))
    synth = cast(Synthesis | None, ctx.get("synthesis"))
    evidence_bullets = _evidence_lines(signals, opportunities, synthesis=synth, max_items=10)

    skills = [opp for opp in opportunities if opp.title] or [
        Opportunity(
            opportunity_id=0,
            trend_id=0,
            persona_id=0,
            title=topic,
            description="",
            why_now="",
            who_benefits="",
            recommended_action="",
            supporting_evidence="",
            score=0.0,
            score_components={},
            lifecycle_state="",
            emerged_date="",
            valid_until="",
            last_score_date="",
            category="Skill",
        )
        for topic in topics[:4]
    ]
    skills = skills[:5]

    lines: list[str] = []
    lines.append(f"## Learning Roadmap: {query}\n")
    lines.append(
        f"A focused learning path for practitioners, grounded in live repository, search, and documentation signals.\n"
    )

    lines.append("### Top Skills\n")
    for idx, skill in enumerate(skills, start=1):
        score_text = f" (score {skill.score:.0f}/100)" if skill.score else ""
        lines.append(f"{idx}. **{skill.title}**{score_text}")
        why = skill.why_now or skill.description
        if why:
            lines.append(f"   - {why.strip()}")
        if skill.supporting_evidence:
            first_evidence = skill.supporting_evidence.strip().split("\n")[0]
            lines.append(f"   - {first_evidence.lstrip('- ').strip()}")
    lines.append("")

    lines.append("### Why These Skills\n")
    why_chunks = [s.why_now or s.description for s in skills if s.why_now or s.description]
    if why_chunks:
        lines.append(" ".join(why_chunks[:3]))
    else:
        lines.append(
            f"The signals around {primary} cluster on practical, production-relevant tools rather than "
            f"experimental or niche technologies. These skills show consistent repository activity, "
            f"documentation depth, and market discussion."
        )
    lines.append("")

    lines.append("### Evidence & Attribution\n")
    _append_evidence_bullets(lines, evidence_bullets)
    lines.append("")

    lines.append("### Learning Sequence\n")
    if skills:
        for idx, skill in enumerate(skills, start=1):
            action = skill.recommended_action or f"build fluency in {skill.title} fundamentals"
            lines.append(f"{idx}. **{skill.title}** — {action.strip()}")
    else:
        lines.append(f"1. Start with {primary} fundamentals.")
        lines.append(f"2. Add the most common adjacent tool from the evidence above.")
        lines.append(f"3. Build and deploy one integrated project.")
    lines.append("")

    lines.append("### Example Projects\n")
    titles = [s.title for s in skills[:3]]
    for title in titles:
        lines.append(
            f"- **{title} project**: Build a focused, production-like exercise using {title} "
            f"with tests, error handling, and observability."
        )
    if len(titles) > 1:
        lines.append(
            f"- **Integration project**: Combine {', '.join(titles)} into one deployable system for {primary}."
        )
    lines.append("")

    return "\n".join(lines)


def _format_career_report(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a career guidance report from skill/career signals."""
    ctx = context or {}
    intent = _safe_intent(ctx)
    persona_name = ctx.get("persona_name", "Engineer")
    primary = _get_canonical_technology_name(query, intent, ctx)
    signals = cast(list[dict[str, Any]], ctx.get("signals", []))
    synth = cast(Synthesis | None, ctx.get("synthesis"))
    evidence_bullets = _evidence_lines(signals, opportunities, synthesis=synth, max_items=10)
    topics = [str(t) for t in intent.get("topics", []) if t]

    skills = [opp for opp in opportunities if opp.title] or [
        Opportunity(
            opportunity_id=0,
            trend_id=0,
            persona_id=0,
            title=topic,
            description="",
            why_now="",
            who_benefits="",
            recommended_action="",
            supporting_evidence="",
            score=0.0,
            score_components={},
            lifecycle_state="",
            emerged_date="",
            valid_until="",
            last_score_date="",
            category="Skill",
        )
        for topic in topics[:4]
    ]
    skills = skills[:5]

    lines: list[str] = []
    lines.append(f"## Career Guidance: {query}\n")
    lines.append(
        f"A career-focused read on what practitioners should prioritize next, based on live market signals.\n"
    )

    lines.append("### In-Demand Skills\n")
    for idx, skill in enumerate(skills, start=1):
        score_text = f" (market signal score {skill.score:.0f}/100)" if skill.score else ""
        lines.append(f"{idx}. **{skill.title}**{score_text}")
        if skill.why_now:
            lines.append(f"   - {skill.why_now.strip()}")
    lines.append("")

    lines.append("### Why These Skills Matter\n")
    why_chunks = [s.why_now or s.description for s in skills if s.why_now or s.description]
    if why_chunks:
        lines.append(" ".join(why_chunks[:3]))
    else:
        lines.append(
            f"Repository activity, documentation investment, and market discussion around {primary} "
            f"point to steady employer demand for practitioners with these capabilities."
        )
    lines.append("")

    lines.append("### Market Evidence\n")
    _append_evidence_bullets(lines, evidence_bullets)
    lines.append("")

    lines.append("### Career Steps\n")
    if skills:
        for idx, skill in enumerate(skills, start=1):
            action = skill.recommended_action or f"build demonstrable {skill.title} experience"
            lines.append(f"{idx}. **{skill.title}** — {action.strip()}")
    else:
        lines.append(f"1. Build a portfolio project in {primary}.")
        lines.append("2. Contribute to or review active open-source repositories in the space.")
        lines.append("3. Document the work publicly and tie it to measurable outcomes.")
    lines.append("")

    lines.append("### Portfolio Projects\n")
    titles = [s.title for s in skills[:3]]
    for title in titles:
        lines.append(
            f"- **{title} portfolio piece**: A public repo that solves a real problem with {title}, "
            f"including README, tests, and deployment instructions."
        )
    if len(titles) > 1:
        lines.append(
            f"- **End-to-end project**: Combine {', '.join(titles)} into one system you can demo in an interview."
        )
    lines.append("")

    return "\n".join(lines)


def _format_comparison_report(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a technology comparison/evaluation report."""
    ctx = context or {}
    intent = _safe_intent(ctx)
    topics = [str(t).strip() for t in intent.get("topics", []) if t]
    primary = _get_canonical_technology_name(query, intent, ctx)
    signals = cast(list[dict[str, Any]], ctx.get("signals", []))

    # Build a small set of candidate technologies from opportunities + signals.
    candidates: list[str] = []
    for opp in opportunities:
        title = _display(opp.title).split()[0]
        if title and title.lower() not in {c.lower() for c in candidates}:
            candidates.append(title)
    for s in signals:
        if not isinstance(s, dict):
            continue
        entity = _display(str(s.get("entity", "")))
        first = entity.split()[0] if entity else ""
        if first and first.lower() not in {c.lower() for c in candidates} and first.lower() != primary.lower():
            candidates.append(first)
    candidates = [c for c in candidates if c.lower() not in {"model", "context", "protocol", "documentation"}][:4]
    if primary not in candidates:
        candidates.insert(0, primary)

    # Gather per-candidate signal aggregates.
    def _candidate_stats(name: str) -> dict[str, Any]:
        name_lower = name.lower()
        sigs = [
            s for s in signals
            if isinstance(s, dict) and name_lower in f"{s.get('entity', '')} {s.get('metric', '')} {s.get('value', '')}".lower()
        ]
        stars = 0
        forks = 0
        repos = 0
        mentions = 0
        for s in sigs:
            metric = str(s.get("metric", ""))
            value = str(s.get("value", ""))
            try:
                if metric == "github_stars":
                    stars = max(stars, int(value))
                elif metric == "github_forks":
                    forks = max(forks, int(value))
                elif metric == "github_repo_results":
                    repos = max(repos, int(value))
                elif metric in ("web_page_mentions", "docs_page_mentions"):
                    mentions = max(mentions, int(value))
            except (ValueError, TypeError):
                pass
        return {"stars": stars, "forks": forks, "repos": repos, "mentions": mentions, "count": len(sigs)}

    lines: list[str] = []
    lines.append(f"## Technology Evaluation: {primary}\n")
    lines.append(
        f"An evidence-based comparison centered on **{primary}** and the most relevant alternatives.\n"
    )

    lines.append("### Comparison Snapshot\n")
    primary_stats = _candidate_stats(primary)
    # Only render primary if it has meaningful data
    if primary_stats["stars"] > 0 or primary_stats["mentions"] > 0 or primary_stats["count"] > 0:
        lines.append(
            f"- **{primary}** — {primary_stats['stars']} stars, {primary_stats['forks']} forks, "
            f"{primary_stats['mentions']} web/doc mentions, {primary_stats['count']} relevant signals."
        )
    for alt in candidates[1:4]:
        stats = _candidate_stats(alt)
        # Only render alternative if it has meaningful data
        if stats["stars"] > 0 or stats["mentions"] > 0 or stats["count"] > 0:
            lines.append(
                f"- **{alt}** — {stats['stars']} stars, {stats['forks']} forks, "
                f"{stats['mentions']} web/doc mentions, {stats['count']} relevant signals."
            )
    # If no meaningful data, add a note
    if not (primary_stats["stars"] > 0 or primary_stats["mentions"] > 0 or primary_stats["count"] > 0):
        lines.append("- Insufficient signal data to render quantitative comparison metrics.")
    lines.append("")

    lines.append("### Evaluation Dimensions\n")
    lines.append(
        "- **Ecosystem activity**: GitHub stars, forks, and contributor velocity indicate community depth."
    )
    lines.append(
        "- **Documentation maturity**: Web and docs-page mentions signal how much learning material is available."
    )
    lines.append(
        "- **Market discussion**: Tavily and LLM-derived insights show whether the technology is a current decision point."
    )
    lines.append(
        "- **Risk**: Recency of commits, open issue volume, and the breadth of production usage."
    )
    lines.append("")

    lines.append("### Evidence\n")
    synth = cast(Synthesis | None, ctx.get("synthesis"))
    evidence_bullets = _evidence_lines(signals, opportunities, synthesis=synth, max_items=10)
    _append_evidence_bullets(lines, evidence_bullets)
    lines.append("")

    lines.append("### Verdict\n")
    if primary_stats["stars"] or primary_stats["mentions"]:
        lines.append(
            f"**{primary}** shows measurable ecosystem activity. Choose it if the team already operates in its "
            f"ecosystem and the project values stability and available talent. Compare against "
            f"{', '.join(candidates[1:4])} when adoption risk, licensing, or specific runtime constraints matter."
        )
    else:
        lines.append(
            f"Live signals for **{primary}** are sparse. Treat any adoption decision as experimental and "
            f"validate with a small proof-of-concept before committing."
        )
    lines.append("")

    lines.append("### Alternatives to Consider\n")
    for alt in candidates[1:4]:
        lines.append(f"- {alt}")
    if len(candidates) == 1:
        lines.append("- No clear alternatives appeared in the signal set.")
    lines.append("")

    return "\n".join(lines)


def _strip_markdown_fences(text: str) -> str:
    """Remove leading ```markdown and trailing ``` code fences from an LLM response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _grounded_response(
    answer: str,
    suggested_questions: list[str],
    opportunities: list[Opportunity],
    formatted: str,
) -> ChatResponse:
    """Return a grounded ChatResponse or the formatted deterministic report."""
    if _answer_grounded(answer, opportunities):
        return ChatResponse(answer=answer, suggested_questions=suggested_questions)
    return ChatResponse(answer=formatted, suggested_questions=suggested_questions)


def _strip_quality_markers(bullets: list[str]) -> list[str]:
    """Remove trailing quality scores from evidence bullets for human reports."""
    return [re.sub(r"\s*\(quality\s+\d+\)$", "", b).strip() for b in bullets]


def _confidence_label(opportunities: list[Opportunity], context: dict[str, Any]) -> tuple[str, str]:
    """Return a human confidence label and one-line rationale."""
    signals = cast(list[dict[str, Any]], context.get("signals", []))
    source_types = {
        str(s.get("source_type", "")).split("_")[0]
        for s in signals
        if isinstance(s, dict) and s.get("source_type")
    }
    top = opportunities[0] if opportunities else None
    score = top.score if top else 0.0
    evidence_count = len(signals) or len(opportunities)
    source_count = len(source_types) or 1

    # Updated thresholds based on signal count
    if evidence_count == 0:
        return "None", "No concrete signals available; this recommendation is speculative."
    elif evidence_count <= 3:
        return "Low", f"Limited evidence ({evidence_count} signals); treat as directional."
    elif evidence_count <= 8:
        return "Medium", f"Moderate evidence from {evidence_count} signals across {source_count} source types."
    elif evidence_count <= 15:
        return "High", f"Strong evidence from {evidence_count} signals across {source_count} source types."
    else:
        return "Very High", f"Very strong evidence from {evidence_count} signals across {source_count} source types."


def _market_trend_narrative(opp: Opportunity) -> str:
    """Translate score components into a plain-language trend status."""
    components = opp.score_components or {}
    momentum = float(components.get("momentum", 0) or 0)
    growth = float(components.get("growth", 0) or 0)
    volume = int(components.get("signal_volume", 0) or 0)

    if momentum >= 25 and (growth >= 8 or volume >= 10):
        return "growing"
    if momentum >= 15 or volume >= 5:
        return "stable and active"
    if volume > 0:
        return "emerging"
    return "unclear"


def _llm_learning_roadmap(
    query: str,
    primary: str,
    skills: list[Opportunity],
) -> tuple[list[str], dict[str, Any]]:
    """Use Ollama to generate an ordered learning roadmap and project ideas."""
    titles = [s.title for s in skills if s.title]
    prompt = (
        "You are a senior technical mentor. Given a user query, a primary skill, and a list of related skills, "
        "return valid JSON with keys:\n"
        '- "roadmap": an ordered list of 6-10 concise learning steps (short phrases)\n'
        '- "projects": an object with "beginner", "intermediate", "advanced", and "integration" project ideas\n'
        '- "risks": a list of 2-3 short risks or tradeoffs for learning this path\n'
        '- "alternatives": a list of 2-3 alternative skills or paths to consider\n\n'
        "Rules:\n"
        "- Do not recommend specific paid individual instructors or single commercial courses "
        "unless the query explicitly asks for course recommendations.\n"
        "- Recommend vendor-neutral, official documentation and open community learning paths.\n"
        "- The roadmap should progress from fundamentals to advanced topics.\n"
        "- Do not include scores, numbers, or technology-specific trivia.\n"
        "- Do not include course content, table of contents, or transcripts.\n"
        "- Focus on practical skills and concepts, not course content.\n"
        "- The roadmap steps should be actionable learning milestones.\n"
        "- Group steps logically: start with foundations, then core concepts, then advanced topics.\n"
        "Use the related skills as milestones where appropriate.\n\n"
        f"Query: {query}\n"
        f"Primary skill: {primary}\n"
        f"Related skills: {', '.join(titles)}\n"
    )
    raw = _ollama_generate(prompt, format="json")
    if not raw:
        return [], {}
    try:
        parsed = json.loads(raw.strip())
        roadmap = [str(s).strip() for s in parsed.get("roadmap", []) if str(s).strip()]
        projects = {
            str(k): str(v)
            for k, v in parsed.get("projects", {}).items()
            if isinstance(v, str)
        }
        risks = [str(r) for r in parsed.get("risks", []) if r]
        alternatives = [str(a) for a in parsed.get("alternatives", []) if a]
        return roadmap, {"projects": projects, "risks": risks, "alternatives": alternatives}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return [], {}


def _fallback_roadmap(primary: str, skills: list[Opportunity]) -> tuple[list[str], dict[str, Any]]:
    """Deterministic fallback roadmap when Ollama is unavailable."""
    roadmap: list[str] = []
    for skill in skills[:5]:
        if skill.title and skill.title.lower() != primary.lower():
            roadmap.append(f"{skill.title} fundamentals")
    if len(skills) > 1:
        roadmap.append(f"Integrate {primary} with {', '.join(s.title for s in skills[1:3])}")
    roadmap.append(f"Build production-ready {primary} systems")
    roadmap.append(f"Deep specialization in {primary}")

    projects = {
        "beginner": f"A small {primary} project with tests and documentation.",
        "intermediate": f"A service that combines {primary} with one adjacent tool from the roadmap.",
        "advanced": f"A distributed or observable {primary} system with deployment automation.",
        "integration": f"An end-to-end project that uses {primary} together with the other skills in this roadmap.",
    }
    risks = [
        f"The demand for {primary} may vary by region and company size.",
        f"Tools around {primary} change quickly; focus on fundamentals before chasing every new library.",
    ]
    alternatives = [s.title for s in skills[1:3] if s.title] or ["a complementary language or framework"]
    return roadmap, {"projects": projects, "risks": risks, "alternatives": alternatives}


def _structured_roadmap(roadmap: list[str], primary: str) -> str:
    """Render a structured learning roadmap with phases and connectors."""
    if not roadmap:
        return "- No clear learning path available from the current evidence."

    # Group roadmap steps into logical phases
    phases = _group_into_phases(roadmap, primary)

    lines: list[str] = []
    for idx, (phase_name, steps) in enumerate(phases):
        lines.append(f"## Phase {idx + 1}: {phase_name}")
        for step in steps:
            lines.append(f"✅ {step}")
        if idx < len(phases) - 1:
            lines.append("")
            lines.append("        ↓")
            lines.append("")

    return "\n".join(lines)


def _group_into_phases(roadmap: list[str], primary: str) -> list[tuple[str, list[str]]]:
    """Group roadmap steps into logical learning phases."""
    if len(roadmap) <= 4:
        return [("Foundations", roadmap)]

    # Simple heuristic grouping based on step count
    phases: list[tuple[str, list[str]]] = []
    total_steps = len(roadmap)

    if total_steps <= 6:
        phases.append(("Foundations", roadmap[:3]))
        phases.append(("Core Concepts", roadmap[3:]))
    elif total_steps <= 9:
        phases.append(("Foundations", roadmap[:3]))
        phases.append(("Core Concepts", roadmap[3:6]))
        phases.append(("Advanced", roadmap[6:]))
    else:
        phases.append(("Foundations", roadmap[:3]))
        phases.append(("Core Concepts", roadmap[3:6]))
        phases.append(("Intermediate", roadmap[6:9]))
        phases.append(("Advanced", roadmap[9:]))

    return phases


def _ascii_roadmap(roadmap: list[str]) -> str:
    """Render an ordered learning roadmap as an ASCII tree."""
    if not roadmap:
        return "- No clear learning path available from the current evidence."
    lines: list[str] = []
    for idx, step in enumerate(roadmap):
        lines.append(f"{step}")
        if idx < len(roadmap) - 1:
            lines.append("      ↓")
    return "\n".join(lines)


def _format_learning_report_human(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a polished learning roadmap report without evidence dumps."""
    ctx = context or {}
    intent = _safe_intent(ctx)

    # Use precomputed pipeline_artifacts if available
    pipeline_artifacts = ctx.get("pipeline_artifacts", {})
    if pipeline_artifacts:
        logger.info("Using precomputed pipeline_artifacts for learning report")

    skills = [opp for opp in opportunities if opp.title] or [
        Opportunity(
            opportunity_id=0,
            trend_id=0,
            persona_id=0,
            title=topic,
            description="",
            why_now="",
            who_benefits="",
            recommended_action="",
            supporting_evidence="",
            score=0.0,
            score_components={},
            lifecycle_state="",
            emerged_date="",
            valid_until="",
            last_score_date="",
            category="Skill",
        )
        for topic in intent.get("topics", [])[:4]
    ]
    skills = skills[:5]
    primary = skills[0].title if skills else (intent.get("primary_technology") or query)
    confidence, confidence_rationale = _confidence_label(opportunities, ctx)

    # Try LLM for dynamic roadmap; fall back to deterministic sequence.
    roadmap, extras = _llm_learning_roadmap(query, primary, skills)
    if not roadmap:
        roadmap, extras = _fallback_roadmap(primary, skills)
    if not isinstance(extras, dict):
        extras = {}
    projects: dict[str, Any] = cast(dict[str, Any], extras.get("projects", {}))
    risks: list[Any] = cast(list[Any], extras.get("risks", []))
    alternatives: list[Any] = cast(list[Any], extras.get("alternatives", []))

    lines: list[str] = []

    # Title
    lines.append(f"# Learning Roadmap: {primary}\n")

    # Learning Outcome (replaces Executive Summary)
    lines.append("## Learning Outcome\n")
    lines.append("By completing this roadmap, you will be able to:\n")
    lines.append(f"- Build {primary} applications from scratch")
    lines.append(f"- Manage state effectively in {primary} projects")
    lines.append(f"- Build reusable components with {primary}")
    lines.append(f"- Work with modern {primary} tooling and ecosystem")
    lines.append(f"- Prepare for professional development with {primary}\n")

    # Structured Roadmap with Phases
    lines.append("## Learning Roadmap\n")
    lines.append(_structured_roadmap(roadmap, primary))
    lines.append("")

    # Example Projects
    lines.append("## Example Projects\n")
    for level in ("beginner", "intermediate", "advanced", "integration"):
        idea = projects.get(level)
        if idea:
            lines.append(f"- **{level.title()}**: {idea}")
    if not projects:
        lines.append(f"- **Beginner**: A focused, tested {primary} exercise.")
        lines.append(f"- **Intermediate**: Combine {primary} with one adjacent tool from the roadmap.")
        lines.append(f"- **Advanced**: Build and deploy a production-like {primary} system.")
    lines.append("")

    # Risks & Tradeoffs (simplified)
    lines.append("## Risks & Tradeoffs\n")
    if risks:
        for risk in risks[:3]:
            lines.append(f"- {risk}")
    else:
        lines.append(f"- The demand for {primary} can vary by company size and region.")
        lines.append(f"- New tooling around {primary} appears frequently; focus on fundamentals first.")
    if alternatives:
        lines.append(f"- **Alternatives to consider**: {', '.join(alternatives[:3])}")
    lines.append("")

    # Final Recommendation (simplified)
    lines.append("## Final Recommendation\n")
    lines.append(
        f"Start with **{primary}** fundamentals and build one small production-like project. "
        f"Use the roadmap above to progress from beginner to advanced, and validate your learning with the example projects.\n"
    )

    return "\n".join(lines)


def _format_career_report_human(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a polished career guidance report without evidence dumps."""
    ctx = context or {}
    intent = _safe_intent(ctx)
    primary = _get_canonical_technology_name(query, intent, ctx)

    # Use precomputed pipeline_artifacts if available
    pipeline_artifacts = ctx.get("pipeline_artifacts", {})
    if pipeline_artifacts:
        logger.info("Using precomputed pipeline_artifacts for career report")

    skills = [opp for opp in opportunities if opp.title] or []
    skills = skills[:5]
    primary = skills[0].title if skills else query
    confidence, confidence_rationale = _confidence_label(opportunities, ctx)

    lines: list[str] = []

    # Title
    lines.append(f"# Career Guidance: {primary}\n")

    # Career Outcome (replaces Executive Summary)
    lines.append("## Career Outcome\n")
    lines.append("By following this career path, you will be able to:\n")
    lines.append(f"- Position yourself for roles requiring {primary} expertise")
    lines.append(f"- Demonstrate practical {primary} experience through projects")
    lines.append(f"- Stay competitive in the {primary} job market")
    lines.append(f"- Build a portfolio that showcases {primary} capabilities")
    lines.append(f"- Navigate career transitions with {primary} skills\n")

    # In-Demand Skills
    lines.append("## In-Demand Skills\n")
    for idx, skill in enumerate(skills, start=1):
        lines.append(f"{idx}. **{skill.title}**")
        if skill.why_now:
            lines.append(f"   - {skill.why_now.strip()}")
    lines.append("")

    # Career Progression
    lines.append("## Career Progression\n")
    lines.append(f"Starting with **{primary}** can lead to:\n")
    lines.append(f"- **Entry Level**: Junior {primary} Developer/Engineer")
    lines.append(f"- **Mid Level**: {primary} Specialist/Lead")
    lines.append(f"- **Senior Level**: Principal {primary} Architect/Engineer")
    lines.append(f"- **Leadership**: {primary} Team Lead/Engineering Manager\n")

    # Skill Development Focus
    lines.append("## Skill Development Focus\n")
    lines.append(f"Prioritize these {primary} capabilities:\n")
    for skill in skills[:3]:
        if skill.recommended_action:
            lines.append(f"- {skill.recommended_action.strip()}")
    if not any(skill.recommended_action for skill in skills[:3]):
        lines.append(f"- Master {primary} fundamentals and core concepts")
        lines.append(f"- Build production-ready {primary} applications")
        lines.append(f"- Learn {primary} ecosystem tools and frameworks")
    lines.append("")

    # Final Recommendation
    lines.append("## Final Recommendation\n")
    lines.append(
        f"Focus on **{primary}** as your primary skill while building complementary abilities. "
        f"Build demonstrable projects, contribute to open source, and stay current with {primary} trends.\n"
    )

    return "\n".join(lines)


def _format_synthesis_report(
    query: str,
    opportunities: list[Opportunity],
    synth: Synthesis,
    context: dict[str, Any],
) -> str:
    """Build an analyst-style report grounded in explicit synthesis."""
    signals = cast(list[dict[str, Any]], context.get("signals", []))
    # When the LLM analysis pipeline produced structured themes, use its
    # synthesized evidence summary instead of raw signal bullets.
    if context.get("analysis"):
        evidence_bullets: list[str] = []
    else:
        evidence_bullets = _strip_quality_markers(_signal_evidence_bullets(signals, max_items=4))

    top = opportunities[0] if opportunities else None
    if top is None:
        return f"## Opportunity Analysis: {query}\n\nNo concrete opportunities were found. Try broadening the query or adding a specific technology/domain."

    confidence, confidence_rationale = _confidence_label(opportunities, context)
    primary = top.title
    trend_status = _market_trend_narrative(top)

    lines: list[str] = []
    lines.append(f"## Opportunity Analysis: {query}\n")
    lines.append(
        f"The strongest opportunity is **{primary}**. "
        f"The trend is **{trend_status}** and the evidence supports **{confidence}** confidence. "
        f"{confidence_rationale}\n"
    )

    key_patterns = [p for p in synth.narrative.key_patterns if _is_renderable(p)]
    if key_patterns:
        lines.append("## Key Patterns Observed\n")
        for pattern in key_patterns[:6]:
            formatted = _format_evidence_text(pattern.strip())
            if "\n" in formatted:
                lines.extend(line for line in formatted.splitlines() if _is_renderable(line))
            else:
                lines.append(f"- {formatted}")
        lines.append("")

    market_implications = [m for m in synth.narrative.market_implications if _is_renderable(m)]
    if market_implications:
        lines.append("## Market Implications\n")
        for implication in market_implications[:5]:
            lines.append(f"- {implication.strip()}")
        lines.append("")

    lines.append("## Timing Factors\n")
    if top.why_now:
        lines.append(f"{top.why_now.strip()}")
    else:
        lines.append(
            "The signal set shows growing activity, but timing details are sparse. "
            "Validate the trend with a small experiment before committing resources."
        )
    lines.append("")

    lines.append("## What Should I Do Next?\n")
    if top.recommended_action:
        lines.append(f"{top.recommended_action.strip()}")
    else:
        lines.append(
            f"Run a focused experiment around {primary}: define a hypothesis, build the smallest proof-of-concept, "
            f"and measure traction with the target users."
        )
    lines.append("")

    if opportunities:
        lines.append("## Opportunity Analysis\n")
        for opp in opportunities[:4]:
            lines.append(f"### {opp.title} (Score: {opp.score:.0f}/100)")
            if opp.description:
                lines.append(f"**Problem:** {opp.description.strip()}")
            if opp.who_benefits:
                lines.append(f"**Users affected:** {opp.who_benefits.strip()}")
            if opp.why_existing_solutions_fail:
                lines.append(f"**Why existing solutions fall short:** {opp.why_existing_solutions_fail.strip()}")
            if opp.why_now:
                lines.append(f"**Why now:** {opp.why_now.strip()}")
            if opp.recommended_action:
                lines.append(f"**Potential solution:** {opp.recommended_action.strip()}")
            if opp.risk_assessment:
                lines.append(f"**Risks:** {opp.risk_assessment.strip()}")
            if opp.business_model:
                lines.append(f"**Business model:** {opp.business_model.strip()}")
            lines.append("")

    lines.append("## Evidence Summary\n")
    if _is_renderable(synth.narrative.evidence_summary):
        formatted = _format_evidence_text(synth.narrative.evidence_summary.strip())
        for para in formatted.split("\n"):
            para = para.strip()
            if _is_renderable(para):
                lines.append(para)
    else:
        lines.append(_FALLBACK_EVIDENCE_MESSAGE)
    lines.append("")

    if evidence_bullets:
        lines.append("### Attributed evidence\n")
        _append_evidence_bullets(lines, evidence_bullets)
        lines.append("")

    lines.append("## Final Recommendation\n")
    lines.append(
        f"Act on **{primary}** first. It sits at the strongest intersection of observed patterns, "
        f"market timing, and an actionable product concept. Use the opportunity analysis above to align "
        f"stakeholders and define measurable next steps.\n"
    )

    return "\n".join(lines)


def _format_opportunity_report_human(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a human-friendly opportunity/business report."""
    ctx = context or {}

    # Use precomputed pipeline_artifacts if available
    pipeline_artifacts = ctx.get("pipeline_artifacts", {})
    if pipeline_artifacts:
        logger.info("Using precomputed pipeline_artifacts for opportunity report")

    synth = cast(Synthesis | None, ctx.get("synthesis"))
    if synth:
        return _format_synthesis_report(query, opportunities, synth, ctx)

    signals = cast(list[dict[str, Any]], ctx.get("signals", []))
    evidence_bullets = _strip_quality_markers(_evidence_lines(signals, opportunities, synthesis=synth, max_items=8))

    top = opportunities[0] if opportunities else None
    if top is None:
        return f"## Opportunity Analysis: {query}\n\nNo concrete opportunities were found. Try broadening the query or adding a specific technology/domain."

    confidence, confidence_rationale = _confidence_label(opportunities, ctx)
    primary = top.title
    trend_status = _market_trend_narrative(top)

    lines: list[str] = []
    lines.append(f"## Opportunity Analysis: {query}\n")
    lines.append(
        f"The strongest opportunity is **{primary}**. "
        f"The trend is **{trend_status}** and the evidence supports **{confidence}** confidence. "
        f"{confidence_rationale}\n"
    )

    lines.append("## Timing Factors\n")
    if top.why_now:
        lines.append(f"{top.why_now.strip()}")
    else:
        lines.append(
            "The signal set shows growing activity, but timing details are sparse. "
            "Validate the trend with a small experiment before committing resources."
        )
    lines.append("")

    lines.append("## What Should I Do Next?\n")
    if top.recommended_action:
        lines.append(f"{top.recommended_action.strip()}")
    else:
        lines.append(
            f"Run a focused experiment around {primary}: define a hypothesis, build the smallest proof-of-concept, "
            f"and measure traction with the target users."
        )
    lines.append("")

    if len(opportunities) > 1:
        lines.append("## Other Opportunities to Watch\n")
        for opp in opportunities[1:4]:
            lines.append(f"### {opp.title}")
            if opp.description:
                lines.append(opp.description.strip())
            if opp.why_now:
                lines.append(f"- **Why now?** {opp.why_now.strip()}")
            if opp.recommended_action:
                lines.append(f"- **Next step:** {opp.recommended_action.strip()}")
            if opp.who_benefits:
                lines.append(f"- **Who benefits:** {opp.who_benefits.strip()}")
            if opp.business_model:
                lines.append(f"- **Business model:** {opp.business_model.strip()}")
            if opp.risk_assessment:
                lines.append(f"- **Risk:** {opp.risk_assessment.strip()}")
            lines.append("")

    lines.append("## Final Recommendation\n")
    lines.append(
        f"Act on **{primary}** first. The evidence points to a clear gap, the timing is favorable, and the "
        f"recommended action provides a concrete path forward. Use the supporting evidence to align stakeholders and "
        f"define measurable next steps.\n"
    )

    return "\n".join(lines)


def _get_canonical_technology_name(query: str, intent: dict, context: dict | None = None) -> str:
    """Get the canonical technology name from TechnologyProfile.

    Args:
        query: Original user query
        intent: Intent dictionary
        context: Pipeline context with resolved technology

    Returns:
        Canonical technology name or fallback
    """
    # Try to get from PipelineContext first
    if context and isinstance(context, dict):
        resolved = context.get("resolved")
        if resolved and isinstance(resolved, dict):
            profile = resolved.get("primary_profile")
            if profile and hasattr(profile, "canonical_name"):
                return profile.canonical_name

    # Fallback to TechnologyResolver
    from ode.technology_resolver import TechnologyResolver
    resolver = TechnologyResolver()
    resolved = resolver.resolve(query, intent)
    if resolved.primary_profile:
        return resolved.primary_profile.canonical_name

    # Final fallback to intent
    topics = [str(t) for t in intent.get("topics", []) if t]
    return intent.get("primary_technology") or (topics[0] if topics else "the topic")


def _format_comparison_report_human(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> str:
    """Build a human-friendly technology comparison/evaluation report."""
    ctx = context or {}
    intent = _safe_intent(ctx)
    primary = _get_canonical_technology_name(query, intent, ctx)
    signals = cast(list[dict[str, Any]], ctx.get("signals", []))

    candidates: list[str] = []
    for opp in opportunities:
        title = _display(opp.title).split()[0]
        if title and title.lower() not in {c.lower() for c in candidates}:
            candidates.append(title)
    for s in signals:
        if not isinstance(s, dict):
            continue
        entity = _display(str(s.get("entity", "")))
        first = entity.split()[0] if entity else ""
        if first and first.lower() not in {c.lower() for c in candidates} and first.lower() != primary.lower():
            candidates.append(first)
    candidates = [c for c in candidates if c.lower() not in {"model", "context", "protocol", "documentation"}][:4]
    if primary not in candidates:
        candidates.insert(0, primary)

    def _candidate_stats(name: str) -> dict[str, Any]:
        name_lower = name.lower()
        sigs = [
            s for s in signals
            if isinstance(s, dict) and name_lower in f"{s.get('entity', '')} {s.get('metric', '')} {s.get('value', '')}".lower()
        ]
        stars = forks = repos = mentions = 0
        for s in sigs:
            metric = str(s.get("metric", ""))
            value = str(s.get("value", ""))
            try:
                if metric == "github_stars":
                    stars = max(stars, int(value))
                elif metric == "github_forks":
                    forks = max(forks, int(value))
                elif metric == "github_repo_results":
                    repos = max(repos, int(value))
                elif metric in ("web_page_mentions", "docs_page_mentions"):
                    mentions = max(mentions, int(value))
            except (ValueError, TypeError):
                pass
        return {"stars": stars, "forks": forks, "repos": repos, "mentions": mentions, "count": len(sigs)}

    synth = cast(Synthesis | None, ctx.get("synthesis"))
    evidence_bullets = _strip_quality_markers(_evidence_lines(signals, opportunities, synthesis=synth, max_items=8))
    confidence, confidence_rationale = _confidence_label(opportunities, ctx)
    primary_stats = _candidate_stats(primary)

    lines: list[str] = []
    lines.append(f"## Technology Evaluation: {query}\n")

    # Only render metrics if there's meaningful data
    if primary_stats["stars"] > 0 or primary_stats["mentions"] > 0 or primary_stats["count"] > 0:
        lines.append(
            f"This comparison centers on **{primary}**. Live signals show "
            f"{primary_stats['stars']} stars, {primary_stats['forks']} forks, and {primary_stats['mentions']} web/doc mentions. "
            f"Confidence: **{confidence}**. {confidence_rationale}\n"
        )
    else:
        lines.append(
            f"This comparison centers on **{primary}**. Confidence: **{confidence}**. {confidence_rationale}\n"
        )

    lines.append("## Why Now?\n")
    if primary_stats["count"] or opportunities:
        lines.append(
            f"There is active discussion and repository activity around {primary} right now. "
            f"The window to make an informed choice is open, but signals change quickly."
        )
    else:
        lines.append(
            "Live signals are sparse. Treat any adoption decision as experimental and validate with a proof-of-concept."
        )
    lines.append("")

    lines.append("## Comparison Snapshot\n")
    has_meaningful_data = False
    for name in candidates[:4]:
        stats = _candidate_stats(name)
        if stats["stars"] > 0 or stats["mentions"] > 0 or stats["count"] > 0:
            has_meaningful_data = True
            lines.append(
                f"- **{name}** — {stats['stars']} stars, {stats['forks']} forks, "
                f"{stats['mentions']} web/doc mentions, {stats['count']} relevant signals."
            )
    if not has_meaningful_data:
        lines.append("- Insufficient signal data to render quantitative comparison metrics.")
    lines.append("")

    lines.append("## What Should I Do Next?\n")
    if primary_stats["stars"] or primary_stats["mentions"]:
        lines.append(
            f"If your team already operates in the **{primary}** ecosystem and values stability plus available talent, "
            f"start there. Compare against {', '.join(candidates[1:4])} if adoption risk, licensing, or specific runtime "
            f"constraints matter."
        )
    else:
        lines.append(
            f"Run a small proof-of-concept with **{primary}** before committing. Measure integration effort, "
            f"performance, and team ramp-up time against {', '.join(candidates[1:4])} using the same workload."
        )
    lines.append("")

    lines.append("## Final Recommendation\n")
    lines.append(
        f"**{primary}** has the strongest signal footprint in your query context. Pair that evidence with "
        f"your team's constraints and run a time-boxed pilot before scaling.\n"
    )

    return "\n".join(lines)


def _validate_technology_specificity(
    response: str,
    primary_technology: str,
    profile: TechnologyProfile | None = None,
) -> tuple[bool, str]:
    """Validate that the response contains technology-specific vocabulary.

    This is now a wrapper around the new graduated validation system.
    """
    # Use the new validation module with the profile if available
    return validate_technology_specificity(response, primary_technology, profile)


def generate_chat_response(
    query: str,
    opportunities: list[Opportunity],
    context: dict[str, Any] | None = None,
) -> ChatResponse:
    """Produce an evidence-grounded recommendation using TechnologyProfile and PipelineContext."""
    ctx = context or {}
    intent = _safe_intent(ctx)

    # Get PipelineContext if available
    pipeline_ctx = ctx.get("context") if isinstance(ctx, dict) else None
    profile = pipeline_ctx.profile if pipeline_ctx else None

    if intent.get("needs_clarification"):
        question = str(intent.get("clarifying_question", "Could you clarify your question?")).strip()
        options = intent.get("clarification_options", [])
        if isinstance(options, list):
            answer = question + "\n\n" + "\n".join(f"- {opt}" for opt in options if opt)
        else:
            answer = question
        return ChatResponse(answer=answer, suggested_questions=[str(o) for o in options if o])

    intent_type = intent.get("intent", "Opportunity Discovery")

    # Generate formatted report for all intents as fallback
    if intent_type == "Skill Learning":
        formatted = _format_learning_report_human(query, opportunities, ctx)
    elif intent_type == "Career Development":
        formatted = _format_career_report_human(query, opportunities, ctx)
    elif intent_type == "Technology Evaluation":
        formatted = _format_comparison_report_human(query, opportunities, ctx)
    else:
        formatted = _format_opportunity_report_human(query, opportunities, ctx)

    # Use TechnologyProfile-based report generation for ALL intents
    if profile:
        return _generate_profile_based_report(query, opportunities, intent, profile, pipeline_ctx, formatted)
    else:
        # Fallback to formatted report if no profile available
        logger.info("No TechnologyProfile available, using formatted report")
        return ChatResponse(answer=_sanitize_vendor_mentions(formatted), suggested_questions=[])


def _signal_to_dict(signal: Any) -> dict[str, Any]:
    """Convert a Signal object to a dict for compatibility."""
    if isinstance(signal, dict):
        return signal
    if hasattr(signal, '__dict__'):
        return signal.__dict__
    return {"entity": str(signal), "value": "", "source_type": "unknown"}


def _generate_profile_based_report(
    query: str,
    opportunities: list[Opportunity],
    intent: dict[str, Any],
    profile: TechnologyProfile,
    pipeline_ctx: PipelineContext | None,
    formatted: str,
) -> ChatResponse:
    """Generate report using TechnologyProfile and evidence-driven section-by-section approach."""

    intent_type = intent.get("intent", "Opportunity Discovery")
    state_summary = _build_state_summary(pipeline_ctx.__dict__ if pipeline_ctx else {})
    topics = ", ".join(str(t) for t in intent.get("topics", []) if t) or "this area"

    synth = getattr(pipeline_ctx, 'synthesis', None) if pipeline_ctx else None
    synthesis_text = ""
    if synth:
        theme_lines = [f"- {t.name}: {t.summary}" for t in synth.themes[:5] if t.name]
        problem_lines = [f"- {p.statement}" for p in synth.problems[:5] if p.statement]
        insight_lines = [f"- {i.statement}" for i in synth.insights[:5] if i.statement]
        synthesis_text = ""
        if theme_lines:
            synthesis_text += "\n".join(["Synthesis themes:", *theme_lines]) + "\n\n"
        if problem_lines:
            synthesis_text += "\n".join(["Synthesis problems:", *problem_lines]) + "\n\n"
        if insight_lines:
            synthesis_text += "\n".join(["Synthesis insights:", *insight_lines]) + "\n\n"

    opportunities_text = "\n\n".join(
        f"### {opp.title}\n"
        f"- Problem Description: {opp.description or 'N/A'}\n"
        f"- Gap in Existing Solutions: {opp.why_existing_solutions_fail or 'N/A'}\n"
        f"- Who Benefits: {opp.who_benefits or 'N/A'}\n"
        f"- Timing Factors: {opp.why_now or 'N/A'}\n"
        f"- Recommended Action: {opp.recommended_action or 'N/A'}\n"
        f"- Business Model: {opp.business_model or 'N/A'}\n"
        f"- Risk Assessment: {opp.risk_assessment or 'N/A'}\n"
        f"- Confidence: {(_confidence_label([opp], {}))[0]}"
        for opp in opportunities[:3]
    )

    # Section-by-section generation with evidence validation
    sections = {}

    # Compute confidence score from evidence
    # Convert Signal objects to dicts for compatibility
    signals_as_dicts = [_signal_to_dict(s) for s in pipeline_ctx.filtered_signals] if pipeline_ctx and pipeline_ctx.filtered_signals else []

    # Track section generation status
    section_status: dict[str, bool] = {}

    confidence_score = compute_confidence_score(
        signals_as_dicts,
        profile,
        pipeline_ctx.trends if pipeline_ctx else [],
        opportunities,
        section_status,
    )
    logger.info("Computed confidence score: %.2f - %s", confidence_score.overall, confidence_score.interpretation)

    # Generate each section with evidence requirements
    sections["opportunity_snapshot"] = _generate_opportunity_snapshot_section(
        opportunities, profile, pipeline_ctx, intent_type
    )
    section_status["opportunity_snapshot"] = sections["opportunity_snapshot"] is not None

    sections["trend_summary"] = _generate_trend_summary_section(
        synth, profile, pipeline_ctx
    )
    section_status["trend_summary"] = sections["trend_summary"] is not None

    sections["market_signals"] = _generate_market_signals_section(
        pipeline_ctx, profile
    )
    section_status["market_signals"] = sections["market_signals"] is not None

    sections["execution_roadmap"] = _generate_execution_roadmap_section(
        opportunities, profile, pipeline_ctx, intent_type
    )
    section_status["execution_roadmap"] = sections["execution_roadmap"] is not None

    sections["recommendation"] = _generate_recommendation_section(
        opportunities, profile, pipeline_ctx, confidence_score, intent_type
    )
    section_status["recommendation"] = sections["recommendation"] is not None

    # Recompute confidence with actual section status
    confidence_score = compute_confidence_score(
        signals_as_dicts,
        profile,
        pipeline_ctx.trends if pipeline_ctx else [],
        opportunities,
        section_status,
    )
    logger.info("Recomputed confidence score with section status: %.2f - %s", confidence_score.overall, confidence_score.interpretation)

    # Build final report from sections
    report_parts = []
    for section_name, section_content in sections.items():
        if section_content:
            report_parts.append(section_content)
        else:
            report_parts.append(f"## {section_name.replace('_', ' ').title()}\n\nUnable to generate section from available evidence.")

    final_report = "\n\n".join(report_parts)

    logger.info("Section-by-section report generation: %d sections generated",
                sum(1 for s in sections.values() if s))

    # Parse sections and populate opportunity object for frontend
    if opportunities and profile:
        top_opp = opportunities[0]

        # Parse opportunity snapshot
        if sections.get("opportunity_snapshot"):
            # Extract content after ## Opportunity Snapshot and clean heading artifacts
            snapshot_text = sections["opportunity_snapshot"] or ""
            snapshot_text = snapshot_text.replace("## Opportunity Snapshot\n\n", "")
            snapshot_text = _clean_section_heading(snapshot_text, "Opportunity Snapshot")
            # Strip leading redundant title or prefix if present
            if top_opp.title and snapshot_text.lower().startswith(top_opp.title.lower()):
                snapshot_text = snapshot_text[len(top_opp.title):].lstrip(" :\n-")
            # Apply linkification to opportunity snapshot
            signals_as_dicts = [_signal_to_dict(s) for s in pipeline_ctx.filtered_signals] if pipeline_ctx and pipeline_ctx.filtered_signals else []
            snapshot_text = _linkify_cited_signals(snapshot_text, signals_as_dicts)
            top_opp.description = snapshot_text

        # Parse execution roadmap
        if sections.get("execution_roadmap"):
            roadmap_text = sections["execution_roadmap"] or ""
            roadmap_text = roadmap_text.replace("## Execution Roadmap\n\n", "")
            # Clean trailing symbols from roadmap text
            roadmap_text = re.sub(r"[\s#\-*]+$", "", roadmap_text).strip()

            # Extract phases using regex
            phase1_match = re.search(r"### Phase\s*1:.*?\n(.*?)(?=### Phase\s*2:|$)", roadmap_text, re.DOTALL)
            phase2_match = re.search(r"### Phase\s*2:.*?\n(.*?)(?=### Phase\s*3:|$)", roadmap_text, re.DOTALL)
            phase3_match = re.search(r"### Phase\s*3:.*?\n(.*?)(?=Build Complexity:|$)", roadmap_text, re.DOTALL)
            complexity_match = re.search(r"Build Complexity: (.*)", roadmap_text)

            if not top_opp.execution_roadmap:
                top_opp.execution_roadmap = {}

            if phase1_match:
                # Clean trailing symbols and ensure proper line breaks
                phase1_text = re.sub(r"[\s#\-*]+$", "", phase1_match.group(1).strip())
                # Normalize roadmap phase formatting
                phase1_text = _normalize_roadmap_phase(phase1_text)
                top_opp.execution_roadmap["phase_1"] = phase1_text
            if phase2_match:
                phase2_text = re.sub(r"[\s#\-*]+$", "", phase2_match.group(1).strip())
                phase2_text = _normalize_roadmap_phase(phase2_text)
                top_opp.execution_roadmap["phase_2"] = phase2_text
            if phase3_match:
                phase3_text = re.sub(r"[\s#\-*]+$", "", phase3_match.group(1).strip())
                phase3_text = _normalize_roadmap_phase(phase3_text)
                top_opp.execution_roadmap["phase_3"] = phase3_text
            if complexity_match:
                complexity_text = re.sub(r"[\s#\-*]+$", "", complexity_match.group(1).strip())
                top_opp.execution_roadmap["build_complexity"] = complexity_text

        # Parse recommendation
        if sections.get("recommendation"):
            rec_text = sections["recommendation"] or ""
            rec_text = rec_text.replace("## Recommendation\n\n", "")
            rec_text = _clean_section_heading(rec_text, "Recommendation")
            # Apply linkification to recommendation
            signals_as_dicts = [_signal_to_dict(s) for s in pipeline_ctx.filtered_signals] if pipeline_ctx and pipeline_ctx.filtered_signals else []
            rec_text = _linkify_cited_signals(rec_text, signals_as_dicts)
            # Clean leading bullet characters to prevent double-rendering
            rec_text = _clean_supporting_factors(rec_text)
            top_opp.why_now = rec_text

        # Apply linkification to supporting_evidence as well
        if top_opp.supporting_evidence:
            signals_as_dicts = [_signal_to_dict(s) for s in pipeline_ctx.filtered_signals] if pipeline_ctx and pipeline_ctx.filtered_signals else []
            top_opp.supporting_evidence = _linkify_cited_signals(top_opp.supporting_evidence, signals_as_dicts)
            # Clean leading bullet characters to prevent double-rendering
            top_opp.supporting_evidence = _clean_supporting_factors(top_opp.supporting_evidence)

    return ChatResponse(answer=final_report, suggested_questions=[])


def _generate_opportunity_snapshot_section(
    opportunities: list[Opportunity],
    profile: TechnologyProfile,
    pipeline_ctx: PipelineContext | None,
    intent_type: str = "Opportunity Discovery",
) -> str | None:
    """Generate opportunity snapshot section with evidence validation."""
    # Build signal summary for prompt
    signal_summary = ""
    if pipeline_ctx and pipeline_ctx.filtered_signals:
        signal_summary = "\n".join([
            f"{i+1}. [{s.get('source_type', 'Unknown')}] {s.get('entity', 'Unknown')}: {s.get('value', '')[:200]}"
            for i, s in enumerate([_signal_to_dict(sig) for sig in pipeline_ctx.filtered_signals[:10]])
        ])

    # Check evidence sufficiency
    signal_count = len(pipeline_ctx.filtered_signals) if pipeline_ctx and pipeline_ctx.filtered_signals else 0
    if signal_count < 2:
        logger.warning("Insufficient evidence for opportunity snapshot: %d signals", signal_count)
        return None

    # Intent-specific prompt
    if intent_type in ("Skill Learning", "Career Development"):
        prompt = f"""
ROLE: You are writing a learning assessment for a technology intelligence report.

TECHNOLOGY: {profile.canonical_name}
DESCRIPTION: {profile.description}
CATEGORY: {profile.category}

COLLECTED SIGNALS:
{signal_summary}

Based on these signals, write a 2-3 sentence assessment:
- Is {profile.canonical_name} worth learning in 2026?
- What career/project opportunities does it unlock?
- What is the current state of demand?

Return only the assessment text, no headers.
"""
    elif intent_type == "Technology Evaluation":
        prompt = f"""
ROLE: You are writing a technology evaluation for a technology intelligence report.

TECHNOLOGY: {profile.canonical_name}
DESCRIPTION: {profile.description}
CATEGORY: {profile.category}

COLLECTED SIGNALS:
{signal_summary}

Based on these signals, write a 2-3 sentence assessment:
- What is {profile.canonical_name} and what is its current state?
- What are its key strengths and weaknesses?
- What is its ecosystem health?

Return only the assessment text, no headers.
"""
    else:  # Opportunity Discovery
        if not opportunities:
            return None

        top_opp = opportunities[0]

        # Build evidence from signals
        evidence_list = []
        if pipeline_ctx and pipeline_ctx.filtered_signals:
            for signal in pipeline_ctx.filtered_signals[:5]:
                signal_dict = _signal_to_dict(signal)
                evidence = create_evidence_from_signal(
                    signal_dict,
                    f"Supports opportunity: {top_opp.title}",
                    confidence=signal_dict.get("evidence_quality", 0.5) / 100
                )
                evidence_list.append(evidence)

        prompt = f"""
ROLE: You are writing a concise opportunity snapshot for a technology intelligence report.

TECHNOLOGY: {profile.canonical_name}
DESCRIPTION: {profile.description}
CATEGORY: {profile.category}

TOP OPPORTUNITY:
  Title: {top_opp.title}
  Description: {top_opp.description}
  Gap: {top_opp.why_existing_solutions_fail}
  Who Benefits: {top_opp.who_benefits}

SUPPORTING EVIDENCE:
{chr(10).join(f"- [{e.source_type}] {e.source_title}: {e.extracted_claim}" for e in evidence_list[:5])}

COLLECTED SIGNALS:
{signal_summary}

QUANTIFIED SIGNALS:
- Total signals: {len(pipeline_ctx.filtered_signals) if pipeline_ctx else 0}
- Opportunities identified: {len(opportunities)}

{build_maturity_prompt_additions(profile.maturity, "opportunity_snapshot")}

Write an opportunity snapshot in 4-6 sentences that:
1. States what the opportunity is, specifically
2. Quantifies the evidence (numbers, project counts, growth rates)
3. Names who would benefit and why current solutions fall short
4. Gives a clear "so what" — why should someone act on this

VOCABULARY TO USE: {', '.join(profile.core_terms[:8])}
VOCABULARY TO AVOID: generic business language, "significant opportunity", "growing market", "strong momentum", "early mover advantage"
"""

    response = _ollama_generate(prompt, format=None)
    if response:
        # Validate technology specificity with graduated scoring
        is_specific, _ = _validate_technology_specificity(response, profile.canonical_name, profile)
        if is_specific:
            return f"## Opportunity Snapshot\n\n{response.strip()}"

    return None


def _generate_trend_summary_section(
    synth: Synthesis | None,
    profile: TechnologyProfile,
    pipeline_ctx: PipelineContext | None,
) -> str | None:
    """Generate trend summary section with evidence validation."""
    if not synth or not synth.themes:
        logger.warning("No synthesis or themes available for trend summary")
        return None

    # Check evidence sufficiency
    if len(synth.themes) < 1:
        logger.warning("Insufficient themes for trend summary: %d themes", len(synth.themes))
        return None

    # Build evidence from themes
    theme_blocks = []
    for theme in synth.themes[:5]:
        evidence_text = "\n".join([
            f"  - {s.get('entity', 'Unknown')}: {s.get('value', '')[:100]}"
            for s in (theme.signals or [])[:3]
        ])
        theme_blocks.append(f"""
Theme: {theme.name}
Summary: {theme.summary}
Evidence:
{evidence_text}
""")

    # Build signal summary for context
    signal_summary = ""
    if pipeline_ctx and pipeline_ctx.filtered_signals:
        signal_summary = "\n".join([
            f"{i+1}. [{s.get('source_type', 'Unknown')}] {s.get('entity', 'Unknown')}: {s.get('value', '')[:200]}"
            for i, s in enumerate([_signal_to_dict(sig) for sig in pipeline_ctx.filtered_signals[:10]])
        ])

    prompt = f"""
You are writing a trend summary for {profile.canonical_name} ({profile.description}).

COLLECTED SIGNALS:
{signal_summary}

THEMES OBSERVED:
{''.join(theme_blocks) if theme_blocks else "Synthesize from collected signals above"}

Generate a structured Trend Summary containing EXACTLY 3-4 bullet points.
Format each bullet point as:
- **[Trend Name]**: [1-2 concise sentences explaining the pattern, citing evidence/repos/numbers from the signals]

Example:
- **Protocol Standardization**: Rapid consensus around MCP tool servers across developer frameworks.
- **Security & Governance Focus**: Emerging demand for auditability and permission controls in AI integrations.
- **Workflow Automation Shift**: Practitioners moving from manual prompt engineering to event-driven agent triggers.

Return ONLY the bulleted list.
"""

    response = _ollama_generate(prompt, format=None)
    if response:
        # Validate technology specificity with graduated scoring
        is_specific, _ = _validate_technology_specificity(response, profile.canonical_name, profile)
        if is_specific:
            return f"## Trend Summary\n\n{response.strip()}"

    return None


def _generate_market_signals_section(
    pipeline_ctx: PipelineContext | None,
    profile: TechnologyProfile,
) -> str | None:
    """Generate market signals section with evidence validation."""
    if not pipeline_ctx or not pipeline_ctx.filtered_signals:
        return None

    # Check evidence sufficiency
    if len(pipeline_ctx.filtered_signals) < 5:
        logger.warning("Insufficient signals for market signals section: %d signals", len(pipeline_ctx.filtered_signals))
        return None

    # Build signal summary with more detail
    signal_summary = "\n".join([
        f"{i+1}. [{_signal_to_dict(s).get('source_type', 'Unknown')}] {_signal_to_dict(s).get('entity', 'Unknown')}: {_signal_to_dict(s).get('value', '')[:200]}"
        for i, s in enumerate(pipeline_ctx.filtered_signals[:15])
    ])

    prompt = f"""
You are writing a market signals section for {profile.canonical_name} ({profile.description}).

You have {len(pipeline_ctx.filtered_signals)} signals from various sources.
Your job is to explain what these signals collectively indicate.

{build_maturity_prompt_additions(profile.maturity, "market_signals")}

RULES:
- Explain what changes if the pattern continues
- Identify who benefits from this pattern
- Explain what problems become more important
- Use vocabulary appropriate for {profile.category}: {', '.join(profile.core_terms[:5])}
- Do NOT add claims beyond what the evidence supports
- Reference specific signal titles and sources in your analysis

COLLECTED SIGNALS:
{signal_summary}

Write the market signals section (3-5 sentences):
"""

    response = _ollama_generate(prompt, format=None)
    if response:
        # Validate technology specificity with graduated scoring
        is_specific, _ = _validate_technology_specificity(response, profile.canonical_name, profile)
        if is_specific:
            return f"## Market Signals\n\n{response.strip()}"

    return None


def _generate_execution_roadmap_section(
    opportunities: list[Opportunity],
    profile: TechnologyProfile,
    pipeline_ctx: PipelineContext | None,
    intent_type: str = "Opportunity Discovery",
) -> str | None:
    """Generate execution roadmap section with evidence validation."""
    # Build signal summary for context
    signal_summary = ""
    if pipeline_ctx and pipeline_ctx.filtered_signals:
        signal_summary = "\n".join([
            f"{i+1}. [{s.get('source_type', 'Unknown')}] {s.get('entity', 'Unknown')}: {s.get('value', '')[:200]}"
            for i, s in enumerate([_signal_to_dict(sig) for sig in pipeline_ctx.filtered_signals[:10]])
        ])

    # Check evidence sufficiency
    signal_count = len(pipeline_ctx.filtered_signals) if pipeline_ctx and pipeline_ctx.filtered_signals else 0
    if signal_count < 2:
        logger.warning("Insufficient evidence for execution roadmap: %d signals", signal_count)
        return None

    # Intent-specific prompt
    if intent_type in ("Skill Learning", "Career Development"):
        prompt = f"""
You are creating a learning roadmap for someone who wants to learn {profile.canonical_name}.

TECHNOLOGY: {profile.canonical_name}
DESCRIPTION: {profile.description}
CATEGORY: {profile.category}

COLLECTED SIGNALS:
{signal_summary}

TECHNOLOGY CONTEXT:
- Key concepts: {', '.join(profile.core_terms)}
- Related tech: {', '.join(profile.related_technologies)}
- Primary languages: {', '.join(profile.programming_languages)}

{build_maturity_prompt_additions(profile.maturity, "execution_roadmap")}

Create a 3-phase learning roadmap that is SPECIFIC to this technology.

Structure:
## Execution Roadmap

### Phase 1: Getting Started
- [3-4 specific steps to start learning {profile.canonical_name}]
- Timeframe: [specific timeframe]

### Phase 2: Building Skills
- [3-4 specific steps to build intermediate skills]
- Timeframe: [specific timeframe]

### Phase 3: Production Proficiency
- [3-4 specific steps to reach production-level proficiency]
- Timeframe: [specific timeframe]

Build Complexity: [assessment]
"""
    elif intent_type == "Technology Evaluation":
        prompt = f"""
You are creating an evaluation roadmap for someone who wants to evaluate {profile.canonical_name}.

TECHNOLOGY: {profile.canonical_name}
DESCRIPTION: {profile.description}
CATEGORY: {profile.category}

COLLECTED SIGNALS:
{signal_summary}

TECHNOLOGY CONTEXT:
- Key concepts: {', '.join(profile.core_terms)}
- Related tech: {', '.join(profile.related_technologies)}
- Primary languages: {', '.join(profile.programming_languages)}

{build_maturity_prompt_additions(profile.maturity, "execution_roadmap")}

Create a 3-phase evaluation roadmap that is SPECIFIC to this technology.

Structure:
## Execution Roadmap

### Phase 1: Evaluate Fit
- [3-4 specific steps to evaluate if {profile.canonical_name} fits your needs]
- Timeframe: [specific timeframe]

### Phase 2: Adopt
- [3-4 specific steps for adoption/migration]
- Timeframe: [specific timeframe]

### Phase 3: Scale
- [3-4 specific steps for production readiness and team training]
- Timeframe: [specific timeframe]

Build Complexity: [assessment]
"""
    else:  # Opportunity Discovery
        if not opportunities:
            logger.warning("No opportunities for execution roadmap (opportunity discovery)")
            return None

        top_opp = opportunities[0]

        # Extract specific buildable patterns from signals
        existing_projects = []
        if pipeline_ctx and pipeline_ctx.filtered_signals:
            existing_projects = [
                _signal_to_dict(s) for s in pipeline_ctx.filtered_signals
                if _signal_to_dict(s).get("source_type") == "github_repo"
            ][:10]

        # Check evidence sufficiency
        if len(existing_projects) < 1 and not top_opp.why_existing_solutions_fail:
            logger.warning("Insufficient evidence for execution roadmap")
            return None

        prompt = f"""
You are creating an execution roadmap for someone who wants to build in the {profile.canonical_name} ecosystem.

OPPORTUNITY: {top_opp.title}
{top_opp.description}

GAP: {top_opp.why_existing_solutions_fail}
TARGET USERS: {top_opp.who_benefits}

COLLECTED SIGNALS:
{signal_summary}

EXISTING PROJECTS IN THIS SPACE (what already exists):
{chr(10).join(f"- {p.get('entity', 'Unknown')}: {p.get('value', '')[:100]}" for p in existing_projects[:5])}

TECHNOLOGY CONTEXT:
- {profile.canonical_name} is {profile.description}
- Key concepts: {', '.join(profile.core_terms)}
- Related tech: {', '.join(profile.related_technologies)}
- Primary languages: {', '.join(profile.programming_languages)}

{build_maturity_prompt_additions(profile.maturity, "execution_roadmap")}

Create a 3-phase execution roadmap that is SPECIFIC to this technology and opportunity.

RULES:
- Each phase must reference specific tools, libraries, or projects from the evidence
- Do NOT use generic phases like "Validate assumptions" or "Scale solution"
- Instead, reference specific things to build, specific integrations, specific communities
- Example of GOOD phase for LangGraph: "Build a reference implementation of a multi-agent customer support workflow using StateGraph with checkpointing, targeting the gap in production-ready templates identified in community discussions"
- Example of BAD phase: "Build MVP and validate with early users"

Structure:
## Execution Roadmap

### Phase 1: [Specific Title]
- [3-4 specific, actionable steps]
- Timeframe: [specific timeframe]

### Phase 2: [Specific Title]
- [3-4 specific, actionable steps]
- Timeframe: [specific timeframe]

### Phase 3: [Specific Title]
- [3-4 specific, actionable steps]
- Timeframe: [specific timeframe]

Build Complexity: [assessment]
"""

    response = _ollama_generate(prompt, format=None)
    if response:
        # Validate technology specificity with graduated scoring
        is_specific, _ = _validate_technology_specificity(response, profile.canonical_name, profile)
        if is_specific:
            return response.strip()

    return None


def _generate_recommendation_section(
    opportunities: list[Opportunity],
    profile: TechnologyProfile,
    pipeline_ctx: PipelineContext | None,
    confidence_score: Any,
    intent_type: str = "Opportunity Discovery",
) -> str | None:
    """Generate recommendation section with evidence validation."""
    # Build signal summary for context
    signal_summary = ""
    if pipeline_ctx and pipeline_ctx.filtered_signals:
        signal_summary = "\n".join([
            f"{i+1}. [{s.get('source_type', 'Unknown')}] {s.get('entity', 'Unknown')}: {s.get('value', '')[:200]}"
            for i, s in enumerate([_signal_to_dict(sig) for sig in pipeline_ctx.filtered_signals[:10]])
        ])

    # Quantified signal data
    signal_stats = {
        "total_signals": len(pipeline_ctx.filtered_signals) if pipeline_ctx else 0,
        "opportunities": len(opportunities),
    }

    # Intent-specific prompt
    if intent_type in ("Skill Learning", "Career Development"):
        prompt = f"""
Technology: {profile.canonical_name}
Category: {profile.category}

QUANTIFIED SIGNALS:
- Total signals collected: {signal_stats['total_signals']}

COLLECTED SIGNALS:
{signal_summary}

{build_maturity_prompt_additions(profile.maturity, "recommendation")}

Write a recommendation (3-4 sentences) that:
1. Gives a clear verdict: Learn now, wait and monitor, or skip
2. Cites specific numbers from the quantified signals
3. Cites specific signal titles and sources by name
4. Identifies the single biggest risk or challenge
5. States what would change your recommendation

Be direct. Be specific. Cite numbers and specific signal names.
"""
    elif intent_type == "Technology Evaluation":
        prompt = f"""
Technology: {profile.canonical_name}
Category: {profile.category}

QUANTIFIED SIGNALS:
- Total signals collected: {signal_stats['total_signals']}

COLLECTED SIGNALS:
{signal_summary}

{build_maturity_prompt_additions(profile.maturity, "recommendation")}

Write a recommendation (3-4 sentences) that:
1. Gives a clear verdict: Use now, wait and monitor, or skip
2. Cites specific numbers from the quantified signals
3. Cites specific signal titles and sources by name
4. Identifies the single biggest risk or limitation
5. States what would change your recommendation

Be direct. Be specific. Cite numbers and specific signal names.
"""
    else:  # Opportunity Discovery
        if not opportunities:
            logger.warning("No opportunities for recommendation (opportunity discovery)")
            return None

        top_opp = opportunities[0]

        # Determine decision based on confidence score
        decision = "BUILD" if confidence_score.overall >= 50 else "INVESTIGATE" if confidence_score.overall >= 30 else "MONITOR"

        prompt = f"""
You are a senior technology analyst writing a decisive recommendation for {profile.canonical_name}.

DECISION TO ALIGN WITH: {decision} (MUST match: If score < 50 => INVESTIGATE/MONITOR; If score >= 50 => BUILD)
TOP OPPORTUNITY: {top_opp.title}

COLLECTED EVIDENCE:
{signal_summary}

RULES:
- NEVER say "signal 1", "signal 5", "as noted in the prompt", or "TOP OPPORTUNITY section".
- Speak directly to the practitioner/builder.
- Your narrative MUST agree with the decision: If decision is INVESTIGATE, explain why validation/monitoring is needed before building. If decision is BUILD, explain why to build now.
- Cite specific tool names and discussion topics naturally by name (e.g., "Discussions on Hacker News regarding automated agent creation demonstrate...").
- Output as 2-3 bullet points starting with "•"
- Each bullet must be maximum 2 sentences and 40 words total
- Be concise and actionable
"""

    response = _ollama_generate(prompt, format=None)
    if response:
        return f"## Recommendation\n\n{response.strip()}"

    return None


def _generate_legacy_report(
    query: str,
    opportunities: list[Opportunity],
    intent: dict[str, Any],
    context: dict[str, Any],
    formatted: str,
) -> ChatResponse:
    """Legacy report generation for when TechnologyProfile is not available."""
    intent_type = intent.get("intent", "Opportunity Discovery")
    state_summary = _build_state_summary(context)
    topics = ", ".join(str(t) for t in intent.get("topics", []) if t) or "this area"
    primary_technology = intent.get("primary_technology", "")
    domain = intent.get("domain", "")

    # Build Technology Profile (legacy method)
    tech_profile = _build_technology_profile(primary_technology, domain, topics)

    # Create a minimal TechnologyProfile for validation
    from ode.technology_resolver import TechnologyProfile
    profile = TechnologyProfile(
        canonical_name=primary_technology or topics.split(",")[0] if topics else "Unknown",
        aliases=[],
        category=domain or "General",
        core_terms=[],
        exclusion_terms=[],
        maturity="Emerging",
        domain=domain or "General",
        signal_weight_hints={},
    )

    synth = cast(Synthesis | None, context.get("synthesis"))
    synthesis_text = ""
    if synth:
        theme_lines = [f"- {t.name}: {t.summary}" for t in synth.themes[:5] if t.name]
        problem_lines = [f"- {p.statement}" for p in synth.problems[:5] if p.statement]
        insight_lines = [f"- {i.statement}" for i in synth.insights[:5] if i.statement]
        synthesis_text = ""
        if theme_lines:
            synthesis_text += "\n".join(["Synthesis themes:", *theme_lines]) + "\n\n"
        if problem_lines:
            synthesis_text += "\n".join(["Synthesis problems:", *problem_lines]) + "\n\n"
        if insight_lines:
            synthesis_text += "\n".join(["Synthesis insights:", *insight_lines]) + "\n\n"

    opportunities_text = "\n\n".join(
        f"### {opp.title}\n"
        f"- Problem Description: {opp.description or 'N/A'}\n"
        f"- Gap in Existing Solutions: {opp.why_existing_solutions_fail or 'N/A'}\n"
        f"- Who Benefits: {opp.who_benefits or 'N/A'}\n"
        f"- Timing Factors: {opp.why_now or 'N/A'}\n"
        f"- Recommended Action: {opp.recommended_action or 'N/A'}\n"
        f"- Business Model: {opp.business_model or 'N/A'}\n"
        f"- Risk Assessment: {opp.risk_assessment or 'N/A'}\n"
        f"- Confidence: {(_confidence_label([opp], context or {}))[0]}"
        for opp in opportunities[:3]
    )

    prompt = (
        "You are a technology opportunity analyst writing a concise, evidence-grounded report. "
        "Use the technology profile, synthesis, and structured opportunities below. "
        "Synthesize a decisive Markdown report with exactly these sections: "
        "## Opportunity Snapshot, ## Trend Summary, ## Market Signals, ## Execution Roadmap, ## Recommendation. "
        "\n\n"
        "Rules for Opportunity Snapshot:\n"
        "- One concise sentence naming the top opportunity and why it wins now.\n"
        "\n"
        "Rules for Trend Summary:\n"
        "- List 3-5 recurring themes visible in the evidence, with what they collectively indicate.\n"
        "\n"
        "Rules for Market Signals:\n"
        "- Explain what changes if the pattern continues, who benefits, and what problems become more important.\n"
        "\n"
        "Rules for Execution Roadmap:\n"
        "- Provide 3 phases (Phase 1, Phase 2, Phase 3) with specific, actionable steps.\n"
        "- Each phase should have concrete deliverables and timeframes.\n"
        "- Include build complexity assessment.\n"
        "\n"
        "Rules for Recommendation:\n"
        "- Give a concrete, time-boxed action the reader can take.\n"
        "\n"
        "Rules for Technology Specificity:\n"
        f"- MUST use the core vocabulary from the Technology Profile for {primary_technology}.\n"
        f"- MUST NOT use disallowed concepts from the Technology Profile.\n"
        "- If you cannot generate technology-specific content, omit that section rather than using generic text.\n"
        "\n"
        "Rules for Evidence Summary:\n"
        "- First write 1-2 narrative paragraphs explaining what the sources collectively mean.\n"
        "- Then list at most 4 attributed source bullets. Do not dump raw signal titles.\n"
        "\n"
        "Return only Markdown.\n\n"
        f"Question: {query}\n"
        f"Intent: {intent_type}\n"
        f"Topics: {topics}\n\n"
        f"{tech_profile}\n\n"
        f"LangGraph State Summary:\n{state_summary}\n\n"
        f"{synthesis_text}"
        f"Structured Opportunities:\n{opportunities_text}\n\n"
        f"Draft Evidence Report (for factual reference only):\n{formatted}\n\n"
        "Generate the final report now."
    )

    logger.info("Legacy report prompt: query=%s, intent=%s, primary_tech=%s, domain=%s, topics=%s, synthesis_themes=%d, synthesis_problems=%d, synthesis_insights=%d, opportunities=%d, formatted_length=%d, prompt_length=%d",
                query, intent_type, primary_technology, domain, topics, len(synth.themes) if synth else 0,
                len(synth.problems) if synth else 0, len(synth.insights) if synth else 0,
                len(opportunities), len(formatted), len(prompt))

    response = _ollama_generate(prompt, format=None)
    logger.info("Report LLM response: %s", "present" if response else "missing")
    if response:
        logger.info("Report LLM response length: %d", len(response))
        logger.info("Report LLM raw response (first 1000 chars): %s", response[:1000])

        # Check for each required section
        required_sections = ["Opportunity Snapshot", "Trend Summary", "Market Signals", "Execution Roadmap", "Recommendation"]
        for section in required_sections:
            if section in response:
                logger.info("Section found in response: %s", section)
            else:
                logger.warning("Section missing from response: %s", section)

        # Validate technology specificity with graduated scoring
        is_specific, specificity_msg = _validate_technology_specificity(response, primary_technology, profile)
        logger.info("Technology specificity validation: %s - %s", is_specific, specificity_msg)

        if not is_specific:
            logger.warning("Response failed technology specificity check, using fallback formatted response")
            return _grounded_response(formatted, [], opportunities, formatted)

        cleaned = _strip_markdown_fences(response.strip())
        logger.info("Grounding check: passed, using LLM response")
        return _grounded_response(cleaned, [], opportunities, formatted)

    logger.warning("LLM response missing, using fallback formatted response")
    return _grounded_response(formatted, [], opportunities, formatted)


def _build_technology_profile(
    primary_technology: str,
    domain: str,
    topics: str,
) -> str:
    """Build a technology profile with vocabulary and disallowed concepts (legacy fallback)."""
    tech_lower = primary_technology.lower()

    # Technology categories and their core vocabulary
    tech_profiles: dict[str, dict[str, Any]] = {
        "langgraph": {
            "category": "Agent Orchestration Framework",
            "core_vocabulary": ["agents", "workflows", "orchestration", "state", "stateful execution", "graph execution", "multi-agent systems", "memory", "cycles", "edges", "nodes"],
            "disallowed": ["knowledge graph", "graph database", "neo4j", "cypher", "triple store"],
        },
        "mcp": {
            "category": "Model Context Protocol",
            "core_vocabulary": ["mcp", "servers", "tooling", "governance", "clients", "prompts", "tools", "context", "anthropic", "claude", "tool calling"],
            "disallowed": ["microsoft", "control panel", "machine control panel"],
        },
        "react": {
            "category": "Frontend Framework",
            "core_vocabulary": ["components", "hooks", "jsx", "virtual dom", "state", "props", "rendering", "declarative", "unidirectional data flow"],
            "disallowed": ["real estate", "react native", "reactos"],
        },
        "kubernetes": {
            "category": "Container Orchestration",
            "core_vocabulary": ["containers", "pods", "services", "deployments", "namespaces", "helm", "kubectl", "scaling", "load balancing"],
            "disallowed": ["kubernetes the movie", "kubernetes ui only"],
        },
        "rust": {
            "category": "Systems Programming Language",
            "core_vocabulary": ["memory safety", "ownership", "borrowing", "traits", "cargo", "crates", "zero-cost abstractions", "unsafe", "ffi"],
            "disallowed": ["rust the game", "rust belt"],
        },
    }

    profile = tech_profiles.get(tech_lower, {
        "category": domain or "General Technology",
        "core_vocabulary": topics.split(", ") if topics else [],
        "disallowed": [],
    })

    profile_text = f"""
Technology: {primary_technology}
Category: {profile['category']}

Core Vocabulary:
{chr(10).join(f"- {v}" for v in profile['core_vocabulary'][:10])}

Disallowed Concepts:
{chr(10).join(f"- {v}" for v in profile['disallowed'][:5]) if profile['disallowed'] else "- None"}
"""
    return profile_text.strip()
