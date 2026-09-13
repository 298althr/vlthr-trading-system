#!/usr/bin/env python3
"""VLTHR Calibration Monitor — Live vs Backtest Deviation Detector
=================================================================
Compares live trading results against backtest expectations and outputs
JSON + CSV notifications when calibration deviates beyond thresholds.

The monitor reads:
  1. Backtest results CSV (from backtest_runner.py)
  2. Live paper_trades from the database (or a live results CSV)
  3. calibration.json (for gate thresholds and expected probabilities)

It outputs:
  1. calibration_notifications.json — structured alerts
  2. calibration_notifications.csv — flat table for dashboards

Notification levels:
  GREEN  — Live performance is within expected range
  YELLOW — Minor deviation, monitor closely
  RED    — Significant deviation, calibration edge is degrading

Usage:
  cd /path/to/DEVOPS/backtest
  python3 calibration_monitor.py --backtest-csv backtest_results.csv
  python3 calibration_monitor.py --backtest-csv backtest_results.csv --live-csv live_trades.csv
  python3 calibration_monitor.py --backtest-csv backtest_results.csv --db-url postgres://...
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

import pandas as pd

_BACKTEST_DIR = Path(__file__).resolve().parent
_DEVOPS = _BACKTEST_DIR.parent
_ENGINE = _DEVOPS / "pipeline" / "engine"

sys.path.insert(0, str(_ENGINE))


# ── DEVIATION THRESHOLDS ────────────────────────────────────────────────────
WIN_RATE_YELLOW_PP = 5.0     # percentage points
WIN_RATE_RED_PP = 10.0
PROFIT_FACTOR_YELLOW = 0.20  # relative
PROFIT_FACTOR_RED = 0.40
TP_RATE_YELLOW_PP = 5.0
TP_RATE_RED_PP = 10.0
SL_RATE_YELLOW_PP = 5.0
SL_RATE_RED_PP = 10.0
EXPECTANCY_YELLOW_PCT = 0.25  # relative
EXPECTANCY_RED_PCT = 0.50
MIN_LIVE_TRADES = 5           # need at least this many live trades to compare


def _load_calibration():
    cal_path = _ENGINE / "calibration.json"
    return json.loads(cal_path.read_text())


def _load_backtest_csv(path: str) -> dict:
    """Load backtest results and compute per-symbol metrics."""
    df = pd.read_csv(path)
    # Filter to closed trades only
    closed = df[df["exit_reason"].isin(["TP_HIT", "SL_HIT", "TIME_EXIT", "BACKTEST_END"])].copy()
    closed["win"] = closed["pnl_usd"] > 0

    by_symbol = {}
    for sym in closed["symbol"].unique():
        sym_df = closed[closed["symbol"] == sym]
        wins = sym_df[sym_df["win"]]
        losses = sym_df[~sym_df["win"]]
        gross_profit = wins["pnl_usd"].sum()
        gross_loss = abs(losses["pnl_usd"].sum())

        by_symbol[sym] = {
            "trades": len(sym_df),
            "wins": len(wins),
            "win_rate": len(wins) / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0,
            "expectancy": sym_df["pnl_usd"].mean() if len(sym_df) > 0 else 0,
            "tp_rate": (sym_df["exit_reason"] == "TP_HIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "sl_rate": (sym_df["exit_reason"] == "SL_HIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "time_exit_rate": (sym_df["exit_reason"] == "TIME_EXIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "total_pnl": sym_df["pnl_usd"].sum(),
        }

    # Overall
    wins = closed[closed["win"]]
    losses = closed[~closed["win"]]
    gross_profit = wins["pnl_usd"].sum()
    gross_loss = abs(losses["pnl_usd"].sum())
    overall = {
        "trades": len(closed),
        "wins": len(wins),
        "win_rate": len(wins) / len(closed) * 100 if len(closed) > 0 else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0,
        "expectancy": closed["pnl_usd"].mean() if len(closed) > 0 else 0,
        "tp_rate": (closed["exit_reason"] == "TP_HIT").sum() / len(closed) * 100 if len(closed) > 0 else 0,
        "sl_rate": (closed["exit_reason"] == "SL_HIT").sum() / len(closed) * 100 if len(closed) > 0 else 0,
        "time_exit_rate": (closed["exit_reason"] == "TIME_EXIT").sum() / len(closed) * 100 if len(closed) > 0 else 0,
        "total_pnl": closed["pnl_usd"].sum(),
    }

    return {"overall": overall, "by_symbol": by_symbol}


def _load_live_csv(path: str) -> dict:
    """Load live trade results CSV and compute per-symbol metrics."""
    df = pd.read_csv(path)
    # Filter to closed trades
    closed = df[df["status"].str.contains("CLOSED", na=False)].copy()
    closed["win"] = closed["net_pnl_usd"] > 0

    by_symbol = {}
    for sym in closed["symbol"].unique():
        sym_df = closed[closed["symbol"] == sym]
        wins = sym_df[sym_df["win"]]
        losses = sym_df[~sym_df["win"]]
        gross_profit = wins["net_pnl_usd"].sum()
        gross_loss = abs(losses["net_pnl_usd"].sum())

        by_symbol[sym] = {
            "trades": len(sym_df),
            "wins": len(wins),
            "win_rate": len(wins) / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0,
            "expectancy": sym_df["net_pnl_usd"].mean() if len(sym_df) > 0 else 0,
            "tp_rate": (sym_df["exit_reason"] == "TP_HIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "sl_rate": (sym_df["exit_reason"] == "SL_HIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "time_exit_rate": (sym_df["exit_reason"] == "TIME_EXIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "total_pnl": sym_df["net_pnl_usd"].sum(),
        }

    wins = closed[closed["win"]]
    losses = closed[~closed["win"]]
    gross_profit = wins["net_pnl_usd"].sum()
    gross_loss = abs(losses["net_pnl_usd"].sum())
    overall = {
        "trades": len(closed),
        "wins": len(wins),
        "win_rate": len(wins) / len(closed) * 100 if len(closed) > 0 else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0,
        "expectancy": closed["net_pnl_usd"].mean() if len(closed) > 0 else 0,
        "tp_rate": (closed["exit_reason"] == "TP_HIT").sum() / len(closed) * 100 if len(closed) > 0 else 0,
        "sl_rate": (closed["exit_reason"] == "SL_HIT").sum() / len(closed) * 100 if len(closed) > 0 else 0,
        "time_exit_rate": (closed["exit_reason"] == "TIME_EXIT").sum() / len(closed) * 100 if len(closed) > 0 else 0,
        "total_pnl": closed["net_pnl_usd"].sum(),
    }

    return {"overall": overall, "by_symbol": by_symbol}


def _load_live_from_db(db_url: str) -> dict:
    """Load live trades from the database."""
    import psycopg2
    conn = psycopg2.connect(db_url)
    query = """
        SELECT symbol, side, entry_price, exit_price, exit_reason,
               net_pnl_usd, status, entry_regime, dqs_score
        FROM paper_trades
        WHERE status LIKE 'CLOSED%'
    """
    df = pd.read_sql(query, conn)
    conn.close()

    df["win"] = df["net_pnl_usd"] > 0
    by_symbol = {}
    for sym in df["symbol"].unique():
        sym_df = df[df["symbol"] == sym]
        wins = sym_df[sym_df["win"]]
        losses = sym_df[~sym_df["win"]]
        gp = wins["net_pnl_usd"].sum()
        gl = abs(losses["net_pnl_usd"].sum())
        by_symbol[sym] = {
            "trades": len(sym_df),
            "wins": len(wins),
            "win_rate": len(wins) / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "profit_factor": gp / gl if gl > 0 else 0,
            "expectancy": sym_df["net_pnl_usd"].mean() if len(sym_df) > 0 else 0,
            "tp_rate": (sym_df["exit_reason"] == "TP_HIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "sl_rate": (sym_df["exit_reason"] == "SL_HIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "time_exit_rate": (sym_df["exit_reason"] == "TIME_EXIT").sum() / len(sym_df) * 100 if len(sym_df) > 0 else 0,
            "total_pnl": sym_df["net_pnl_usd"].sum(),
        }

    wins = df[df["win"]]
    losses = df[~df["win"]]
    gp = wins["net_pnl_usd"].sum()
    gl = abs(losses["net_pnl_usd"].sum())
    overall = {
        "trades": len(df),
        "wins": len(wins),
        "win_rate": len(wins) / len(df) * 100 if len(df) > 0 else 0,
        "profit_factor": gp / gl if gl > 0 else 0,
        "expectancy": df["net_pnl_usd"].mean() if len(df) > 0 else 0,
        "tp_rate": (df["exit_reason"] == "TP_HIT").sum() / len(df) * 100 if len(df) > 0 else 0,
        "sl_rate": (df["exit_reason"] == "SL_HIT").sum() / len(df) * 100 if len(df) > 0 else 0,
        "time_exit_rate": (df["exit_reason"] == "TIME_EXIT").sum() / len(df) * 100 if len(df) > 0 else 0,
        "total_pnl": df["net_pnl_usd"].sum(),
    }
    return {"overall": overall, "by_symbol": by_symbol}


def _classify_deviation(bt_val: float, live_val: float, bt_trades: int,
                        live_trades: int, metric_type: str) -> dict:
    """Classify the deviation between backtest and live for a metric.

    metric_type: 'win_rate' (percentage points), 'profit_factor' (relative),
                 'tp_rate' (percentage points), 'sl_rate' (percentage points),
                 'expectancy' (relative)
    """
    if live_trades < MIN_LIVE_TRADES:
        return {"level": "INSUFFICIENT_DATA", "delta": 0, "message": f"Only {live_trades} live trades (need {MIN_LIVE_TRADES})"}

    delta = live_val - bt_val

    if metric_type in ("win_rate", "tp_rate", "sl_rate"):
        abs_delta = abs(delta)
        if abs_delta >= 10.0:
            level = "RED"
        elif abs_delta >= 5.0:
            level = "YELLOW"
        else:
            level = "GREEN"
    elif metric_type == "profit_factor":
        rel_delta = abs(delta / bt_val) if bt_val != 0 else 0
        if rel_delta >= 0.40:
            level = "RED"
        elif rel_delta >= 0.20:
            level = "YELLOW"
        else:
            level = "GREEN"
    elif metric_type == "expectancy":
        rel_delta = abs(delta / bt_val) if bt_val != 0 else 0
        if rel_delta >= 0.50:
            level = "RED"
        elif rel_delta >= 0.25:
            level = "YELLOW"
        else:
            level = "GREEN"
    else:
        level = "GREEN"

    direction = "better" if delta > 0 else "worse" if delta < 0 else "same"
    return {"level": level, "delta": round(delta, 2), "direction": direction,
            "bt_val": round(bt_val, 2), "live_val": round(live_val, 2)}


def compare(bt_data: dict, live_data: dict, cal: dict) -> list:
    """Compare backtest vs live and produce notifications."""
    notifications = []
    bt_overall = bt_data["overall"]
    live_overall = live_data["overall"]

    # Overall comparison
    metrics_to_check = [
        ("win_rate", "Win Rate %", "percentage_points"),
        ("profit_factor", "Profit Factor", "relative"),
        ("expectancy", "Expectancy $", "relative"),
        ("tp_rate", "TP Hit Rate %", "percentage_points"),
        ("sl_rate", "SL Hit Rate %", "percentage_points"),
    ]

    for key, label, mtype in metrics_to_check:
        dev = _classify_deviation(bt_overall[key], live_overall[key],
                                  bt_overall["trades"], live_overall["trades"], key)
        notifications.append({
            "scope": "overall",
            "symbol": "ALL",
            "metric": key,
            "metric_label": label,
            "level": dev["level"],
            "bt_value": dev.get("bt_val", bt_overall[key]),
            "live_value": dev.get("live_val", live_overall[key]),
            "delta": dev["delta"],
            "direction": dev.get("direction", ""),
            "bt_trades": bt_overall["trades"],
            "live_trades": live_overall["trades"],
            "message": dev.get("message", ""),
            "calibration_gate": cal.get("calibration_gates", {}).get("min_calibrated_prob_to_open", 0.48),
        })

    # Per-symbol comparison
    all_symbols = set(bt_data["by_symbol"].keys()) | set(live_data["by_symbol"].keys())
    for sym in sorted(all_symbols):
        bt_sym = bt_data["by_symbol"].get(sym, {"trades": 0, "win_rate": 0, "profit_factor": 0,
                                                  "expectancy": 0, "tp_rate": 0, "sl_rate": 0})
        live_sym = live_data["by_symbol"].get(sym, {"trades": 0, "win_rate": 0, "profit_factor": 0,
                                                      "expectancy": 0, "tp_rate": 0, "sl_rate": 0})

        for key, label, mtype in metrics_to_check:
            dev = _classify_deviation(bt_sym[key], live_sym[key],
                                      bt_sym["trades"], live_sym["trades"], key)
            cal_gate = cal.get("calibration_gates", {}).get("per_symbol_overrides", {}).get(sym, 0.48)
            notifications.append({
                "scope": "per_symbol",
                "symbol": sym,
                "metric": key,
                "metric_label": label,
                "level": dev["level"],
                "bt_value": dev.get("bt_val", bt_sym[key]),
                "live_value": dev.get("live_val", live_sym[key]),
                "delta": dev["delta"],
                "direction": dev.get("direction", ""),
                "bt_trades": bt_sym["trades"],
                "live_trades": live_sym["trades"],
                "message": dev.get("message", ""),
                "calibration_gate": cal_gate,
            })

    return notifications


def write_notifications_json(notifications: list, output_path: str):
    """Write notifications as structured JSON."""
    red_count = sum(1 for n in notifications if n["level"] == "RED")
    yellow_count = sum(1 for n in notifications if n["level"] == "YELLOW")
    green_count = sum(1 for n in notifications if n["level"] == "GREEN")
    insufficient = sum(1 for n in notifications if n["level"] == "INSUFFICIENT_DATA")

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "total_checks": len(notifications),
            "red": red_count,
            "yellow": yellow_count,
            "green": green_count,
            "insufficient_data": insufficient,
            "overall_status": "RED" if red_count > 0 else "YELLOW" if yellow_count > 0 else "GREEN",
        },
        "notifications": notifications,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)


def write_notifications_csv(notifications: list, output_path: str):
    """Write notifications as flat CSV."""
    fieldnames = ["scope", "symbol", "metric", "metric_label", "level",
                  "bt_value", "live_value", "delta", "direction",
                  "bt_trades", "live_trades", "message", "calibration_gate"]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for n in notifications:
            writer.writerow(n)


def print_summary(notifications: list):
    """Print a summary of notifications."""
    red = [n for n in notifications if n["level"] == "RED"]
    yellow = [n for n in notifications if n["level"] == "YELLOW"]
    green = [n for n in notifications if n["level"] == "GREEN"]
    insufficient = [n for n in notifications if n["level"] == "INSUFFICIENT_DATA"]

    print(f"\n{'='*70}")
    print(f"  CALIBRATION MONITOR — Live vs Backtest")
    print(f"{'='*70}")
    print(f"  Total checks: {len(notifications)}")
    print(f"  RED: {len(red)}  YELLOW: {len(yellow)}  GREEN: {len(green)}  N/A: {len(insufficient)}")

    overall_status = "RED" if red else "YELLOW" if yellow else "GREEN"
    print(f"  Overall status: {overall_status}")

    if red:
        print(f"\n  RED ALERTS:")
        for n in red:
            print(f"    [{n['symbol']}] {n['metric_label']}: BT={n['bt_value']} Live={n['live_value']} "
                  f"Delta={n['delta']:+.2f} ({n['direction']})")

    if yellow:
        print(f"\n  YELLOW WARNINGS:")
        for n in yellow:
            print(f"    [{n['symbol']}] {n['metric_label']}: BT={n['bt_value']} Live={n['live_value']} "
                  f"Delta={n['delta']:+.2f} ({n['direction']})")

    if insufficient:
        print(f"\n  INSUFFICIENT DATA (need {MIN_LIVE_TRADES}+ live trades):")
        for n in insufficient:
            print(f"    [{n['symbol']}] {n['metric_label']}: {n['message']}")

    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="VLTHR Calibration Monitor")
    parser.add_argument("--backtest-csv", type=str, required=True,
                        help="Path to backtest_results.csv")
    parser.add_argument("--live-csv", type=str, default=None,
                        help="Path to live trades CSV (alternative to DB)")
    parser.add_argument("--db-url", type=str, default=None,
                        help="Database URL for live trades (alternative to CSV)")
    parser.add_argument("--output-json", type=str, default=None,
                        help="Output JSON path")
    parser.add_argument("--output-csv", type=str, default=None,
                        help="Output CSV path")
    args = parser.parse_args()

    output_json = args.output_json or str(_BACKTEST_DIR / "calibration_notifications.json")
    output_csv = args.output_csv or str(_BACKTEST_DIR / "calibration_notifications.csv")

    # Load backtest
    print(f"Loading backtest: {args.backtest_csv}")
    bt_data = _load_backtest_csv(args.backtest_csv)
    print(f"  Backtest: {bt_data['overall']['trades']} closed trades, "
          f"WR={bt_data['overall']['win_rate']:.1f}%, "
          f"PF={bt_data['overall']['profit_factor']:.2f}")

    # Load live
    if args.db_url:
        print(f"Loading live from DB: {args.db_url}")
        live_data = _load_live_from_db(args.db_url)
    elif args.live_csv:
        print(f"Loading live CSV: {args.live_csv}")
        live_data = _load_live_csv(args.live_csv)
    else:
        print("No live data source provided. Outputting backtest-only baseline.")
        live_data = {"overall": {"trades": 0, "wins": 0, "win_rate": 0, "profit_factor": 0,
                                  "expectancy": 0, "tp_rate": 0, "sl_rate": 0,
                                  "time_exit_rate": 0, "total_pnl": 0},
                     "by_symbol": {}}

    print(f"  Live: {live_data['overall']['trades']} closed trades, "
          f"WR={live_data['overall']['win_rate']:.1f}%, "
          f"PF={live_data['overall']['profit_factor']:.2f}")

    # Load calibration
    cal = _load_calibration()

    # Compare
    notifications = compare(bt_data, live_data, cal)

    # Output
    write_notifications_json(notifications, output_json)
    print(f"\n  JSON: {output_json}")
    write_notifications_csv(notifications, output_csv)
    print(f"  CSV:  {output_csv}")

    print_summary(notifications)


if __name__ == "__main__":
    main()
