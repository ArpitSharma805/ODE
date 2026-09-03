import os
import sqlite3
from pathlib import Path
from typing import Generator

import pytest


def _truncate_mutable_tables(db_path: Path) -> None:
    """Reset test data tables without removing personas/users."""
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=10.0)
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("DELETE FROM user_opportunity_interactions")
        conn.execute("DELETE FROM trend_signals")
        conn.execute("DELETE FROM opportunities")
        conn.execute("DELETE FROM trends")
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM ingestion_runs")
        conn.execute("DELETE FROM sources")
    finally:
        conn.close()


@pytest.fixture(scope="session")
def e2e_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a temporary database path for E2E tests.

    Isolates E2E test data from the development ``data/ode.sqlite`` file.
    """
    return tmp_path_factory.mktemp("ode_e2e") / "ode.sqlite"
