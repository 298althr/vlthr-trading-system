"""
Bybit V5 Ingestion Core Module
==============================
Shared logic used by per-symbol fetch scripts.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

import numpy as np
import pandas as pd
import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────

BYBIT_BASE_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_CATEGORY = "linear"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]

TIMEFRAMES = ["4h", "1h", "30m", "15m", "5m"]

INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D",
}

INTERVAL_MINUTES = {
    "4h": 240, "1h": 60, "30m": 30, "15m": 15, "5m": 5, "1m": 1, "1d": 1440,
}

DATA_ROOT = Path("data/bybit")
DEFAULT_START = datetime(2023, 1, 1, tzinfo=timezone.utc)
REQUEST_DELAY = 1.0
MAX_PER_PAGE = 1000
MAX_RETRIES = 6
BACKOFF_BASE = 2.0
RATE_LIMIT_SLEEP = 30.0
GAP_THRESHOLD_MULT = 1.5

# ── LOGGING ─────────────────────────────────────────────────────────────────

class Colors:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[94m"
    END = "\033[0m"

def log_ok(msg: str):
    print(f"{Colors.OK}[OK]{Colors.END} {msg}")

def log_info(msg: str):
    print(f"{Colors.INFO}[INFO]{Colors.END} {msg}")

def log_warn(msg: str):
    print(f"{Colors.WARN}[WARN]{Colors.END} {msg}", file=sys.stderr)

def log_err(msg: str):
    print(f"{Colors.ERR}[ERR]{Colors.END} {msg}", file=sys.stderr)

# ── BYBIT CLIENT ────────────────────────────────────────────────────────────

class BybitClient:
    def __init__(self, base_url: str = BYBIT_BASE_URL, delay: float = REQUEST_DELAY):
        self.base_url = base_url
        self.delay = delay
        self._last_request_at = 0.0
        # Session with connection pooling disabled — fixes "Connection reset" in restricted networks
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "VLTHR-Engine/1.0 (Data Ingestion)",
            "Connection": "close",
            "Accept": "application/json",
        })
        # Proxy support — read from env (e.g. http://user:pass@host:port)
        proxy_url = os.environ.get("PROXY_URL", "")
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_at = time.monotonic()

    def _request(self, params: dict) -> dict:
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
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
                    log_warn(f"Rate limit hit (attempt {attempt}), sleeping {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
                return data
            except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Connection failed after {MAX_RETRIES} attempts: {e}")
                wait = BACKOFF_BASE ** attempt
                log_warn(f"Connection reset (attempt {attempt}), retrying in {wait:.1f}s...")
                time.sleep(wait)
            except requests.exceptions.RequestException as e:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Request failed after {MAX_RETRIES} attempts: {e}")
                wait = BACKOFF_BASE ** attempt
                log_warn(f"Request error (attempt {attempt}): {e}, retrying in {wait:.1f}s...")
                time.sleep(wait)
        raise RuntimeError(f"Exhausted {MAX_RETRIES} retries")

    def fetch_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = MAX_PER_PAGE) -> List[List]:
        params = {
            "category": BYBIT_CATEGORY,
            "symbol": symbol,
            "interval": interval,
            "start": str(start_ms),
            "end": str(end_ms),
            "limit": str(limit),
        }
        data = self._request(params)
        return data.get("result", {}).get("list", [])

    def fetch_range(self, symbol: str, interval: str, since_ms: int, until_ms: int) -> pd.DataFrame:
        all_candles: List[List] = []
        current_end = until_ms
        while current_end > since_ms:
            candles = self.fetch_klines(symbol, interval, since_ms, current_end)
            if not candles:
                break
            all_candles.extend(candles)
            oldest_ts = int(candles[-1][0])
            if oldest_ts <= since_ms:
                break
            current_end = oldest_ts - 1
        if not all_candles:
            return pd.DataFrame()
        return _candles_to_df(all_candles)

# ── DATAFRAME UTILITIES ───────────────────────────────────────────────────

def _candles_to_df(candles: List[List]) -> pd.DataFrame:
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

def _df_schema(df: pd.DataFrame) -> pd.DataFrame:
    expected = ["timestamp", "open", "high", "low", "close", "volume", "turnover"]
    for col in expected:
        if col not in df.columns:
            df[col] = np.nan
    return df[expected].copy()

# ── PARQUET I/O ────────────────────────────────────────────────────────────

def parquet_path(symbol: str, tf: str, year: int, month: int) -> Path:
    return DATA_ROOT / symbol / tf / f"{year:04d}" / f"{month:02d}.parquet"

def read_existing(symbol: str, tf: str) -> pd.DataFrame:
    tf_dir = DATA_ROOT / symbol / tf
    if not tf_dir.exists():
        return pd.DataFrame()
    dfs = []
    for year_dir in tf_dir.iterdir():
        if not year_dir.is_dir():
            continue
        for month_file in year_dir.iterdir():
            if month_file.suffix != ".parquet":
                continue
            try:
                df = pd.read_parquet(month_file)
                if not df.empty and "timestamp" in df.columns:
                    dfs.append(df)
            except Exception as e:
                log_warn(f"Could not read {month_file}: {e}")
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
    combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    return _df_schema(combined)

def write_partitions(symbol: str, tf: str, df: pd.DataFrame):
    if df.empty:
        return
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    for (year, month), group in df.groupby(["year", "month"]):
        out = group.drop(columns=["year", "month"]).copy()
        out_path = parquet_path(symbol, tf, year, month)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        out.to_parquet(tmp, compression="zstd", index=False)
        tmp.replace(out_path)
    tf_dir = DATA_ROOT / symbol / tf
    if tf_dir.exists():
        for year_dir in tf_dir.iterdir():
            if not year_dir.is_dir():
                continue
            for f in year_dir.iterdir():
                if f.suffix != ".parquet":
                    continue
                try:
                    y, m = int(year_dir.name), int(f.stem)
                    if not ((df["year"] == y) & (df["month"] == m)).any():
                        f.unlink()
                        log_info(f"Removed stale partition {f}")
                except ValueError:
                    continue

# ── GAP DETECTION & BACKFILL ───────────────────────────────────────────────

def detect_gaps(df: pd.DataFrame, interval_min: int) -> List[Tuple[datetime, datetime]]:
    if df.empty or len(df) < 2:
        return []
    df = df.sort_values("timestamp").copy()
    expected = timedelta(minutes=interval_min)
    gaps = []
    for i in range(1, len(df)):
        prev_ts = df.iloc[i - 1]["timestamp"]
        curr_ts = df.iloc[i]["timestamp"]
        actual = curr_ts - prev_ts
        if actual > expected * GAP_THRESHOLD_MULT:
            gaps.append((prev_ts + expected, curr_ts))
    return gaps

def backfill_gaps(client: BybitClient, symbol: str, interval: str, df: pd.DataFrame, interval_min: int) -> pd.DataFrame:
    gaps = detect_gaps(df, interval_min)
    if not gaps:
        return df
    log_warn(f"{symbol}/{interval}: detected {len(gaps)} gap(s)")
    for gap_start, gap_end in gaps:
        log_info(f"  backfilling gap: {gap_start.isoformat()} → {gap_end.isoformat()}")
        gap_df = client.fetch_range(symbol, interval, int(gap_start.timestamp() * 1000), int(gap_end.timestamp() * 1000))
        if not gap_df.empty:
            df = pd.concat([df, gap_df], ignore_index=True)
            df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
            log_ok(f"  backfilled {len(gap_df)} rows for gap")
        else:
            log_warn(f"  no data returned for gap")
    return df

# ── VALIDATION ─────────────────────────────────────────────────────────────

def validate_ohlcv(df: pd.DataFrame, symbol: str, tf: str) -> Tuple[bool, List[str]]:
    issues = []
    if df.empty:
        issues.append("DataFrame is empty")
        return False, issues
    for col in ("open", "high", "low", "close", "volume"):
        nulls = df[col].isna().sum()
        if nulls:
            issues.append(f"{nulls} null values in {col}")
    invalid_high = (df["high"] < df[["open", "close"]].max(axis=1)).sum()
    invalid_low = (df["low"] > df[["open", "close"]].min(axis=1)).sum()
    if invalid_high:
        issues.append(f"{invalid_high} rows where high < max(open,close)")
    if invalid_low:
        issues.append(f"{invalid_low} rows where low > min(open,close)")
    neg = (df[["open", "high", "low", "close", "volume"]] < 0).any(axis=1).sum()
    if neg:
        issues.append(f"{neg} rows with negative price or volume")
    dupes = df["timestamp"].duplicated().sum()
    if dupes:
        issues.append(f"{dupes} duplicate timestamps")
    last_ts = df["timestamp"].max()
    lag = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
    if lag > INTERVAL_MINUTES[tf] * 2:
        issues.append(f"Data is stale: last candle {lag:.0f} min ago")
    return len(issues) == 0, issues

# ── MAIN WORKFLOW ──────────────────────────────────────────────────────────

def ingest_symbol_tf(client: BybitClient, symbol: str, tf: str, dry_run: bool = False) -> Dict[str, Any]:
    interval = INTERVAL_MAP[tf]
    interval_min = INTERVAL_MINUTES[tf]
    result = {
        "symbol": symbol, "tf": tf,
        "existing_rows": 0, "fetched_rows": 0, "final_rows": 0,
        "gaps_found": 0, "status": "pending", "issues": [],
    }
    log_info(f"--- {symbol} | {tf} ---")

    existing = read_existing(symbol, tf)
    if not existing.empty:
        result["existing_rows"] = len(existing)
        last_ts = existing["timestamp"].max()
        log_info(f"Existing: {len(existing):,} rows, last {last_ts.isoformat()}")
        since = last_ts - timedelta(minutes=interval_min * 2)
    else:
        log_info("No existing data found")
        since = DEFAULT_START

    since_ms = int(since.timestamp() * 1000)
    until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    log_info(f"Fetching from {datetime.fromtimestamp(since_ms/1000, tz=timezone.utc).isoformat()}")
    new_df = client.fetch_range(symbol, interval, since_ms, until_ms)
    if not new_df.empty:
        result["fetched_rows"] = len(new_df)
        log_ok(f"Fetched {len(new_df):,} new rows")
    else:
        log_info("No new data to fetch")

    existing = _df_schema(existing)
    new_df = _df_schema(new_df)
    if existing.empty:
        combined = new_df.copy()
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)

    if combined.empty:
        result["status"] = "no_data"
        log_warn("No data available")
        return result

    combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    combined = backfill_gaps(client, symbol, interval, combined, interval_min)
    result["gaps_found"] = len(detect_gaps(combined, interval_min))
    combined = _df_schema(combined)

    ok, issues = validate_ohlcv(combined, symbol, tf)
    result["issues"] = issues
    if not ok:
        for issue in issues:
            log_warn(f"Validation: {issue}")
    else:
        log_ok("Validation passed")

    result["final_rows"] = len(combined)
    if not dry_run:
        write_partitions(symbol, tf, combined)
        log_ok(f"Written {len(combined):,} rows to parquet")
    else:
        log_info(f"Dry run: would write {len(combined):,} rows")

    result["status"] = "ok" if ok else "issues"
    return result


def run_symbol(symbol: str, dry_run: bool = False, delay: float = REQUEST_DELAY, start: str | None = None, testnet: bool = False):
    """Run ingestion for a single symbol across all timeframes."""
    global DEFAULT_START, BYBIT_BASE_URL
    if start:
        DEFAULT_START = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    base_url = "https://api-testnet.bybit.com/v5/market/kline" if testnet else BYBIT_BASE_URL

    client = BybitClient(base_url=base_url, delay=delay)

    print("=" * 70)
    print(f"  BYBIT V5 DATA INGESTION — {symbol}")
    print(f"  Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"  API:        {base_url}")
    print(f"  Delay:      {delay}s between requests")
    print(f"  Dry run:    {dry_run}")
    print("=" * 70)

    results = []
    total_start = time.monotonic()

    for tf in TIMEFRAMES:
        try:
            res = ingest_symbol_tf(client, symbol, tf, dry_run=dry_run)
            results.append(res)
            print()
        except Exception as e:
            log_err(f"{symbol}/{tf} failed: {e}")
            results.append({"symbol": symbol, "tf": tf, "status": "error", "error": str(e)})
            print()

    total_elapsed = time.monotonic() - total_start

    print("=" * 70)
    print(f"  SUMMARY — {symbol}")
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

    summary_path = DATA_ROOT / f"ingestion_summary_{symbol}.json"
    summary = {"run_at": datetime.now(timezone.utc).isoformat(), "elapsed_sec": total_elapsed, "results": results}
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log_info(f"Summary written to {summary_path}")
