"""Test database migration for pipeline_artifacts column."""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from ode.db import _ensure_database, _migrate, get_db_connection


def test_migration_adds_pipeline_artifacts_to_existing_db():
    """Test that migration adds pipeline_artifacts column to existing investigations table."""
    # Create a temporary database path
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ode.sqlite")

        # First, create a database without the investigations table (simulating old schema)
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
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
            FOREIGN KEY (source_id) REFERENCES sources(source_id)
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

        CREATE TABLE IF NOT EXISTS user_opportunity_interactions (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            persona_id INTEGER NOT NULL,
            opportunity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (persona_id) REFERENCES personas(persona_id),
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
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
            error TEXT
        );
        """)
        conn.close()

        # Verify pipeline_artifacts column does not exist
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(investigations)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "pipeline_artifacts" not in columns, "pipeline_artifacts should not exist before migration"

        # Run migration
        conn = sqlite3.connect(db_path, isolation_level=None)
        _migrate(conn)
        conn.close()

        # Verify pipeline_artifacts column now exists
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(investigations)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "pipeline_artifacts" in columns, "pipeline_artifacts should exist after migration"


def test_migration_idempotent():
    """Test that running migration multiple times doesn't cause errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ode.sqlite")

        # Create database with full schema (including pipeline_artifacts)
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS user_opportunity_interactions (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            persona_id INTEGER NOT NULL,
            opportunity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (persona_id) REFERENCES personas(persona_id),
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
        );
        """)
        conn.close()

        # Run migration multiple times
        for _ in range(3):
            conn = sqlite3.connect(db_path, isolation_level=None)
            _migrate(conn)
            conn.close()

        # Verify column still exists and no errors occurred
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(investigations)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "pipeline_artifacts" in columns


def test_ensure_database_runs_migration_on_existing_db():
    """Test that _ensure_database runs migrations on existing databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ode.sqlite")

        # Create a database without investigations table (old schema)
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
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
            FOREIGN KEY (source_id) REFERENCES sources(source_id)
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

        CREATE TABLE IF NOT EXISTS user_opportunity_interactions (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            persona_id INTEGER NOT NULL,
            opportunity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (persona_id) REFERENCES personas(persona_id),
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
        );
        """)
        conn.close()

        # Create investigations table without pipeline_artifacts
        conn = sqlite3.connect(db_path)
        conn.execute("""
        CREATE TABLE investigations (
            investigation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            final_state TEXT,
            trace_log TEXT DEFAULT '[]',
            agent_states TEXT DEFAULT '{}',
            error TEXT
        );
        """)
        conn.close()

        # Run _ensure_database (should run migration)
        _ensure_database(db_path)

        # Verify pipeline_artifacts column was added
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(investigations)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "pipeline_artifacts" in columns


def test_fresh_database_has_pipeline_artifacts():
    """Test that a fresh database created through _ensure_database has pipeline_artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ode.sqlite")

        # Create fresh database
        _ensure_database(db_path)

        # Verify investigations table exists with pipeline_artifacts
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='investigations'")
        assert cur.fetchone() is not None, "investigations table should exist"
        cur.execute("PRAGMA table_info(investigations)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "pipeline_artifacts" in columns


def test_get_db_connection_runs_migration():
    """Test that get_db_connection runs migrations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_ode.sqlite")

        # Create old schema without investigations
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
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
            FOREIGN KEY (source_id) REFERENCES sources(source_id)
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

        CREATE TABLE IF NOT EXISTS user_opportunity_interactions (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            persona_id INTEGER NOT NULL,
            opportunity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (persona_id) REFERENCES personas(persona_id),
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id)
        );

        CREATE TABLE investigations (
            investigation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            final_state TEXT,
            trace_log TEXT DEFAULT '[]',
            agent_states TEXT DEFAULT '{}',
            error TEXT
        );
        """)
        conn.close()

        # Get connection through get_db_connection (should run migration)
        conn = get_db_connection(db_path)
        conn.close()

        # Verify pipeline_artifacts was added
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(investigations)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "pipeline_artifacts" in columns
