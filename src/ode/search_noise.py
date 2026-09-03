"""Shared noise filtering and acronym expansion for web/search signals."""

from __future__ import annotations

# Phrases that commonly pollute technical searches for ambiguous acronyms like "MCP".
NOISE_PHRASES: list[str] = [
    "audio visual",
    "chicago",
    "microsoft certified professional",
    "management has the right to revise",
]

# Ambiguous acronyms that should be expanded to their full disambiguated form
# before querying search APIs or canonicalizing search-derived concepts.
ACRONYM_EXPANSIONS: dict[str, str] = {
    "mcp": "Model Context Protocol",
}


def contains_noise(text: str) -> bool:
    """Return True when text contains known non-tech search noise."""
    lowered = str(text).lower()
    return any(phrase in lowered for phrase in NOISE_PHRASES)


def expand_acronyms(text: str) -> str:
    """Replace bare acronyms with their full form so context is unambiguous."""
    words = str(text).split()
    expanded: list[str] = []
    for word in words:
        key = word.lower().rstrip(",.!?:;")
        replacement = ACRONYM_EXPANSIONS.get(key)
        if replacement:
            # Preserve original casing heuristic: title-case if the acronym was upper-cased.
            if word[0].isupper():
                replacement = replacement.title()
            expanded.append(replacement)
        else:
            expanded.append(word)
    return " ".join(expanded)
