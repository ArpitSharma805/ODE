"""Demo data seeding for ODE."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ode.agents.opportunity_analyst import generate_opportunities
from ode.collector import run_ingestion
from ode.personas import seed_default_personas
from ode.sources import create_source
from ode.trends import run_trend_detection


DEMO_FIXTURE = str(
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "trend_fixture.csv"
)


def seed_demo_data(conn: sqlite3.Connection) -> None:
    """Populate the database with the LangGraph fixture.

    Creates a source, ingests signals, detects trends, and scores opportunities.
    """
    seed_default_personas(conn)
    source_id = create_source(
        conn,
        name="LangGraph Fixture",
        source_type="fixture_csv",
        trust_tier=80,
        endpoint=DEMO_FIXTURE,
        refresh_frequency="manual",
        owner="dev",
    )
    run_ingestion(conn, source_id)
    run_trend_detection(conn)
    generate_opportunities(conn, persona_name="Software Engineer")
