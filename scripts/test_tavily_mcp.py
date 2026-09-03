#!/usr/bin/env python3
"""Test connectivity to the Tavily MCP server from within ODE."""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ode.mcp_client import call_tool, list_tools


def main() -> int:
    print("Listing Tavily MCP tools...")
    tools = list_tools("tavily")
    print(f"  success: {tools.success}")
    print(f"  duration: {tools.duration:.2f}s")
    print(f"  tools: {tools.data}")
    if tools.error:
        print(f"  error: {tools.error}")

    if not tools.success:
        return 1

    print("\nCalling tavily_search('MCP market adoption')...")
    search = call_tool("tavily", "tavily_search", {"query": "MCP market adoption", "max_results": 5})
    print(f"  success: {search.success}")
    print(f"  duration: {search.duration:.2f}s")
    if search.data:
        print(f"  response preview: {str(search.data)[:500]}")
    if search.error:
        print(f"  error: {search.error}")

    print("\nCalling tavily_research('MCP ecosystem')...")
    research = call_tool("tavily", "tavily_research", {"query": "MCP ecosystem", "max_results": 3})
    print(f"  success: {research.success}")
    print(f"  duration: {research.duration:.2f}s")
    if research.data:
        print(f"  response preview: {str(research.data)[:500]}")
    if research.error:
        print(f"  error: {research.error}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
