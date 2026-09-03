"""LLM-generated explanations and natural-language responses for ODE.

When the ``OLLAMA_URL`` and ``OLLAMA_MODEL`` environment variables are set,
all text generation in this module routes through the Ollama ``/api/generate``
endpoint. If Ollama is not configured or the call fails, the module falls back
to deterministic templates so the pipeline stays testable and operational.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ode.config.timeouts import OLLAMA_TIMEOUT
from ode.technology_resolver import strip_forced_mcp_prefix

if TYPE_CHECKING:
    from ode.personas import Persona


logger = logging.getLogger(__name__)


def _intent_dict(intent: Any) -> dict[str, Any]:
    """Return *intent* if it is a dict, otherwise an empty dict."""
    return intent if isinstance(intent, dict) else {}


@dataclass
class Explanation:
    title: str
    why_now: str
    who_benefits: str
    recommended_action: str
    supporting_evidence: str
    summary: str
    category: str = "Product"
    categories: list[str] = field(default_factory=list)


_METRIC_ANGLE: dict[str, str] = {
    "github_commits": "Developer Momentum",
    "github_contributors": "Community Ecosystem",
    "github_open_issues": "Customer Pain Points",
    "github_repo_results": "Market Landscape",
    "web_page_text": "Digital Presence",
    "docs_page_text": "Documentation Ecosystem",
    "stars": "Adoption",
    "forks": "Ecosystem Forking",
    "adoption": "Adoption",
    "developer_pain": "Pain Points",
    "community_discussion": "Community Discussion",
    "market_demand": "Market Demand",
    "hiring": "Hiring Demand",
    "product_launch": "Product Launch",
    "news_mention": "News Coverage",
}


_INTENT_SUFFIX: dict[str, str] = {
    "Skill Learning": "Foundations",
    "Career Development": "Roadmap",
    "Technology Evaluation": "Assessment",
    "Market Intelligence": "Market",
    "Opportunity Discovery": "Solutions",
    "Product Ideas": "Products",
    "Business Opportunities": "Services",
}


def _specific_title_from_intent(
    base: str,
    intent: dict[str, Any] | None,
) -> str | None:
    """Return a more specific title when the intent topics include a focused phrase.

    Looks for the longest topic that contains the base concept and adds at least
    one additional content word. This prevents broad titles like ``MCP Platforms``
    when the user asked about ``MCP observability tooling``.
    """
    if not isinstance(intent, dict):
        return None
    primary = str(intent.get("primary_technology", base)).strip().lower()
    topics = [str(t).strip() for t in (intent.get("topics") or []) if t]

    def _tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}

    base_terms = _tokens(base) | _tokens(primary)
    if not base_terms:
        return None

    best = ""
    for topic in topics:
        t = topic.lower()
        topic_tokens = _tokens(t)
        # Topic must share at least one core token with the base concept.
        if not (base_terms & topic_tokens):
            continue
        # Topic must add at least one content word beyond the base concept.
        extra = topic_tokens - base_terms
        if not extra:
            continue
        if len(t) > len(best):
            best = t
    return best.title() if best else None


def _market_title(
    entity: str,
    metric: str,
    intent: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic, intent-aware opportunity title."""
    base = entity.replace("Frameworks", "").strip().replace(" Ecosystem Growth", "").replace("Ecosystem Growth", "")
    intent_type = _intent_dict(intent).get("intent", "")

    specific = _specific_title_from_intent(base, intent)
    if specific and intent_type in (
        "Opportunity Discovery",
        "Product Ideas",
        "Business Opportunities",
    ):
        return specific

    if intent_type in ("Skill Learning", "Career Development"):
        return specific or base
    if intent_type == "Technology Evaluation":
        return f"{base} Technology Assessment"

    if intent_type in ("Opportunity Discovery", "Product Ideas", "Business Opportunities", "Market Intelligence"):
        if specific:
            return specific
        suffix = _INTENT_SUFFIX.get(intent_type, "Solutions")
        if base:
            return f"{base} {suffix}"

    if metric == "market_trend" and intent:
        if specific:
            return specific
        suffix = _INTENT_SUFFIX.get(intent_type, "Solutions")
        if base:
            return f"{base} {suffix}"
        domain = str(intent.get("domain", "")).strip()
        if domain:
            return f"{domain} {suffix}"
    if metric == "market_trend":
        return base
    angle = _METRIC_ANGLE.get(metric, metric.replace("github_", "").replace("_", " ").title())
    return f"{base} {angle}".strip()


