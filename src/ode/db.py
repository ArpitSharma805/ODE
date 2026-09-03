"""Database initialization and schema management."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ode.config.timeouts import DB_TIMEOUT, DB_BUSY_TIMEOUT


def _repo_root() -> Path:
    """Find the repository root by looking for pyproject.toml."""
    path = Path(__file__).resolve().parent
    while path != path.parent:
        if (path / "pyproject.toml").exists():
            return path
        path = path.parent
    return Path.cwd()


DEFAULT_DB_PATH = os.environ.get(
    "ODE_DB_PATH",
    str(_repo_root() / "data" / "ode.sqlite"),
)


def _tables_exist(conn: sqlite3.Connection) -> bool:
    """Return True when the core ODE tables are already present."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('sources', 'signals', 'trends', 'opportunities')"
    )
    return len(cur.fetchall()) == 4


def _ensure_database(db_path: str) -> None:
    """Create schema and seed default data if the database is missing or fresh."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=DB_TIMEOUT)
    try:
        if not _tables_exist(conn):
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            now = datetime.now(timezone.utc).isoformat()
            for statement in _SEED.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(statement, {"now": now})
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Always run migrations for both new and existing databases
        _migrate(conn)
    finally:
        conn.close()


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return an autocommit SQLite connection with concurrency-friendly pragmas.

    Automatically initializes the schema and seed data when the database is
    missing or does not yet contain the core tables.
    """
    _ensure_database(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=DB_TIMEOUT)
    conn.execute("PRAGMA busy_timeout = %d" % int(DB_BUSY_TIMEOUT * 1000))
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personas (
    persona_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT NOT NULL,
    goals TEXT,
    interests TEXT,
    industry_preferences TEXT,
    risk_appetite TEXT,
    preferred_horizon TEXT,
    geography TEXT,
    capital_availability TEXT,
    skill_profile TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS domains (
    domain_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    trust_tier INTEGER NOT NULL,
    refresh_frequency TEXT,
    endpoint TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT NOT NULL,
    signals_created INTEGER NOT NULL DEFAULT 0,
    errors TEXT,
    metadata TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    ingestion_run_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    entity TEXT NOT NULL,
    metric TEXT NOT NULL,
    value TEXT NOT NULL,
    unit TEXT,
    timestamp TEXT NOT NULL,
    ingest_date TEXT NOT NULL,
    raw_payload TEXT,
    normalized_payload TEXT,
    evidence_quality REAL NOT NULL,
    confidence REAL NOT NULL,
    tags TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_runs(run_id)
);

CREATE TABLE IF NOT EXISTS trends (
    trend_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    metric TEXT NOT NULL,
    start_date TEXT NOT NULL,
    last_updated_date TEXT NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    momentum REAL,
    signal_volume INTEGER,
    evidence_quality REAL,
    growth_velocity REAL,
    created_date TEXT,
    forecast TEXT
);

CREATE TABLE IF NOT EXISTS trend_signals (
    trend_signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trend_id INTEGER NOT NULL,
    signal_id INTEGER NOT NULL,
    FOREIGN KEY (trend_id) REFERENCES trends(trend_id),
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id),
    UNIQUE (trend_id, signal_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trend_id INTEGER NOT NULL,
    persona_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    description TEXT,
    why_now TEXT,
    who_benefits TEXT,
    recommended_action TEXT,
    supporting_evidence TEXT,
    why_existing_solutions_fail TEXT,
    business_model TEXT,
    risk_assessment TEXT,
    score REAL NOT NULL,
    score_components TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'Emerging',
    emerged_date TEXT NOT NULL,
    valid_until TEXT,
    last_score_date TEXT NOT NULL,
    FOREIGN KEY (trend_id) REFERENCES trends(trend_id),
    FOREIGN KEY (persona_id) REFERENCES personas(persona_id),
    UNIQUE (trend_id, persona_id)
);

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

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    final_state TEXT,
    trace_log TEXT DEFAULT '[]',
    agent_states TEXT DEFAULT '{}',
    pipeline_artifacts TEXT DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS technology_discovery_metrics (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    maturity TEXT NOT NULL,
    domain TEXT,
    trend_score INTEGER NOT NULL,
    momentum TEXT NOT NULL,
    project_count INTEGER NOT NULL,
    opportunity_count INTEGER NOT NULL,
    total_stars INTEGER NOT NULL,
    total_forks INTEGER NOT NULL DEFAULT 0,
    recent_repos_30d INTEGER NOT NULL,
    hn_mentions_30d INTEGER NOT NULL,
    top_projects TEXT,
    last_updated TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_SEED = """
INSERT OR IGNORE INTO users (email, created_at, updated_at)
VALUES ('local@ode.dev', :now, :now);

INSERT OR IGNORE INTO domains (name, description)
VALUES ('Technology', 'Technology, tools, and engineering trends.');

INSERT OR IGNORE INTO personas (
    user_id, name, goals, interests, industry_preferences, risk_appetite,
    preferred_horizon, geography, capital_availability, skill_profile,
    created_at, updated_at
)
VALUES (
    1,
    'Engineer',
    '["learn new skills", "stay employable", "work on interesting problems"]',
    '["artificial intelligence", "developer tools", "programming languages"]',
    '["technology", "software"]',
    'moderate',
    '1-2 years',
    'global',
    'time',
    '["python", "software engineering"]',
    :now,
    :now
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply lightweight schema migrations to an existing database."""
    cur = conn.cursor()
    # Check if trends table exists before migrating it
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trends'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(trends)")
        trend_columns = {row[1] for row in cur.fetchall()}
        if "created_date" not in trend_columns:
            conn.execute("ALTER TABLE trends ADD COLUMN created_date TEXT")
        if "forecast" not in trend_columns:
            conn.execute("ALTER TABLE trends ADD COLUMN forecast TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_signals (
            trend_signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trend_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            FOREIGN KEY (trend_id) REFERENCES trends(trend_id),
            FOREIGN KEY (signal_id) REFERENCES signals(signal_id),
            UNIQUE (trend_id, signal_id)
        )
        """
    )
    # Check if opportunities table exists before migrating it
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(opportunities)")
        opportunity_columns = {row[1] for row in cur.fetchall()}
        if "persona_id" not in opportunity_columns:
            conn.execute("ALTER TABLE opportunities ADD COLUMN persona_id INTEGER")
        if "category" not in opportunity_columns:
            conn.execute("ALTER TABLE opportunities ADD COLUMN category TEXT")
        if "why_existing_solutions_fail" not in opportunity_columns:
            conn.execute("ALTER TABLE opportunities ADD COLUMN why_existing_solutions_fail TEXT")
        if "business_model" not in opportunity_columns:
            conn.execute("ALTER TABLE opportunities ADD COLUMN business_model TEXT")
        if "risk_assessment" not in opportunity_columns:
            conn.execute("ALTER TABLE opportunities ADD COLUMN risk_assessment TEXT")
        if "execution_roadmap" not in opportunity_columns:
            conn.execute("ALTER TABLE opportunities ADD COLUMN execution_roadmap TEXT DEFAULT '{}'")

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_trend_persona
        ON opportunities(trend_id, persona_id)
        """
    )
    # Check if investigations table exists before trying to migrate it
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='investigations'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(investigations)")
        investigation_columns = {row[1] for row in cur.fetchall()}
        if "pipeline_artifacts" not in investigation_columns:
            conn.execute("ALTER TABLE investigations ADD COLUMN pipeline_artifacts TEXT DEFAULT '{}'")

    # Ensure technology_discovery_metrics table exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS technology_discovery_metrics (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            maturity TEXT NOT NULL,
            domain TEXT,
            trend_score INTEGER NOT NULL,
            momentum TEXT NOT NULL,
            project_count INTEGER NOT NULL,
            opportunity_count INTEGER NOT NULL,
            total_stars INTEGER NOT NULL,
            total_forks INTEGER NOT NULL DEFAULT 0,
            recent_repos_30d INTEGER NOT NULL,
            hn_mentions_30d INTEGER NOT NULL,
            top_projects TEXT,
            last_updated TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Add updated_at column if it doesn't exist (migration)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='technology_discovery_metrics'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(technology_discovery_metrics)")
        columns = [row[1] for row in cur.fetchall()]

        # Add updated_at column if missing
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE technology_discovery_metrics ADD COLUMN updated_at TEXT")

        # Add total_forks column if missing
        if "total_forks" not in columns:
            conn.execute("ALTER TABLE technology_discovery_metrics ADD COLUMN total_forks INTEGER NOT NULL DEFAULT 0")

        # Add project_suggestions column if missing
        if "project_suggestions" not in columns:
            conn.execute("ALTER TABLE technology_discovery_metrics ADD COLUMN project_suggestions TEXT DEFAULT '[]'")


def init_database(db_path: str) -> None:
    """Initialize the ODE SQLite database and seed default data."""
    _ensure_database(db_path)
