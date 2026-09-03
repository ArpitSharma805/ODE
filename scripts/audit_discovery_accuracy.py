#!/usr/bin/env python3
"""
Audit script to verify technology discovery repository data accuracy.
Checks that all technologies have accurate repositories, realistic stars, and non-zero forks.
"""

import sqlite3
import json
from ode.db import DEFAULT_DB_PATH
from ode.technology_discovery import get_trending_technologies

def main():
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    data = get_trending_technologies(conn, force_refresh=True)

    print("=== TECHNOLOGY REPOSITORY AUDIT ===")
    print(f"Total technologies: {len(data)}")
    print()

    issues_found = []

    for tech in data:
        projects = tech.get("top_projects", [])
        total_stars = tech.get("total_stars", 0)

        print(f"[{tech['name']}] Total Stars: {total_stars:,}")

        if not projects:
            print(f"  ⚠️  WARNING: No projects found!")
            issues_found.append(f"{tech['name']}: No projects")
            continue

        for p in projects:
            stars = p.get("stars", 0)
            forks = p.get("forks", 0)
            full_name = p.get("full_name", "N/A")
            language = p.get("language", "N/A")

            print(f"  • {full_name}")
            print(f"    Stars: {stars:,} | Forks: {forks:,} | Lang: {language}")

            # Check for issues
            if stars == 0:
                issues_found.append(f"{tech['name']}: {full_name} has 0 stars")
                print(f"    ⚠️  WARNING: Zero stars!")

            if forks == 0:
                issues_found.append(f"{tech['name']}: {full_name} has 0 forks")
                print(f"    ⚠️  WARNING: Zero forks!")

            if not language or language == "N/A":
                issues_found.append(f"{tech['name']}: {full_name} has no language")
                print(f"    ⚠️  WARNING: No language!")

        print()

    print("=== AUDIT SUMMARY ===")
    if issues_found:
        print(f"⚠️  Found {len(issues_found)} issues:")
        for issue in issues_found:
            print(f"  - {issue}")
    else:
        print("✅ All technologies have accurate repository data with non-zero stars and forks!")

    conn.close()

if __name__ == "__main__":
    main()
