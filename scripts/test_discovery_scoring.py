#!/usr/bin/env python3
"""Test script to verify trend score calculation for discovered technologies."""

import sqlite3
import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ode.db import DEFAULT_DB_PATH
from ode.technology_discovery import discover_custom_technology, calculate_dynamic_trend_score

def test_scoring_formula():
    """Test the scoring formula with sample data."""
    print("Testing scoring formula with sample data...\n")

    # Test case 1: EMV-like technology (2,679 stars, ~500 forks, 4 projects)
    score1 = calculate_dynamic_trend_score(
        total_stars=2679,
        total_forks=500,
        project_count=4,
        recent_repos_30d=0,
        hn_mentions=0,
        opportunity_count=0
    )
    print(f"EMV-like (2,679 stars, 500 forks, 4 projects): {score1}")

    # Test case 2: Popular technology (50,000 stars, 5,000 forks, 100 projects)
    score2 = calculate_dynamic_trend_score(
        total_stars=50000,
        total_forks=5000,
        project_count=100,
        recent_repos_30d=10,
        hn_mentions=5,
        opportunity_count=3
    )
    print(f"Popular tech (50k stars, 5k forks, 100 projects): {score2}")

    # Test case 3: Small emerging technology (500 stars, 50 forks, 2 projects)
    score3 = calculate_dynamic_trend_score(
        total_stars=500,
        total_forks=50,
        project_count=2,
        recent_repos_30d=0,
        hn_mentions=0,
        opportunity_count=0
    )
    print(f"Small emerging (500 stars, 50 forks, 2 projects): {score3}")

    # Test case 4: Major technology (100,000 stars, 10,000 forks, 500 projects)
    score4 = calculate_dynamic_trend_score(
        total_stars=100000,
        total_forks=10000,
        project_count=500,
        recent_repos_30d=20,
        hn_mentions=10,
        opportunity_count=5
    )
    print(f"Major tech (100k stars, 10k forks, 500 projects): {score4}")

    print()

def test_discovery_queries():
    """Test discovery with real queries."""
    print("Testing discovery with real queries...\n")

    conn = sqlite3.connect(DEFAULT_DB_PATH)

    test_queries = ["EMV", "Polars", "FastMCP", "Vite"]

    for q in test_queries:
        try:
            tech = discover_custom_technology(q, conn)
            if tech:
                print(f"[Scored Discovery: {tech['name']}]")
                print(f"  - Trend Score: {tech['trend_score']} ({tech['momentum']})")
                print(f"  - Total Stars: {tech.get('total_stars', 0):,}")
                print(f"  - Total Forks: {tech.get('total_forks', 0):,}")
                print(f"  - Project Count: {tech.get('project_count', 0):,}")
                if tech.get('top_projects'):
                    print(f"  - Top Project: {tech['top_projects'][0]['full_name']} ({tech['top_projects'][0].get('stars', 0):,} stars, {tech['top_projects'][0].get('forks', 0):,} forks)")
                print()
            else:
                print(f"[Discovery Failed: {q}]")
                print()
        except Exception as e:
            print(f"[Discovery Error: {q}] {e}")
            print()

    conn.close()

if __name__ == "__main__":
    test_scoring_formula()
    test_discovery_queries()
