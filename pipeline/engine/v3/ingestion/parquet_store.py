"""
V3 Parquet Store
=================
Unified parquet I/O with schema validation, atomic writes, and partitioning.
Handles all data types: OHLCV, funding, open_interest, orderbook, ls_ratio, liquidations.

Replaces scattered read/write functions in bybit_ingest_core + bybit_market_data_core.
"""
from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List

import pandas as pd
import numpy as np

# Data root — check env var first, then resolve relative to repo root
# Walk up from engine/v3/ingestion/ to find data/bybit/
_repo_root = Path(__file__).resolve().parents[5]  # engine/v3/ingestion -> engine -> vlthr-signal-dashboard -> paper_trade_unzipped -> repo
_env_data_root = os.environ.get("PARQUET_DATA_ROOT", "")
if _env_data_root:
    DATA_ROOT = Path(_env_data_root)
elif (_repo_root / "data" / "bybit").exists():
    DATA_ROOT = _repo_root / "data" / "bybit"
else:
    DATA_ROOT = Path("data/bybit")  # fallback to CWD-relative

# Schema definitions: required columns and types per data type
SCHEMAS = {
    "ohlcv": {
        "required": ["timestamp", "open", "high", "low", "close", "volume"],
        "optional": ["turnover"],
        "types": {"timestamp": "datetime64[ns, UTC]"},
    },
    "funding_rate": {
        "required": ["symbol", "fundingRate", "fundingTime"],
        "types": {"fundingTime": "datetime64[ns, UTC]"},
    },
    "open_interest": {
        "required": ["symbol", "openInterest", "timestamp"],
        "types": {"timestamp": "datetime64[ns, UTC]"},
    },
    "orderbook": {
        "required": ["timestamp", "symbol", "side", "level", "price", "size"],
        "types": {"timestamp": "datetime64[ns, UTC]"},
    },
    "ls_ratio": {
        "required": ["symbol", "buyRatio", "sellRatio", "timestamp"],
        "types": {"timestamp": "datetime64[ns, UTC]"},
    },
    "liquidations": {
        "required": ["symbol", "side", "size", "price", "liqTime"],
        "types": {"liqTime": "datetime64[ns, UTC]"},
    },
}

# Partition column per data type (for year/month directory structure)
PARTITION_COL = {
    "ohlcv": "timestamp",
    "funding_rate": "fundingTime",
    "open_interest": "timestamp",
    "orderbook": "timestamp",
    "ls_ratio": "timestamp",
    "liquidations": "liqTime",
}

# Subdirectory name per data type
SUBDIR = {
    "ohlcv": "",  # OHLCV uses {SYMBOL}/{TF}/ pattern
    "funding_rate": "funding_rate",
    "open_interest": "open_interest",
    "orderbook": "orderbook",
    "ls_ratio": "ls_ratio",
    "liquidations": "liquidations",
}


def validate_df(df: pd.DataFrame, data_type: str) -> tuple[bool, list[str]]:
    """Validate a DataFrame against the schema for its data type."""
    issues = []
    schema = SCHEMAS.get(data_type)
    if not schema:
        return False, [f"Unknown data type: {data_type}"]

    # Check required columns
    missing = [c for c in schema["required"] if c not in df.columns]
    if missing:
        issues.append(f"missing columns: {missing}")

    # Check for nulls in required columns
    for col in schema["required"]:
        if col in df.columns and df[col].isna().any():
            issues.append(f"null values in {col}")

    return len(issues) == 0, issues


def _atomic_write(df: pd.DataFrame, out_path: Path):
    """Write parquet atomically: write to .tmp then rename."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd", index=False)
    tmp.replace(out_path)


def _get_path(data_type: str, symbol: str, timeframe: str = None) -> Path:
    """Get the base directory for a data type / symbol."""
    subdir = SUBDIR.get(data_type, "")
    if data_type == "ohlcv":
        return DATA_ROOT / symbol / timeframe
    return DATA_ROOT / symbol / subdir


def write_partitions(df: pd.DataFrame, data_type: str, symbol: str,
                     timeframe: str = None) -> List[Path]:
    """
    Write a DataFrame as year/month partitioned parquet files.
    For orderbook, partitions by year/month/day (higher volume).
    Returns list of written file paths.
    """
    if df.empty:
        return []

    ok, issues = validate_df(df, data_type)
    if not ok:
        print(f"[parquet_store] Validation issues for {data_type}/{symbol}: {issues}")

    df = df.copy()
    part_col = PARTITION_COL[data_type]
    df[part_col] = pd.to_datetime(df[part_col], utc=True)
    df["__year"] = df[part_col].dt.year
    df["__month"] = df[part_col].dt.month

    base = _get_path(data_type, symbol, timeframe)
    written = []

    if data_type == "orderbook":
        df["__day"] = df[part_col].dt.day
        for (y, m, d), g in df.groupby(["__year", "__month", "__day"]):
            out = g.drop(columns=["__year", "__month", "__day"]).reset_index(drop=True)
            path = base / f"{y:04d}" / f"{m:02d}" / f"{d:02d}.parquet"
            _atomic_write(out, path)
            written.append(path)
    else:
        for (y, m), g in df.groupby(["__year", "__month"]):
            out = g.drop(columns=["__year", "__month"]).reset_index(drop=True)
            path = base / f"{y:04d}" / f"{m:02d}.parquet"
            _atomic_write(out, path)
            written.append(path)

    return written


def read_existing(data_type: str, symbol: str, timeframe: str = None) -> pd.DataFrame:
    """
    Read all existing parquet files for a data type / symbol.
    Returns concatenated, sorted, deduplicated DataFrame (or empty if none).
    """
    base = _get_path(data_type, symbol, timeframe)
    if not base.exists():
        return pd.DataFrame()

    dfs = []
    for f in base.rglob("*.parquet"):
        try:
            df = pd.read_parquet(f)
            if not df.empty:
                dfs.append(df)
        except Exception as e:
            print(f"[parquet_store] Could not read {f}: {e}")

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    part_col = PARTITION_COL[data_type]
    combined[part_col] = pd.to_datetime(combined[part_col], utc=True)
    # Deduplicate: for orderbook and liquidations, multiple rows share the same timestamp
    # so we deduplicate on all columns, not just the timestamp
    if data_type in ("orderbook", "liquidations"):
        combined = combined.drop_duplicates().sort_values(part_col).reset_index(drop=True)
    else:
        combined = combined.sort_values(part_col).drop_duplicates(subset=[part_col], keep="last").reset_index(drop=True)
    return combined


def get_last_timestamp(data_type: str, symbol: str, timeframe: str = None) -> Optional[datetime]:
    """Get the most recent timestamp for a data type / symbol."""
    df = read_existing(data_type, symbol, timeframe)
    if df.empty:
        return None
    part_col = PARTITION_COL[data_type]
    return df[part_col].max().to_pydatetime()


def get_row_count(data_type: str, symbol: str, timeframe: str = None) -> int:
    """Get total row count for a data type / symbol."""
    df = read_existing(data_type, symbol, timeframe)
    return len(df)
