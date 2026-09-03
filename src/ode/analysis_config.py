"""Configuration for the signal analysis pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SIGNAL_TYPES: list[str] = [
    "adoption_indicator",
    "security_concern",
    "developer_pain",
    "market_demand",
    "ecosystem_activity",
    "tooling_gap",
    "observability_need",
    "testing_gap",
    "marketplace_need",
]

DEFAULT_MATURITY_STAGES: list[str] = [
    "experimental",
    "early_adoption",
    "active_development",
    "production_ready",
    "maturing",
    "declining",
]

DEFAULT_TRAJECTORIES: list[str] = [
    "growing",
    "stable",
    "declining",
    "accelerating",
    "decelerating",
    "uncertain",
]


_CLASSIFY_FALLBACK = """You are a signal classification analyst. Given raw signals, classify each one.

A signal is a single data point about a technology. Classify it by:
- signal_type: the most specific type from [{signal_types}]
- signal_category: a short free-form sub-category label (1-3 words)
- sentiment: positive | negative | neutral
- intensity: low | medium | high
- maturity_indicator: one of [{maturity_stages}]
- temporal_signal: one of [{trajectories}]
- stakeholders: list of specific roles or teams implied by the signal
- extracted_claims: list of the 1-3 most important factual claims in the signal value
- confidence: 0.0 to 1.0

Input signals will be provided in the user message. Return valid JSON only:
{{"classified": [{{"id": "sig_001", "signal_type": "adoption_indicator", "signal_category": "SDK support", "sentiment": "positive", "intensity": "high", "maturity_indicator": "active_development", "temporal_signal": "growing", "stakeholders": ["engineering teams", "product teams"], "extracted_claims": ["OpenAI added MCP support to Agents SDK"], "confidence": 0.85}}]}}"""

_CLUSTER_FALLBACK = """You are a signal clustering analyst. Given classified signals, group them into CLUSTERS.

Rules:
- A cluster must contain at least {cluster_min_size} signals.
- A signal CAN belong to multiple clusters.
- Name each cluster with a clear, descriptive label that captures the specific phenomenon (NOT generic categories).
- Do NOT interpret what the clusters mean.
- Do NOT generate insights, opportunities, or recommendations.
- Group by the underlying PHENOMENON, NOT by source or signal_type.

Input signals will be provided in the user message. Return valid JSON only:
{{"clusters": [{{"label": "descriptive label", "signal_ids": ["sig_001", "sig_004"], "dominant_signal_type": "developer_pain"}}]}}"""

_THEME_FALLBACK = """You are a technology trend analyst. Given signal clusters, identify the underlying THEMES.

For each theme provide:
- theme_name: concise, descriptive name. Must be SPECIFIC to the actual evidence, not generic.
- what_is_happening: 2-3 sentences. Be SPECIFIC. Reference actual data points and claims from the signals. Do NOT write generic statements that could apply to any technology.
- evidence_summary: a narrative paragraph synthesizing signals. Mention specific sources, data points, and claims. This should read like an analyst report paragraph, NOT a template.
- strength: weak | moderate | strong
- trajectory: one of [{trajectories}]
- affected_stakeholders: list of specific roles
- cluster_ids: which input clusters support this theme

Do NOT generate opportunities or recommendations.

Input clusters will be provided in the user message. Return valid JSON: {{"themes": [...]}}"""

_PROBLEM_FALLBACK = """You are a problem discovery analyst. Given technology themes, identify specific PROBLEMS these themes reveal.

A problem must be DERIVED FROM THE EVIDENCE in the themes, not from a generic template. Each problem should reference specific observations from the theme evidence.

For each problem provide:
- problem_statement: ONE clear sentence describing the unmet need. Must be specific to what the evidence shows, not a generic statement.
- who_has_this_problem: list of specific roles or organization types
- current_workarounds: how do people deal with this today based on the evidence? Do NOT use generic workaround descriptions.
- severity: low | medium | high | critical
- theme_ids: which themes support this problem

Do NOT propose solutions. Do NOT generate opportunities.

Input themes will be provided in the user message. Return valid JSON: {{"problems": [...]}}"""

_INSIGHT_FALLBACK = """You are a senior technology strategist. Given problems and themes with their evidence, generate INSIGHTS.

