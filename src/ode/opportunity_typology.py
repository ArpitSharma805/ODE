"""Category-specific opportunity typology for differentiated opportunity detection.

This module defines opportunity types, metrics, and patterns that are specific
to different technology categories, ensuring that opportunities are differentiated
and relevant to the technology's ecosystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ==================== Narrative Structure ====================

@dataclass
class NarrativeStructure:
    """Defines the narrative structure for a technology maturity stage."""

    maturity: str
    tone: str
    emphasis: list[str]
    risk_framing: list[str]
    opportunity_framing: list[str]
    roadmap_style: str
    section_guidance: dict[str, str]


# Maturity-based narrative structures
NARRATIVE_STRUCTURES: dict[str, NarrativeStructure] = {
    "emerging": NarrativeStructure(
        maturity="emerging",
        tone="exploratory, forward-looking",
        emphasis=[
            "what's being built",
            "who's experimenting",
            "what patterns are forming",
            "early adopter feedback",
            "specification evolution",
        ],
        risk_framing=[
            "adoption uncertainty",
            "specification instability",
            "ecosystem fragmentation",
            "lack of established patterns",
            "early mover vs follower tradeoffs",
        ],
        opportunity_framing=[
            "first-mover in defining patterns and tools",
            "establishing best practices",
            "building foundational infrastructure",
            "capturing early adopter mindshare",
        ],
        roadmap_style="experimentation-focused: explore → prototype → contribute to ecosystem",
        section_guidance={
            "opportunity_snapshot": "Focus on being first to define patterns and establish infrastructure",
            "trend_summary": "Highlight experimentation, early adopter feedback, and pattern formation",
            "market_signals": "Emphasize early signals, experimental projects, and community discussions",
            "execution_roadmap": "Structure as: explore → prototype → contribute → establish",
            "recommendation": "Emphasize timing advantages of early entry and pattern definition",
        },
    ),
    "growth": NarrativeStructure(
        maturity="growth",
        tone="energetic, opportunity-rich",
        emphasis=[
            "what's working",
            "what's scaling",
            "where gaps are appearing",
            "adoption patterns",
            "competitive dynamics",
        ],
        risk_framing=[
            "competition intensifying",
            "best practices not yet established",
            "ecosystem fragmentation",
            "talent scarcity",
            "platform lock-in risks",
        ],
        opportunity_framing=[
            "serve growing demand with specialized solutions",
            "establish category leadership",
            "build on proven patterns",
            "target underserved segments",
        ],
        roadmap_style="product-focused: identify niche → build solution → capture segment → scale",
        section_guidance={
            "opportunity_snapshot": "Focus on capturing growing demand and establishing category leadership",
            "trend_summary": "Highlight scaling patterns, adoption trends, and emerging gaps",
            "market_signals": "Emphasize growth metrics, competitive activity, and ecosystem expansion",
            "execution_roadmap": "Structure as: identify niche → build → capture → scale",
            "recommendation": "Emphasize urgency of capturing segment before competition intensifies",
        },
    ),
    "mature": NarrativeStructure(
        maturity="mature",
        tone="analytical, differentiation-focused",
        emphasis=[
            "where innovation is happening at the edges",
            "what's being replaced",
            "consolidation patterns",
            "optimization opportunities",
            "specialization niches",
        ],
        risk_framing=[
            "commoditization",
            "ecosystem consolidation",
            "migration to next generation",
            "diminishing returns",
            "incumbent dominance",
        ],
        opportunity_framing=[
            "improve existing workflows",
            "target underserved segments",
            "specialize for vertical markets",
            "optimize performance/cost",
            "bridge to next generation",
        ],
        roadmap_style="integration-focused: find friction → build tool → integrate deeply → defend niche",
        section_guidance={
            "opportunity_snapshot": "Focus on differentiation, optimization, and underserved segments",
            "trend_summary": "Highlight edge innovation, consolidation patterns, and optimization trends",
            "market_signals": "Emphasize consolidation metrics, incumbent activity, and specialization signals",
            "execution_roadmap": "Structure as: find friction → build → integrate → defend",
            "recommendation": "Emphasize differentiation and niche focus over broad market entry",
        },
    ),
    "declining": NarrativeStructure(
        maturity="declining",
        tone="pragmatic, migration-focused",
        emphasis=[
            "what's replacing it",
            "who's still using it",
            "migration pain points",
            "long-tail support needs",
            "legacy maintenance",
        ],
        risk_framing=[
            "shrinking market",
            "talent exodus",
            "deprecation risk",
            "vendor abandonment",
            "security vulnerabilities",
        ],
        opportunity_framing=[
            "migration tooling",
            "long-tail support",
            "legacy modernization",
            "knowledge transfer",
            "sunset assistance",
        ],
        roadmap_style="transition-focused: identify migration pain → build bridge → sunset → exit",
        section_guidance={
            "opportunity_snapshot": "Focus on migration tooling, long-tail support, and legacy modernization",
            "trend_summary": "Highlight migration patterns, replacement technologies, and decline indicators",
            "market_signals": "Emphasize declining metrics, migration activity, and replacement adoption",
            "execution_roadmap": "Structure as: identify pain → build bridge → support → exit",
            "recommendation": "Emphasize migration planning and exit strategy over new investment",
        },
    ),
}


def get_narrative_structure(maturity: str) -> NarrativeStructure | None:
    """Get the narrative structure for a technology maturity stage.

    Args:
        maturity: Technology maturity stage (emerging, growth, mature, declining)

    Returns:
        NarrativeStructure for the maturity, or None if not found
    """
    return NARRATIVE_STRUCTURES.get(maturity)


def get_section_guidance(
    section_name: str,
    maturity: str,
) -> str:
    """Get narrative guidance for a specific report section based on maturity.

    Args:
        section_name: Name of the report section (e.g., "opportunity_snapshot")
        maturity: Technology maturity stage

    Returns:
        Guidance string for the section
    """
    structure = get_narrative_structure(maturity)
    if structure and section_name in structure.section_guidance:
        return structure.section_guidance[section_name]

    # Generic fallback
    return "Provide specific, evidence-based content for this section"


def build_maturity_prompt_additions(
    maturity: str,
    section_name: str,
) -> str:
    """Build prompt additions based on maturity and section.

    Args:
        maturity: Technology maturity stage
        section_name: Name of the report section

    Returns:
        String with maturity-specific prompt additions
    """
    structure = get_narrative_structure(maturity)
    if not structure:
        return ""

    section_guidance = get_section_guidance(section_name, maturity)

    return f"""
