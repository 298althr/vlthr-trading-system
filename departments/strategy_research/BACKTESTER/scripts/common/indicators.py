"""
Shared indicator library for BACKTESTER.
All indicator functions accept a DataFrame and return a DataFrame with new columns added.
"""
import numpy as np
import pandas as pd


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ATR column to DataFrame (must have high, low, close)."""
    df = df.copy()
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=period, min_periods=period).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add RSI column."""
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ADX, +DI, -DI columns."""
    df = df.copy()
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=period, min_periods=period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    df["adx"] = dx.rolling(window=period, min_periods=period).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    return df


def add_ema(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
    """Add EMA columns for given periods."""
    df = df.copy()
    if periods is None:
        periods = [8, 13, 21, 34, 50, 100, 200]
    for p in periods:
        df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()
    return df


def add_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Add Bollinger Bands columns."""
    df = df.copy()
    sma = df["close"].rolling(window=period, min_periods=period).mean()
    std = df["close"].rolling(window=period, min_periods=period).std()
    df["bb_upper"] = sma + std_dev * std
    df["bb_lower"] = sma - std_dev * std
    df["bb_mid"] = sma
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Add MACD columns."""
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def add_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """Add Stochastic Oscillator columns."""
    df = df.copy()
    lowest_low = df["low"].rolling(window=k_period, min_periods=k_period).min()
    highest_high = df["high"].rolling(window=k_period, min_periods=k_period).max()
    df["stoch_k"] = 100 * ((df["close"] - lowest_low) / (highest_high - lowest_low))
    df["stoch_d"] = df["stoch_k"].rolling(window=d_period, min_periods=d_period).mean()
    return df


def add_log_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Add log returns column."""
    df = df.copy()
    df["log_return"] = np.log(df["close"] / df["close"].shift(1))
    return df


def add_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Add volume / rolling average volume ratio."""
    df = df.copy()
    if "volume" in df.columns:
        df["volume_ratio"] = df["volume"] / df["volume"].rolling(window=period, min_periods=period).mean()
    return df


def add_atr_percentile(df: pd.DataFrame, lookback: int = 100) -> pd.DataFrame:
    """Add ATR percentile rank in rolling window."""
    df = df.copy()
    if "atr" not in df.columns:
        df = add_atr(df)
    df["atr_rank"] = df["atr"].rolling(window=lookback, min_periods=lookback).rank(pct=True)
    return df


def classify_session(ts: pd.Timestamp) -> str:
    """Classify UTC timestamp into trading session.

    Delegates to portfolio_config.classify_session for single source of truth.
    Falls back to local definition if portfolio_config is not importable (e.g.
    when indicators.py is used standalone outside the pipeline).
    """
    try:
        from portfolio_config import classify_session as _classify
        return _classify(ts)
    except ImportError:
        pass
    hour = ts.hour
    if 0 <= hour < 8:
        return "asian"
    elif 8 <= hour < 12:
        return "london"
    elif 12 <= hour < 17:
        return "ny_open"
    else:
        return "ny_late"
