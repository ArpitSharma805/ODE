"""Report serialization utilities for proper JSON handling in the UI.

This module provides serialization functions to handle structured data
and prevent JSON from appearing as raw strings in the frontend.
"""

from __future__ import annotations

import json
from typing import Any


def serialize_section(section_content: Any) -> dict[str, Any]:
    """Serialize a report section for proper UI rendering.

    This function handles different content types and ensures structured
    data is properly formatted for the frontend.

    Args:
        section_content: The content to serialize (dict, str, list, or None)

    Returns:
        Dictionary with type information and properly formatted content
    """
    if isinstance(section_content, dict):
        # Structured data — format it properly for the UI
        return {
            "type": "structured",
            "data": section_content
        }
    elif isinstance(section_content, str):
        # Check if it's accidentally JSON-as-string
        try:
            parsed = json.loads(section_content)
            return {
                "type": "structured",
                "data": parsed
            }
        except (json.JSONDecodeError, TypeError):
            # Regular text content
            return {
                "type": "text",
                "content": section_content
            }
    elif isinstance(section_content, list):
        # List content
        return {
            "type": "list",
            "items": section_content
        }
    elif section_content is None:
        return {
            "type": "unavailable",
            "content": "Unable to generate from available evidence"
        }
    else:
        # Fallback for other types
        return {
            "type": "raw",
            "content": str(section_content)
        }


def serialize_report(report_data: dict[str, Any]) -> dict[str, Any]:
    """Serialize an entire report for proper UI rendering.

    This function processes all sections in a report and ensures
    they are properly formatted for the frontend.

    Args:
        report_data: Dictionary containing report sections

    Returns:
        Dictionary with all sections properly serialized
    """
    serialized = {}

    # Common report section keys
    section_keys = [
        "opportunity_snapshot",
        "trend_summary",
        "market_signals",
        "execution_roadmap",
        "recommendation",
        "top_opportunities",
        "themes",
        "problems",
        "insights",
        "answer"
    ]

    for key in section_keys:
        if key in report_data:
            serialized[key] = serialize_section(report_data[key])

    # Copy over non-section fields
    for key, value in report_data.items():
        if key not in section_keys:
            serialized[key] = value

    return serialized


def clean_json_string(content: str) -> str:
    """Clean a string that might contain JSON.

    This function attempts to detect and format JSON strings
    that should be displayed as structured data.

    Args:
        content: String content that might contain JSON

    Returns:
        Cleaned content (either parsed JSON or original string)
    """
    if not isinstance(content, str):
        return content

    # Try to parse as JSON
    try:
        parsed = json.loads(content)
        # If successful, return as formatted JSON string
        return json.dumps(parsed, indent=2)
    except (json.JSONDecodeError, TypeError):
        # Not JSON, return as-is
        return content


def format_opportunity_for_ui(opportunity: dict[str, Any]) -> dict[str, Any]:
    """Format an opportunity dictionary for UI display.

    This ensures opportunity fields are properly formatted and
    any structured data is handled correctly.

    Args:
        opportunity: Opportunity dictionary

    Returns:
        Formatted opportunity dictionary
    """
    formatted = {}

    # Text fields that should remain as strings
    text_fields = [
        "title", "description", "why_existing_solutions_fail",
        "who_benefits", "why_now", "supporting_evidence",
        "recommended_action", "business_model", "risk_assessment"
    ]

    for field in text_fields:
        if field in opportunity:
            value = opportunity[field]
            if isinstance(value, str):
                # Clean any JSON strings
                formatted[field] = clean_json_string(value)
            else:
                formatted[field] = value

    # Numeric fields
    numeric_fields = ["score", "confidence"]
    for field in numeric_fields:
        if field in opportunity:
            formatted[field] = opportunity[field]

    # Copy over any other fields
    for key, value in opportunity.items():
        if key not in text_fields and key not in numeric_fields:
            formatted[key] = value

    return formatted


def format_signal_for_ui(signal: dict[str, Any]) -> dict[str, Any]:
    """Format a signal dictionary for UI display.

    This ensures signal fields are properly formatted and
    any structured data is handled correctly.

    Args:
        signal: Signal dictionary

    Returns:
        Formatted signal dictionary
    """
    formatted = {}

    # Handle potential JSON strings in signal fields
    for key, value in signal.items():
        if isinstance(value, str):
            formatted[key] = clean_json_string(value)
        else:
            formatted[key] = value

    return formatted
