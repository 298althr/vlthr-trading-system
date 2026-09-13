"""D0 — Data loader. Reads Parquet files from engine/data/bybit/."""
from __future__ import annotations
from pathlib import Path
import os
import pandas as pd
import numpy as np
from typing import Optional

# Root of the data store — env override for Docker, fallback to relative path
_data_root_env = os.environ.get("DATA_ROOT")
if _data_root_env:
    _DATA_ROOT = Path(_data_root_env)
else:
    _DATA_ROOT = Path(__file__).parents[3] / "data" / "bybit"


def data_root() -> Path:
    return _DATA_ROOT


def _resolve_symbol_base(symbol: str, subpath: str = "") -> Path:
    """Find symbol data under bybit/ or capital.com/ subdirectories."""
    candidates = [
        _DATA_ROOT / "bybit" / symbol / subpath,
        _DATA_ROOT / "capital.com" / symbol / subpath,
        _DATA_ROOT / symbol / subpath,  # fallback for flat layout
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # Return the first candidate for error reporting
    return candidates[0]


def load_ohlcv(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Load and concatenate Parquet files for a symbol/interval within date range."""
    base = _resolve_symbol_base(symbol, interval)
    if not base.exists():
        raise FileNotFoundError(f"No data at {base}")

    start_dt = pd.Timestamp(start, tz="UTC")
    end_dt = pd.Timestamp(end, tz="UTC")

    frames = []
    for year_dir in sorted(base.iterdir()):
        if not year_dir.is_dir():
            continue
        year = int(year_dir.name)
        if year < start_dt.year - 1 or year > end_dt.year + 1:
            continue
        for pq in sorted(year_dir.glob("*.parquet")):
            df = pd.read_parquet(pq)
            frames.append(df)

    if not frames:
        raise ValueError(f"No Parquet files found for {symbol}/{interval}")

    df = pd.concat(frames, ignore_index=True)

    # Normalise timestamp column
    ts_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
    df = df.rename(columns={ts_col: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp")

    # Normalise OHLCV column names (lower-case)
    df.columns = [c.lower() for c in df.columns]
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing column '{col}' in {symbol}/{interval}")

    df = df[(df["timestamp"] >= start_dt) & (df["timestamp"] <= end_dt)]
    df = df.reset_index(drop=True)
    return df


def load_funding(symbol: str, start: str, end: str) -> pd.DataFrame:
    base = _resolve_symbol_base(symbol, "funding_rate")
    if not base.exists():
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    frames = [pd.read_parquet(p) for p in sorted(base.glob("**/*.parquet"))]
    if not frames:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]
    ts_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
    df = df.rename(columns={ts_col: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp")
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    return df[(df["timestamp"] >= s) & (df["timestamp"] <= e)].reset_index(drop=True)


def load_open_interest(symbol: str, start: str, end: str) -> pd.DataFrame:
    base = _resolve_symbol_base(symbol, "open_interest")
    if not base.exists():
        return pd.DataFrame(columns=["timestamp", "open_interest"])
    frames = [pd.read_parquet(p) for p in sorted(base.glob("**/*.parquet"))]
    if not frames:
        return pd.DataFrame(columns=["timestamp", "open_interest"])
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]
    ts_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
    df = df.rename(columns={ts_col: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp")
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    return df[(df["timestamp"] >= s) & (df["timestamp"] <= e)].reset_index(drop=True)
