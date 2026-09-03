"""Load test for mcp_cache.py concurrent SQLite access."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import asyncio
import sqlite3

from ode.db import get_db_connection, init_database
from ode.mcp_cache import cached_call_tool
from ode.mcp_client import MCPResult


def _fake_raw_call(*args, **kwargs):
    async def _inner(*a, **k):
        await asyncio.sleep(0.01)
        return MCPResult(success=True, data='{"items":[]}', duration=0.05)
    return _inner(*args, **kwargs)


def _count(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def main() -> None:
    import ode.mcp_cache as cache_module

    cache_module._raw_call_tool = _fake_raw_call

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ode.sqlite")
        init_database(db_path)

        # 20 unique misses -> 20 cache entries, 20 metric rows
        miss_tasks = [
            (f"github", "search_repositories", {"query": f"python theme {i}", "per_page": 8})
            for i in range(20)
        ]

        start = time.time()
        errors: list[Exception] = []
        lock = threading.Lock()

        def run_missed(task):
            server, tool, args = task
            try:
                return cached_call_tool(server, tool, args, db_path=db_path)
            except Exception as exc:
                with lock:
                    errors.append(exc)
                raise

        with ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(run_missed, miss_tasks))
        miss_elapsed = time.time() - start

        conn = get_db_connection(db_path)
        try:
            cache_count = _count(conn, "mcp_cache")
            metric_count = _count(conn, "mcp_metrics")
        finally:
            conn.close()

        # 20 hits on a single cached call -> more metrics, same cache row
        hit_task = ("github", "search_repositories", {"query": "python theme 0", "per_page": 8})

        start = time.time()
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(cached_call_tool, *hit_task, db_path=db_path)
                for _ in range(20)
            ]
            for future in futures:
                future.result()
        hit_elapsed = time.time() - start

        conn = get_db_connection(db_path)
        try:
            cache_count_after = _count(conn, "mcp_cache")
            metric_count_after = _count(conn, "mcp_metrics")
        finally:
            conn.close()

    print("Load test results:")
    print(f"  20 unique misses: {miss_elapsed:.2f}s")
    print(f"  20 concurrent hits: {hit_elapsed:.2f}s")
    print(f"  cache rows after misses: {cache_count} (expected 20)")
    print(f"  metric rows after misses: {metric_count} (expected 20)")
    print(f"  cache rows after hits: {cache_count_after} (expected 20)")
    print(f"  metric rows after hits: {metric_count_after} (expected >= 40)")
    print(f"  errors: {len(errors)}")

    assert cache_count == 20, f"expected 20 cache rows, got {cache_count}"
    assert metric_count == 20, f"expected 20 metric rows, got {metric_count}"
    assert cache_count_after == 20, f"expected cache rows unchanged, got {cache_count_after}"
    assert metric_count_after >= 40, f"expected >= 40 metric rows after hits, got {metric_count_after}"
    assert not errors, f"errors occurred: {errors}"

    print("All assertions passed.")


if __name__ == "__main__":
    main()
