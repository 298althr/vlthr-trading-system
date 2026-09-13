"""
V3 Long/Short Ratio Fetcher
============================
Fetches trader sentiment data from Bybit /v5/market/account-ratio.
Stores to data/bybit/{SYMBOL}/ls_ratio/{YYYY}/{MM}.parquet

API: https://api.bybit.com/v5/market/account-ratio
Params: category=linear, symbol, period (5m,15m,30m,1h,4h,1d), limit (max 200)
Returns: buyRatio, sellRatio, timestamp (ms)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any

import pandas as pd
import numpy as np

from .bybit_client import BybitClient, paginate_history
from .parquet_store import write_partitions, read_existing, validate_df


def fetch_ls_ratio(client: BybitClient, symbol: str, period: str = "1h",
                   backfill_hours: int = None) -> Dict[str, Any]:
    """
    Fetch long/short ratio for a symbol.

    Args:
        client: BybitClient instance
        symbol: e.g. BTCUSDT
        period: 5m, 15m, 30m, 1h, 4h, 1d
        backfill_hours: If set, fetch last N hours (for initial backfill)

    Returns:
        Result dict with status, rows fetched, etc.
    """
    result = {
        "symbol": symbol, "tf": period, "type": "ls_ratio",
        "existing": 0, "fetched": 0, "final": 0,
        "status": "pending", "issues": [],
    }

    try:
        existing = read_existing("ls_ratio", symbol)
        if not existing.empty:
            result["existing"] = len(existing)
            since = existing["timestamp"].max() - timedelta(hours=1)
        else:
            if backfill_hours:
                since = datetime.now(timezone.utc) - timedelta(hours=backfill_hours)
            else:
                since = datetime.now(timezone.utc) - timedelta(hours=200)

        # Fetch latest data (API returns most recent first, no pagination needed for recent)
        rows = client.fetch_ls_ratio(symbol, period=period, limit=200)
        result["fetched"] = len(rows)

        if not rows:
            result["status"] = "no_new_data"
            return result

        df = pd.DataFrame(rows)
        df["buyRatio"] = pd.to_numeric(df["buyRatio"], errors="coerce")
        df["sellRatio"] = pd.to_numeric(df["sellRatio"], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
        df["symbol"] = symbol
        df = df[["symbol", "buyRatio", "sellRatio", "timestamp"]].copy()

        # Merge with existing
        if existing.empty:
            combined = df
        else:
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)

        # Validate
        ok, issues = validate_df(combined, "ls_ratio")
        result["issues"] = issues
        result["final"] = len(combined)

        write_partitions(combined, "ls_ratio", symbol)
        result["status"] = "ok" if ok else "issues"

        if result["fetched"] > 0:
            print(f"[ls_ratio] {symbol}/{period}: +{result['fetched']} rows (total {result['final']})")
        else:
            print(f"[ls_ratio] {symbol}/{period}: up to date ({result['final']} rows)")

    except Exception as e:
        result["status"] = "error"
        result["issues"].append(str(e))
        print(f"[ls_ratio] {symbol} failed: {e}")

    return result
