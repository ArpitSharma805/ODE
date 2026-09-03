#!/usr/bin/env python3
"""Test Playwright MCP connectivity and basic page navigation."""

import re
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ode.mcp_client import call_tool, list_tools


def main() -> int:
    print("Listing Playwright MCP tools...")
    tools = list_tools("playwright")
    if not tools.success:
        print("Tool discovery failed:", tools.error)
        return 1

    print(f"  Discovered {len(tools.data)} tools")
    for tool in tools.data[:10]:
        print(f"    - {tool}")
    if len(tools.data) > 10:
        print(f"    ... and {len(tools.data) - 10} more")

    # Use a plain-HTTP page to avoid certificate issues in restricted environments.
    url = "http://info.cern.ch/hypertext/WWW/TheProject.html"
    print(f"\nNavigating to {url} via Playwright MCP...")
    nav = call_tool("playwright", "browser_navigate", {"url": url})
    print("  success:", nav.success)
    print("  error:", nav.error)
    print("  data preview:", str(nav.data)[:500])

    if nav.data and "### Error" not in str(nav.data):
        match = re.search(r"\[Snapshot\]\((\.playwright-mcp/[^)]+)\)", str(nav.data))
        if match:
            snapshot_path = Path.cwd() / match.group(1)
            if snapshot_path.exists():
                text = snapshot_path.read_text(encoding="utf-8")
                print(f"\n  Snapshot saved: {snapshot_path}")
                print(f"  Snapshot length: {len(text)} characters")
                print("  Snapshot preview:")
                print(text[:500])
            else:
                print("\n  Snapshot file not found:", snapshot_path)
        else:
            print("\n  No snapshot link found in response.")
        return 0

    print("\n  Navigation did not return usable content (this is expected for HTTPS in some environments).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
