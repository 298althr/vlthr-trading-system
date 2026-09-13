#!/usr/bin/env python3
"""Session Split Statistical Test
==================================
Runs Mann-Whitney U test per symbol across the 4 standard sessions
using existing backtest trade results. Determines which sessions are
statistically distinguishable for each symbol.

Output: per-symbol session map showing which splits are justified (p < 0.05).

Usage:
  cd /path/to/DEVOPS/backtest
  python3 session_split_test.py
"""
import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd

# Path setup
_BACKTEST_DIR = Path(__file__).resolve().parent
_DEVOPS = _BACKTEST_DIR.parent
sys.path.insert(0, str(_BACKTEST_DIR))
sys.path.insert(0, str(_DEVOPS / "pipeline" / "engine"))

from stat_gates import mann_whitney_test, validate_sample_size
from portfolio_config import classify_session, ACTIVE_SESSIONS

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
SESSIONS = ["asian", "london", "ny_open", "ny_late"]
ALPHA = 0.05
FREE_PARAMS = 3  # sl_mult, tp_mult, adx_min


def load_backtest_trades(csv_path: str) -> pd.DataFrame:
    """Load backtest CSV, filter to approved trades, add session column."""
    df = pd.read_csv(csv_path)
    df = df[df["approved"] == True].copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"], utc=True)
    df["session"] = df["entry_date"].apply(classify_session)
    # R-multiple: pnl_pct / risk_pct (risk = sl distance / entry price)
    df["sl_dist_pct"] = ((df["entry_price"] - df["sl_price"]).abs() / df["entry_price"]) * 100
    df["r_multiple"] = df["pnl_pct"] / df["sl_dist_pct"].replace(0, np.nan)
    df = df.dropna(subset=["r_multiple"])
    return df


def run_session_tests(df: pd.DataFrame) -> dict:
    """Run Mann-Whitney U test for all session pairs per symbol.

    Returns dict: {symbol: {pair: MannWhitneyResult, ...}, ...}
    """
    results = {}

    for symbol in SYMBOLS:
        sym_df = df[df["symbol"] == symbol]
        if len(sym_df) == 0:
            print(f"\n{symbol}: No approved trades found")
            results[symbol] = {}
            continue

        print(f"\n{'='*60}")
        print(f"{symbol}: {len(sym_df)} approved trades")

        # Session distribution
        session_counts = sym_df["session"].value_counts()
        for sess in SESSIONS:
            count = session_counts.get(sess, 0)
            ss = validate_sample_size(count, FREE_PARAMS)
            print(f"  {sess:10s}: {count:4d} trades  (required: {ss.required_trades}, {'PASS' if ss.passed else 'FAIL'})")

        # Pairwise Mann-Whitney tests
        sym_results = {}
        for i, sess_a in enumerate(SESSIONS):
            for sess_b in SESSIONS[i+1:]:
                a = sym_df[sym_df["session"] == sess_a]["r_multiple"].values
                b = sym_df[sym_df["session"] == sess_b]["r_multiple"].values

                if len(a) < 5 or len(b) < 5:
                    sym_results[f"{sess_a}_vs_{sess_b}"] = None
                    print(f"  {sess_a:10s} vs {sess_b:10s}: SKIP (insufficient data: {len(a)} vs {len(b)})")
                    continue

                mw = mann_whitney_test(a, b, alpha=ALPHA)
                sym_results[f"{sess_a}_vs_{sess_b}"] = mw
                status = "SPLIT" if mw.significant else "MERGE"
                print(f"  {sess_a:10s} vs {sess_b:10s}: p={mw.p_value:.4f} r={mw.effect_size_r:.3f}  [{status}]")

        results[symbol] = sym_results

    return results


def build_session_map(results: dict) -> dict:
    """Build per-symbol session map: which sessions should be separate cells.

    A session gets its own cell if it shows a statistically significant
    difference from at least one other session.
    """
    session_map = {}

    for symbol, sym_results in results.items():
        if not sym_results:
            session_map[symbol] = {"london", "ny_open", "ny_late"}
            continue

        # Track which sessions have at least one significant split
        significant_sessions = set()
        for pair_key, mw in sym_results.items():
            if mw is not None and mw.significant:
                sess_a, sess_b = pair_key.split("_vs_")
                significant_sessions.add(sess_a)
                significant_sessions.add(sess_b)

        # Sessions with no significant difference from any other -> can be merged
        all_sessions = set(SESSIONS)
        mergeable = all_sessions - significant_sessions

        if mergeable:
            session_map[symbol] = {
                "separate": sorted(significant_sessions),
                "mergeable": sorted(mergeable),
            }
        else:
            session_map[symbol] = {
                "separate": sorted(all_sessions),
                "mergeable": [],
            }

    return session_map


def main():
    csv_path = _BACKTEST_DIR / "backtest_results.csv"
    if not csv_path.exists():
        print(f"ERROR: Backtest results not found at {csv_path}")
        sys.exit(1)

    print("Loading backtest trade results...")
    df = load_backtest_trades(str(csv_path))
    print(f"Total approved trades: {len(df)}")
    print(f"Symbols: {df['symbol'].unique().tolist()}")

    results = run_session_tests(df)
    session_map = build_session_map(results)

    print(f"\n{'='*60}")
    print("SESSION MAP SUMMARY")
    print(f"{'='*60}")
    for symbol, mapping in session_map.items():
        if isinstance(mapping, set):
            print(f"  {symbol}: {sorted(mapping)} (default)")
        else:
            separate = mapping.get("separate", [])
            mergeable = mapping.get("mergeable", [])
            print(f"  {symbol}: separate={separate}, mergeable={mergeable}")

    # Save results
    output_path = _BACKTEST_DIR / "session_split_results.json"
    import json
    output = {}
    for symbol, mapping in session_map.items():
        if isinstance(mapping, set):
            output[symbol] = {"separate": sorted(mapping), "mergeable": []}
        else:
            output[symbol] = mapping
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