def _deterministic_category(
    entity: str,
    metric: str,
    intent: dict[str, Any] | None = None,
) -> str:
    """Pick a single market category from the allowed set."""
    intent_type = _intent_dict(intent).get("intent", "")
    if intent_type in ("Skill Learning", "Career Development"):
        return "Skill"
    if intent_type == "Technology Evaluation":
        return "Technology"

    text = f"{entity} {metric}".lower()
    if any(k in text for k in ("commits", "contributors", "developer", "engineer", "skill")):
        return "Skill"
    if any(k in text for k in ("issues", "forks", "stars", "downloads")):
        return "Product"
    if any(k in text for k in ("platform", "repo_results", "infrastructure")):
        return "Platform"
    if "service" in text or "support" in text:
        return "Service"
    if any(k in text for k in ("business", "market", "enterprise")):
        return "Business"
    return "Product"


def _deterministic_categories(
    entity: str,
    metric: str,
    category: str,
    intent: dict[str, Any] | None = None,
) -> list[str]:
    """Return a deterministic list of relevant market categories."""
    categories: list[str] = [category]
    text = f"{entity} {metric}".lower()
    intent_type = _intent_dict(intent).get("intent", "")
    if intent_type in ("Skill Learning", "Career Development"):
        categories.append("Learning")
    if "ai" in text or "agent" in text or "llm" in text:
        categories.append("AI")
    if "github_" in metric or "git" in text:
        categories.append("Open Source")
    if "cloud" in text:
        categories.append("Cloud")
    if "data" in text:
        categories.append("Data")
    return list(dict.fromkeys(categories))


def _template_explanation(
    entity: str,
    metric: str,
    score: float,
    components: dict[str, Any],
    persona_name: str,
    persona: "Persona" | None = None,
    context7_summary: str = "",
    intent: dict[str, Any] | None = None,
) -> Explanation:
    """Return a deterministic explanation when no LLM backend is available."""
    intent_type = _intent_dict(intent).get("intent", "Opportunity Discovery")
    title = _market_title(entity, metric, intent)
    title = strip_forced_mcp_prefix(title, None, intent, entity)
    primary = entity.strip() or "this technology"

    evidence = (
        f"Analysis based on {components.get('signal_volume', 0):.0f} signals: "
        f"evidence quality {components.get('evidence_quality', 0):.1f}, "
        f"momentum {components.get('momentum', 0):.1f}, "
        f"adoption {components.get('adoption', 0):.1f}, "
        f"growth {components.get('growth', 0):.1f}, "
        f"relevance {components.get('relevance', 0):.1f}."
    )
    if context7_summary:
        evidence += f" Documentation context: {context7_summary[:200].strip()}"
    category = _deterministic_category(entity, metric, intent)
    categories = _deterministic_categories(entity, metric, category, intent)

    if intent_type == "Skill Learning":
        why_now = (
            f"{primary} shows a signal score of {score:.0f}/100. {evidence} This suggests it "
            f"remains a practical skill to invest time in."
        )
        who_benefits = (
            f"Practitioners who build or design software, especially when working with "
            f"{category.lower()} technologies"
        )
        recommended_action = (
            f"Start with {primary} fundamentals, build one small production-like project, then "
            f"integrate adjacent tools from the evidence above into a focused learning plan."
        )
        summary = (
            f"{primary} is a worthwhile skill to learn: {evidence}"
        )
    elif intent_type == "Technology Evaluation":
        why_now = (
            f"{primary} shows a signal score of {score:.0f}/100. {evidence} This suggests it "
            f"has ongoing maintenance and community investment rather than becoming obsolete."
        )
        who_benefits = (
            f"Teams deciding whether to adopt, maintain, or replace {primary}"
        )
        recommended_action = (
            f"Compare {primary} against alternatives using the adoption, ecosystem, and "
            f"risk signals collected above, then run a small proof-of-concept."
        )
        summary = (
            f"{primary} remains relevant based on current activity, but adoption should be "
            f"weighed against project needs and ecosystem trends."
        )
    elif intent_type == "Career Development":
        why_now = (
            f"{primary} shows a signal score of {score:.0f}/100. {evidence} This indicates "
            f"steady demand for practitioners with this capability."
        )
        who_benefits = (
            f"Practitioners planning their next career or skill move"
        )
        recommended_action = (
            f"Build demonstrable {primary} experience through public repos, measurable "
            f"contributions, and a portfolio project tied to the evidence above."
        )
        summary = (
            f"{primary} offers a clear skill trajectory: {evidence}"
        )
    elif intent_type == "Market Intelligence":
        why_now = (
            f"{primary} is generating measurable signal volume across repositories, search, "
            f"and documentation sources, indicating market movement."
        )
        who_benefits = (
            f"Strategic planners and investors tracking {primary}"
        )
        recommended_action = (
            f"Track {primary} momentum over the next quarter and compare it with adjacent "
            f"technologies in the same domain."
        )
        summary = (
            f"{primary} shows sustained market activity and is worth monitoring for "
            f"strategic positioning."
        )
    else:  # Opportunity Discovery and default
        # Use the specific title so the prose matches the focused opportunity name.
        named = title or primary
        why_now = (
            f"{named} is generating strong, multi-source signal volume (GitHub, discussion "
            f"forums, and documentation). The evidence points to a focused opportunity in "
            f"this area rather than a generic technology trend."
        )
        who_benefits = (
            f"Teams and practitioners exploring new product or service ideas"
        )
        recommended_action = (
            f"Validate the strongest signal first: pick the highest-confidence evidence item, "
            f"speak to one potential user, and build a minimal prototype around {named}."
        )
        summary = (
            f"{named} presents actionable opportunities grounded in the collected "
            f"evidence and current market activity."
        )

    return Explanation(
        title=title,
        category=category,
        categories=categories,
        why_now=why_now,
        who_benefits=who_benefits,
        recommended_action=recommended_action,
        supporting_evidence=evidence,
        summary=summary,
    )


