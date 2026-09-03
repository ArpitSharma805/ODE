"""SQLite-backed cache and metrics for MCP tool calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ode.config.timeouts import MCP_TIMEOUT
from ode.db import DEFAULT_DB_PATH, get_db_connection
from ode.mcp_client import MCPResult, _call_tool as _raw_call_tool


_LOGGER = logging.getLogger(__name__)
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
    _LOGGER.addHandler(_handler)
    _LOGGER.propagate = False

# SQLite is not safe for concurrent writes from multiple threads. Serialize all
# cache/metric database access so the actual MCP call can still run concurrently.
_CACHE_LOCK = threading.Lock()
_CONN_LOCAL = threading.local()
_MAX_RETRIES = 5
_BACKOFF_BASE = 0.05
_DISABLE_METRICS = os.environ.get("ODE_DISABLE_MCP_METRICS", "0") == "1"


def _args_hash(arguments: dict[str, Any]) -> str:
    """Return a stable hash for a set of tool arguments."""
    payload = json.dumps(arguments, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
    """Return True if the exception is a transient lock error we can retry."""
    text = str(exc).lower()
    return "database is locked" in text or "locked" in text


def _execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] = (),
) -> sqlite3.Cursor:
    """Execute SQL with exponential backoff on transient lock errors."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            cur = conn.execute(sql, params)
            return cur
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if not _is_locked_error(exc):
                raise
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(_BACKOFF_BASE * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def _executescript_with_retry(conn: sqlite3.Connection, script: str) -> None:
    """Execute a script with exponential backoff on transient lock errors."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            conn.executescript(script)
            return
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if not _is_locked_error(exc):
                raise
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(_BACKOFF_BASE * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def _get_thread_connection(db_path: str) -> sqlite3.Connection:
    """Return a per-thread SQLite connection. Do not share across threads."""
    local = _CONN_LOCAL
    if getattr(local, "db_path", None) != db_path or getattr(local, "conn", None) is None:
        _LOGGER.info(
            "opening new connection thread=%s conn_id=<new> db_path=%s",
            threading.get_ident(),
            db_path,
        )
        local.conn = get_db_connection(db_path)
        local.db_path = db_path
        _ensure_cache_tables(local.conn)
    return local.conn


def _ensure_cache_tables(conn: sqlite3.Connection) -> None:
    _executescript_with_retry(
        conn,
        """
        CREATE TABLE IF NOT EXISTS mcp_cache (
            server TEXT NOT NULL,
            tool TEXT NOT NULL,
            args_hash TEXT PRIMARY KEY,
            arguments TEXT NOT NULL,
            response TEXT,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mcp_metrics (
            server TEXT NOT NULL,
            tool TEXT NOT NULL,
            cache_hit INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            created_at REAL NOT NULL
        );
        """,
    )


def _log_access(
    op: str,
    conn: sqlite3.Connection,
    server: str,
    tool: str,
    cache_hit: int | None = None,
) -> None:
    _LOGGER.info(
        "thread=%s conn_id=%s op=%s server=%s tool=%s cache_hit=%s",
        threading.get_ident(),
        id(conn),
        op,
        server,
        tool,
        cache_hit,
    )


def _log_metric(
    conn: sqlite3.Connection,
    server: str,
    tool: str,
    cache_hit: int,
    duration_ms: int,
) -> None:
    if _DISABLE_METRICS:
        _log_access("metric_skip", conn, server, tool, cache_hit)
        return
    _log_access("metric_write", conn, server, tool, cache_hit)
    _execute_with_retry(
        conn,
        """
        INSERT INTO mcp_metrics (server, tool, cache_hit, duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (server, tool, cache_hit, duration_ms, time.time()),
    )


def _parse_cached_response(response: str) -> Any:
    """Try to parse a cached response as JSON, otherwise return the raw text."""
    try:
        return json.loads(response)
    except (json.JSONDecodeError, TypeError):
        return response


def cached_call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    ttl_seconds: int = 86400,
    config_path: Path | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> MCPResult:
    """Call an MCP tool with SQLite caching and request metrics.

    On a cache hit the response is returned as parsed JSON when possible and a
    metric row with cache_hit=1 is recorded. On a miss the raw MCP call is
    invoked outside the SQLite lock, then the raw text response is cached, a
    cache_hit=0 metric is recorded, and the result is returned.

    Each thread obtains its own connection. Cache/metric writes are serialized
    by _CACHE_LOCK while the actual MCP call happens outside the lock.
    """
    now = time.time()
    arg_hash = _args_hash(arguments)
    arg_json = json.dumps(arguments, sort_keys=True)

    # Try the cache under a lock; if we miss, release the lock while the real
    # MCP call runs so multiple MCP calls can still execute concurrently.
    with _CACHE_LOCK:
        conn = _get_thread_connection(db_path)
        _log_access("cache_read", conn, server_name, tool_name)
        cur = _execute_with_retry(
            conn,
            """
            SELECT response, expires_at FROM mcp_cache
            WHERE server = ? AND tool = ? AND args_hash = ?
            """,
            (server_name, tool_name, arg_hash),
        )
        row = cur.fetchone()
        if row is not None and now < row[1]:
            _log_metric(conn, server_name, tool_name, 1, 0)
            return MCPResult(
                success=True,
                data=_parse_cached_response(row[0]),
                duration=0.0,
            )

    hang_logged = threading.Event()

    def _hang_watchdog():
        if not hang_logged.is_set():
            _LOGGER.warning(
                "[HANG DETECTED] MCP call server=%s tool=%s args=%s has exceeded 10s",
                server_name,
                tool_name,
                arguments,
            )

    watchdog = threading.Timer(10.0, _hang_watchdog)
    watchdog.start()

    try:
        mcp_timeout = MCP_TIMEOUT
        raw = asyncio.run(
            asyncio.wait_for(
                _raw_call_tool(server_name, tool_name, arguments, config_path),
                timeout=mcp_timeout,
            )
        )
    except asyncio.TimeoutError:
        raw = MCPResult(
            success=False,
            data=None,
            duration=mcp_timeout,
            error=f"MCP call timed out after {mcp_timeout}s",
        )
    finally:
        hang_logged.set()
        watchdog.cancel()

    duration_ms = int(round((raw.duration or 0.0) * 1000))
    _LOGGER.info(
        "MCP call server=%s tool=%s duration=%.2fs success=%s",
        server_name,
        tool_name,
        raw.duration or 0.0,
        raw.success,
    )

    with _CACHE_LOCK:
        conn = _get_thread_connection(db_path)
        _log_metric(conn, server_name, tool_name, 0, duration_ms)
        if raw.success:
            _log_access("cache_write", conn, server_name, tool_name)
            _execute_with_retry(
                conn,
                """
                INSERT OR REPLACE INTO mcp_cache
                    (server, tool, args_hash, arguments, response, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_name,
                    tool_name,
                    arg_hash,
                    arg_json,
                    str(raw.data or ""),
                    now,
                    now + ttl_seconds,
                ),
            )

    return MCPResult(
        success=raw.success,
        data=_parse_cached_response(str(raw.data or "")),
        duration=raw.duration or 0.0,
        error=raw.error,
    )
