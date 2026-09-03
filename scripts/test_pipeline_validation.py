#!/usr/bin/env python3
"""Test script to validate the ODE pipeline with real queries.

This script tests:
1. Evidence → Themes → Problems → Insights → Opportunities → Narrative pipeline
2. Whether the bottleneck is missing signals or weak evidence synthesis
3. Quality of outputs for different query types
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ode.agents.orchestrator import run_copilot
from ode.db import get_db_connection

# Test queries across different categories
TEST_QUERIES = [
    ("What opportunities exist in MCP?", "Opportunity Discovery"),
    ("What products could a solo founder build around MCP?", "Product Ideas"),
    ("Should I learn Go?", "Technology Evaluation"),
    ("What should backend engineers learn?", "Skill Learning"),
    ("Go vs Rust", "Technology Evaluation"),
]

def analyze_pipeline_output(query: str, expected_intent: str):
    """Analyze a single query through the pipeline."""
    print(f"\n{'='*80}")
    print(f"QUERY: {query}")
    print(f"EXPECTED INTENT: {expected_intent}")
    print(f"{'='*80}\n")

    conn = get_db_connection()
    try:
        events = list(run_copilot(query, conn, seed_only=False))

        # Track pipeline stages
        stages = {
            "signals_collected": 0,
            "themes_detected": 0,
            "problems_detected": 0,
            "insights_generated": 0,
            "opportunities_generated": 0,
            "sources_used": set(),
        }

        final_state = None
        for event in events:
            if event.get("type") == "status":
                detail = event.get("detail", "")
                if "signals" in detail.lower():
                    # Extract signal count if available
                    pass
            elif event.get("type") == "final":
                final_state = event

        if final_state:
            # Extract pipeline metrics
            signals = final_state.get("signals", [])
            stages["signals_collected"] = len(signals)
            stages["sources_used"] = {s.get("source_type", "unknown") for s in signals if isinstance(s, dict)}

            # Check for themes, problems, insights
            synthesis = final_state.get("synthesis")
            if synthesis:
                stages["themes_detected"] = len(synthesis.get("themes", []))
                stages["problems_detected"] = len(synthesis.get("problems", []))
                stages["insights_generated"] = len(synthesis.get("insights", []))

            # Check opportunities
            opportunities = final_state.get("opportunities", [])
            stages["opportunities_generated"] = len(opportunities)

            # Print analysis
            print(f"PIPELINE METRICS:")
            print(f"  Signals Collected: {stages['signals_collected']}")
            print(f"  Sources Used: {', '.join(sorted(stages['sources_used']))}")
            print(f"  Themes Detected: {stages['themes_detected']}")
            print(f"  Problems Detected: {stages['problems_detected']}")
            print(f"  Insights Generated: {stages['insights_generated']}")
            print(f"  Opportunities Generated: {stages['opportunities_generated']}")

            print(f"\nTHEMES:")
            if synthesis and synthesis.get("themes"):
                for theme in synthesis["themes"][:3]:
                    print(f"  - {theme.get('name', 'Unknown')}")
            else:
                print("  No themes detected")

            print(f"\nPROBLEMS:")
            if synthesis and synthesis.get("problems"):
                for problem in synthesis["problems"][:3]:
                    print(f"  - {problem.get('statement', 'Unknown')}")
            else:
                print("  No problems detected")

            print(f"\nOPPORTUNITIES:")
            if opportunities:
                for opp in opportunities[:3]:
                    print(f"  - {opp.title} (score: {opp.score:.0f})")
            else:
                print("  No opportunities generated")

            print(f"\nREPORT PREVIEW:")
            answer = final_state.get("answer", "")
            if answer:
                print(f"  {answer[:300]}...")
            else:
                print("  No report generated")

            # Determine bottleneck
            print(f"\nBOTTLENECK ANALYSIS:")
            if stages["signals_collected"] < 5:
                print("  ⚠️  MISSING SIGNALS: Insufficient signal collection")
            elif stages["themes_detected"] == 0:
                print("  ⚠️  WEAK THEME EXTRACTION: Signals collected but no themes detected")
            elif stages["problems_detected"] == 0:
                print("  ⚠️  WEAK PROBLEM DETECTION: Themes detected but no problems identified")
            elif stages["insights_generated"] == 0:
                print("  ⚠️  WEAK INSIGHT GENERATION: Problems detected but no insights generated")
            elif stages["opportunities_generated"] == 0:
                print("  ⚠️  WEAK OPPORTUNITY GENERATION: Insights available but no opportunities")
            else:
                print("  ✅ PIPELINE FLOWING: All stages producing output")

        else:
            print("ERROR: No final state received")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

def main():
    """Run all test queries and analyze results."""
    print("ODE PIPELINE VALIDATION")
    print("="*80)
    print("Testing Evidence → Themes → Problems → Insights → Opportunities → Narrative")
    print("="*80)

    # Set fast-fail for LLM calls
    os.environ["OLLAMA_TIMEOUT"] = "0.001"

    results = []
    for query, expected_intent in TEST_QUERIES:
        try:
            analyze_pipeline_output(query, expected_intent)
        except Exception as e:
            print(f"FAILED: {query} - {e}")
            results.append((query, "FAILED"))

    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Total queries tested: {len(TEST_QUERIES)}")
    print(f"Successful: {len([r for r in results if r[1] != 'FAILED'])}")
    print(f"Failed: {len([r for r in results if r[1] == 'FAILED'])}")

if __name__ == "__main__":
    main()