def _ollama_generate(prompt: str, format: str | None = "json") -> str | None:
    """Call the Ollama /api/generate endpoint and return the generated text.

    Enforces a token budget (``OLLAMA_MAX_TOKENS``) and a network timeout
    (``OLLAMA_TIMEOUT``) so a single runaway generation cannot stall the whole
    pipeline. Logs prompt/response sizes and latency for observability.
    """
    ollama_url = os.environ.get("OLLAMA_URL") or "http://localhost:11434"
    ollama_model = os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b"
    if not ollama_url:
        logger.info("Ollama generate skipped: OLLAMA_URL not configured")
        return None

    # Normalize URL to avoid double /api issues
    ollama_url = ollama_url.rstrip("/")
    if ollama_url.endswith("/api"):
        ollama_url = ollama_url[:-4]

    max_tokens = int(os.environ.get("OLLAMA_MAX_TOKENS") or 2048)
    timeout = OLLAMA_TIMEOUT

    # Warn if timeout is unreasonably short (likely test configuration)
    if timeout < 1.0:
        logger.warning(
            "Ollama timeout is very short (%.2fs) - this may cause legitimate requests to fail. "
            "Use OLLAMA_TIMEOUT=15.0 or higher for production use.",
            timeout
        )

    # Log timeout configuration for debugging
    logger.info(
        "Ollama generate configured: model=%s timeout=%.2fs max_tokens=%d",
        ollama_model,
        timeout,
        max_tokens,
    )

    payload: dict[str, Any] = {
        "model": ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
        },
    }
    if format:
        payload["format"] = format

    req = urllib.request.Request(
        f"{ollama_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    prompt_size = len(prompt)
    start = time.time()
    hang_logged = threading.Event()

    def _hang_watchdog():
        if not hang_logged.is_set():
            logger.warning(
                "[HANG DETECTED] Ollama generate for model=%s prompt_size=%d has exceeded 10s",
                ollama_model,
                prompt_size,
            )

    watchdog = threading.Timer(10.0, _hang_watchdog)
    watchdog.start()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        response_text = str(data.get("response", ""))
        duration = time.time() - start

        # Log token usage if available
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)
        total_tokens = data.get("eval_count", 0) + prompt_tokens

        logger.info(
            "Ollama generate SUCCESS: model=%s prompt_size=%d response_size=%d latency=%.2fs prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            ollama_model,
            prompt_size,
            len(response_text),
            duration,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
        return response_text
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
        duration = time.time() - start
        logger.warning(
            "Ollama generate FAILED after %.2fs (using deterministic fallback): %s",
            duration,
            exc,
        )
        return None
    finally:
        hang_logged.set()
        watchdog.cancel()


def generate_explanation(
    entity: str,
    metric: str,
    score: float,
    components: dict[str, Any],
    persona_name: str = "Engineer",
    persona: "Persona" | None = None,
    context7_summary: str = "",
    intent: dict[str, Any] | None = None,
    supporting_signals: list[str] | None = None,
    topics: list[str] | None = None,
) -> Explanation:
    """Generate an explanation for an Opportunity.

    Prefer Ollama when configured; otherwise use deterministic templates.
    Scores are computed separately and passed in; this function never
    recalculates scores.
    """
    intent_type = _intent_dict(intent).get("intent", "Opportunity Discovery")
    domain = _intent_dict(intent).get("domain", "")
    topic_list = ", ".join(topics or [])
    signal_list = "\n".join(f"- {s}" for s in (supporting_signals or [])[:12])
    prompt = (
        "You are a senior technology analyst writing for an industry audience. "
        "Given the evidence cluster below, return a JSON object with exactly these keys: "
        "title, category, categories, why_now, who_benefits, "
        "recommended_action, supporting_evidence, summary.\n\n"
        f"Intent: {intent_type}. This intent must shape the title, category, and tone.\n"
        "- Skill Learning / Career Development: the title should be a skill/technology name, "
        "  category must be 'Skill', recommended_action should be a concrete learning path, "
        "  and summary should suggest example practice projects.\n"
        "- Technology Evaluation: the title should be an assessment of the primary technology, "
        "  category must be 'Technology', recommended_action should list alternatives and evaluation criteria.\n"
        "- Opportunity Discovery / Product Ideas / Business Opportunities: the title should be a specific "
        "  product/platform/infrastructure concept, NOT a bare skill or technology name.\n\n"
        "Naming rules for 'title':\n"
        "- Write a concise, analyst-style name (5 words or fewer).\n"
        "- For opportunities, prefer: specializations, capabilities, disciplines, market categories, engineering domains.\n"
        "- Avoid unless explicitly supported by evidence: 'Ecosystem Growth', 'Roadmap', 'Market Trend', 'Growth Opportunity'.\n"
        "- Do not use repository names. Repository names are evidence, not titles.\n"
        "- For learning/career queries, the title should simply be the skill or technology.\n\n"
        "The 'category' must be exactly one of: Skill, Technology, Product, Platform, Service, Business.\n"
        "The 'categories' is a list of relevant categories from the same allowed set.\n\n"
        f"Intent: {intent_type}\n"
        f"Domain: {domain}\n"
        f"Topic/Entity: {entity}\n"
        f"Query topics: {topic_list}\n"
        f"Evidence Metric: {metric}\n"
        f"Score (0-100): {score:.1f}\n"
        f"Score components: {json.dumps(components)}\n"
        f"Context7 documentation summary: {context7_summary}\n\n"
        "Clustered evidence:\n"
        f"{signal_list}\n\n"
        "Generate the JSON now."
    )
    response = _ollama_generate(prompt)
    if response:
        try:
            parsed = json.loads(response)
            if not isinstance(parsed, dict):
                raise ValueError("LLM metadata JSON was not a JSON object")
            title = str(parsed.get("title", "")).strip()
            if not title:
                title = _market_title(entity, metric, intent)
            title = strip_forced_mcp_prefix(title, None, intent, entity)
            category = str(parsed.get("category", "Product")).strip()
            if category not in {"Skill", "Technology", "Product", "Platform", "Service", "Business"}:
                category = _deterministic_category(entity, metric, intent)
            raw_categories = parsed.get("categories")
            categories: list[str] = []
            if isinstance(raw_categories, list):
                categories = [str(c) for c in raw_categories]
            if not categories:
                categories = _deterministic_categories(entity, metric, category, intent)
            return Explanation(
                title=title,
                category=category,
                categories=categories,
                why_now=str(parsed["why_now"]),
                who_benefits=str(parsed["who_benefits"]),
                recommended_action=str(parsed["recommended_action"]),
                supporting_evidence=str(parsed["supporting_evidence"]),
                summary=str(parsed["summary"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            pass
    return _template_explanation(entity, metric, score, components, persona_name, persona, context7_summary, intent)


def _extract_json(response: str) -> Any:
    """Parse a JSON response, stripping any Markdown code fences."""
    text = response.strip()
    if text.startswith("```"):
        # Extract the first fenced code block, optionally labelled json.
        match = re.match(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    return json.loads(text)


def analyze_mcp_result(
    server: str,
    tool: str,
    raw_data: str,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use Ollama to extract strategic signal intelligence from raw MCP output.

    Returns a dict with keys: friction, gaps, signals, questions.
    """
    if not raw_data or not raw_data.strip():
        return {}

    primary = str(_intent_dict(intent).get("primary_technology", "") or "")
    if not primary and isinstance(intent, dict):
        topics = [str(t) for t in intent.get("topics", []) if t]
        if topics:
            primary = topics[0]
    if not primary:
        primary = "the queried topic"

    prompt = (
        f"You are a senior technology signal analyst. Analyze the raw {server}.{tool} result below for the topic \"{primary}\".\n"
        "Extract strategic signal intelligence. Return a JSON object with exactly these keys:\n"
        '- "friction": list of workflow frictions / operational pain points (max 5, concise, specific)\n'
        '- "gaps": list of missing tools, infrastructure gaps, or ecosystem bottlenecks (max 5, concise). Think across categories such as security, marketplace/curation, observability/monitoring, testing, governance/compliance, and deployment.\n'
        '- "signals": list of concrete market signals such as demand patterns, adoption blockers, scaling challenges (max 5, concise)\n'
        '- "questions": list of high-value follow-up questions a founder would ask (max 3)\n\n'
        "Rules: Be specific. Do not restate the query as a finding. Avoid generic statements like 'X is growing'. "
        "Use only evidence in the raw data. Keep each item one sentence.\n\n"
        f"Raw data:\n{raw_data[:4000]}"
    )

    response = _ollama_generate(prompt, format="json")
    if not response:
        return {}

    try:
        parsed = _extract_json(response)
        if isinstance(parsed, dict):
            return {
                k: v
                for k, v in parsed.items()
                if k in ("friction", "gaps", "signals", "questions") and isinstance(v, list)
            }
    except (json.JSONDecodeError, TypeError, KeyError):
        logger.warning("Failed to parse MCP analysis for %s.%s", server, tool)

    return {}


def _signal_summary(signals: list[dict[str, Any]], max_signals: int = 40) -> str:
    """Build a compact, analysis-friendly summary of collected signals."""
    lines: list[str] = []
    for s in sorted(
        (sig for sig in signals if isinstance(sig, dict)),
        key=lambda x: float(x.get("evidence_quality", 0) or 0),
        reverse=True,
    )[:max_signals]:
        entity = str(s.get("entity", "")).strip() or "n/a"
        metric = str(s.get("metric", "")).strip() or "n/a"
        value = str(s.get("value", "")).strip()[:300]
        lines.append(f"- {entity} [{metric}]: {value}")
    return "\n".join(lines)


def synthesize_trends(
    signals: list[dict[str, Any]],
    intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Use Ollama to synthesize repository and market signals into market trends.

    Framework: Signals -> Trend -> Friction -> Gap.
    """
    if not signals:
        return []

    primary = str(_intent_dict(intent).get("primary_technology", "") or "")
    if not primary and isinstance(intent, dict):
        topics = [str(t) for t in intent.get("topics", []) if t]
        if topics:
            primary = topics[0]

    signals_text = _signal_summary(signals, max_signals=80)
    prompt = (
        f"You are a technology market analyst. Synthesize the signals below into 3-5 high-confidence market trends for \"{primary or 'the queried topic'}\".\n"
        "Framework: Signals -> Trend -> Friction -> Gap.\n\n"
        "Return a JSON object with key \"trends\" containing a list. Each trend must have:\n"
        '- "name": concise market trend name (not a repository name, not a category label like "MCP Platforms")\n'
        '- "summary": one sentence describing the pattern and its business implication\n'
        '- "confidence": integer 0-100\n'
        '- "evidence_count": integer\n'
        '- "evidence_quality": integer 0-100\n'
        '- "supporting_signals": list of concrete entity names / URLs supporting it (max 6)\n'
        '- "friction": the workflow friction or operational pain driving the trend\n'
        '- "gap": the missing capability or infrastructure gap. Prefer gaps in security, marketplace/curation, observability, testing, governance/compliance, or deployment infrastructure.\n\n'
        "Rules: Avoid generic labels that simply restate the query. Be specific about what is missing or broken. "
        "Prioritize trends that imply a commercial infrastructure opportunity.\n\n"
        f"Signals:\n{signals_text}"
    )

    response = _ollama_generate(prompt, format="json")
    if not response:
        return []

    try:
        parsed = _extract_json(response)
        trends = parsed.get("trends") if isinstance(parsed, dict) else parsed
        if isinstance(trends, list):
            return [
                {
                    "name": str(t.get("name", "")).strip(),
                    "summary": str(t.get("summary", "")).strip(),
                    "confidence": int(t.get("confidence", 0) or 0),
                    "evidence_count": int(t.get("evidence_count", 0) or 0),
                    "evidence_quality": int(t.get("evidence_quality", 0) or 0),
                    "supporting_signals": [str(s) for s in t.get("supporting_signals", []) if s][:6],
                    "friction": str(t.get("friction", "")).strip(),
                    "gap": str(t.get("gap", "")).strip(),
                }
                for t in trends
                if isinstance(t, dict) and str(t.get("name", "")).strip()
            ]
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning("Failed to parse trend synthesis response")

    return []


def generate_business_theses(
    signals: list[dict[str, Any]],
    trends: list[dict[str, Any]],
    persona: "Persona" | None,
    intent: dict[str, Any] | None = None,
    context7_summaries: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Use Ollama to generate a small set of high-quality, evidence-backed opportunities.

    Output follows the framework: Signals -> Trend -> Friction -> Gap -> Opportunity -> Business Thesis.
    """
    # Log input data size
    logger.info("=== OPPORTUNITY GENERATION INPUT ===")
    logger.info("Raw signals count: %d", len(signals))
    logger.info("Trends count: %d", len(trends))
    logger.info("Context7 summaries: %d", len(context7_summaries) if context7_summaries else 0)

    # Use research depth to determine signal limit (lazy import to avoid circular dependency)
    max_signals = 80
    max_trends = 6
    try:
        from ode.research import ResearchDepth, get_research_depth
        research_depth = get_research_depth(intent)
        depth_config = ResearchDepth.get_config(research_depth)
        max_signals = depth_config["signal_summary_limit"]
        max_trends = depth_config["max_trends"]
    except ImportError:
        pass  # Use defaults if circular import

    signals_text = _signal_summary(signals, max_signals=max_signals)
    trend_lines = []
    for t in trends[:max_trends]:
        if isinstance(t, dict):
            trend_lines.append(
                f"- {t.get('name')}: {t.get('summary')} (friction: {t.get('friction')}; gap: {t.get('gap')})"
            )
    trends_text = "\n".join(trend_lines)

    context7_text = ""
    if context7_summaries:
        for library, summary in list(context7_summaries.items())[:5]:
            context7_text += f"\n- {library}: {summary[:500]}"

    primary = str(_intent_dict(intent).get("primary_technology", "") or "")
    if not primary and isinstance(intent, dict):
        topics = [str(t) for t in intent.get("topics", []) if t]
        if topics:
            primary = topics[0]
    if not primary:
        primary = "the queried topic"

    persona_name = persona.name if persona else "Engineer"
    persona_goals = ", ".join(persona.goals[:3]) if persona and persona.goals else "build valuable technology"
    persona_skills = ", ".join(persona.skill_profile[:3]) if persona and persona.skill_profile else "software engineering"

    # Get technology profile for category-specific guidance
    from ode.technology_resolver import TechnologyResolver
    resolver = TechnologyResolver()
    resolved = resolver.resolve(primary, intent)
    profile = resolved.primary_profile if resolved else None

    prompt_intro = f'You are a technology opportunity analyst evaluating startup opportunities around "{primary}".\n'
    prompt_intro += "Given the collected signals and market trends below, generate one "
    prompt_intro += "exceptional, differentiated business thesis for EACH trend provided.\n\n"

    prompt_parts = [
        prompt_intro,
        "Return a JSON object with key \"opportunities\" containing a list. The list must contain exactly one ",
        "opportunity per trend, in the same order as the trends below. Each opportunity must have exactly these keys:\n",
        '- "target_trend": the exact trend name this opportunity addresses\n',
        f'- "title": a specific, investable opportunity title rooted in the ACTUAL signals for "{primary}". The title MUST reflect what the signals actually discuss. If signals mention tracing/observability, title should be about tracing/observability. If signals mention evaluation/benchmarking, title should be about evaluation. If signals mention migration/upgrades, title should be about migration tooling. DO NOT use generic templates.\n',
        '- "core_problem": the concrete problem being solved (2-3 sentences) - MUST cite specific signal content\n',
        '- "why_existing_solutions_fail": why current tools / ecosystem fail to solve it (2-3 sentences) - MUST cite specific signal content\n',
        '- "target_users": who urgently needs this - MUST be derived from signal content\n',
        '- "why_now": market timing / why this moment creates the opportunity - MUST cite specific signal content\n',
        '- "supporting_evidence": bullet list of concrete evidence from the signals - MUST include specific signal titles and sources\n',
        '- "recommended_action": a concrete, time-boxed action the reader can take (e.g., "Build a PoC of X for Y audience within 2 weeks"), not a product description\n',
        '- "business_model": how it makes money\n',
        '- "risk_assessment": key risks and mitigations\n',
        '- "confidence_score": integer 0-100\n',
        '- "category": one of Product, Platform, Service, Business, Skill\n',
        '- "execution_roadmap": a JSON object with three phases:\n',
        '  - "phase_1": specific MVP/PoC steps for this opportunity (e.g. "Build tracing integration for LangGraph with MLflow")\n',
        '  - "phase_2": specific core product build steps for this opportunity (e.g. "Add evaluation metrics and comparison dashboard")\n',
        '  - "phase_3": specific scale steps for this opportunity (e.g. "Launch with support for multiple observability platforms")\n',
        '  - "build_complexity": one of Low, Medium, High with brief justification\n\n',
        f"TECHNOLOGY CONTEXT:\n",
        f"- Technology: {primary}\n",
        f"- Category: {profile.category if profile else 'General'}\n",
        f"- Core terms: {', '.join(profile.core_terms[:5]) if profile else 'N/A'}\n\n",
        "CRITICAL: All content must be derived from the ACTUAL signals provided below. Do NOT use generic templates or assumptions.\n",
        "- Analyze the signal titles and content to determine what KIND of opportunity makes sense\n",
        "- If signals discuss tracing/observability, generate opportunities about tracing/observability tooling\n",
        "- If signals discuss evaluation/benchmarking, generate opportunities about evaluation frameworks\n",
        "- If signals discuss migration/upgrades, generate opportunities about migration tooling\n",
        "- If signals discuss alternatives/comparison, generate opportunities about competitive analysis tools\n",
        "- NEVER generate a \"Server Marketplace\" opportunity unless signals explicitly discuss discovery/registry mechanisms\n",
        "- NEVER generate a \"Compliance Manager\" opportunity unless signals explicitly discuss governance/compliance\n",
        "- The opportunity type MUST match what the signals actually discuss\n",
        "- supporting_evidence MUST include specific signal titles and sources (e.g. \"Tracing LangGraph with MLflow (Tavily)\")\n",
        "- recommended_action must be a concrete action (build PoC, validate with users, market test), not a product description\n",
        'If you cannot generate specific, evidence-driven content from the signals, omit the field rather than using generic filler.\n\n',
        "Framework per trend: Signals -> Trend -> Friction -> Gap -> Opportunity -> Business Thesis -> Execution Roadmap.\n",
        "Rules:\n",
        "- Each opportunity must be rooted in its trend's friction and gap AND the actual signal content.\n",
        "- Reject generic recommendations that simply restate the query or label a category.\n",
        "- Demand + Evidence + Gap + Timing must all be present for each opportunity.\n",
        f'- Each title must describe a concrete product/platform/infrastructure function that the signals actually support.\n',
        "- Do NOT prepend generic prefixes unless the signals specifically support it.\n",
        "- Reject titles that are just a bare skill, technology, or category name (e.g. 'Go', 'Docker', 'Databases', 'AI'). A title must describe a concrete product, platform, or infrastructure function.\n",
        "- Weak titles to avoid unless the evidence specifically supports only that: generic hubs, generic trackers, generic managers, generic category labels, and bare skill names.\n",
        "- Differentiate each opportunity from the others; avoid overlapping concepts.\n",
        "- Be specific and evidence-backed. Avoid filler and generic summaries.\n",
        "- Execution roadmap phases must be specific to the opportunity, not generic startup phases.\n",
        f"- Target persona: {persona_name} (goals: {persona_goals}; skills: {persona_skills})\n\n",
        f"Signals:\n{signals_text}\n\n",
        f"Trends (one opportunity per trend):\n{trends_text}\n\n",
        f"Documentation gaps:\n{context7_text or 'None'}",
    ]

    prompt = "".join(prompt_parts)

    response = _ollama_generate(prompt, format="json")
    logger.info("Business thesis LLM response: %s", "present" if response else "missing")
    if response:
        logger.info("Business thesis response length: %d", len(response))
        logger.info("Business thesis raw response: %s", response[:500])
    if not response:
        logger.warning("Business thesis LLM response missing, returning empty opportunities")
        return []

    try:
        parsed = _extract_json(response)
        logger.info("Business thesis parsed: opportunities=%d", len(parsed.get("opportunities", [])) if isinstance(parsed, dict) else len(parsed) if isinstance(parsed, list) else 0)
        opportunities = parsed.get("opportunities") if isinstance(parsed, dict) else parsed
        if isinstance(opportunities, list):
            allowed_categories = {"Product", "Platform", "Service", "Business", "Skill"}
            result = [
                {
                    "target_trend": str(o.get("target_trend", "")).strip(),
                    "title": strip_forced_mcp_prefix(str(o.get("title", "")).strip(), primary, intent),
                    "core_problem": str(o.get("core_problem", "")).strip(),
                    "why_existing_solutions_fail": str(o.get("why_existing_solutions_fail", "")).strip(),
                    "target_users": str(o.get("target_users", "")).strip(),
                    "why_now": str(o.get("why_now", "")).strip(),
                    "supporting_evidence": str(o.get("supporting_evidence", "")).strip(),
                    "recommended_action": str(o.get("recommended_action", "")).strip(),
                    "business_model": str(o.get("business_model", "")).strip(),
                    "risk_assessment": str(o.get("risk_assessment", "")).strip(),
                    "confidence_score": int(o.get("confidence_score", 0) or 0),
                    "category": str(o.get("category", "Product")).strip() if str(o.get("category", "")).strip() in allowed_categories else "Product",
                    "execution_roadmap": o.get("execution_roadmap") if o.get("execution_roadmap") else None,
                }
                for o in opportunities
                if isinstance(o, dict) and str(o.get("title", "")).strip()
            ]
            logger.info("Business thesis result: opportunities=%d", len(result))
            for idx, opp in enumerate(result):
                logger.info("Opportunity %d: title=%s, has_roadmap=%s", idx, opp['title'], "yes" if opp.get('execution_roadmap') else "no")
                if opp.get('execution_roadmap'):
                    logger.info("Roadmap: %s", json.dumps(opp['execution_roadmap'], indent=2)[:200])
            return result
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        logger.warning("Failed to parse business thesis response: %s", e)
        logger.warning("Raw response: %s", response[:500])

    return []


def critique_opportunities(
    opportunities: list[dict[str, Any]],
    query: str,
    intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Use Ollama as a skeptical partner to filter weak, generic, or repetitive theses."""
    if not opportunities:
        return []

    opp_text = json.dumps(opportunities, indent=2)
    prompt = (
        f"You are a skeptical investment partner reviewing the opportunity theses below for the query: \"{query}\".\n"
        "Evaluate each with these questions:\n"
        "- Would a founder pay attention to this?\n"
        "- Is it genuinely actionable and specific?\n"
        "- Is it just a category label or generic hub/tool name?\n"
        "- Does it repeat the query?\n"
        "- Is it differentiated from the other opportunities?\n"
        "- Does it address an infrastructure gap (security, marketplace, observability, testing, governance, deployment)?\n"
        "- Are the execution_roadmap phases specific to the opportunity, or generic startup templates?\n"
        "- Is the content technology-specific, or could it apply to any technology?\n\n"
        "Return a JSON object with key \"opportunities\" containing only the kept theses (max 3). "
        "For each kept opportunity, preserve ALL original keys (title, core_problem, why_existing_solutions_fail, target_users, why_now, supporting_evidence, recommended_action, business_model, risk_assessment, confidence_score, category, execution_roadmap, target_trend) and add:\n"
        '- "verdict": "keep"\n'
        '- "critique": one-sentence critique explaining why it passes\n\n'
        "Reject titles that are generic hubs (e.g. '<topic> Hub', '<topic> Project Hub'), generic trackers (e.g. '<topic> IssueTracker'), or generic managers unless the evidence uniquely supports them. Prefer specific infrastructure opportunities. "
        "Reject opportunities with generic execution_roadmap content like 'validate assumptions', 'build product', 'scale solution' - these must be specific to the opportunity. "
        "Reject opportunities with generic narrative content like 'growing ecosystem activity', 'strong developer interest', 'increasing adoption' - these must be specific to the technology. "
        "Do not keep titles that prepend 'MCP' unless the query or primary topic explicitly mentions MCP. "
        "Remove weak theses entirely. If all are weak, return an empty list. If two opportunities are too similar, keep the stronger one.\n\n"
        f"Opportunities:\n{opp_text}"
    )

    response = _ollama_generate(prompt, format="json")
    logger.info("Critique LLM response: %s", "present" if response else "missing")
    if response:
        logger.info("Critique response length: %d", len(response))
        logger.info("Critique raw response: %s", response[:500])
    if not response:
        logger.warning("Critique LLM response missing, returning empty opportunities")
        return []

    try:
        parsed = _extract_json(response)
        logger.info("Critique parsed: opportunities=%d", len(parsed.get("opportunities", [])) if isinstance(parsed, dict) else len(parsed) if isinstance(parsed, list) else 0)
        kept = parsed.get("opportunities") if isinstance(parsed, dict) else parsed
        if isinstance(kept, list) and kept:
            logger.info("Critique result: kept=%d, rejected=%d", len(kept), len(opportunities) - len(kept))
            for idx, opp in enumerate(kept):
                logger.info("Kept opportunity %d: title=%s, verdict=%s, critique=%s",
                           idx, opp.get('title'), opp.get('verdict'), opp.get('critique'))
            return [
                {
                    "target_trend": str(o.get("target_trend", "")).strip(),
                    "title": strip_forced_mcp_prefix(str(o.get("title", "")).strip(), query, intent),
                    "core_problem": str(o.get("core_problem", "")).strip(),
                    "why_existing_solutions_fail": str(o.get("why_existing_solutions_fail", "")).strip(),
                    "target_users": str(o.get("target_users", "")).strip(),
                    "why_now": str(o.get("why_now", "")).strip(),
                    "supporting_evidence": str(o.get("supporting_evidence", "")).strip(),
                    "recommended_action": str(o.get("recommended_action", "")).strip(),
                    "business_model": str(o.get("business_model", "")).strip(),
                    "risk_assessment": str(o.get("risk_assessment", "")).strip(),
                    "confidence_score": int(o.get("confidence_score", 0) or 0),
                    "category": str(o.get("category", "Product")).strip(),
                    "execution_roadmap": o.get("execution_roadmap") if o.get("execution_roadmap") else None,
                    "critique": str(o.get("critique", "")).strip(),
                }
                for o in kept
                if isinstance(o, dict) and str(o.get("title", "")).strip() and str(o.get("verdict", "")).strip().lower() in ("keep", "")
            ][:3]
        logger.warning("Critique returned non-list or empty, returning empty opportunities")
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        logger.warning("Failed to parse critique response: %s", e)
        logger.warning("Raw response: %s", response[:500])

    return opportunities[:3]