MATURITY CONTEXT:
|- This technology is in the {maturity} stage
|- Tone: {structure.tone}
|- Emphasis: {', '.join(structure.emphasis[:3])}
|- Risk framing: {', '.join(structure.risk_framing[:2])}
|- Opportunity framing: {', '.join(structure.opportunity_framing[:2])}

SECTION-SPECIFIC GUIDANCE ({section_name}):
{section_guidance}
"""


def validate_maturity_consistency(
    content: str,
    maturity: str,
) -> tuple[bool, str]:
    """Validate that content matches the expected tone for the maturity stage.

    Args:
        content: The generated content
        maturity: The technology maturity stage

    Returns:
        Tuple of (is_consistent, reason)
    """
    structure = get_narrative_structure(maturity)
    if not structure:
        return True, "Maturity not in structure, allowing generic content"

    content_lower = content.lower()

    # Check for maturity-inappropriate language
    if maturity == "emerging":
        # Emerging should not talk about "established", "mature", "commodity"
        inappropriate = ["established", "mature", "commodity", "consolidated", "standardized"]
        for term in inappropriate:
            if term in content_lower:
                return False, f"Contains mature-stage term '{term}' in emerging-stage content"

    elif maturity == "mature":
        # Mature should not talk about "experimental", "uncertain", "unproven"
        inappropriate = ["experimental", "uncertain", "unproven", "speculative", "early stage"]
        for term in inappropriate:
            if term in content_lower:
                return False, f"Contains emerging-stage term '{term}' in mature-stage content"

    return True, "Content is consistent with maturity stage"


@dataclass
class OpportunityTypology:
    """Defines opportunity types and patterns for a technology category."""

    category: str
    opportunity_types: list[str]
    metrics_that_matter: list[str]
    example_opportunities: list[str]
    common_patterns: list[str]


# Category-specific opportunity typologies
OPPORTUNITY_TYPOLOGIES: dict[str, OpportunityTypology] = {
    "llm-integration-protocol": OpportunityTypology(
        category="llm-integration-protocol",
        opportunity_types=[
            "connector/adapter (build a bridge between X and Y)",
            "reference implementation (production-ready template for use case Z)",
            "developer tooling (debugging, testing, monitoring for the protocol)",
            "marketplace/registry (discovery and distribution of integrations)",
            "security/governance (access control, audit, compliance layer)",
        ],
        metrics_that_matter=[
            "number of integrations/connectors",
            "protocol adoption by platforms",
            "active server/client implementations",
        ],
        example_opportunities=[
            "MCP Security Gateway: An access control and audit layer for MCP servers",
            "MCP Registry: A centralized marketplace for discovering and distributing MCP integrations",
            "MCP Debugger: A debugging tool for inspecting MCP server-client communication",
        ],
        common_patterns=[
            "Missing production-ready templates for specific use cases",
            "Lack of standard security and governance patterns",
            "Fragmented tooling ecosystem",
            "No centralized discovery mechanism",
        ],
    ),
    "agent-orchestration-framework": OpportunityTypology(
        category="agent-orchestration-framework",
        opportunity_types=[
            "vertical agent (specialized multi-agent system for industry X)",
            "agent pattern library (reusable workflow patterns)",
            "observability/debugging (agent trace visualization, state inspection)",
            "evaluation framework (benchmarking agent performance)",
            "deployment infrastructure (hosting, scaling agent workflows)",
        ],
        metrics_that_matter=[
            "agent architectures being built",
            "production deployments",
            "framework comparison benchmarks",
        ],
        example_opportunities=[
            "LangGraph E-commerce Agent: A specialized multi-agent system for e-commerce workflows",
            "LangGraph Pattern Library: A collection of reusable agent workflow patterns",
            "LangGraph Inspector: A visualization tool for agent state and execution traces",
        ],
        common_patterns=[
            "Lack of domain-specific agent templates",
            "Difficulty debugging complex agent workflows",
            "No standard evaluation benchmarks",
            "Challenges in production deployment",
        ],
    ),
    "frontend-framework": OpportunityTypology(
        category="frontend-framework",
        opportunity_types=[
            "component library (specialized UI kit for domain X)",
            "performance tooling (bundle analysis, rendering optimization)",
            "developer experience (scaffolding, migration, testing)",
            "design system integration (bridging design tools and code)",
            "full-stack framework (opinionated architecture on top of the framework)",
        ],
        metrics_that_matter=[
            "npm downloads",
            "component ecosystem size",
            "build tooling adoption",
        ],
        example_opportunities=[
            "React Analytics Dashboard: A specialized component library for analytics dashboards",
            "React Bundle Analyzer: Advanced bundle analysis and optimization tooling",
            "React Design System Bridge: Tool for integrating design tools with React components",
        ],
        common_patterns=[
            "Need for domain-specific component libraries",
            "Performance optimization challenges",
            "Design system integration friction",
            "Migration and upgrade pain points",
        ],
    ),
    "container-orchestration": OpportunityTypology(
        category="container-orchestration",
        opportunity_types=[
            "operator/controller (automated operations for workload type X)",
            "developer platform (PaaS-like abstraction on top of orchestration)",
            "security/policy (network policy, admission control, compliance)",
            "cost optimization (resource right-sizing, spot instance management)",
            "edge/hybrid deployment (specialized deployment topology)",
        ],
        metrics_that_matter=[
            "CNCF project adoption",
            "enterprise deployment scale",
            "operator ecosystem growth",
        ],
        example_opportunities=[
            "Kubernetes AI Operator: Automated operations for AI/ML workloads",
            "Kubernetes Cost Optimizer: Resource right-sizing and spot instance management",
            "Kubernetes Policy Engine: Network policy and admission control framework",
        ],
        common_patterns=[
            "Need for workload-specific operators",
            "Cost management challenges",
            "Security and compliance complexity",
            "Edge deployment requirements",
        ],
    ),
    "systems-programming-language": OpportunityTypology(
        category="systems-programming-language",
        opportunity_types=[
            "runtime/library (core system library for domain X)",
            "tooling (build tools, package managers, IDE integration)",
            "FFI/interop (bridging to other languages and ecosystems)",
            "embedded/specialized (targeting specific hardware or domains)",
            "migration tooling (helping teams adopt the language)",
        ],
        metrics_that_matter=[
            "crates.io downloads",
            "compiler performance",
            "ecosystem growth rate",
        ],
        example_opportunities=[
            "Rust Web Framework: A high-performance web framework for Rust",
            "Rust FFI Generator: Automated FFI bindings for C/C++ libraries",
            "Rust Migration Assistant: Tool for helping teams migrate from C++ to Rust",
        ],
        common_patterns=[
            "Need for domain-specific libraries",
            "FFI and interop challenges",
            "Adoption and migration friction",
            "Tooling gaps",
        ],
    ),
}


def get_opportunity_typology(category: str) -> OpportunityTypology | None:
    """Get the opportunity typology for a technology category.

    Args:
        category: Technology category (e.g., "agent-orchestration-framework")

    Returns:
        OpportunityTypology for the category, or None if not found
    """
    return OPPORTUNITY_TYPOLOGIES.get(category)


def get_opportunity_types_for_category(category: str) -> list[str]:
    """Get the opportunity types for a technology category.

    Args:
        category: Technology category

    Returns:
        List of opportunity types, or generic types if category not found
    """
    typology = get_opportunity_typology(category)
    if typology:
        return typology.opportunity_types

    # Generic fallback
    return [
        "tool/library (build a tool for X)",
        "platform/service (build a platform for Y)",
        "integration (connect A and B)",
        "optimization (improve X)",
    ]


def get_metrics_for_category(category: str) -> list[str]:
    """Get the metrics that matter for a technology category.

    Args:
        category: Technology category

    Returns:
        List of metrics, or generic metrics if category not found
    """
    typology = get_opportunity_typology(category)
    if typology:
        return typology.metrics_that_matter

    # Generic fallback
    return [
        "adoption rate",
        "community activity",
        "signal volume",
    ]


def validate_opportunity_category_match(
    opportunity_title: str,
    category: str,
) -> tuple[bool, str]:
    """Validate that an opportunity matches the expected patterns for its category.

    Args:
        opportunity_title: The title of the opportunity
        category: The technology category

    Returns:
        Tuple of (is_valid, reason)
    """
    typology = get_opportunity_typology(category)
    if not typology:
        return True, "Category not in typology, allowing generic opportunity"

    # Check if the opportunity title matches expected patterns
    opportunity_lower = opportunity_title.lower()

    # Check against common patterns
    for pattern in typology.common_patterns:
        if pattern.lower() in opportunity_lower:
            return True, f"Matches common pattern: {pattern}"

    # Check against opportunity types
    for opp_type in typology.opportunity_types:
        type_keywords = opp_type.split("(")[0].strip().lower().split()
        if any(keyword in opportunity_lower for keyword in type_keywords):
            return True, f"Matches opportunity type: {opp_type}"

    return False, f"Does not match expected patterns for {category}. Expected patterns: {typology.common_patterns[:3]}"


def suggest_opportunity_titles(
    category: str,
    technology_name: str,
    count: int = 3,
) -> list[str]:
    """Suggest opportunity titles for a technology category.

    Args:
        category: Technology category
        technology_name: Name of the technology
        count: Number of suggestions to generate

    Returns:
        List of suggested opportunity titles
    """
    typology = get_opportunity_typology(category)
    if not typology:
        return [
            f"{technology_name} Tool for X",
            f"{technology_name} Platform for Y",
            f"{technology_name} Integration for Z",
        ]

    suggestions = []
    for i, example in enumerate(typology.example_opportunities[:count]):
        # Replace generic tech name with specific tech name
        suggestion = example.replace("MCP", technology_name).replace("LangGraph", technology_name).replace("React", technology_name).replace("Kubernetes", technology_name).replace("Rust", technology_name)
        suggestions.append(suggestion)

    return suggestions
