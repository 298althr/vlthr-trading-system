"""
Sentiment Data Fetchers ($0 Budget, No API Keys)
=================================================
Fetches sentiment and alternative data from free public APIs.

Sources:
  - alternative.me: Crypto Fear & Greed Index (daily, no auth)
  - CoinGecko Keyless: BTC dominance, total market cap (no auth, ~10-30 calls/min)
  - Binance public: Long/Short ratio, taker buy/sell volume (no auth)
  - Bybit V5 public: Long/Short ratio (no auth, ported from V3 ls_ratio.py)

All data stored to data/bybit/{SYMBOL}/{type}/{YYYY}/{MM}.parquet
Global data (Fear/Greed, BTC dominance) stored to data/sentiment/{type}/{YYYY}/{MM}.parquet

Usage:
    from sentiment_fetchers import SentimentFetchers
    sf = SentimentFetchers()
    sf.fetch_fear_greed()
    sf.fetch_btc_dominance()
    sf.fetch_bybit_ls_ratio("BTCUSDT")
    sf.fetch_binance_ls_ratio("BTCUSDT")
    sf.fetch_binance_taker_volume("BTCUSDT")
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "data"))
SENTIMENT_ROOT = DATA_ROOT / "sentiment"
BYBIT_ROOT = DATA_ROOT / "bybit"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]

# API endpoints
FNG_URL = "https://api.alternative.me/fng/"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_LS_RATIO_ENDPOINT = "/futures/data/globalLongShortAccountRatio"
BINANCE_TAKER_VOL_ENDPOINT = "/futures/data/takerlongshortRatio"
BYBIT_LS_RATIO_URL = "https://api.bybit.com/v5/market/account-ratio"

# Retry config
MAX_RETRIES = 4
BACKOFF_BASE = 2.0
TIMEOUT = 30

# ── LOGGING ─────────────────────────────────────────────────────────────────

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


# ── PARQUET HELPERS ─────────────────────────────────────────────────────────

def _write_parquet(df: pd.DataFrame, root: Path, data_type: str, symbol: str = ""):
    """Write DataFrame to partitioned parquet: {root}/{data_type}/{symbol}/{YYYY}/{MM}.parquet"""
    if df.empty:
        return

    if "timestamp" in df.columns:
        df = df.copy()
        df["year"] = df["timestamp"].dt.year
        df["month"] = df["timestamp"].dt.month
    else:
        ts = datetime.now(timezone.utc)
        df = df.copy()
        df["year"] = ts.year
        df["month"] = ts.month

    for (year, month), group in df.groupby(["year", "month"]):
        out_dir = root / data_type
        if symbol:
            out_dir = out_dir / symbol
        out_dir = out_dir / str(year) / f"{month:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{data_type}_{year}{month:02d}.parquet"
        group = group.drop(columns=["year", "month"])
        group.to_parquet(out_file, index=False, engine="pyarrow")


def _read_existing(root: Path, data_type: str, symbol: str = "") -> pd.DataFrame:
    """Read existing parquet partitions for a data type."""
    base = root / data_type
    if symbol:
        base = base / symbol
    if not base.exists():
        return pd.DataFrame()

    files = sorted(base.rglob("*.parquet"))
    if not files:
        return pd.DataFrame()

    dfs = []
    for f in files:
        try:
            dfs.append(pd.read_parquet(f, engine="pyarrow"))
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


# ── HTTP HELPER ─────────────────────────────────────────────────────────────

def _http_get(url: str, params: dict = None, headers: dict = None) -> Optional[dict]:
    """HTTP GET with retry and exponential backoff. Returns JSON dict or None."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "VLTHR-Engine/1.0",
        "Accept": "application/json",
    })
    proxy_url = os.environ.get("PROXY_URL", "")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES:
                log_err(f"HTTP GET failed after {MAX_RETRIES} attempts: {url} — {e}")
                return None
            wait = BACKOFF_BASE ** attempt
            log_warn(f"HTTP GET retry {attempt}/{MAX_RETRIES} ({url}): {e}, waiting {wait:.1f}s")
            time.sleep(wait)
    return None


# ── FETCHER CLASS ───────────────────────────────────────────────────────────

