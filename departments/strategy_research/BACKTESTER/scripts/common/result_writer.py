"""
Result writer — creates timestamped result folders with standard outputs.
"""
import json
import csv
from datetime import datetime
from pathlib import Path

RESULTS_ROOT = Path(__file__).resolve().parents[2] / "results"


def create_run_folder(prefix: str, symbol: str, timeframe: str = "") -> Path:
    """Create a unique timestamped folder for a test run."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tf_part = f"_{timeframe}" if timeframe else ""
    folder = RESULTS_ROOT / f"{prefix}_{symbol}{tf_part}_{ts}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_run_metadata(folder: Path, meta: dict):
    """Write run_metadata.json to the result folder."""
    path = folder / "run_metadata.json"
    with open(path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    return path


def write_summary_metrics(folder: Path, metrics: dict):
    """Write summary_metrics.csv (single row with headers)."""
    path = folder / "summary_metrics.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(metrics.keys()))
        writer.writeheader()
        writer.writerow(metrics)
    return path


def write_trade_statement(folder: Path, trades: list):
    """Write trade_statement.csv. Trades is a list of dicts."""
    if not trades:
        path = folder / "trade_statement.csv"
        with open(path, "w", newline="") as f:
            pass
        return path

    cols = [
        "trade_id", "entry_time", "exit_time", "symbol", "timeframe",
        "direction", "entry_price", "exit_price", "sl_price", "tp_price",
        "size", "pnl_usd", "pnl_pct", "duration_hours", "exit_reason",
        "session", "regime", "year", "month",
        "leverage", "liq_price", "liq_distance_pct", "sl_distance_pct",
        "sl_inside_liq_zone", "funding_cost_usd", "funding_events",
        "net_pnl_usd", "net_pnl_pct"
    ]
    # Ensure all trades have all columns
    for t in trades:
        for c in cols:
            if c not in t:
                t[c] = ""

    path = folder / "trade_statement.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(trades)
    return path


def write_equity_curve(folder: Path, equity: dict):
    """
    Write equity_curve.csv.
    equity: dict mapping ISO timestamp string -> equity value
    """
    path = folder / "equity_curve.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "equity"])
        for ts, val in equity.items():
            writer.writerow([ts, val])
    return path


def write_csv(folder: Path, filename: str, rows: list, headers: list = None):
    """Generic CSV writer."""
    path = folder / filename
    with open(path, "w", newline="") as f:
        if headers:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(f)
            writer.writerows(rows)
    return path
