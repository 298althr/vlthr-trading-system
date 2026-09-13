"""
Bybit V5 Enriched Data Builder Core
====================================
Merges OHLCV + open_interest + funding_rate into a single enriched parquet
per (symbol, timeframe, year, month) using merge_asof for temporal alignment.

Raw source files are left untouched; output goes to:
    data/bybit/{SYMBOL}/enriched/{TF}/{YYYY}/{MM}.parquet

Alignment rule:
    For each candle timestamp, fetch the most recent OI and fundingRate
    at or before that timestamp (backward merge_asof).  No future leakage.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict

import numpy as np
import pandas as pd

DATA_ROOT = Path("data/bybit")
SENTIMENT_ROOT = Path("data/sentiment")

# ── I/O HELPERS ─────────────────────────────────────────────────────────────

def _load_all_parquets(glob_path: Path, ts_col: str) -> pd.DataFrame:
    """Read all .parquet files under a directory tree, normalize timestamps."""
    files = sorted(glob_path.rglob("*.parquet"))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            if df.empty or ts_col not in df.columns:
                continue
            df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
            dfs.append(df)
        except Exception as e:
            print(f"[WARN] Could not read {f}: {e}", file=sys.stderr)
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True).sort_values(ts_col)
    return combined.drop_duplicates(ts_col).reset_index(drop=True)


def _write_parquet(df: pd.DataFrame, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd", index=False)
    tmp.replace(out_path)


# ── ENRICH LOGIC ────────────────────────────────────────────────────────────

def enrich_symbol_tf(symbol: str, tf: str, dry_run: bool = False) -> Dict:
    """
    Build enriched parquets for a single symbol/timeframe combination.
    Returns a summary dict with stats.
    """
    res = {
        "symbol": symbol,
        "timeframe": tf,
        "candles_loaded": 0,
        "oi_merged": 0,
        "funding_merged": 0,
        "null_oi_pct": 0.0,
        "null_funding_pct": 0.0,
        "partitions_written": 0,
        "status": "pending",
    }

    # --- 1. Load raw OHLCV -------------------------------------------------
    ohlcv_dir = DATA_ROOT / symbol / tf
    ohlcv = _load_all_parquets(ohlcv_dir, "timestamp")
    if ohlcv.empty:
        res["status"] = "no_ohlcv"
        return res
    res["candles_loaded"] = len(ohlcv)
    ohlcv["timestamp"] = ohlcv["timestamp"].dt.as_unit("ms")
    print(f"  {symbol}/{tf}: loaded {len(ohlcv):,} candles")

    # --- 2. Load auxiliary data ---------------------------------------------
    oi = _load_all_parquets(DATA_ROOT / symbol / "open_interest", "timestamp")
    funding = _load_all_parquets(DATA_ROOT / symbol / "funding_rate", "fundingTime")
    ls_ratio = _load_all_parquets(DATA_ROOT / symbol / "ls_ratio", "timestamp")
    binance_ls = _load_all_parquets(SENTIMENT_ROOT / "binance_ls_ratio" / symbol, "timestamp")
    binance_taker = _load_all_parquets(SENTIMENT_ROOT / "binance_taker_vol" / symbol, "timestamp")
    fear_greed = _load_all_parquets(SENTIMENT_ROOT / "fear_greed", "timestamp")
    btc_dom = _load_all_parquets(SENTIMENT_ROOT / "btc_dominance", "timestamp")
    if not oi.empty:
        print(f"  {symbol}/{tf}: loaded {len(oi):,} OI rows")
    if not funding.empty:
        print(f"  {symbol}/{tf}: loaded {len(funding):,} funding rows")
    if not ls_ratio.empty:
        print(f"  {symbol}/{tf}: loaded {len(ls_ratio):,} L/S ratio rows")
    if not binance_ls.empty:
        print(f"  {symbol}/{tf}: loaded {len(binance_ls):,} Binance LS rows")
    if not binance_taker.empty:
        print(f"  {symbol}/{tf}: loaded {len(binance_taker):,} Binance taker rows")
    if not fear_greed.empty:
        print(f"  {symbol}/{tf}: loaded {len(fear_greed):,} Fear/Greed rows")
    if not btc_dom.empty:
        print(f"  {symbol}/{tf}: loaded {len(btc_dom):,} BTC dominance rows")

    # Prepare auxiliary DataFrames for merge_asof
    if not oi.empty:
        oi = oi[["timestamp", "openInterest"]].rename(columns={"openInterest": "open_interest"}).copy()
        oi["timestamp"] = oi["timestamp"].dt.as_unit("ms")
        oi = oi.sort_values("timestamp").reset_index(drop=True)
    if not funding.empty:
        funding = funding[["fundingTime", "fundingRate"]].rename(columns={"fundingRate": "funding_rate", "fundingTime": "timestamp"}).copy()
        funding["timestamp"] = funding["timestamp"].dt.as_unit("ms")
        funding = funding.sort_values("timestamp").reset_index(drop=True)
    if not ls_ratio.empty:
        ls_ratio = ls_ratio[["timestamp", "buyRatio", "sellRatio"]].copy()
        ls_ratio["timestamp"] = ls_ratio["timestamp"].dt.as_unit("ms")
        ls_ratio = ls_ratio.sort_values("timestamp").reset_index(drop=True)
    if not binance_ls.empty:
        binance_ls = binance_ls[["timestamp", "longShortRatio", "longAccount", "shortAccount"]].copy()
        binance_ls["timestamp"] = binance_ls["timestamp"].dt.as_unit("ms")
        binance_ls = binance_ls.sort_values("timestamp").reset_index(drop=True)
    if not binance_taker.empty:
        binance_taker = binance_taker[["timestamp", "taker_buy_ratio"]].copy()
        binance_taker["timestamp"] = binance_taker["timestamp"].dt.as_unit("ms")
        binance_taker = binance_taker.sort_values("timestamp").reset_index(drop=True)
    if not fear_greed.empty:
        fear_greed = fear_greed[["timestamp", "value"]].rename(columns={"value": "fear_greed"}).copy()
        fear_greed["timestamp"] = fear_greed["timestamp"].dt.as_unit("ms")
        fear_greed = fear_greed.sort_values("timestamp").reset_index(drop=True)
    if not btc_dom.empty:
        btc_dom = btc_dom[["timestamp", "btc_dominance"]].copy()
        btc_dom["timestamp"] = btc_dom["timestamp"].dt.as_unit("ms")
        btc_dom = btc_dom.sort_values("timestamp").reset_index(drop=True)

    # --- 3. Enrich month by month -------------------------------------------
    ohlcv["year"] = ohlcv["timestamp"].dt.year
    ohlcv["month"] = ohlcv["timestamp"].dt.month

    partitions_written = 0
    total_null_oi = 0
    total_null_funding = 0
    total_rows = 0

    for (year, month), group in ohlcv.groupby(["year", "month"]):
        month_df = group.drop(columns=["year", "month"]).copy()

        # Merge OI backward
        if not oi.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                oi,
                on="timestamp",
                direction="backward",
            )

        # Merge funding backward
        if not funding.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                funding,
                on="timestamp",
                direction="backward",
            )

        # Merge L/S ratio backward
        if not ls_ratio.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                ls_ratio,
                on="timestamp",
                direction="backward",
            )

        # Merge Binance LS ratio backward
        if not binance_ls.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                binance_ls,
                on="timestamp",
                direction="backward",
            )

        # Merge Binance taker buy ratio backward
        if not binance_taker.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                binance_taker,
                on="timestamp",
                direction="backward",
            )

        # Merge Fear/Greed backward (daily data, backward fill to each candle)
        if not fear_greed.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                fear_greed,
                on="timestamp",
                direction="backward",
            )

        # Merge BTC dominance backward (5-min snapshots, backward fill)
        if not btc_dom.empty:
            month_df = pd.merge_asof(
                month_df.sort_values("timestamp"),
                btc_dom,
                on="timestamp",
                direction="backward",
            )

        # Keep only expected columns
        expected_cols = ["timestamp", "open", "high", "low", "close", "volume", "turnover",
                         "open_interest", "funding_rate", "buyRatio", "sellRatio",
                         "longShortRatio", "longAccount", "shortAccount",
                         "taker_buy_ratio", "fear_greed", "btc_dominance"]
        for col in expected_cols:
            if col not in month_df.columns:
                month_df[col] = np.nan
        month_df = month_df[expected_cols].copy()

        # Stats
        total_rows += len(month_df)
        total_null_oi += month_df["open_interest"].isna().sum()
        total_null_funding += month_df["funding_rate"].isna().sum()

        out_path = DATA_ROOT / symbol / "enriched" / tf / f"{year:04d}" / f"{month:02d}.parquet"
        if not dry_run:
            _write_parquet(month_df, out_path)
        partitions_written += 1

    res["partitions_written"] = partitions_written
    res["null_oi_pct"] = round(total_null_oi / total_rows * 100, 2) if total_rows else 0.0
    res["null_funding_pct"] = round(total_null_funding / total_rows * 100, 2) if total_rows else 0.0
    res["status"] = "ok"
    print(f"  {symbol}/{tf}: wrote {partitions_written} partitions | OI null={res['null_oi_pct']}% | funding null={res['null_funding_pct']}%")
    return res


def run_symbol(symbol: str, tfs: List[str] = None, dry_run: bool = False):
    if tfs is None:
        tfs = ["4h", "1h", "30m", "15m", "5m"]
    print("=" * 70)
    print(f"  ENRICH: {symbol}  |  timeframes={','.join(tfs)}")
    print("=" * 70)
    results = []
    for tf in tfs:
        try:
            results.append(enrich_symbol_tf(symbol, tf, dry_run))
        except Exception as e:
            print(f"[ERR] {symbol}/{tf}: {e}", file=sys.stderr)
            results.append({"symbol": symbol, "timeframe": tf, "status": "error", "error": str(e)})
    print("=" * 70)
    print(f"  SUMMARY — {symbol}")
    print("=" * 70)
    for r in results:
        print(f"  {r.get('timeframe','?'):>4s}  {r.get('status','?'):8s}  candles={r.get('candles_loaded',0):>7,}  partitions={r.get('partitions_written',0):>2d}  oi_null={r.get('null_oi_pct',0.0):>5.1f}%  fr_null={r.get('null_funding_pct',0.0):>5.1f}%")
    return results
