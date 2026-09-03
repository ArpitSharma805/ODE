"""Application health checks."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ode.db import get_db_connection


@dataclass
class Health:
    database: str
    tables: list[str]


def get_health(db_path: str) -> Health:
    """Return the health status of the database at the given path."""
    path = Path(db_path)
    if not path.exists():
        return Health(database="missing", tables=[])

    try:
        conn = get_db_connection(str(path))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        conn.close()
    except sqlite3.Error:
        return Health(database="error", tables=[])

    status = "ok" if "users" in tables else "schema missing"
    return Health(database=status, tables=tables)
