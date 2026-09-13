"""
VLTHR Data Scheduler Core — Shared Throttle + Incremental Fetch
=================================================================

Imports existing read/write utilities from bybit_ingest_core and
bybit_market_data_core. Adds a globally-throttled unified client for
scheduled incremental data fetching.

All API requests go through a single 2-second cooldown gate.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# Auto-load .env so PROXY_URL is available before any client is created
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
except Exception:
    pass

# ── IMPORT EXISTING CORE LOGIC ────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bybit_ingest_core import (
    BybitClient as _BybitKlineClient,
    INTERVAL_MAP,
    INTERVAL_MINUTES,
    _candles_to_df,
    _df_schema,
    read_existing as read_existing_ohlcv,
    write_partitions as write_ohlcv_partitions,
    validate_ohlcv,
    backfill_gaps,
    detect_gaps,
    DATA_ROOT,
    DEFAULT_START,
    MAX_PER_PAGE,
    MAX_RETRIES,
    BACKOFF_BASE,
    RATE_LIMIT_SLEEP,
)

from bybit_market_data_core import (
    Client as _BybitMarketClient,
    read_existing_funding,
    read_existing_oi,
    read_existing_orderbook,
    write_funding,
    write_oi,
    write_orderbook,
    paginate_history,
    SYMBOLS,
    CAT,
    PAGE_LIMIT,
)

from bybit_enrich_core import run_symbol as _enrich_run_symbol

try:
    from sentiment_fetchers import SentimentFetchers
except ImportError:
    SentimentFetchers = None

# ── CONFIGURATION ─────────────────────────────────────────────────────────

REQUEST_COOLDOWN = 2.0  # seconds between ANY API request
OVERLAP_BARS = 2        # overlap when fetching OHLCV (for safety)

# Schedule intervals (minutes)
SCHEDULE = {
    "ohlcv": {
        "4h":  240,
        "1h":  60,
        "30m": 30,
        "15m": 15,
        "5m":  5,
    },
    "funding": 60,
    "oi": 60,
    "orderbook": 5,
    "enrich": 15,
    # Sentiment data (no API keys, free sources)
    "fear_greed": 1440,       # daily (alternative.me)
    "btc_dominance": 5,       # every 5 min (CoinGecko keyless)
    "bybit_ls_ratio": 60,    # every 1 hour (Bybit V5 public)
    "binance_ls_ratio": 60,  # every 1 hour (Binance public)
    "binance_taker_vol": 60, # every 1 hour (Binance public)
}

# ── LOGGING ───────────────────────────────────────────────────────────────

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
    print(f"{C.WARN}[WARN]{C.END} [{ts}] {msg}", file=sys.stderr, flush=True)


def log_err(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"{C.ERR}[ERR]{C.END} [{ts}] {msg}", file=sys.stderr, flush=True)


# ── SHARED THROTTLE ────────────────────────────────────────────────────────

# Cross-process lock file for throttle coordination
_THROTTLE_LOCK_PATH = os.path.join(os.environ.get("DATA_ROOT", "/tmp"), ".bybit_throttle.lock")

class GlobalThrottle:
    """Singleton gate: enforces N-second cooldown between ANY API request.
    Uses file-lock for cross-process coordination (data_scheduler + minute_scheduler)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, cooldown: float = REQUEST_COOLDOWN):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.cooldown = cooldown
                cls._instance._last = 0.0
                cls._instance._gate = threading.Lock()
                cls._instance._lock_path = _THROTTLE_LOCK_PATH
                cls._instance._lock_fd = None
                try:
                    cls._instance._lock_fd = open(cls._instance._lock_path, "w")
                except Exception:
                    pass  # Falls back to in-process-only throttle
            return cls._instance

    def _acquire_file_lock(self):
        if self._lock_fd is None:
            return False
        try:
            import fcntl
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            return True
        except (ImportError, OSError):
            return False  # Windows or no fcntl — in-process only

    def _release_file_lock(self):
        if self._lock_fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass

    def _read_shared_last(self) -> float:
        if self._lock_fd is None:
            return self._last
        try:
            self._lock_fd.seek(0)
            content = self._lock_fd.read().strip()
            return float(content) if content else 0.0
        except (ValueError, OSError):
            return self._last

    def _write_shared_last(self, ts: float):
        self._last = ts
        if self._lock_fd is None:
            return
        try:
            self._lock_fd.seek(0)
            self._lock_fd.truncate()
            self._lock_fd.write(f"{ts:.6f}")
            self._lock_fd.flush()
        except OSError:
            pass

    def wait(self):
        with self._gate:
            has_file = self._acquire_file_lock()
            try:
                if has_file:
                    shared_last = self._read_shared_last()
                else:
                    shared_last = self._last
                elapsed = time.monotonic() - shared_last
                if elapsed < self.cooldown:
                    time.sleep(self.cooldown - elapsed)
                now = time.monotonic()
                if has_file:
                    self._write_shared_last(now)
                else:
                    self._last = now
            finally:
                self._release_file_lock()


