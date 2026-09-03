"""Persona configuration and retrieval."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


_DEFAULT_MARKET_KEYWORDS: dict[str, dict[str, float]] = {
    "Software Engineer": {
        "agent": 10,
        "integration": 10,
        "api": 10,
        "sdk": 10,
        "developer tools": 15,
        "developer experience": 10,
        "code": 10,
        "programming": 10,
    },
    "AI Engineer": {
        "agent": 15,
        "agents": 15,
        "ai": 15,
        "llm": 15,
        "model": 10,
        "framework": 10,
        "machine learning": 10,
        "infrastructure": 8,
    },
    "Data Engineer": {
        "data": 15,
        "pipeline": 10,
        "database": 10,
        "streaming": 10,
        "analytics": 10,
        "etl": 10,
    },
    "Platform Engineer": {
        "platform": 15,
        "infrastructure": 15,
        "mcp": 12,
        "service": 10,
        "cloud": 10,
        "devops": 10,
        "observability": 10,
        "tooling": 8,
    },
    "Product Manager": {
        "workflow": 15,
        "automation": 12,
        "product": 10,
        "strategy": 10,
        "market": 10,
        "user": 8,
        "adoption": 8,
    },
    "Business Leader": {
        "business": 15,
        "enterprise": 12,
        "growth": 12,
        "market": 12,
        "revenue": 10,
        "strategy": 10,
        "competitive": 8,
    },
}


@dataclass
class Persona:
    persona_id: int
    name: str
    goals: list[str]
    interests: list[str]
    skill_profile: list[str]
    keywords: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.keywords:
            keywords: dict[str, float] = {}
            for goal in self.goals:
                keywords[goal.lower().strip()] = 5.0
            for interest in self.interests:
                keywords[interest.lower().strip()] = 15.0
            for skill in self.skill_profile:
                keywords[skill.lower().strip()] = 10.0
            keywords.update(_DEFAULT_MARKET_KEYWORDS.get(self.name, {}))
            self.keywords = keywords


def get_persona_by_name(conn: sqlite3.Connection, name: str) -> Persona | None:
    """Return a persona by name."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT persona_id, name, goals, interests, skill_profile
        FROM personas
        WHERE name = ?
        """,
        (name,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return Persona(
        persona_id=row[0],
        name=row[1],
        goals=json.loads(row[2] or "[]"),
        interests=json.loads(row[3] or "[]"),
        skill_profile=json.loads(row[4] or "[]"),
    )


_DEFAULT_PERSONAS: list[dict[str, Any]] = [
    {
        "name": "Software Engineer",
        "goals": ["build reliable systems", "ship high-quality code"],
        "interests": ["programming languages", "developer tools", "software architecture"],
        "industry_preferences": '["technology", "software"]',
        "risk_appetite": "moderate",
        "preferred_horizon": "1-2 years",
        "geography": "global",
        "capital_availability": "time",
        "skill_profile": '["python", "javascript", "software engineering"]',
    },
    {
        "name": "AI Engineer",
        "goals": ["ship AI-powered products", "stay ahead of model capabilities"],
        "interests": ["machine learning", "large language models", "agent frameworks"],
        "industry_preferences": '["technology", "artificial intelligence"]',
        "risk_appetite": "high",
        "preferred_horizon": "1-2 years",
        "geography": "global",
        "capital_availability": "time",
        "skill_profile": '["python", "pytorch", "machine learning"]',
    },
    {
        "name": "Data Engineer",
        "goals": ["build scalable data pipelines", "enable data-driven decisions"],
        "interests": ["data pipelines", "databases", "streaming systems"],
        "industry_preferences": '["technology", "data"]',
        "risk_appetite": "moderate",
        "preferred_horizon": "1-2 years",
        "geography": "global",
        "capital_availability": "time",
        "skill_profile": '["sql", "python", "data engineering"]',
    },
    {
        "name": "Platform Engineer",
        "goals": ["improve developer experience", "automate infrastructure"],
        "interests": ["platform engineering", "devops", "cloud infrastructure"],
        "industry_preferences": '["technology", "infrastructure"]',
        "risk_appetite": "moderate",
        "preferred_horizon": "2-3 years",
        "geography": "global",
        "capital_availability": "budget",
        "skill_profile": '["kubernetes", "terraform", "observability"]',
    },
    {
        "name": "Product Manager",
        "goals": ["identify market opportunities", "deliver customer value"],
        "interests": ["product strategy", "user research", "market trends"],
        "industry_preferences": '["technology", "software"]',
        "risk_appetite": "moderate",
        "preferred_horizon": "1-2 years",
        "geography": "global",
        "capital_availability": "budget",
        "skill_profile": '["product management", "data analysis", "strategy"]',
    },
    {
        "name": "Business Leader",
        "goals": ["drive business growth", "allocate capital efficiently"],
        "interests": ["market expansion", "competitive advantage", "digital transformation"],
        "industry_preferences": '["technology", "enterprise"]',
        "risk_appetite": "low",
        "preferred_horizon": "3-5 years",
        "geography": "global",
        "capital_availability": "capital",
        "skill_profile": '["strategy", "leadership", "operations"]',
    },
]


def seed_default_personas(conn: sqlite3.Connection) -> None:
    """Insert the six default personas if they are not already present."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    for persona in _DEFAULT_PERSONAS:
        cur.execute(
            """
            INSERT OR IGNORE INTO personas (
                user_id, name, goals, interests, industry_preferences,
                risk_appetite, preferred_horizon, geography, capital_availability,
                skill_profile, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                persona["name"],
                json.dumps(persona["goals"]),
                json.dumps(persona["interests"]),
                persona["industry_preferences"],
                persona["risk_appetite"],
                persona["preferred_horizon"],
                persona["geography"],
                persona["capital_availability"],
                persona["skill_profile"],
                now,
                now,
            ),
        )
    conn.commit()
