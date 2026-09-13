"""
VLTHR 1-Minute Data Scheduler — Dedicated High-Frequency Price Feed
===================================================================

Fetches 1m OHLCV candles for all 6 symbols every minute using PROXY_URL_2.
Designed to power the live dashboard with sub-minute price granularity.

Key behaviours:
1. PROXY_URL_2: Reads a secondary proxy from env (falls back to PROXY_URL).
2. CANDLE-ALIGNED: Fires at the start of each minute (:00) + 5s offset.
3. SEQUENTIAL: One symbol at a time with 2-second cooldown.
4. RATE-LIMIT SAFE: 6 req/min = 0.1 req/s — well inside Bybit's 50 req/s.

Storage:
    data/bybit/{SYMBOL}/1m/{YYYY}/{MM}.parquet

Usage:
    python data/bybit/workers/minute_scheduler.py
    python data/bybit/workers/minute_scheduler.py --run-once
    python data/bybit/workers/minute_scheduler.py --cooldown 1.0
    python data/bybit/workers/minute_scheduler.py --symbols BTCUSDT ETHUSDT
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── LOAD .ENV FROM ENGINE ROOT ──────────────────────────────────────────
# Must happen BEFORE any proxy reads so env vars are populated.
_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
try:
    from dotenv import load_dotenv
    load_dotenv(_env_path, override=False)
    print(f"[1m-Scheduler] Loaded env from {_env_path}")
except ImportError:
    print("[1m-Scheduler] python-dotenv not installed; using existing env vars only")
except Exception as e:
    print(f"[1m-Scheduler] Could not load .env: {e}")


# ── PROXY NORMALIZATION ─────────────────────────────────────────────────

def normalize_proxy_url(raw: str) -> str:
    """
    Convert shorthand proxy format (user:pass:host:port)
    to full URL (http://user:pass@host:port).
    """
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    parts = raw.split(":")
    if len(parts) < 4:
        if "@" in raw:
            return f"http://{raw}"
        raise ValueError(f"Cannot parse proxy URL: {raw}")
    port = parts[-1]
    host = parts[-2]
    user_pass = ":".join(parts[:-2])
    return f"http://{user_pass}@{host}:{port}"


# ── PROXY SETUP ───────────────────────────────────────────────────────────
# Use PROXY_URL_2 if available; otherwise fall back to PROXY_URL.
# Must be done BEFORE importing scheduler_core (it reads PROXY_URL on import).
_PROXY_2_RAW = os.environ.get("PROXY_URL_2", "")
_PROXY_1_RAW = os.environ.get("PROXY_URL", "")
_PROXY_2 = ""
_PROXY_1 = ""

try:
    if _PROXY_2_RAW:
        _PROXY_2 = normalize_proxy_url(_PROXY_2_RAW)
        os.environ["PROXY_URL"] = _PROXY_2
        print(f"[1m-Scheduler] Using PROXY_URL_2: {_PROXY_2.split('@')[-1]}")
    elif _PROXY_1_RAW:
        _PROXY_1 = normalize_proxy_url(_PROXY_1_RAW)
        os.environ["PROXY_URL"] = _PROXY_1
        print(f"[1m-Scheduler] Using PROXY_URL (fallback): {_PROXY_1.split('@')[-1]}")
    else:
        print("[1m-Scheduler] No proxy configured — direct connection")
except ValueError as e:
    print(f"[1m-Scheduler] [WARN] Proxy parse error: {e}")
    print("[1m-Scheduler] Falling back to direct connection")

# ── IMPORTS (must come after proxy swap) ────────────────────────────────

import scheduler_core as _sc
from scheduler_core import (
    DataScheduler,
    GlobalThrottle,
    REQUEST_COOLDOWN,
    SYMBOLS,
    log_info,
    log_ok,
    log_warn,
    log_err,
)
from bybit_ingest_core import read_existing as _read_existing_ohlcv, write_partitions as _write_ohlcv_partitions

# ── CONFIGURATION ─────────────────────────────────────────────────────────

COOLDOWN_SECONDS = 2.0
FETCH_OFFSET_SECONDS = 5      # fetch 5s past each minute boundary
HEALTH_INTERVAL_SEC = 300     # health report every 5 min
BACKFILL_HOURS = 24           # initial backfill window for missing 1m data

# ── TIME ALIGNMENT ───────────────────────────────────────────────────────

def seconds_until_next_minute(offset: int = FETCH_OFFSET_SECONDS) -> float:
    """Return seconds until the next minute boundary + offset."""
    now = datetime.now(timezone.utc)
    sec_in_minute = now.second + now.microsecond / 1_000_000
    wait = (60 - sec_in_minute) + offset
    return float(wait)


# ── BOOTSTRAP / BACKFILL ──────────────────────────────────────────────────

def _has_1m_data(symbol: str) -> bool:
    """Check if 1m parquet exists and has rows."""
    df = _read_existing_ohlcv(symbol, "1m")
    return not df.empty


def bootstrap_1m(scheduler: DataScheduler, symbols: List[str]) -> None:
    """
    For each symbol, if 1m parquet is missing or empty, backfill the last 24h.
    Uses the same incremental fetch logic but temporarily overrides DEFAULT_START
    so we don't pull from 2023.
    """
    log_info("Bootstrap check — verifying 1m data exists for all symbols...")
    needs_backfill = [s for s in symbols if not _has_1m_data(s)]
    if not needs_backfill:
        log_ok("All symbols have existing 1m data — no backfill needed.")
        return

    log_warn(f"Backfill required for: {', '.join(needs_backfill)}")

    # Temporarily patch DEFAULT_START in scheduler_core to 24h ago
    original_default = _sc.DEFAULT_START
    backfill_since = datetime.now(timezone.utc) - timedelta(hours=BACKFILL_HOURS)
    _sc.DEFAULT_START = backfill_since
    log_info(f"DEFAULT_START temporarily set to {backfill_since.isoformat()}")

    try:
        for symbol in needs_backfill:
            log_info(f"Backfilling {symbol}/1m (last {BACKFILL_HOURS}h)...")
            result = scheduler.fetch_ohlcv(symbol, "1m")
            status = result.get("status", "unknown")
            fetched = result.get("fetched", 0)
            final = result.get("final", 0)
            if status == "error":
                log_err(f"  {symbol}/1m backfill FAILED: {result.get('issues', [])}")
            else:
                log_ok(f"  {symbol}/1m backfill OK | +{fetched} rows | total={final}")
    finally:
        _sc.DEFAULT_START = original_default
        log_info("DEFAULT_START restored.")


# ── SEQUENTIAL PASS ───────────────────────────────────────────────────────

def run_1m_pass(scheduler: DataScheduler, symbols: List[str]) -> None:
    """Fetch 1m OHLCV for all symbols sequentially (incremental)."""
    log_info("1m pass starting...")
    for symbol in symbols:
        result = scheduler.fetch_ohlcv(symbol, "1m")
        status = result.get("status", "unknown")
        fetched = result.get("fetched", 0)
        final = result.get("final", 0)
        log_info(f"  {symbol}/1m → {status} | +{fetched} rows | total={final}")
    log_ok("1m pass complete.")


# ── MAIN LOOP ─────────────────────────────────────────────────────────────

def run_minute_scheduler(
    scheduler: DataScheduler,
    symbols: List[str],
    health_interval_sec: int = HEALTH_INTERVAL_SEC,
) -> None:
    """Master loop: fires every minute, sequential passes, no bursts."""
    log_ok("1-Minute Scheduler running. Press Ctrl+C to stop.")

    # Bootstrap: backfill 24h of 1m data for any missing symbols
    bootstrap_1m(scheduler, symbols)

    # Wait until the next minute boundary + offset before first fetch
    wait = seconds_until_next_minute()
    log_info(f"Aligning to next minute boundary — waiting {wait:.1f}s")
    time.sleep(wait)

    next_run = time.monotonic()
    last_health = time.monotonic()

    while True:
        try:
            now = time.monotonic()

            if now >= next_run:
                log_info("Triggering 1m interval")
                run_1m_pass(scheduler, symbols)
                next_run = now + 60.0  # next minute
                log_info(f"  Next 1m pass in 60s")

            # Health report
            if now - last_health >= health_interval_sec:
                stats = scheduler.get_stats()
                uptime = stats["uptime_sec"]
                h = int(uptime // 3600)
                m = int((uptime % 3600) // 60)
                s = int(uptime % 60)
                log_info(
                    f"Health — uptime {h:02d}:{m:02d}:{s:02d} | "
                    f"requests={stats['requests']} | errors={stats['errors']}"
                )
                last_health = now

            time.sleep(0.5)

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
    parser = argparse.ArgumentParser(description="VLTHR 1-Minute Data Scheduler")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help=f"Symbols (default: {SYMBOLS})")
    parser.add_argument("--cooldown", type=float, default=COOLDOWN_SECONDS, help=f"Seconds between API requests (default: {COOLDOWN_SECONDS})")
    parser.add_argument("--testnet", action="store_true", help="Use Bybit testnet")
    parser.add_argument("--health-interval", type=int, default=HEALTH_INTERVAL_SEC, help="Health report interval in seconds")
    parser.add_argument("--run-once", action="store_true", help="Run one pass immediately, then exit")
    args = parser.parse_args()

    # Override global throttle cooldown
    if args.cooldown != REQUEST_COOLDOWN:
        GlobalThrottle._instance = None
        _ = GlobalThrottle(cooldown=args.cooldown)
        log_info(f"Global cooldown overridden to {args.cooldown}s")

    scheduler = DataScheduler(symbols=args.symbols, testnet=args.testnet)

    print("=" * 70)
    print("  VLTHR 1-MINUTE DATA SCHEDULER")
    print(f"  Symbols:    {', '.join(args.symbols)}")
    print(f"  Timeframe:  1m")
    print(f"  Cooldown:   {args.cooldown}s between requests")
    print(f"  API:        {'testnet' if args.testnet else 'mainnet'}")
    print("=" * 70)

    if args.run_once:
        bootstrap_1m(scheduler, args.symbols)
        run_1m_pass(scheduler, args.symbols)
        return

    # Register signal handlers
    def _sigterm_handler(signum, frame):
        log_warn(f"Received signal {signum}. Shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    run_minute_scheduler(scheduler, args.symbols, health_interval_sec=args.health_interval)


if __name__ == "__main__":
    main()
