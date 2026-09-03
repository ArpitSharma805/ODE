"""Centralized timeout configuration for all external API calls.

This module provides a single source of truth for timeout values across:
- MCP servers (GitHub, Tavily, Context7, etc.)
- LLM calls (Ollama)
- JIT tool execution
- Database operations
- Other external services

All timeouts are in seconds and can be overridden via environment variables.
"""

import os


# MCP and external API timeouts
MCP_TIMEOUT = float(os.environ.get("MCP_TIMEOUT") or 30.0)
JIT_TOOL_TIMEOUT = float(os.environ.get("JIT_TOOL_TIMEOUT") or 5.0)
TECH_RADAR_TIMEOUT = float(os.environ.get("TECH_RADAR_TIMEOUT") or 25.0)

# LLM timeouts
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT") or 120.0)

# Database timeouts
DB_TIMEOUT = float(os.environ.get("DB_TIMEOUT") or 10.0)
DB_BUSY_TIMEOUT = float(os.environ.get("DB_BUSY_TIMEOUT") or 10.0)

# Research source timeouts (use MCP_TIMEOUT by default)
RESEARCH_SOURCE_TIMEOUT = MCP_TIMEOUT


__all__ = [
    "MCP_TIMEOUT",
    "JIT_TOOL_TIMEOUT",
    "TECH_RADAR_TIMEOUT",
    "OLLAMA_TIMEOUT",
    "DB_TIMEOUT",
    "DB_BUSY_TIMEOUT",
    "RESEARCH_SOURCE_TIMEOUT",
]
