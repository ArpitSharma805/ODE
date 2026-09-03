#!/usr/bin/env python3
"""Test connectivity to the GitHub MCP server from within ODE."""

import os
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ode.mcp_client import call_tool, list_tools


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set.")
        print("Add it to .devin/mcp_config.local.json (gitignored) or run:")
        print("  export GITHUB_TOKEN=ghp_...")
        return 1

    print("Listing GitHub MCP tools...")
    result = list_tools("github")
    print(f"  success: {result.success}")
    print(f"  duration: {result.duration:.2f}s")
    print(f"  tools: {result.data}")
    if result.error:
        print(f"  error: {result.error}")

    if not result.success:
        return 1

    print("\nCalling search_repositories('langgraph')...")
    search = call_tool("github", "search_repositories", {"query": "langgraph"})
    print(f"  success: {search.success}")
    print(f"  duration: {search.duration:.2f}s")
    if search.data:
        print(f"  response preview: {str(search.data)[:500]}")
    if search.error:
        print(f"  error: {search.error}")

    return 0 if search.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
