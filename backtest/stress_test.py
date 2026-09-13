#!/usr/bin/env python3
"""VLTHR Staged Stress Test Framework
=====================================
Runs the backtest engine at multiple stress levels to measure how the
trading edge degrades under real-world conditions:

  Stage 0 — Baseline:       No simulation (clean data, no latency)
  Stage 1 — Latency:        1-2 bar signal delay (15-30 min)
  Stage 2 — Data Gaps:      5-10% of bars missing per symbol
  Stage 3 — Downtime:       2-4 hour server outage windows
  Stage 4 — Combined:       All stressors applied together

Each stage runs all three modes (control, treatment_a, treatment_b) and
produces a comparison CSV + JSON summary.

Usage:
  cd /path/to/DEVOPS/backtest
  python3 stress_test.py --start 2026-01-01 --end 2026-06-30
  python3 stress_test.py --start 2026-01-01 --end 2026-06-30 --mode treatment_b_phase2
"""

import argparse
import csv
import json
import os
import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

import pandas as pd

# ── PATH SETUP ──────────────────────────────────────────────────────────────
_BACKTEST_DIR = Path(__file__).resolve().parent
_DEVOPS = _BACKTEST_DIR.parent
sys.path.insert(0, str(_BACKTEST_DIR))
sys.path.insert(0, str(_DEVOPS / "pipeline" / "engine"))

from backtest_runner import BacktestEngine, compute_metrics, check_gates, write_csv, SignalRecord


# ── STRESS STAGES ────────────────────────────────────────────────────────────

STAGES = [
    {
        "name": "stage0_baseline",
        "label": "Baseline (clean conditions)",
        "latency_bars": 0,
        "data_gap_prob": 0.0,
        "downtime_periods": [],
    },
    {
        "name": "stage1_latency",
        "label": "Latency (1 bar = 15min delay)",
        "latency_bars": 1,
        "data_gap_prob": 0.0,
        "downtime_periods": [],
    },
    {
        "name": "stage2_data_gaps",
        "label": "Data Gaps (5% missing bars)",
        "latency_bars": 0,
        "data_gap_prob": 0.05,
        "downtime_periods": [],
    },
    {
        "name": "stage3_downtime",
        "label": "Downtime (2h outage every ~7 days)",
        "latency_bars": 0,
        "data_gap_prob": 0.0,
        "downtime_periods": "auto",
    },
    {
        "name": "stage4_combined",
        "label": "Combined (latency + gaps + downtime)",
        "latency_bars": 2,
        "data_gap_prob": 0.10,
        "downtime_periods": "auto",
    },
]


def _generate_downtime_periods(start_date: str, end_date: str,
                               interval_days: int = 7,
                               outage_hours: int = 2) -> list:
    """Generate periodic downtime periods spanning the backtest range."""
    start = pd.Timestamp(start_date, tz="UTC")
    end = pd.Timestamp(end_date, tz="UTC")
    periods = []
    current = start
    while current < end:
        outage_start = current + pd.Timedelta(hours=8)  # outage at 8am UTC
        outage_end = outage_start + pd.Timedelta(hours=outage_hours)
        if outage_end <= end:
            periods.append((outage_start, outage_end))
        current = current + pd.Timedelta(days=interval_days)
    return periods


def _run_stage(stage: dict, mode: str, start: str, end: str,
               verbose: bool = True, seed: int = 42) -> dict:
    """Run one stress stage for one mode. Returns metrics dict."""
    latency_bars = stage["latency_bars"]
    data_gap_prob = stage["data_gap_prob"]

    if stage["downtime_periods"] == "auto":
        downtime = _generate_downtime_periods(start, end)
    else:
        downtime = stage["downtime_periods"]

    engine = BacktestEngine(
        mode, start, end,
        walk_forward=False, verbose=verbose,
        latency_bars=latency_bars,
        data_gap_prob=data_gap_prob,
        downtime_periods=downtime,
        seed=seed,
    )
    records = engine.run()
    metrics = compute_metrics(records)
    gates = check_gates(metrics)

    return {
        "stage": stage["name"],
        "stage_label": stage["label"],
        "mode": mode,
        "latency_bars": latency_bars,
        "data_gap_prob": data_gap_prob,
        "downtime_count": len(downtime),
        "metrics": metrics,
        "gates": gates,
        "n_records": len(records),
    }