class SentimentFetchers:
    """Fetch sentiment and alternative data from free public APIs."""

    def __init__(self, symbols: List[str] = None):
        self.symbols = symbols or SYMBOLS.copy()
        SENTIMENT_ROOT.mkdir(parents=True, exist_ok=True)

    # ── FEAR & GREED INDEX (alternative.me) ──────────────────────────────

    def fetch_fear_greed(self, limit: int = 0) -> Dict[str, Any]:
        """
        Fetch Crypto Fear & Greed Index from alternative.me.
        Daily data. No API key. No auth.
        Returns value 0-100 and classification text.
        """
        result = {
            "symbol": "GLOBAL", "tf": "1d", "type": "fear_greed",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            params = {"limit": limit} if limit > 0 else {}
            data = _http_get(FNG_URL, params=params)
            if not data or "data" not in data:
                result["status"] = "no_data"
                return result

            rows = data["data"]
            df = pd.DataFrame(rows)
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="s", utc=True)
            df = df[["timestamp", "value", "value_classification"]].copy()
            df.rename(columns={"value_classification": "classification"}, inplace=True)

            existing = _read_existing(SENTIMENT_ROOT, "fear_greed")
            if not existing.empty:
                result["existing"] = len(existing)
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
            else:
                combined = df

            result["fetched"] = len(df)
            result["final"] = len(combined)

            issues = []
            if combined["value"].isna().sum():
                issues.append("null fear_greed value")
            if ((combined["value"] < 0) | (combined["value"] > 100)).any():
                issues.append("value out of range 0-100")
            result["issues"] = issues

            _write_parquet(combined, SENTIMENT_ROOT, "fear_greed")
            result["status"] = "ok" if not issues else "issues"

            if result["fetched"] > 0:
                latest = combined.iloc[-1]
                log_ok(f"Fear/Greed: +{result['fetched']} rows (total {result['final']}), latest={latest['value']} ({latest['classification']})")
            else:
                log_info(f"Fear/Greed: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Fear/Greed failed: {e}")

        return result

    # ── BTC DOMINANCE (CoinGecko Keyless) ────────────────────────────────

    def fetch_btc_dominance(self) -> Dict[str, Any]:
        """
        Fetch BTC dominance and global market cap from CoinGecko Keyless API.
        No API key. No signup. ~10-30 calls/min shared IP rate limit.
        Called once every 5 minutes = 288 calls/day. Well within limits.
        """
        result = {
            "symbol": "GLOBAL", "tf": "snapshot", "type": "btc_dominance",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            data = _http_get(COINGECKO_GLOBAL_URL)
            if not data or "data" not in data:
                result["status"] = "no_data"
                return result

            d = data["data"]
            mcp = d.get("market_cap_percentage", {})
            now = datetime.now(timezone.utc)

            row = {
                "timestamp": now,
                "btc_dominance": mcp.get("btc"),
                "eth_dominance": mcp.get("eth"),
                "usdt_dominance": mcp.get("usdt"),
                "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
                "total_volume_usd": d.get("total_volume", {}).get("usd"),
                "market_cap_change_24h": d.get("market_cap_change_percentage_24h_usd"),
                "active_cryptocurrencies": d.get("active_cryptocurrencies"),
                "markets": d.get("markets"),
            }
            df = pd.DataFrame([row])

            existing = _read_existing(SENTIMENT_ROOT, "btc_dominance")
            if not existing.empty:
                result["existing"] = len(existing)
                # Keep last 10000 rows to prevent unbounded growth (snapshot data)
                if len(existing) > 10000:
                    existing = existing.iloc[-10000:]
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
            else:
                combined = df

            result["fetched"] = 1
            result["final"] = len(combined)

            issues = []
            if combined["btc_dominance"].isna().any():
                issues.append("null btc_dominance")
            if ((combined["btc_dominance"] < 0) | (combined["btc_dominance"] > 100)).any():
                issues.append("btc_dominance out of range")
            result["issues"] = issues

            _write_parquet(combined, SENTIMENT_ROOT, "btc_dominance")
            result["status"] = "ok" if not issues else "issues"

            log_ok(f"BTC Dominance: {row['btc_dominance']:.2f}% (total {result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"BTC Dominance failed: {e}")

        return result

    # ── BYBIT LONG/SHORT RATIO (ported from V3 ls_ratio.py) ──────────────

    def fetch_bybit_ls_ratio(self, symbol: str, period: str = "1h",
                             backfill_hours: int = 200) -> Dict[str, Any]:
        """
        Fetch Long/Short Ratio from Bybit V5 public API.
        No API key. Public market data endpoint.
        Ported from pipeline/engine/v3/ingestion/ls_ratio.py.
        """
        result = {
            "symbol": symbol, "tf": period, "type": "bybit_ls_ratio",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "period": period,
                "limit": 200,
            }
            data = _http_get(BYBIT_LS_RATIO_URL, params=params)
            if not data or data.get("retCode") != 0:
                result["status"] = "error"
                result["issues"].append(f"Bybit API error: {data.get('retMsg', 'unknown') if data else 'no response'}")
                return result

            rows = data["result"]["list"]
            if not rows:
                result["status"] = "no_data"
                return result

            df = pd.DataFrame(rows)
            df["buyRatio"] = pd.to_numeric(df["buyRatio"], errors="coerce")
            df["sellRatio"] = pd.to_numeric(df["sellRatio"], errors="coerce")
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
            df["symbol"] = symbol
            df = df[["symbol", "buyRatio", "sellRatio", "timestamp"]].copy()

            existing = _read_existing(BYBIT_ROOT, "ls_ratio", symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
            else:
                combined = df

            result["fetched"] = len(df)
            result["final"] = len(combined)

            issues = []
            if combined["buyRatio"].isna().sum():
                issues.append("null buyRatio")
            if combined["sellRatio"].isna().sum():
                issues.append("null sellRatio")
            result["issues"] = issues

            _write_parquet(combined, BYBIT_ROOT, "ls_ratio", symbol)
            result["status"] = "ok" if not issues else "issues"

            if result["fetched"] > 0:
                log_ok(f"Bybit LS Ratio {symbol}/{period}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"Bybit LS Ratio {symbol}/{period}: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Bybit LS Ratio {symbol} failed: {e}")

        return result

    # ── BINANCE LONG/SHORT RATIO ─────────────────────────────────────────

    def fetch_binance_ls_ratio(self, symbol: str, period: str = "1h",
                               limit: int = 200) -> Dict[str, Any]:
        """
        Fetch Long/Short Ratio from Binance Futures public API.
        No API key. No auth. Public market data.
        Cross-reference with Bybit LS ratio for divergence detection.
        """
        result = {
            "symbol": symbol, "tf": period, "type": "binance_ls_ratio",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            # Binance uses lowercase symbol without USDT suffix for some endpoints
            # globalLongShortAccountRatio uses symbol with USDT
            params = {
                "symbol": symbol,
                "period": period,
                "limit": limit,
            }
            url = f"{BINANCE_FAPI_BASE}{BINANCE_LS_RATIO_ENDPOINT}"
            data = _http_get(url, params=params)
            if not data or not isinstance(data, list):
                result["status"] = "no_data"
                return result

            df = pd.DataFrame(data)
            df["longShortRatio"] = pd.to_numeric(df["longShortRatio"], errors="coerce")
            df["longAccount"] = pd.to_numeric(df["longAccount"], errors="coerce")
            df["shortAccount"] = pd.to_numeric(df["shortAccount"], errors="coerce")
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
            df["symbol"] = symbol
            df = df[["symbol", "longShortRatio", "longAccount", "shortAccount", "timestamp"]].copy()

            existing = _read_existing(SENTIMENT_ROOT, "binance_ls_ratio", symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
            else:
                combined = df

            result["fetched"] = len(df)
            result["final"] = len(combined)

            issues = []
            if combined["longShortRatio"].isna().sum():
                issues.append("null longShortRatio")
            if (combined["longShortRatio"] <= 0).any():
                issues.append("non-positive longShortRatio")
            result["issues"] = issues

            _write_parquet(combined, SENTIMENT_ROOT, "binance_ls_ratio", symbol)
            result["status"] = "ok" if not issues else "issues"

            if result["fetched"] > 0:
                log_ok(f"Binance LS Ratio {symbol}/{period}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"Binance LS Ratio {symbol}/{period}: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Binance LS Ratio {symbol} failed: {e}")

        return result

    # ── BINANCE TAKER BUY/SELL VOLUME ────────────────────────────────────

    def fetch_binance_taker_volume(self, symbol: str, period: str = "1h",
                                   limit: int = 200) -> Dict[str, Any]:
        """
        Fetch Taker Buy/Sell Volume from Binance Futures public API.
        No API key. No auth. Public market data.
        Taker buy ratio > 0.6 = bullish pressure. < 0.4 = bearish pressure.
        """
        result = {
            "symbol": symbol, "tf": period, "type": "binance_taker_vol",
            "existing": 0, "fetched": 0, "final": 0,
            "status": "pending", "issues": [],
        }
        try:
            # Binance USDT-M taker long/short ratio uses symbol param
            params = {
                "symbol": symbol,
                "period": period,
                "limit": limit,
            }
            url = f"{BINANCE_FAPI_BASE}{BINANCE_TAKER_VOL_ENDPOINT}"
            data = _http_get(url, params=params)
            if not data or not isinstance(data, list):
                result["status"] = "no_data"
                return result

            df = pd.DataFrame(data)
            # Binance returns buySellRatio, buyVol, sellVol
            if "buySellRatio" not in df.columns:
                result["status"] = "no_data"
                result["issues"].append("unexpected response format")
                return result

            df["buySellRatio"] = pd.to_numeric(df["buySellRatio"], errors="coerce")
            df["buyVol"] = pd.to_numeric(df["buyVol"], errors="coerce")
            df["sellVol"] = pd.to_numeric(df["sellVol"], errors="coerce")
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
            df["symbol"] = symbol
            # Compute taker buy ratio: buyVol / (buyVol + sellVol)
            total = df["buyVol"] + df["sellVol"]
            df["taker_buy_ratio"] = np.where(total > 0, df["buyVol"] / total, np.nan)
            df = df[["symbol", "buySellRatio", "buyVol", "sellVol", "taker_buy_ratio", "timestamp"]].copy()

            existing = _read_existing(SENTIMENT_ROOT, "binance_taker_vol", symbol)
            if not existing.empty:
                result["existing"] = len(existing)
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
            else:
                combined = df

            result["fetched"] = len(df)
            result["final"] = len(combined)

            issues = []
            if combined["buySellRatio"].isna().sum():
                issues.append("null buySellRatio")
            if combined["taker_buy_ratio"].isna().sum():
                issues.append("null taker_buy_ratio")
            result["issues"] = issues

            _write_parquet(combined, SENTIMENT_ROOT, "binance_taker_vol", symbol)
            result["status"] = "ok" if not issues else "issues"

            if result["fetched"] > 0:
                log_ok(f"Binance Taker Vol {symbol}/{period}: +{result['fetched']} rows (total {result['final']})")
            else:
                log_info(f"Binance Taker Vol {symbol}/{period}: up to date ({result['final']} rows)")

        except Exception as e:
            result["status"] = "error"
            result["issues"].append(str(e))
            log_err(f"Binance Taker Vol {symbol} failed: {e}")

        return result

    # ── FETCH ALL (for scheduler integration) ────────────────────────────

    def fetch_all_bybit_ls_ratio(self, period: str = "1h") -> List[Dict[str, Any]]:
        """Fetch Bybit LS ratio for all symbols."""
        results = []
        for sym in self.symbols:
            results.append(self.fetch_bybit_ls_ratio(sym, period=period))
            time.sleep(0.5)
        return results

    def fetch_all_binance_ls_ratio(self, period: str = "1h") -> List[Dict[str, Any]]:
        """Fetch Binance LS ratio for all symbols."""
        results = []
        for sym in self.symbols:
            results.append(self.fetch_binance_ls_ratio(sym, period=period))
            time.sleep(0.5)
        return results

    def fetch_all_binance_taker_volume(self, period: str = "1h") -> List[Dict[str, Any]]:
        """Fetch Binance taker volume for all symbols."""
        results = []
        for sym in self.symbols:
            results.append(self.fetch_binance_taker_volume(sym, period=period))
            time.sleep(0.5)
        return results


# ── CLI ENTRY ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VLTHR Sentiment Data Fetchers")
    parser.add_argument("--source", choices=["fng", "coingecko", "bybit-ls", "binance-ls", "binance-taker", "all"],
                        default="all", help="Data source to fetch")
    parser.add_argument("--symbol", default=None, help="Single symbol (for per-symbol sources)")
    parser.add_argument("--period", default="1h", help="Period for LS ratio / taker volume")
    args = parser.parse_args()

    sf = SentimentFetchers()

    if args.source in ("fng", "all"):
        sf.fetch_fear_greed()
    if args.source in ("coingecko", "all"):
        sf.fetch_btc_dominance()
    if args.source in ("bybit-ls", "all"):
        if args.symbol:
            sf.fetch_bybit_ls_ratio(args.symbol, period=args.period)
        else:
            sf.fetch_all_bybit_ls_ratio(period=args.period)
    if args.source in ("binance-ls", "all"):
        if args.symbol:
            sf.fetch_binance_ls_ratio(args.symbol, period=args.period)
        else:
            sf.fetch_all_binance_ls_ratio(period=args.period)
    if args.source in ("binance-taker", "all"):
        if args.symbol:
            sf.fetch_binance_taker_volume(args.symbol, period=args.period)
        else:
            sf.fetch_all_binance_taker_volume(period=args.period)

    log_ok("Sentiment fetch complete.")
