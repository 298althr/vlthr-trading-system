"""
VLTHR Data Scheduler — Candle-Aligned Sequential Bybit Data Ingestion
======================================================================

Key behaviours:
1. IMMEDIATE STARTUP: On launch, runs a full sequential pass for ALL
   symbols and ALL data types.  No waiting for interval boundaries.
2. CANDLE-ALIGNED SCHEDULING: After startup, each interval fires at
   absolute wall-clock boundaries (:00, :05, :10 … for 5 m) plus a
   small offset so we fetch just after the candle closes.
3. SEQUENTIAL PROCESSING: For each interval, all symbols are processed
   one-by-one with the global 2 s throttle.  No burst patterns.
4. RATE-LIMIT SAFE: 6 symbols × ~9 requests = ~18 s per interval pass.
   Well inside Bybit's 600 req / 5 s limit.

Schedule (absolute wall-clock + offset):
- Orderbook  : every 5  min  (+10 s offset)
- OHLCV 5m   : every 5  min  (+30 s offset)
- OHLCV 15m  : every 15 min  (+60 s offset)
- OHLCV 30m  : every 30 min  (+90 s offset)
- Enrich     : every 15 min  (+120 s offset)
- OHLCV 1h   : every 1  hour (+150 s offset)
- Funding    : every 1  hour (+180 s offset)
- OI         : every 1  hour (+210 s offset)
- OHLCV 4h   : every 4  hours (+240 s offset)
- Sentiment (Fear/Greed, BTC dominance, LS ratios, taker vol) : staggered

Usage:
    python data/bybit/workers/data_scheduler.py
    python data/bybit/workers/data_scheduler.py --run-once
    python data/bybit/workers/data_scheduler.py --cooldown 3.0
    python data/bybit/workers/data_scheduler.py --symbols BTCUSDT ETHUSDT
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scheduler_core import DataScheduler, log_info, log_ok, log_warn, log_err, REQUEST_COOLDOWN, SYMBOLS

# ── CONFIGURATION ─────────────────────────────────────────────────────────

OHLCV_TFS = ["4h", "1h", "30m", "15m", "5m", "1d"]

# (interval_name, interval_minutes, offset_seconds)
# Offsets spread load so intervals don't all fire at once
INTERVAL_CONFIG: List[Tuple[str, int, int]] = [
    ("orderbook",   5,   10),   # 10 s past each 5-min boundary
    ("ohlcv_5m",    5,   30),   # 30 s past each 5-min boundary
    ("ohlcv_15m",   15,  60),   # 1 min past each 15-min boundary
    ("ohlcv_30m",   30,  90),   # 1 min 30 s past each 30-min boundary
    ("enrich",      15,  120),  # 2 min past each 15-min boundary
    ("ohlcv_1h",    60,  150),  # 2 min 30 s past each hour
    ("funding",     60,  180),  # 3 min past each hour
    ("oi",          60,  210),  # 3 min 30 s past each hour
    ("ohlcv_4h",    240, 240),  # 4 min past each 4-hour boundary
    ("ohlcv_1d",   1440, 300),  # 5 min past each daily boundary (00:00 UTC)
    # Sentiment data (no API keys, free sources)
    ("btc_dominance",     5,   15),  # every 5 min, 15s offset (CoinGecko keyless)
    ("bybit_ls_ratio",   60,  240),  # every 1h, 4 min offset (Bybit public)
    ("binance_ls_ratio", 60,  270),  # every 1h, 4.5 min offset (Binance public)
    ("binance_taker_vol",60,  300),  # every 1h, 5 min offset (Binance public)
    ("fear_greed",     1440,  360),  # daily, 6 min offset (alternative.me)
]


# ── TIME ALIGNMENT ────────────────────────────────────────────────────────

def seconds_until_next_boundary(interval_min: int, offset_sec: int) -> float:
    """Return seconds until the next interval boundary + offset."""
    now = datetime.now(timezone.utc)
    sec_in_day = now.hour * 3600 + now.minute * 60 + now.second
    interval_sec = interval_min * 60
    next_boundary = ((sec_in_day // interval_sec) + 1) * interval_sec
    wait = next_boundary - sec_in_day + offset_sec
    return float(wait)


# ── SEQUENTIAL PASS RUNNERS ───────────────────────────────────────────────

def run_full_pass(scheduler: DataScheduler, symbols: List[str], tfs: List[str]) -> None:
    """Immediate sequential catch-up at startup."""
    log_info("STARTUP — full sequential pass beginning...")
    for symbol in symbols:
        for tf in tfs:
            scheduler.fetch_ohlcv(symbol, tf)
        scheduler.fetch_funding(symbol)
        scheduler.fetch_oi(symbol)
        scheduler.fetch_orderbook(symbol)
        scheduler.enrich(symbol, tfs)
    # Sentiment data (global + per-symbol)
    scheduler.fetch_fear_greed()
    scheduler.fetch_btc_dominance()
    for symbol in symbols:
        scheduler.fetch_bybit_ls_ratio(symbol)
        scheduler.fetch_binance_ls_ratio(symbol)
        scheduler.fetch_binance_taker_volume(symbol)
    log_ok("STARTUP — full pass complete.")


def run_interval_pass(
    scheduler: DataScheduler,
    symbols: List[str],
    interval_name: str,
    tfs: List[str],
) -> None:
    """Run one interval type for all symbols sequentially."""
    if interval_name.startswith("ohlcv_"):
        tf = interval_name.replace("ohlcv_", "")
        for symbol in symbols:
            scheduler.fetch_ohlcv(symbol, tf)
    elif interval_name == "funding":
        for symbol in symbols:
            scheduler.fetch_funding(symbol)
    elif interval_name == "oi":
        for symbol in symbols:
            scheduler.fetch_oi(symbol)
    elif interval_name == "orderbook":
        for symbol in symbols:
            scheduler.fetch_orderbook(symbol)
    elif interval_name == "enrich":
        for symbol in symbols:
            scheduler.enrich(symbol, tfs)
    elif interval_name == "fear_greed":
        scheduler.fetch_fear_greed()
    elif interval_name == "btc_dominance":
        scheduler.fetch_btc_dominance()
    elif interval_name == "bybit_ls_ratio":
        for symbol in symbols:
            scheduler.fetch_bybit_ls_ratio(symbol)
    elif interval_name == "binance_ls_ratio":
        for symbol in symbols:
            scheduler.fetch_binance_ls_ratio(symbol)
    elif interval_name == "binance_taker_vol":
        for symbol in symbols:
            scheduler.fetch_binance_taker_volume(symbol)


# ── HEALTH REPORT ─────────────────────────────────────────────────────────

def health_report(scheduler: DataScheduler, jobs_pending: int):
    stats = scheduler.get_stats()
    uptime = stats["uptime_sec"]
    h = int(uptime // 3600)
    m = int((uptime % 3600) // 60)
    s = int(uptime % 60)
    log_info(
        f"Health — uptime {h:02d}:{m:02d}:{s:02d} | "
        f"requests={stats['requests']} | errors={stats['errors']} | "
        f"intervals pending={jobs_pending}"
    )


# ── MAIN LOOP ─────────────────────────────────────────────────────────────

def run_scheduler(
    scheduler: DataScheduler,
    symbols: List[str],
    tfs: List[str],
    health_interval_sec: int = 300,
) -> None:
    """Master loop: aligned boundaries, sequential passes, no bursts."""
    log_ok("Scheduler running. Press Ctrl+C to stop.")

    # Compute initial next-run times (aligned to boundaries + offsets)
    next_run: dict[str, float] = {}
    for name, interval_min, offset in INTERVAL_CONFIG:
        wait = seconds_until_next_boundary(interval_min, offset)
        next_run[name] = time.monotonic() + wait
        log_info(f"  Next {name}: in {wait:.0f}s")

    last_health = time.monotonic()

    while True:
        try:
            now = time.monotonic()

            # Fire any due intervals
            for name, interval_min, offset in INTERVAL_CONFIG:
                if now >= next_run[name]:
                    log_info(f"Triggering interval: {name}")
                    run_interval_pass(scheduler, symbols, name, tfs)
                    next_run[name] = now + (interval_min * 60)
                    log_info(f"  Next {name}: in {interval_min * 60}s")

            # Health report
            if now - last_health >= health_interval_sec:
                pending = sum(1 for n, _, _ in INTERVAL_CONFIG if now >= next_run[n])
                health_report(scheduler, pending)
                last_health = now

            time.sleep(1)

        except KeyboardInterrupt:
            log_warn("Interrupted by user. Shutting down...")
            break
        except Exception as e:
            log_err(f"Main loop error: {e}")
            time.sleep(5)

    stats = scheduler.get_stats()
    log_info(f"Shutdown complete. Total requests: {stats['requests']} | Errors: {stats['errors']}")


# ── ENTRY POINT ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VLTHR Bybit V5 Data Scheduler")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help=f"Symbols (default: {SYMBOLS})")
    parser.add_argument("--tfs", nargs="+", default=OHLCV_TFS, help=f"Timeframes (default: {OHLCV_TFS})")
    parser.add_argument("--cooldown", type=float, default=REQUEST_COOLDOWN, help=f"Seconds between API requests (default: {REQUEST_COOLDOWN})")
    parser.add_argument("--testnet", action="store_true", help="Use Bybit testnet")
    parser.add_argument("--health-interval", type=int, default=300, help="Health report interval in seconds (default: 300)")
    parser.add_argument("--run-once", action="store_true", help="Run all jobs once immediately, then exit")
    args = parser.parse_args()

    if args.cooldown != REQUEST_COOLDOWN:
        from scheduler_core import GlobalThrottle
        GlobalThrottle._instance = None
        _ = GlobalThrottle(cooldown=args.cooldown)
        log_info(f"Global cooldown overridden to {args.cooldown}s")

    scheduler = DataScheduler(symbols=args.symbols, testnet=args.testnet)

    print("=" * 70)
    print("  VLTHR DATA SCHEDULER")
    print(f"  Symbols:    {', '.join(args.symbols)}")
    print(f"  Timeframes: {', '.join(args.tfs)}")
    print(f"  Cooldown:   {args.cooldown}s between requests")
    print(f"  API:        {'testnet' if args.testnet else 'mainnet'}")
    print("=" * 70)

    if args.run_once:
        run_full_pass(scheduler, args.symbols, args.tfs)
        return

    # Startup catch-up
    run_full_pass(scheduler, args.symbols, args.tfs)

    # Register signal handlers
    def _sigterm_handler(signum, frame):
        log_warn(f"Received signal {signum}. Shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    run_scheduler(scheduler, args.symbols, args.tfs, health_interval_sec=args.health_interval)


if __name__ == "__main__":
    main()