def _run_stage_all_modes(stage: dict, start: str, end: str,
                         verbose: bool = True, modes: list = None,
                         seed: int = 42) -> list:
    """Run one stress stage across all modes."""
    if modes is None:
        modes = ["control", "treatment_a_phase1", "treatment_b_phase2"]

    results = []
    for mode in modes:
        if verbose:
            print(f"\n{'='*70}")
            print(f"  Stage: {stage['name']} | Mode: {mode}")
            print(f"{'='*70}")
        result = _run_stage(stage, mode, start, end, verbose=verbose, seed=seed)
        results.append(result)
    return results


def write_stress_csv(all_results: list, output_path: str):
    """Write stress test summary CSV with one row per stage+mode."""
    rows = []
    for r in all_results:
        m = r["metrics"]
        rows.append({
            "stage": r["stage"],
            "stage_label": r["stage_label"],
            "mode": r["mode"],
            "latency_bars": r["latency_bars"],
            "data_gap_prob": r["data_gap_prob"],
            "downtime_count": r["downtime_count"],
            "total_signals": m["total_signals"],
            "approved": m["approved"],
            "rejected": m["rejected"],
            "promoted": m["promoted"],
            "closed_trades": m["closed_trades"],
            "expired": m["expired"],
            "wins": m["wins"],
            "losses": m["losses"],
            "win_rate_pct": m["win_rate_pct"],
            "profit_factor": m["profit_factor"],
            "expectancy_usd": m["expectancy_usd"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "tp_hit_rate_pct": m["tp_hit_rate_pct"],
            "sl_hit_rate_pct": m["sl_hit_rate_pct"],
            "time_exits": m["time_exits"],
            "tp_hits": m["tp_hits"],
            "sl_hits": m["sl_hits"],
            "avg_hold_hours": m["avg_hold_hours"],
            "final_balance": m["final_balance"],
            "expired_rate_pct": m["expired_rate_pct"],
            "all_gates_pass": all(r["gates"].values()),
        })

    fieldnames = list(rows[0].keys()) if rows else []
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def write_stress_json(all_results: list, output_path: str):
    """Write stress test summary as JSON for the calibration monitor."""
    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "stages": [],
    }
    for r in all_results:
        m = r["metrics"]
        output["stages"].append({
            "stage": r["stage"],
            "stage_label": r["stage_label"],
            "mode": r["mode"],
            "latency_bars": r["latency_bars"],
            "data_gap_prob": r["data_gap_prob"],
            "downtime_count": r["downtime_count"],
            "win_rate_pct": m["win_rate_pct"],
            "profit_factor": m["profit_factor"],
            "expectancy_usd": m["expectancy_usd"],
            "total_pnl": m["final_balance"] - 10000.0,
            "max_drawdown_pct": m["max_drawdown_pct"],
            "closed_trades": m["closed_trades"],
            "tp_hit_rate_pct": m["tp_hit_rate_pct"],
            "sl_hit_rate_pct": m["sl_hit_rate_pct"],
            "all_gates_pass": all(r["gates"].values()),
        })

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)