# ── THROTTLED CLIENTS ─────────────────────────────────────────────────────

class ThrottledKlineClient(_BybitKlineClient):
    """Bybit kline client with external global throttle (delay=0 internally)."""

    def __init__(self, base_url: str, throttle: GlobalThrottle):
        super().__init__(base_url=base_url, delay=0.0)
        self._throttle = throttle

    def _throttle_fn(self):
        self._throttle.wait()

    def _request(self, params: dict) -> dict:
        # Override to inject global throttle before each request attempt
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle_fn()
            try:
                resp = self._session.get(
                    self.base_url, params=params, timeout=60
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("retCode") == 10006:
                    reset_ts = resp.headers.get("X-Bapi-Limit-Reset-Timestamp")
                    if reset_ts:
                        wait = max(1.0, (int(reset_ts) - time.time() * 1000) / 1000 + 1)
                    else:
                        wait = RATE_LIMIT_SLEEP * attempt
                    log_warn(f"Rate limit (attempt {attempt}), sleeping {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
                return data
            except (ConnectionError, Exception) as e:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {e}")
                wait = BACKOFF_BASE ** attempt
                log_warn(f"Error (attempt {attempt}): {e}, retrying in {wait:.1f}s...")
                time.sleep(wait)
        raise RuntimeError("Exhausted retries")


class ThrottledMarketClient(_BybitMarketClient):
    """Bybit market data client with external global throttle (delay=0 internally)."""

    def __init__(self, throttle: GlobalThrottle, testnet: bool = False):
        super().__init__(delay=0.0, testnet=testnet)
        self._throttle = throttle

    def _throttle_fn(self):
        self._throttle.wait()

    def _req(self, endpoint, params):
        url = f"{self.base}/{endpoint}"
        for a in range(1, MAX_RETRIES + 1):
            self._throttle_fn()
            try:
                r = self._session.get(url, params=params, timeout=60)
                r.raise_for_status()
                d = r.json()
                if d.get("retCode") == 10006:
                    ts = r.headers.get("X-Bapi-Limit-Reset-Timestamp")
                    wait = max(1.0, (int(ts) - time.time() * 1000) / 1000 + 1) if ts else RATE_LIMIT_SLEEP * a
                    log_warn(f"Rate limit (attempt {a}), sleep {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                if d.get("retCode") != 0:
                    raise RuntimeError(f"Bybit error: {d.get('retMsg')}")
                return d
            except (ConnectionError, Exception) as e:
                if a == MAX_RETRIES:
                    raise RuntimeError(f"Failed: {e}")
                wait = BACKOFF_BASE ** a
                log_warn(f"Error (attempt {a}): {e}, retry {wait:.1f}s...")
                time.sleep(wait)
        raise RuntimeError("Exhausted retries")


# ── INCREMENTAL FETCH JOBS ────────────────────────────────────────────────

class DataScheduler:
    """Manages all scheduled data fetch jobs with a shared throttle."""

    def __init__(self, symbols: List[str] = None, testnet: bool = False):
        self.symbols = symbols or SYMBOLS.copy()
        self.throttle = GlobalThrottle(REQUEST_COOLDOWN)
        self.kline_client = ThrottledKlineClient(
            base_url="https://api.bybit.com/v5/market/kline",
            throttle=self.throttle,
        )
        self.market_client = ThrottledMarketClient(
            throttle=self.throttle,
            testnet=testnet,
        )
        self._stats = {"requests": 0, "errors": 0, "start_time": datetime.now(timezone.utc)}
        self._lock = threading.Lock()
        self._db_url = os.environ.get("DB_URL", "")
        self._db_available = False
        if self._db_url and psycopg2:
            self._ensure_ingestion_log_table()
        self._sentiment = SentimentFetchers(symbols=self.symbols) if SentimentFetchers else None

        proxy = os.environ.get("PROXY_URL", "")
        if proxy:
            log_ok(f"Proxy active: {proxy.split('@')[-1] if '@' in proxy else proxy}")

    def _ensure_ingestion_log_table(self):
        """Create ingestion_log table if it doesn't exist."""
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
                pass  # TimescaleDB not available — plain table is fine
            conn.commit()
            cur.close()
            conn.close()
            self._db_available = True
            log_ok("ingestion_log table ready")
        except Exception as e:
            log_warn(f"ingestion_log table creation failed (DB logging disabled): {e}")
            self._db_available = False

    def log_ingestion(self, result: Dict[str, Any]):
        """Log a fetch result to the ingestion_log table."""
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

    def _inc_requests(self):
        with self._lock:
            self._stats["requests"] += 1

    def _inc_errors(self):
        with self._lock:
            self._stats["errors"] += 1

    # ── OHLCV ─────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, tf: str) -> Dict[str, Any]:
        """Incremental OHLCV fetch for one symbol/timeframe."""
        result = {
            "symbol": symbol, "tf": tf, "type": "ohlcv",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            interval = INTERVAL_MAP[tf]
            interval_min = INTERVAL_MINUTES[tf]

            existing = read_existing_ohlcv(symbol, tf)
            if not existing.empty:
                result["existing"] = len(existing)
                last_ts = existing["timestamp"].max()
                since = last_ts - timedelta(minutes=interval_min * OVERLAP_BARS)
            else:
                since = DEFAULT_START

            since_ms = int(since.timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            candles = self.kline_client.fetch_klines(symbol, interval, since_ms, until_ms, MAX_PER_PAGE)
            self._inc_requests()

            if not candles:
                result["status"] = "no_new_data"
                self.log_ingestion(result)
                return result

            new_df = _candles_to_df(candles)
            result["fetched"] = len(new_df)

            existing = _df_schema(existing)
            new_df = _df_schema(new_df)

            if existing.empty:
                combined = new_df.copy()
            else:
                combined = pd.concat([existing, new_df], ignore_index=True)

            combined = (
                combined.sort_values("timestamp")
                .drop_duplicates(subset=["timestamp"], keep="last")
                .reset_index(drop=True)
            )

            combined = backfill_gaps(self.kline_client, symbol, interval, combined, interval_min)
            result["gaps_found"] = len(detect_gaps(combined, interval_min))
            combined = _df_schema(combined)

            ok, issues = validate_ohlcv(combined, symbol, tf)
            result["issues"] = issues
            result["final"] = len(combined)

            if not ok:
                log_warn(f"OHLCV {symbol}/{tf}: validation FAILED — quarantining {len(issues)} issue(s): {'; '.join(issues)}")
                result["status"] = "quarantined"
                self.log_ingestion(result)
                return result

            write_ohlcv_partitions(symbol, tf, combined)
            result["status"] = "ok"

            if result["fetched"] > 0 or result.get("gaps_found", 0) > 0:
                log_ok(f"OHLCV {symbol}/{tf}: +{result['fetched']} rows (total {result['final']}, gaps={result.get('gaps_found', 0)})")
            else:
                log_info(f"OHLCV {symbol}/{tf}: up to date ({result['final']} rows)")

        except Exception as e:
            self._inc_errors()
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"OHLCV {symbol}/{tf} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── FUNDING RATE ──────────────────────────────────────────────────────

    def fetch_funding(self, symbol: str) -> Dict[str, Any]:
        """Incremental funding rate fetch."""
        result = {
            "symbol": symbol, "tf": "8h", "type": "funding_rate",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            existing = read_existing_funding(symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                since = existing["fundingTime"].max() - timedelta(hours=1)
            else:
                since = DEFAULT_START

            since_ms = int(since.timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            df = paginate_history(
                self.market_client, symbol,
                lambda s, end_ms, limit=PAGE_LIMIT: self.market_client.fetch_funding(s, end_ms, limit),
                since_ms, until_ms,
            )
            self._inc_requests()

            if not df.empty:
                result["fetched"] = len(df)
                df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
                df["fundingTime"] = pd.to_datetime(df["fundingRateTimestamp"].astype(np.int64), unit="ms", utc=True)
                df = df[["symbol", "fundingRate", "fundingTime"]].copy()

            if existing.empty and df.empty:
                combined = existing.copy()
            elif existing.empty:
                combined = df.copy()
            else:
                combined = pd.concat([existing, df], ignore_index=True)

            if combined.empty:
                result["status"] = "no_data"
                self.log_ingestion(result)
                return result

            combined = combined.sort_values("fundingTime").drop_duplicates("fundingTime").reset_index(drop=True)

            issues = []
            if combined["fundingRate"].isna().sum():
                issues.append("null fundingRate")
            if ((combined["fundingRate"] < -0.1) | (combined["fundingRate"] > 0.1)).any():
                issues.append("suspicious fundingRate")
            result["issues"] = issues
            result["final"] = len(combined)

            write_funding(symbol, combined)
            result["status"] = "ok" if not issues else "issues"

            if result["fetched"] > 0:
                log_ok(f"Funding {symbol}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"Funding {symbol}: up to date ({result['final']} rows)")

        except Exception as e:
            self._inc_errors()
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Funding {symbol} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── OPEN INTEREST ─────────────────────────────────────────────────────

    def fetch_oi(self, symbol: str, interval: str = "1h") -> Dict[str, Any]:
        """Incremental open interest fetch."""
        result = {
            "symbol": symbol, "tf": interval, "type": f"open_interest_{interval}",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            existing = read_existing_oi(symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                since = existing["timestamp"].max() - timedelta(hours=1)
            else:
                since = DEFAULT_START

            since_ms = int(since.timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            df = paginate_history(
                self.market_client, symbol,
                lambda s, end_ms, limit=PAGE_LIMIT: self.market_client.fetch_oi(s, interval, end_ms, limit),
                since_ms, until_ms,
            )
            self._inc_requests()

            if not df.empty:
                result["fetched"] = len(df)
                df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce")
                df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
                df["symbol"] = symbol
                df = df[["symbol", "openInterest", "timestamp"]].copy()

            if existing.empty and df.empty:
                combined = existing.copy()
            elif existing.empty:
                combined = df.copy()
            else:
                combined = pd.concat([existing, df], ignore_index=True)

            if combined.empty:
                result["status"] = "no_data"
                self.log_ingestion(result)
                return result

            combined = combined.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

            issues = []
            if combined["openInterest"].isna().sum():
                issues.append("null openInterest")
            if (combined["openInterest"] <= 0).any():
                issues.append("non-positive openInterest")
            result["issues"] = issues
            result["final"] = len(combined)

            write_oi(symbol, combined)
            result["status"] = "ok" if not issues else "issues"

            if result["fetched"] > 0:
                log_ok(f"OI {symbol}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"OI {symbol}: up to date ({result['final']} rows)")

        except Exception as e:
            self._inc_errors()
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"OI {symbol} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── ORDERBOOK ───────────────────────────────────────────────────────────

    def fetch_orderbook(self, symbol: str, depth: int = 50) -> Dict[str, Any]:
        """Orderbook snapshot fetch (always fresh)."""
        result = {
            "symbol": symbol, "tf": "snapshot", "type": "orderbook",
            "fetched": 0, "status": "pending", "issues": [],
        }
        try:
            rows, ts = self.market_client.fetch_orderbook(symbol, depth)
            self._inc_requests()

            if not rows:
                result["status"] = "no_data"
                self.log_ingestion(result)
                return result

            df = pd.DataFrame(rows, columns=["timestamp", "symbol", "side", "level", "price", "size"])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
            result["fetched"] = len(df)

            issues = []
            if df["price"].isna().sum():
                issues.append("null prices")
            if df["size"].isna().sum():
                issues.append("null sizes")
            best_bid = df[df["side"] == "bid"]["price"].max() if len(df[df["side"] == "bid"]) else 0
            best_ask = df[df["side"] == "ask"]["price"].min() if len(df[df["side"] == "ask"]) else float("inf")
            if best_bid >= best_ask:
                issues.append(f"spread inverted: bid {best_bid} >= ask {best_ask}")
            result["issues"] = issues

            write_orderbook(symbol, df)
            result["status"] = "ok" if not issues else "issues"
            log_ok(f"Orderbook {symbol}: snapshot {len(df)} levels")

        except Exception as e:
            self._inc_errors()
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Orderbook {symbol} failed: {e}")

        self.log_ingestion(result)
        return result

    # ── ENRICH ────────────────────────────────────────────────────────────

    def enrich(self, symbol: str, tfs: List[str]) -> Dict[str, Any]:
        """Run enrichment for a symbol across specified timeframes."""
        result = {"symbol": symbol, "tf": ",".join(tfs), "type": "enrich", "tfs": tfs, "status": "ok", "issues": []}
        try:
            for tf in tfs:
                _enrich_run_symbol(symbol, tfs=[tf], dry_run=False)
                log_ok(f"Enriched {symbol}/{tf}")
        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Enrich {symbol} failed: {e}")
        self.log_ingestion(result)
        return result

    # ── SENTIMENT DATA ────────────────────────────────────────────────────

    def fetch_fear_greed(self) -> Dict[str, Any]:
        """Fetch Fear & Greed Index from alternative.me (daily, no auth)."""
        if not self._sentiment:
            return {"status": "disabled", "issues": ["sentiment_fetchers not available"]}
        result = self._sentiment.fetch_fear_greed()
        self._inc_requests()
        if result["status"] == "error":
            self._inc_errors()
        self.log_ingestion(result)
        return result

    def fetch_btc_dominance(self) -> Dict[str, Any]:
        """Fetch BTC dominance from CoinGecko keyless API (no auth)."""
        if not self._sentiment:
            return {"status": "disabled", "issues": ["sentiment_fetchers not available"]}
        result = self._sentiment.fetch_btc_dominance()
        self._inc_requests()
        if result["status"] == "error":
            self._inc_errors()
        self.log_ingestion(result)
        return result

    def fetch_bybit_ls_ratio(self, symbol: str, period: str = "1h") -> Dict[str, Any]:
        """Fetch Bybit Long/Short Ratio (public, no auth)."""
        if not self._sentiment:
            return {"status": "disabled", "issues": ["sentiment_fetchers not available"]}
        result = self._sentiment.fetch_bybit_ls_ratio(symbol, period=period)
        self._inc_requests()
        if result["status"] == "error":
            self._inc_errors()
        self.log_ingestion(result)
        return result

    def fetch_binance_ls_ratio(self, symbol: str, period: str = "1h") -> Dict[str, Any]:
        """Fetch Binance Long/Short Ratio (public, no auth)."""
        if not self._sentiment:
            return {"status": "disabled", "issues": ["sentiment_fetchers not available"]}
        result = self._sentiment.fetch_binance_ls_ratio(symbol, period=period)
        self._inc_requests()
        if result["status"] == "error":
            self._inc_errors()
        self.log_ingestion(result)
        return result

    def fetch_binance_taker_volume(self, symbol: str, period: str = "1h") -> Dict[str, Any]:
        """Fetch Binance Taker Buy/Sell Volume (public, no auth)."""
        if not self._sentiment:
            return {"status": "disabled", "issues": ["sentiment_fetchers not available"]}
        result = self._sentiment.fetch_binance_taker_volume(symbol, period=period)
        self._inc_requests()
        if result["status"] == "error":
            self._inc_errors()
        self.log_ingestion(result)
        return result

    # ── STATS ─────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            stats = self._stats.copy()
        stats["uptime_sec"] = (datetime.now(timezone.utc) - stats["start_time"]).total_seconds()
        stats["symbols"] = self.symbols
        return stats
