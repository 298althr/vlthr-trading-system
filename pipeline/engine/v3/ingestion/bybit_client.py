"""
V3 Unified Bybit Client
========================
Single client for all Bybit V5 API endpoints with:
- Global throttle (configurable cooldown)
- Exponential backoff retry
- Proxy support (PROXY_URL env var)
- Rate limit detection (retCode 10006)
- Connection pooling with fallback

Replaces ThrottledKlineClient + ThrottledMarketClient from scheduler_core.
"""
from __future__ import annotations

import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import requests
import pandas as pd
import numpy as np

# Load .env — parents[5] from engine/v3/ingestion/ = repo root (VLTHR/)
_env = Path(__file__).resolve().parents[5] / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


BYBIT_BASE = "https://api.bybit.com/v5/market"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
CAT = "linear"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]

DEFAULT_COOLDOWN = 2.0
MAX_RETRIES = 6
BACKOFF_BASE = 2.0
RATE_LIMIT_SLEEP = 30.0
PAGE_LIMIT = 200
REQUEST_TIMEOUT = 60

# Interval mapping for kline API
INTERVAL_MAP = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "4h": "240", "1d": "D",
}
INTERVAL_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}


class GlobalThrottle:
    """Singleton gate: enforces N-second cooldown between ANY API request."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, cooldown: float = DEFAULT_COOLDOWN):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.cooldown = cooldown
                cls._instance._last = 0.0
                cls._instance._gate = threading.Lock()
            return cls._instance

    def wait(self):
        with self._gate:
            elapsed = time.monotonic() - self._last
            if elapsed < self.cooldown:
                sleep_for = self.cooldown - elapsed
                time.sleep(sleep_for)
            self._last = time.monotonic()


class BybitClient:
    """Unified Bybit V5 API client with throttle, retry, and proxy support."""

    def __init__(self, cooldown: float = DEFAULT_COOLDOWN, testnet: bool = False):
        self.base = "https://api-testnet.bybit.com/v5/market" if testnet else BYBIT_BASE
        self.kline_url = BYBIT_KLINE_URL if not testnet else "https://api-testnet.bybit.com/v5/market/kline"
        self.throttle = GlobalThrottle(cooldown)
        self.testnet = testnet
        self._stats = {"requests": 0, "errors": 0, "start_time": datetime.now(timezone.utc)}
        self._lock = threading.Lock()

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "VLTHR-V3-Engine/1.0",
            "Connection": "close",
            "Accept": "application/json",
        })
        proxy_url = os.environ.get("PROXY_URL", "")
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}
            host = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
            print(f"[BybitClient] Proxy active: {host}")
        else:
            print(f"[BybitClient] Direct connection (no proxy)")

    def _request(self, url: str, params: dict) -> dict:
        """Make a throttled, retried GET request to Bybit API."""
        for attempt in range(1, MAX_RETRIES + 1):
            self.throttle.wait()
            try:
                resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()

                if data.get("retCode") == 10006:
                    reset_ts = resp.headers.get("X-Bapi-Limit-Reset-Timestamp")
                    if reset_ts:
                        wait = max(1.0, (int(reset_ts) - time.time() * 1000) / 1000 + 1)
                    else:
                        wait = RATE_LIMIT_SLEEP * attempt
                    print(f"[BybitClient] Rate limit (attempt {attempt}), sleeping {wait:.1f}s...")
                    time.sleep(wait)
                    continue

                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit API error: {data.get('retMsg')} (code {data.get('retCode')})")

                with self._lock:
                    self._stats["requests"] += 1
                return data

            except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
                with self._lock:
                    self._stats["errors"] += 1
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Connection failed after {MAX_RETRIES} attempts: {e}")
                wait = BACKOFF_BASE ** attempt
                print(f"[BybitClient] Connection reset (attempt {attempt}), retry in {wait:.1f}s...")
                time.sleep(wait)

            except requests.exceptions.RequestException as e:
                with self._lock:
                    self._stats["errors"] += 1
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Request failed after {MAX_RETRIES} attempts: {e}")
                wait = BACKOFF_BASE ** attempt
                print(f"[BybitClient] Request error (attempt {attempt}): {e}, retry in {wait:.1f}s...")
                time.sleep(wait)

        raise RuntimeError("Exhausted retries")

    # ── KLINE / OHLCV ──────────────────────────────────────────────────────

    def fetch_klines(self, symbol: str, interval: str, since_ms: int, until_ms: int,
                     limit: int = PAGE_LIMIT) -> List[dict]:
        """Fetch kline candles. Returns list of raw API rows."""
        params = {
            "category": CAT, "symbol": symbol, "interval": interval,
            "start": since_ms, "end": until_ms, "limit": limit,
        }
        data = self._request(self.kline_url, params)
        rows = data.get("result", {}).get("list", [])
        # Bybit returns newest-first; reverse to oldest-first
        return list(reversed(rows))

    # ── FUNDING RATE ───────────────────────────────────────────────────────

    def fetch_funding(self, symbol: str, end_ms: int, limit: int = PAGE_LIMIT) -> List[dict]:
        """Fetch funding rate history."""
        params = {"category": CAT, "symbol": symbol, "limit": limit, "endTime": end_ms}
        data = self._request(f"{self.base}/funding/history", params)
        return data.get("result", {}).get("list", [])

    # ── OPEN INTEREST ──────────────────────────────────────────────────────

    def fetch_open_interest(self, symbol: str, interval: str, end_ms: int,
                            limit: int = PAGE_LIMIT) -> List[dict]:
        """Fetch open interest history."""
        params = {"category": CAT, "symbol": symbol, "intervalTime": interval,
                  "limit": limit, "endTime": end_ms}
        data = self._request(f"{self.base}/open-interest", params)
        return data.get("result", {}).get("list", [])

    # ── ORDERBOOK ──────────────────────────────────────────────────────────

    def fetch_orderbook(self, symbol: str, depth: int = 50) -> Tuple[List[list], int]:
        """Fetch orderbook snapshot. Returns (rows, timestamp_ms)."""
        params = {"category": CAT, "symbol": symbol, "limit": depth}
        data = self._request(f"{self.base}/orderbook", params)
        res = data.get("result", {})
        ts = int(res.get("ts", 0))
        bids = [[ts, symbol, "bid", i + 1, float(b[0]), float(b[1])]
                for i, b in enumerate(res.get("b", []))]
        asks = [[ts, symbol, "ask", i + 1, float(a[0]), float(a[1])]
                for i, a in enumerate(res.get("a", []))]
        return bids + asks, ts

    # ── LONG/SHORT RATIO ───────────────────────────────────────────────────

    def fetch_ls_ratio(self, symbol: str, period: str = "1h",
                       limit: int = PAGE_LIMIT) -> List[dict]:
        """
        Fetch long/short ratio from /v5/market/account-ratio.
        period: 5m, 15m, 30m, 1h, 4h, 1d
        Returns list of dicts with keys: symbol, buyRatio, sellRatio, timestamp
        """
        params = {"category": CAT, "symbol": symbol, "period": period, "limit": limit}
        data = self._request(f"{self.base}/account-ratio", params)
        return data.get("result", {}).get("list", [])

    # ── LIQUIDATIONS ───────────────────────────────────────────────────────
    # Note: Bybit V5 has no REST endpoint for liquidations.
    # Use the WebSocket allLiquidation stream instead.
    # See liquidations.py::LiquidationCollector for real-time capture.

    # ── STATS ──────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            stats = self._stats.copy()
        stats["uptime_sec"] = (datetime.now(timezone.utc) - stats["start_time"]).total_seconds()
        return stats


def paginate_history(client: BybitClient, symbol: str,
                     fetch_fn, since_ms: int, until_ms: int, **kwargs) -> pd.DataFrame:
    """Generic backward pagination for time-series endpoints."""
    all_rows: List[dict] = []
    current_end = until_ms
    while current_end > since_ms:
        rows = fetch_fn(symbol, end_ms=current_end, **kwargs)
        if not rows:
            break
        all_rows.extend(rows)
        oldest = int(rows[-1].get("fundingRateTimestamp", rows[-1].get("timestamp", 0)))
        if oldest <= since_ms or oldest >= current_end:
            break
        current_end = oldest - 1
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)
