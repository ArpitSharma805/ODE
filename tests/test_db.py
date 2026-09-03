import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from ode.db import init_database


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_init_database_creates_all_tables(temp_db_path: str) -> None:
    init_database(temp_db_path)
    conn = sqlite3.connect(temp_db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    expected = {
        "users",
        "personas",
        "domains",
        "sources",
        "ingestion_runs",
        "signals",
        "trends",
        "trend_signals",
        "opportunities",
        "investigations",
        "mcp_cache",
        "mcp_metrics",
    }
    assert expected <= tables


def test_init_database_seeds_default_domain_and_persona(temp_db_path: str) -> None:
    init_database(temp_db_path)
    conn = sqlite3.connect(temp_db_path)
    cur = conn.cursor()

    cur.execute("SELECT name FROM domains")
    domain_names = {row[0] for row in cur.fetchall()}

    cur.execute("SELECT name FROM personas")
    persona_names = {row[0] for row in cur.fetchall()}

    conn.close()

    assert "Technology" in domain_names
    assert "Engineer" in persona_names


def test_init_database_is_idempotent(temp_db_path: str) -> None:
    init_database(temp_db_path)
    init_database(temp_db_path)

    conn = sqlite3.connect(temp_db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM domains WHERE name = 'Technology'")
    domain_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM personas WHERE name = 'Engineer'")
    persona_count = cur.fetchone()[0]
    conn.close()

    assert domain_count == 1
    assert persona_count == 1


def test_init_database_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ode.sqlite"
    init_database(str(path))
    assert path.exists()
