"""Technical indicator calculations used across D1-D7 stages."""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_val = atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_val
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_val
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def bollinger_bands(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid - std_mult * std, mid, mid + std_mult * std


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return (typical * df["volume"]).cumsum() / df["volume"].cumsum()


def add_all(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Add all standard indicators to the dataframe in-place."""
    p = params or {}
    df = df.copy()
    df["rsi"] = rsi(df["close"], p.get("rsi_period", 14))
    df["atr"] = atr(df, p.get("atr_period", 14))
    df["adx"] = adx(df, p.get("adx_period", 14))
    df["ema_fast"] = ema(df["close"], p.get("fast", 8))
    df["ema_slow"] = ema(df["close"], p.get("slow", 21))
    df["ema_200"] = ema(df["close"], 200)
    df["bb_lower"], df["bb_mid"], df["bb_upper"] = bollinger_bands(df["close"])
    df["vwap"] = vwap(df)
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    # 4h context: rolling 16-bar (4h on 15m) trend
    df["ctx_bull"] = (df["close"].rolling(16).mean() > df["close"].rolling(64).mean()).astype(int)
    return df
