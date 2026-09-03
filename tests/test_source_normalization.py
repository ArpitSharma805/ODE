"""Unit tests for fixture CSV ingestion / source normalization."""

import csv
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from ode.collector import run_ingestion
from ode.db import init_database
from ode.sources import create_source


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


def _make_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["entity", "metric", "value", "timestamp"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_fixture_csv_ingestion_creates_signals(temp_db_path: str, tmp_path: Path) -> None:
    """Ingesting a fixture CSV creates a Signal per valid row."""
    fixture = tmp_path / "fixture.csv"
    _make_csv(
        fixture,
        [
            {"entity": "LangGraph", "metric": "stars", "value": "1200", "timestamp": "2026-01-01"},
            {"entity": "LangGraph", "metric": "stars", "value": "1300", "timestamp": "2026-01-02"},
            {"entity": "", "metric": "stars", "value": "100", "timestamp": "2026-01-03"},
        ],
    )

    init_database(temp_db_path)
    conn = sqlite3.connect(temp_db_path)
    try:
        source_id = create_source(
            conn,
            name="Fixture",
            source_type="fixture_csv",
            trust_tier=80,
            endpoint=str(fixture),
        )
        signals_created = run_ingestion(conn, source_id)

        assert signals_created == 2

        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM signals WHERE entity = ? AND metric = ?",
            ("LangGraph", "stars"),
        )
        assert cur.fetchone()[0] == 2
    finally:
        conn.close()


def test_fixture_csv_skips_empty_values(temp_db_path: str, tmp_path: Path) -> None:
    """Rows with missing values are skipped during ingestion."""
    fixture = tmp_path / "empty.csv"
    _make_csv(
        fixture,
        [
            {"entity": "LangGraph", "metric": "stars", "value": "", "timestamp": "2026-01-01"},
            {"entity": "LangGraph", "metric": "", "value": "100", "timestamp": "2026-01-02"},
        ],
    )

    init_database(temp_db_path)
    conn = sqlite3.connect(temp_db_path)
    try:
        source_id = create_source(
            conn,
            name="Empty",
            source_type="fixture_csv",
            trust_tier=80,
            endpoint=str(fixture),
        )
        signals_created = run_ingestion(conn, source_id)
        assert signals_created == 0
    finally:
        conn.close()
