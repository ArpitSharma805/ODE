"""MCP client for ODE agents.

Reads `.devin/mcp_config.json` and exposes synchronous wrappers around the
async `mcp` Python SDK so agents can call MCP tools without managing coroutines.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / ".devin" / "mcp_config.json"


@dataclass
class MCPResult:
    success: bool
    data: Any
    duration: float
    error: str | None = None


def _expand_env(value: Any) -> Any:
    """Expand ${env:VAR} placeholders in config values."""
    if isinstance(value, str) and value.startswith("${env:") and value.endswith("}"):
        var = value[6:-1]
        return os.environ.get(var, "")
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_server_config(
    server_name: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    path = config_path or DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"MCP config not found: {path}")
    with open(path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    servers = cfg.get("mcpServers", {})
    if server_name not in servers:
        raise KeyError(f"Server '{server_name}' not configured in {path}")
    server_cfg = _expand_env(servers[server_name])

    # Merge optional .local.json overrides (gitignored, for secrets).
    local_path = path.with_suffix(".local.json")
    if local_path.exists():
        with open(local_path, encoding="utf-8") as handle:
            local_cfg = json.load(handle)
        local_server = _expand_env(local_cfg.get("mcpServers", {}).get(server_name, {}))
        for key, value in local_server.items():
            if key == "env" and "env" in server_cfg:
                server_cfg["env"] = {**server_cfg["env"], **value}
            else:
                server_cfg[key] = value

    return server_cfg


async def _list_tools(
    server_name: str,
    config_path: Path | None = None,
) -> MCPResult:
    start = time.time()
    try:
        cfg = _load_server_config(server_name, config_path)
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env={**os.environ, **cfg.get("env", {})},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return MCPResult(
                    success=True,
                    data=[t.name for t in tools.tools],
                    duration=time.time() - start,
                )
    except Exception as exc:  # noqa: BLE001
        return MCPResult(
            success=False,
            data=[],
            duration=time.time() - start,
            error=str(exc),
        )


async def _call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    config_path: Path | None = None,
) -> MCPResult:
    start = time.time()
    try:
        cfg = _load_server_config(server_name, config_path)
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env={**os.environ, **cfg.get("env", {})},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text_parts = [
                    str(c.text) for c in result.content if isinstance(c, TextContent)
                ]
                return MCPResult(
                    success=True,
                    data="".join(text_parts),
                    duration=time.time() - start,
                )
    except Exception as exc:  # noqa: BLE001
        return MCPResult(
            success=False,
            data=None,
            duration=time.time() - start,
            error=str(exc),
        )


def list_tools(server_name: str, config_path: Path | None = None) -> MCPResult:
    """List the tools exposed by an MCP server."""
    return asyncio.run(_list_tools(server_name, config_path))


def call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    config_path: Path | None = None,
) -> MCPResult:
    """Call an MCP tool through the SQLite-backed cache."""
    # Import lazily to avoid a circular import with ode.mcp_cache.
    from ode.mcp_cache import cached_call_tool

    return cached_call_tool(server_name, tool_name, arguments, config_path=config_path)


def health(server_name: str, config_path: Path | None = None) -> dict[str, Any]:
    """Return a health dict for an MCP server."""
    result = list_tools(server_name, config_path)
    return {
        "server": server_name,
        "available": result.success,
        "duration": f"{result.duration:.2f}s",
        "error": result.error,
        "tools": result.data if result.success else [],
    }
