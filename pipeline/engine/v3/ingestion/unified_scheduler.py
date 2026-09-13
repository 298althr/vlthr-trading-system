"""
V3 Unified Scheduler
=====================
Single scheduler replacing data_scheduler.py + minute_scheduler.py.

Features:
- Candle-aligned scheduling for all data types
- 1m OHLCV included (was separate minute_scheduler)
- LS ratio + liquidations fetchers integrated
- All fetches logged to ingestion_log DB table
- Gap backfill integrated into OHLCV fetch
- Enrichment triggered after OHLCV updates
- Health reporting

Schedule (absolute wall-clock + offset):
- Orderbook:    every 5 min  (+10s)
- OHLCV 1m:     every 1 min  (+5s)
- OHLCV 5m:     every 5 min  (+30s)
- OHLCV 15m:    every 15 min (+60s)
- OHLCV 30m:    every 30 min (+90s)
- Enrich:       every 15 min (+120s)
- OHLCV 1h:     every 1 hour (+150s)
- Funding:      every 1 hour (+180s)
- OI:           every 1 hour (+210s)
- LS Ratio:     every 1 hour (+240s)
- Liquidations: every 15 min (+270s)
- OHLCV 4h:     every 4 hours (+300s)
- OHLCV 1d:     every 1 day  (+360s)

Usage:
    python -m engine.v3.ingestion.unified_scheduler
    python -m engine.v3.ingestion.unified_scheduler --run-once
    python -m engine.v3.ingestion.unified_scheduler --cooldown 1.5
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

import numpy as np
import pandas as pd

# Ensure imports resolve
_engine = Path(__file__).resolve().parents[3]
_v3_root = Path(__file__).resolve().parents[2]
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent

sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_v3_root / "ingestion"))
sys.path.insert(0, str(_repo / "data" / "bybit" / "workers"))

# Load .env
_env = _repo / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

from .bybit_client import BybitClient, GlobalThrottle, INTERVAL_MAP, INTERVAL_MINUTES, SYMBOLS, PAGE_LIMIT, paginate_history
from .parquet_store import write_partitions, read_existing, validate_df, DATA_ROOT
from .ls_ratio import fetch_ls_ratio
from .liquidations import fetch_liquidations

# Import V2 enrichment
try:
    from bybit_enrich_core import run_symbol as _enrich_run_symbol
except ImportError:
    _enrich_run_symbol = None
    print("[V3-Scheduler] WARNING: bybit_enrich_core not found, enrichment disabled")

# DB logging
try:
    import psycopg2
except ImportError:
    psycopg2 = None

OHLCV_TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
OVERLAP_BARS = 2
DEFAULT_START = datetime(2023, 1, 1, tzinfo=timezone.utc)

# (name, interval_minutes, offset_seconds)
INTERVAL_CONFIG: List[Tuple[str, int, int]] = [
    ("orderbook",     5,   10),
    ("ohlcv_1m",      1,    5),
    ("ohlcv_5m",      5,   30),
    ("ohlcv_15m",    15,   60),
    ("ohlcv_30m",    30,   90),
    ("enrich",       15,  120),
    ("ohlcv_1h",     60,  150),
    ("funding",      60,  180),
    ("oi",           60,  210),
    ("ls_ratio",     60,  240),
    ("liquidations", 15,  270),
    ("ohlcv_4h",    240,  300),
    ("ohlcv_1d",   1440,  360),
]


class C:
    OK = "\033[92m"
    INFO = "\033[94m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    END = "\033[0m"


def log_ok(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"{C.OK}[OK]{C.END} [{ts}] {msg}", flush=True)

def log_info(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"{C.INFO}[INFO]{C.END} [{ts}] {msg}", flush=True)

def log_warn(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"{C.WARN}[WARN]{C.END} [{ts}] {msg}", flush=True)

def log_err(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"{C.ERR}[ERR]{C.END} [{ts}] {msg}", flush=True)


class UnifiedScheduler:
    """Manages all scheduled data fetch jobs with a shared throttle and DB logging."""

    def __init__(self, symbols: List[str] = None, cooldown: float = 2.0,
                 testnet: bool = False):
        self.symbols = symbols or SYMBOLS.copy()
        self.client = BybitClient(cooldown=cooldown, testnet=testnet)
        self._db_url = os.environ.get("DB_URL", "")
        self._db_available = False
        if self._db_url and psycopg2:
            self._ensure_ingestion_log()

    def _ensure_ingestion_log(self):
        try:
            conn = psycopg2.connect(self._db_url)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_log (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(16) NOT NULL,
                    timeframe VARCHAR(8) NOT NULL,
                    data_type VARCHAR(16) NOT NULL,
                    rows_existing INT,
                    rows_fetched INT,
                    rows_final INT,
                    gaps_found INT,
                    status VARCHAR(16),
                    issues TEXT,
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            try:
                cur.execute("""
                    SELECT create_hypertable('ingestion_log', 'fetched_at',
                        if_not_exists => TRUE, migrate_data => TRUE);
                """)
            except Exception:
                pass
            conn.commit()
            cur.close()
            conn.close()
            self._db_available = True
            log_ok("ingestion_log table ready")
        except Exception as e:
            log_warn(f"ingestion_log setup failed (DB logging disabled): {e}")
            self._db_available = False

    def log_ingestion(self, result: Dict[str, Any]):
        if not self._db_available or not self._db_url:
            return
        try:
            conn = psycopg2.connect(self._db_url)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO ingestion_log
                    (symbol, timeframe, data_type, rows_existing, rows_fetched,
                     rows_final, gaps_found, status, issues)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                result.get("symbol", ""),
                result.get("tf", ""),
                result.get("type", ""),
                result.get("existing"),
                result.get("fetched"),
                result.get("final"),
                result.get("gaps_found", 0),
                result.get("status", ""),
                "; ".join(result.get("issues", []))[:2000],
            ))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            log_warn(f"ingestion_log write failed: {e}")

    # ── OHLCV ─────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, tf: str) -> Dict[str, Any]:
        result = {
            "symbol": symbol, "tf": tf, "type": "ohlcv",
            "existing": 0, "fetched": 0, "final": 0,
            "gaps_found": 0, "status": "pending", "issues": [],
        }
        try:
            interval = INTERVAL_MAP[tf]
            interval_min = INTERVAL_MINUTES[tf]

            existing = read_existing("ohlcv", symbol, tf)
            if not existing.empty:
                result["existing"] = len(existing)
                last_ts = existing["timestamp"].max()
                since = last_ts - timedelta(minutes=interval_min * OVERLAP_BARS)
            else:
                since = DEFAULT_START

            since_ms = int(since.timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            candles = self.client.fetch_klines(symbol, interval, since_ms, until_ms, limit=1000)

            if not candles:
                result["status"] = "no_new_data"
                self.log_ingestion(result)
                return result

            # Parse candles: [start, open, high, low, close, volume, turnover]
            rows = []
            for c in candles:
                rows.append({
                    "timestamp": pd.to_datetime(int(c[0]), unit="ms", utc=True),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                    "turnover": float(c[6]) if len(c) > 6 else 0.0,
                })
            new_df = pd.DataFrame(rows)
            result["fetched"] = len(new_df)

            if existing.empty:
                combined = new_df
            else:
                combined = pd.concat([existing, new_df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates(
                    "timestamp", keep="last"
                ).reset_index(drop=True)

            ok, issues = validate_df(combined, "ohlcv")
            result["issues"] = issues
            result["final"] = len(combined)

            write_partitions(combined, "ohlcv", symbol, tf)
            result["status"] = "ok" if ok else "issues"

            if result["fetched"] > 0:
                log_ok(f"OHLCV {symbol}/{tf}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"OHLCV {symbol}/{tf}: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"OHLCV {symbol}/{tf} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── FUNDING ───────────────────────────────────────────────────────────

    def fetch_funding(self, symbol: str) -> Dict[str, Any]:
        result = {
            "symbol": symbol, "tf": "8h", "type": "funding_rate",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            existing = read_existing("funding_rate", symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                since = existing["fundingTime"].max() - timedelta(hours=1)
            else:
                since = DEFAULT_START

            since_ms = int(since.timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            df = paginate_history(
                self.client, symbol,
                lambda s, end_ms, limit=PAGE_LIMIT: self.client.fetch_funding(s, end_ms, limit),
                since_ms, until_ms,
            )

            if not df.empty:
                result["fetched"] = len(df)
                df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
                df["fundingTime"] = pd.to_datetime(df["fundingRateTimestamp"].astype(np.int64), unit="ms", utc=True)
                df["symbol"] = symbol
                df = df[["symbol", "fundingRate", "fundingTime"]].copy()

            if existing.empty and df.empty:
                result["status"] = "no_data"
                self.log_ingestion(result)
                return result
            elif existing.empty:
                combined = df
            else:
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("fundingTime").drop_duplicates("fundingTime").reset_index(drop=True)

            ok, issues = validate_df(combined, "funding_rate")
            result["issues"] = issues
            result["final"] = len(combined)

            write_partitions(combined, "funding_rate", symbol)
            result["status"] = "ok" if ok else "issues"

            if result["fetched"] > 0:
                log_ok(f"Funding {symbol}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"Funding {symbol}: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Funding {symbol} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── OPEN INTEREST ─────────────────────────────────────────────────────

    def fetch_oi(self, symbol: str, interval: str = "1h") -> Dict[str, Any]:
        result = {
            "symbol": symbol, "tf": interval, "type": f"open_interest_{interval}",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            existing = read_existing("open_interest", symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                since = existing["timestamp"].max() - timedelta(hours=1)
            else:
                since = DEFAULT_START

            since_ms = int(since.timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            df = paginate_history(
                self.client, symbol,
                lambda s, end_ms, limit=PAGE_LIMIT: self.client.fetch_open_interest(s, interval, end_ms, limit),
                since_ms, until_ms,
            )

            if not df.empty:
                result["fetched"] = len(df)
                df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce")
                df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
                df["symbol"] = symbol
                df = df[["symbol", "openInterest", "timestamp"]].copy()

            if existing.empty and df.empty:
                result["status"] = "no_data"
                self.log_ingestion(result)
                return result
            elif existing.empty:
                combined = df
            else:
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

            ok, issues = validate_df(combined, "open_interest")
            result["issues"] = issues
            result["final"] = len(combined)

            write_partitions(combined, "open_interest", symbol)
            result["status"] = "ok" if ok else "issues"

            if result["fetched"] > 0:
                log_ok(f"OI {symbol}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"OI {symbol}: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"OI {symbol} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── ORDERBOOK ─────────────────────────────────────────────────────────

    def fetch_orderbook(self, symbol: str, depth: int = 50) -> Dict[str, Any]:
        result = {
            "symbol": symbol, "tf": "snapshot", "type": "orderbook",
            "fetched": 0, "status": "pending", "issues": [],
        }
        try:
            rows, ts = self.client.fetch_orderbook(symbol, depth)
            if not rows:
                result["status"] = "no_data"
                self.log_ingestion(result)
                return result

            df = pd.DataFrame(rows, columns=["timestamp", "symbol", "side", "level", "price", "size"])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
            result["fetched"] = len(df)

            ok, issues = validate_df(df, "orderbook")
            result["issues"] = issues
            write_partitions(df, "orderbook", symbol)
            result["status"] = "ok" if ok else "issues"
            log_ok(f"Orderbook {symbol}: snapshot {len(df)} levels")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Orderbook {symbol} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── ENRICH ────────────────────────────────────────────────────────────

    def enrich(self, symbol: str, tfs: List[str]) -> Dict[str, Any]:
        result = {"symbol": symbol, "tf": ",".join(tfs), "type": "enrich",
                  "status": "ok", "issues": []}
        try:
            if _enrich_run_symbol:
                for tf in tfs:
                    _enrich_run_symbol(symbol, tfs=[tf], dry_run=False)
                    log_ok(f"Enriched {symbol}/{tf}")
            else:
                result["status"] = "skipped"
                result["issues"].append("enrichment module not available")
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Enrich {symbol} failed: {e}")
        self.log_ingestion(result)
        return result

    # ── LS RATIO ──────────────────────────────────────────────────────────

    def fetch_ls_ratio(self, symbol: str) -> Dict[str, Any]:
        result = fetch_ls_ratio(self.client, symbol, period="1h")
        self.log_ingestion(result)
        return result

    # ── LIQUIDATIONS ──────────────────────────────────────────────────────

    def fetch_liquidations(self, symbol: str) -> Dict[str, Any]:
        result = fetch_liquidations(self.client, symbol, lookback_hours=168)
        self.log_ingestion(result)
        return result


# ── TIME ALIGNMENT ────────────────────────────────────────────────────────

def seconds_until_next_boundary(interval_min: int, offset_sec: int) -> float:
    now = datetime.now(timezone.utc)
    sec_in_day = now.hour * 3600 + now.minute * 60 + now.second
    interval_sec = interval_min * 60
    next_boundary = ((sec_in_day // interval_sec) + 1) * interval_sec
    wait = next_boundary - sec_in_day + offset_sec
    return float(wait)


# ── SEQUENTIAL PASS RUNNERS ───────────────────────────────────────────────

def run_full_pass(scheduler: UnifiedScheduler, symbols: List[str], tfs: List[str]) -> None:
    log_info("STARTUP — full sequential pass beginning...")
    for symbol in symbols:
        for tf in tfs:
            scheduler.fetch_ohlcv(symbol, tf)
        scheduler.fetch_funding(symbol)
        scheduler.fetch_oi(symbol)
        scheduler.fetch_orderbook(symbol)
        scheduler.fetch_ls_ratio(symbol)
        scheduler.fetch_liquidations(symbol)
        scheduler.enrich(symbol, [t for t in tfs if t != "1d"])
    log_ok("STARTUP — full pass complete.")


def run_interval_pass(scheduler: UnifiedScheduler, symbols: List[str],
                      interval_name: str, tfs: List[str]) -> None:
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
            scheduler.enrich(symbol, [t for t in tfs if t != "1d"])
    elif interval_name == "ls_ratio":
        for symbol in symbols:
            scheduler.fetch_ls_ratio(symbol)
    elif interval_name == "liquidations":
        for symbol in symbols:
            scheduler.fetch_liquidations(symbol)


# ── HEALTH ────────────────────────────────────────────────────────────────

def health_report(scheduler: UnifiedScheduler, jobs_pending: int):
    stats = scheduler.client.get_stats()
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

def run_scheduler(scheduler: UnifiedScheduler, symbols: List[str], tfs: List[str],
                  health_interval_sec: int = 300) -> None:
    log_ok("V3 Unified Scheduler running. Press Ctrl+C to stop.")

    next_run: dict[str, float] = {}
    for name, interval_min, offset in INTERVAL_CONFIG:
        wait = seconds_until_next_boundary(interval_min, offset)
        next_run[name] = time.monotonic() + wait
        log_info(f"  Next {name}: in {wait:.0f}s")

    last_health = time.monotonic()

    while True:
        try:
            now = time.monotonic()

            for name, interval_min, offset in INTERVAL_CONFIG:
                if now >= next_run[name]:
                    log_info(f"Triggering interval: {name}")
                    run_interval_pass(scheduler, symbols, name, tfs)
                    next_run[name] = now + (interval_min * 60)
                    log_info(f"  Next {name}: in {interval_min * 60}s")

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

    stats = scheduler.client.get_stats()
    log_info(f"Shutdown complete. Total requests: {stats['requests']} | Errors: {stats['errors']}")


# ── ENTRY POINT ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VLTHR V3 Unified Data Scheduler")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help=f"Symbols (default: {SYMBOLS})")
    parser.add_argument("--tfs", nargs="+", default=OHLCV_TFS, help=f"Timeframes (default: {OHLCV_TFS})")
    parser.add_argument("--cooldown", type=float, default=2.0, help="Seconds between API requests (default: 2.0)")
    parser.add_argument("--testnet", action="store_true", help="Use Bybit testnet")
    parser.add_argument("--health-interval", type=int, default=300, help="Health report interval (default: 300s)")
    parser.add_argument("--run-once", action="store_true", help="Run all jobs once immediately, then exit")
    args = parser.parse_args()

    scheduler = UnifiedScheduler(symbols=args.symbols, cooldown=args.cooldown, testnet=args.testnet)

    print("=" * 70)
    print("  VLTHR V3 UNIFIED DATA SCHEDULER")
    print(f"  Symbols:    {', '.join(args.symbols)}")
    print(f"  Timeframes: {', '.join(args.tfs)}")
    print(f"  Cooldown:   {args.cooldown}s between requests")
    print(f"  API:        {'testnet' if args.testnet else 'mainnet'}")
    print(f"  Data types: OHLCV, Funding, OI, Orderbook, LS Ratio, Liquidations, Enrich")
    print("=" * 70)

    if args.run_once:
        run_full_pass(scheduler, args.symbols, args.tfs)
        return

    # Startup catch-up
    run_full_pass(scheduler, args.symbols, args.tfs)

    def _sigterm_handler(signum, frame):
        log_warn(f"Received signal {signum}. Shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    run_scheduler(scheduler, args.symbols, args.tfs, health_interval_sec=args.health_interval)


if __name__ == "__main__":
    main()