def print_comparison_report(all_results: list):
    """Print a formatted comparison table across all stages and modes."""
    print(f"\n{'='*90}")
    print(f"  STAGED STRESS TEST COMPARISON")
    print(f"{'='*90}")

    # Group by mode
    by_mode = defaultdict(list)
    for r in all_results:
        by_mode[r["mode"]].append(r)

    for mode, results in sorted(by_mode.items()):
        print(f"\n  Mode: {mode}")
        print(f"  {'Stage':25s} {'Trades':>7s} {'Win%':>7s} {'PF':>6s} {'Exp$':>8s} "
              f"{'MaxDD%':>8s} {'TP%':>6s} {'SL%':>6s} {'Final$':>10s} {'Gates':>6s}")
        print(f"  {'-'*25} {'-'*7} {'-'*7} {'-'*6} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*10} {'-'*6}")

        for r in results:
            m = r["metrics"]
            gates_str = "PASS" if all(r["gates"].values()) else "FAIL"
            print(f"  {r['stage']:25s} {m['closed_trades']:7d} {m['win_rate_pct']:7.1f} "
                  f"{m['profit_factor']:6.2f} {m['expectancy_usd']:8.2f} "
                  f"{m['max_drawdown_pct']:8.2f} {m['tp_hit_rate_pct']:6.1f} "
                  f"{m['sl_hit_rate_pct']:6.1f} {m['final_balance']:10.2f} {gates_str:>6s}")

    # Degradation analysis
    print(f"\n{'='*90}")
    print(f"  EDGE DEGRADATION (baseline vs combined)")
    print(f"{'='*90}")

    for mode in sorted(by_mode.keys()):
        results = by_mode[mode]
        baseline = next((r for r in results if r["stage"] == "stage0_baseline"), None)
        combined = next((r for r in results if r["stage"] == "stage4_combined"), None)
        if not baseline or not combined:
            continue

        bm = baseline["metrics"]
        cm = combined["metrics"]
        wr_delta = cm["win_rate_pct"] - bm["win_rate_pct"]
        pf_delta = cm["profit_factor"] - bm["profit_factor"]
        pnl_delta = cm["final_balance"] - bm["final_balance"]

        print(f"\n  {mode}:")
        print(f"    Win rate:  {bm['win_rate_pct']:.1f}% -> {cm['win_rate_pct']:.1f}%  ({wr_delta:+.1f}pp)")
        print(f"    PF:        {bm['profit_factor']:.2f} -> {cm['profit_factor']:.2f}  ({pf_delta:+.2f})")
        print(f"    Final PnL: ${bm['final_balance']:.2f} -> ${cm['final_balance']:.2f}  (${pnl_delta:+.2f})")
        print(f"    Trades:    {bm['closed_trades']} -> {cm['closed_trades']}")

    print(f"{'='*90}")


def main():
    parser = argparse.ArgumentParser(description="VLTHR Staged Stress Test")
    parser.add_argument("--start", type=str, default="2026-01-01",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-06-30",
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--mode", type=str, default=None,
                        choices=["control", "treatment_a_phase1", "treatment_b_phase2"],
                        help="Run only one mode (default: all three)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output CSV path")
    parser.add_argument("--json-output", type=str, default=None,
                        help="Output JSON path for calibration monitor")
    parser.add_argument("--quiet", action="store_true",
                        help="Reduce verbose output")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for data gap simulation (default: 42)")
    args = parser.parse_args()

    output_csv = args.output or str(_BACKTEST_DIR / "stress_test_staged_results.csv")
    output_json = args.json_output or str(_BACKTEST_DIR / "stress_test_staged_results.json")
    verbose = not args.quiet
    modes = [args.mode] if args.mode else ["control", "treatment_a_phase1", "treatment_b_phase2"]

    all_results = []
    for stage in STAGES:
        if verbose:
            print(f"\n{'#'*70}")
            print(f"  Running {stage['name']}: {stage['label']}")
            print(f"{'#'*70}")
        results = _run_stage_all_modes(stage, args.start, args.end,
                                       verbose=verbose, modes=modes, seed=args.seed)
        all_results.extend(results)

    write_stress_csv(all_results, output_csv)
    print(f"\n  Stress test CSV: {output_csv}")

    write_stress_json(all_results, output_json)
    print(f"  Stress test JSON: {output_json}")

    print_comparison_report(all_results)


if __name__ == "__main__":
    main()
