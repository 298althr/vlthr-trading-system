"""
Bybit V5 Historical Data Ingestion — Manual Standalone Script
===============================================================
Thin wrapper around bybit_ingest_core. All logic (API client, parquet I/O,
gap detection, backfill, validation) lives in the core module.

Usage:
    python fetch_bybit_data.py
    python fetch_bybit_data.py --symbols BTCUSDT --timeframes 15m 5m
    python fetch_bybit_data.py --start 2024-01-01 --dry-run
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bybit_ingest_core
from bybit_ingest_core import (
    BybitClient,
    ingest_symbol_tf,
    SYMBOLS,
    TIMEFRAMES,
    DATA_ROOT,
    REQUEST_DELAY,
    log_info,
    log_err,
)
import json
import time
from datetime import datetime, timezone

# ── MAIN WORKFLOW ──────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Bybit V5 manual data ingestion for VLTHR Engine"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=SYMBOLS,
        help=f"Symbols to fetch (default: {SYMBOLS})",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=TIMEFRAMES,
        help=f"Timeframes to fetch (default: {TIMEFRAMES})",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Override start date (YYYY-MM-DD) when no existing data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate but do not write parquet",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY,
        help=f"Seconds between API requests (default: {REQUEST_DELAY})",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use Bybit testnet API",
    )
    args = parser.parse_args()

    if args.start:
        bybit_ingest_core.DEFAULT_START = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    base_url = "https://api-testnet.bybit.com/v5/market/kline" if args.testnet else "https://api.bybit.com/v5/market/kline"

    client = BybitClient(base_url=base_url, delay=args.delay)

    print("=" * 70)
    print("  BYBIT V5 DATA INGESTION")
    print(f"  Symbols:    {', '.join(args.symbols)}")
    print(f"  Timeframes: {', '.join(args.timeframes)}")
    print(f"  API:        {base_url}")
    print(f"  Delay:      {args.delay}s between requests")
    print(f"  Dry run:    {args.dry_run}")
    print("=" * 70)

    results = []
    total_start = time.monotonic()

    for symbol in args.symbols:
        for tf in args.timeframes:
            try:
                res = ingest_symbol_tf(client, symbol, tf, dry_run=args.dry_run)
                results.append(res)
                print()
            except Exception as e:
                log_err(f"{symbol}/{tf} failed: {e}")
                results.append({
                    "symbol": symbol, "tf": tf,
                    "status": "error", "error": str(e),
                })
                print()

    total_elapsed = time.monotonic() - total_start

    # Summary
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    issue_count = sum(1 for r in results if r.get("status") == "issues")
    err_count = sum(1 for r in results if r.get("status") in ("error", "no_data"))

    print(f"  Completed:  {ok_count} OK | {issue_count} with issues | {err_count} error/no data")
    print(f"  Elapsed:    {total_elapsed:.1f}s")
    for r in results:
        s = f"  {r['symbol']:10s} {r['tf']:5s} -> {r.get('status','?'):10s}"
        s += f"  existing={r.get('existing_rows',0):>7,}"
        s += f"  fetched={r.get('fetched_rows',0):>7,}"
        s += f"  final={r.get('final_rows',0):>7,}"
        if r.get("gaps_found"):
            s += f"  gaps={r['gaps_found']}"
        if r.get("issues"):
            s += f"  issues={len(r['issues'])}"
        print(s)
    print("=" * 70)

    # Save summary JSON
    summary_path = DATA_ROOT / "ingestion_summary.json"
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": total_elapsed,
        "results": results,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log_info(f"Summary written to {summary_path}")


if __name__ == "__main__":
    main()
