"""
Unified data loader for BACKTESTER.
Loads parquet data from broker-specific folders.
"""
import os
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

DATA_ROOTS = {
    "XAUUSD":  "data/deriv/XAUUSD",
    "BTCUSDT": "data/bybit/BTCUSDT",
    "ETHUSDT": "data/bybit/ETHUSDT",
    "SOLUSDT": "data/bybit/SOLUSDT",
    "XRPUSDT": "data/bybit/XRPUSDT",
    "BNBUSDT": "data/bybit/BNBUSDT",
    "DOGEUSDT": "data/bybit/DOGEUSDT",
}

# Resolve relative to repo root (BACKTESTER is at repo/BACKTESTER)
REPO_ROOT = Path(__file__).resolve().parents[5]


def resolve_symbol_path(symbol: str, timeframe: str) -> Path:
    """Return the folder containing parquet files for a symbol/timeframe."""
    symbol = symbol.upper()
    rel = DATA_ROOTS.get(symbol)
    if not rel:
        raise ValueError(f"Unknown symbol: {symbol}. Supported: {list(DATA_ROOTS.keys())}")
    return REPO_ROOT / rel / timeframe


def load_symbol_tf(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Load all parquet files for a symbol/timeframe, sort by timestamp.
    Returns DataFrame with columns: timestamp, open, high, low, close, volume (if present)
    """
    folder = resolve_symbol_path(symbol, timeframe)
    if not folder.exists():
        raise FileNotFoundError(f"Data folder not found: {folder}")

    parquet_files = sorted(folder.rglob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files in {folder}")

    dfs = []
    for p in parquet_files:
        try:
            df = pq.read_table(p).to_pandas()
            dfs.append(df)
        except Exception as e:
            print(f"[WARN] Failed to read {p}: {e}")
            continue

    if not dfs:
        raise ValueError(f"No readable parquet files in {folder}")

    df = pd.concat(dfs, ignore_index=True)

    # Normalise timestamp
    ts_col = "timestamp" if "timestamp" in df.columns else None
    if ts_col is None:
        # Try common alternatives
        for c in ["time", "date", "ts", "datetime"]:
            if c in df.columns:
                ts_col = c
                break
    if ts_col is None:
        raise KeyError(f"No timestamp column found in {folder}. Columns: {list(df.columns)}")

    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.sort_values(ts_col).reset_index(drop=True)

    # Drop duplicates on timestamp
    before = len(df)
    df = df.drop_duplicates(subset=[ts_col])
    dropped = before - len(df)
    if dropped:
        print(f"[INFO] {symbol}/{timeframe}: dropped {dropped} duplicate timestamps")

    # Normalise column names to lowercase
    rename_map = {}
    for col in df.columns:
        lc = col.lower()
        if lc in ["open", "high", "low", "close", "volume", "timestamp"]:
            rename_map[col] = lc
    df = df.rename(columns=rename_map)

    return df


def get_available_timeframes(symbol: str) -> list:
    """List available OHLCV timeframe folders for a symbol."""
    symbol = symbol.upper()
    rel = DATA_ROOTS.get(symbol)
    if not rel:
        return []
    root = REPO_ROOT / rel
    if not root.exists():
        return []
    exclude = {"enriched", "funding_rate", "open_interest", "orderbook"}
    return sorted([d.name for d in root.iterdir() if d.is_dir() and d.name not in exclude])
