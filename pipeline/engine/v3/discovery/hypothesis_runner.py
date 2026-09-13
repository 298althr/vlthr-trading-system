"""
V3 Hypothesis Runner — Batch Testing & Lifecycle Management
=============================================================
Runs all hypotheses against live data, updates Bayesian posteriors,
and manages hypothesis lifecycle (untested -> testing -> confirmed/refuted).

CLI:
    python -m engine.v3.discovery.hypothesis_runner --symbols BTCUSDT ETHUSDT --bars 2000
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

from .hypothesis_engine import (
    Hypothesis, generate_hypotheses, test_hypothesis, test_lead_lag_hypothesis,
    upsert_hypothesis, load_hypothesis, load_all_hypotheses,
    get_confirmed_hypotheses, bayesian_update,
    UNTESTED, TESTING, CONFIRMED, REFUTED, RETIRED,
)
from .probe_runner import load_enriched_data, load_btc_data

# Ensure V2 enrichment is importable
_repo = Path(__file__).resolve().parents[5]
_engine = Path(__file__).resolve().parents[3]
for p in [str(_repo / "data" / "bybit" / "workers"), str(_engine)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def run_hypothesis_cycle(symbols: List[str], timeframe: str = "15m",
                         bars: int = 2000, write_to_db: bool = True) -> Dict[str, Any]:
    """
    Run a complete hypothesis testing cycle across all symbols.
    Returns a summary dict with all results and status changes.
    """
    print(f"[hypothesis_runner] Starting cycle for {len(symbols)} symbols")
    print(f"[hypothesis_runner] Timeframe: {timeframe}, Bars: {bars}")

    # Generate or load hypotheses
    hypotheses = generate_hypotheses(symbols)
    print(f"[hypothesis_runner] {len(hypotheses)} hypotheses to test")

    # Load existing state from DB
    if write_to_db:
        for hyp in hypotheses:
            existing = load_hypothesis(hyp.hypothesis_name)
            if existing:
                hyp.posterior_probability = existing.posterior_probability
                hyp.evidence_count = existing.evidence_count
                hyp.evidence_for = existing.evidence_for
                hyp.evidence_against = existing.evidence_against
                hyp.bayesian_updates = existing.bayesian_updates
                hyp.confidence = existing.confidence
                hyp.status = existing.status

    # Load BTC data once for lead-lag
    btc_df = None
    if "BTCUSDT" in symbols:
        btc_df = load_btc_data(timeframe, bars)

    # Test each hypothesis
    results = []
    promotions = []  # untested/testing -> confirmed
    refutations = []  # untested/testing -> refuted
    errors = []

    for hyp in hypotheses:
        print(f"[hypothesis_runner] Testing: {hyp.hypothesis_name} ({hyp.hypothesis_type})")

        if hyp.hypothesis_type == "lead_lag":
            # Lead-lag: test BTC vs each altcoin
            if btc_df is None or btc_df.empty:
                errors.append(f"{hyp.hypothesis_name}: no BTC data for lead-lag")
                continue
            alt_symbols = [s for s in symbols if s != "BTCUSDT"]
            for alt_sym in alt_symbols:
                alt_df = load_enriched_data(alt_sym, timeframe, bars)
                if alt_df is None or alt_df.empty:
                    continue
                result = test_lead_lag_hypothesis(hyp, btc_df, alt_df, alt_sym)
                if "error" in result:
                    errors.append(f"{hyp.hypothesis_name}/{alt_sym}: {result['error']}")
        else:
            # Standard: test against each symbol
            for symbol in symbols:
                df = load_enriched_data(symbol, timeframe, bars)
                if df is None or df.empty:
                    errors.append(f"{hyp.hypothesis_name}/{symbol}: no data")
                    continue
                result = test_hypothesis(hyp, df, symbol)
                if "error" in result:
                    if "insufficient condition matches" not in result.get("error", ""):
                        errors.append(f"{hyp.hypothesis_name}/{symbol}: {result['error']}")

        # Track status changes
        old_status = UNTESTED
        if write_to_db:
            existing = load_hypothesis(hyp.hypothesis_name)
            if existing:
                old_status = existing.status

        if hyp.status != old_status:
            if hyp.status == CONFIRMED:
                promotions.append({
                    "hypothesis": hyp.hypothesis_name,
                    "from": old_status, "to": CONFIRMED,
                    "posterior": round(hyp.posterior_probability, 4),
                    "confidence": round(hyp.confidence, 4),
                    "evidence_for": hyp.evidence_for,
                    "evidence_against": hyp.evidence_against,
                })
            elif hyp.status == REFUTED:
                refutations.append({
                    "hypothesis": hyp.hypothesis_name,
                    "from": old_status, "to": REFUTED,
                    "posterior": round(hyp.posterior_probability, 4),
                    "confidence": round(hyp.confidence, 4),
                    "evidence_for": hyp.evidence_for,
                    "evidence_against": hyp.evidence_against,
                })

        if write_to_db:
            upsert_hypothesis(hyp)

        results.append({
            "name": hyp.hypothesis_name,
            "type": hyp.hypothesis_type,
            "status": hyp.status,
            "posterior": round(hyp.posterior_probability, 4),
            "confidence": round(hyp.confidence, 4),
            "evidence_count": hyp.evidence_count,
            "evidence_for": hyp.evidence_for,
            "evidence_against": hyp.evidence_against,
        })

    # Sort by posterior descending
    results.sort(key=lambda x: x["posterior"], reverse=True)

    # State distribution
    state_dist = {}
    for r in results:
        s = r["status"]
        state_dist[s] = state_dist.get(s, 0) + 1

    summary = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "timeframe": timeframe,
        "total_hypotheses": len(hypotheses),
        "promotions": promotions,
        "refutations": refutations,
        "errors": errors,
        "results": results,
        "state_distribution": state_dist,
        "top_hypotheses": results[:10],
    }

    print(f"[hypothesis_runner] Cycle complete: {len(hypotheses)} hypotheses, "
          f"{len(promotions)} confirmed, {len(refutations)} refuted, {len(errors)} errors")

    return summary


def main():
    """CLI entry point for the hypothesis runner."""
    parser = argparse.ArgumentParser(description="V3 Hypothesis Testing Runner")
    parser.add_argument("--symbols", nargs="+",
                        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"],
                        help="Symbols to test")
    parser.add_argument("--timeframe", default="15m", help="Timeframe to use")
    parser.add_argument("--bars", type=int, default=2000, help="Number of bars to analyze")
    parser.add_argument("--no-db", action="store_true", help="Skip DB writes")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    summary = run_hypothesis_cycle(
        symbols=args.symbols,
        timeframe=args.timeframe,
        bars=args.bars,
        write_to_db=not args.no_db,
    )

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"\n{'='*70}")
        print(f"  V3 HYPOTHESIS CYCLE SUMMARY")
        print(f"{'='*70}")
        print(f"  Symbols:           {', '.join(args.symbols)}")
        print(f"  Timeframe:         {args.timeframe}")
        print(f"  Total hypotheses:  {summary.get('total_hypotheses', 0)}")
        print(f"  Confirmed:         {len(summary.get('promotions', []))}")
        print(f"  Refuted:           {len(summary.get('refutations', []))}")
        print(f"  Errors:            {len(summary.get('errors', []))}")
        print(f"\n  State Distribution:")
        for state, count in summary.get("state_distribution", {}).items():
            print(f"    {state:15s}: {count}")
        print(f"\n  Top 10 Hypotheses by Posterior:")
        for i, h in enumerate(summary.get("top_hypotheses", [])[:10]):
            print(f"    {i+1:2d}. {h['name']:35s} P={h['posterior']:.4f}  "
                  f"conf={h['confidence']:.4f}  for={h['evidence_for']}  against={h['evidence_against']}")
        if summary.get("promotions"):
            print(f"\n  Confirmations:")
            for p in summary["promotions"]:
                print(f"    {p['hypothesis']:35s} {p['from']} -> {p['to']}: "
                      f"P={p['posterior']:.4f} conf={p['confidence']:.4f}")
        if summary.get("refutations"):
            print(f"\n  Refutations:")
            for r in summary["refutations"]:
                print(f"    {r['hypothesis']:35s} {r['from']} -> {r['to']}: "
                      f"P={r['posterior']:.4f} conf={r['confidence']:.4f}")
        print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