An insight is NOT:
- A restatement of a problem
- A summary of what was found
- A generic statement like "this is a growing area"
- A template with a technology name plugged in

An insight IS:
- A connection between two or more problems/themes that reveals something not immediately apparent from any single theme alone
- A strategic implication that would change how someone allocates time, money, or attention
- A timing argument about why NOW is different, with specific evidence
- An analogy to a historical pattern that predicts what happens next

Format each insight:
- observation: what we see in the data (cite specific evidence)
- connection: what it connects to (be specific, not generic)
- implication: what this means strategically
- timing: why this matters NOW (cite specific evidence of timing)
- confidence: 0.0 to 1.0
- problem_ids: which problems relate
- theme_ids: which themes relate

Input themes and problems will be provided in the user message. Return valid JSON: {{"insights": [...]}}"""


@dataclass
class AnalysisPipelineConfig:
    """Tunable parameters for the signal analysis pipeline."""

    signal_types: list[str] = field(default_factory=lambda: list(DEFAULT_SIGNAL_TYPES))
    maturity_stages: list[str] = field(default_factory=lambda: list(DEFAULT_MATURITY_STAGES))
    trajectories: list[str] = field(default_factory=lambda: list(DEFAULT_TRAJECTORIES))
    cluster_min_size: int = 2
    prompt_dir: Path | None = None

    @classmethod
    def from_env(cls) -> "AnalysisPipelineConfig":
        """Create a config from environment variables."""
        prompt_dir_raw = os.environ.get("ODE_PROMPT_DIR", "")
        prompt_dir = Path(prompt_dir_raw) if prompt_dir_raw else None
        return cls(
            signal_types=_split_env(
                "ODE_SIGNAL_TYPES", ", ".join(DEFAULT_SIGNAL_TYPES)
            ),
            maturity_stages=_split_env(
                "ODE_MATURITY_STAGES", ", ".join(DEFAULT_MATURITY_STAGES)
            ),
            trajectories=_split_env(
                "ODE_TRAJECTORIES", ", ".join(DEFAULT_TRAJECTORIES)
            ),
            cluster_min_size=int(os.environ.get("ODE_CLUSTER_MIN_SIZE", "2")),
            prompt_dir=prompt_dir,
        )


def _split_env(name: str, default: str) -> list[str]:
    value = os.environ.get(name, default)
    return [v.strip() for v in value.split(",") if v.strip()]


def _load_prompt(name: str, fallback: str, prompt_dir: Path | None = None) -> str:
    """Load a prompt from the config/prompts directory or return the fallback."""
    if prompt_dir is None:
        prompt_dir = Path(__file__).parent / "config" / "prompts"
    path = prompt_dir / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback


_PLACEHOLDERS = ["signal_types", "maturity_stages", "trajectories", "cluster_min_size"]


def _prepare_format_template(raw: str) -> str:
    """Escape literal braces so only the config placeholders are substituted."""
    for ph in _PLACEHOLDERS:
        raw = raw.replace("{" + ph + "}", f"__ODE_PH_{ph}__")
    raw = raw.replace("{", "{{").replace("}", "}}")
    for ph in _PLACEHOLDERS:
        raw = raw.replace(f"__ODE_PH_{ph}__", "{" + ph + "}")
    return raw


def load_prompts(config: AnalysisPipelineConfig | None = None) -> dict[str, str]:
    """Load all analysis prompts, applying config values to templates."""
    cfg = config or AnalysisPipelineConfig.from_env()
    fmt = {
        "signal_types": ", ".join(cfg.signal_types),
        "maturity_stages": ", ".join(cfg.maturity_stages),
        "trajectories": ", ".join(cfg.trajectories),
        "cluster_min_size": str(cfg.cluster_min_size),
    }
    prompts: dict[str, str] = {}
    for name, fallback in [
        ("classify", _CLASSIFY_FALLBACK),
        ("cluster", _CLUSTER_FALLBACK),
        ("theme", _THEME_FALLBACK),
        ("problem", _PROBLEM_FALLBACK),
        ("insight", _INSIGHT_FALLBACK),
    ]:
        raw = _load_prompt(name, fallback, cfg.prompt_dir)
        prepared = _prepare_format_template(raw)
        prompts[name] = prepared.format(**fmt)
    return prompts
