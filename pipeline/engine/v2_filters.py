"""
v2_filters.py — V2 Strategy Filters for Live Pipeline
======================================================
Implements the V2 backtest-validated filters:
  1. Daily bias filter (EMA50 + RSI on daily bars)
  2. Volume + RSI momentum confirmation
  3. Dynamic strategy selection by regime
  4. TP scaling by ATR percentile
  5. BTC correlation filter for XRP

All filters are designed to be applied after DQS scoring but before
trade entry in the portfolio orchestrator.
"""
import os
import time
import numpy as np
import pandas as pd
from pathlib import Path

REPO_ROOT = Path(os.environ.get(
    "VLTHR_REPO_ROOT",
    Path(__file__).resolve().parents[2]
))

DATA_ROOT = Path(os.environ.get("PARQUET_DATA_ROOT", REPO_ROOT / "data")) / "bybit"


def _tf_to_minutes(tf):
    """Convert timeframe string to minutes (e.g. '15m' -> 15, '1h' -> 60, '1d' -> 1440)."""
    tf = tf.lower().strip()
    if tf.endswith('m'):
        return int(tf[:-1])
    if tf.endswith('h'):
        return int(tf[:-1]) * 60
    if tf.endswith('d'):
        return int(tf[:-1]) * 1440
    return 15


def _load_parquet_tf(symbol, tf):
    """Load parquet data for a symbol/timeframe, return completed bars only.
    Drops the last bar if it is still forming (current time has not passed the
    bar's close). This prevents the volume filter from reading partial bars."""
    tf_dir = DATA_ROOT / symbol / tf
    if not tf_dir.exists():
        return pd.DataFrame()
    files = sorted(tf_dir.rglob("*.parquet"))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files[-2:]:  # last 2 files for efficiency
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if len(df) > 0:
        bar_minutes = _tf_to_minutes(tf)
        last_bar_open = df["timestamp"].iloc[-1]
        last_bar_close = last_bar_open + pd.Timedelta(minutes=bar_minutes)
        now_utc = pd.Timestamp.now(tz='UTC')
        if now_utc < last_bar_close:
            df = df.iloc[:-1].reset_index(drop=True)
    return df


_daily_bias_cache: dict = {}
_DAILY_BIAS_TTL_SECONDS = 3600  # 1 hour — refreshes from new daily bars without being too aggressive


def load_daily_bias(symbol):
    """
    Load daily bars, compute EMA50 and RSI for daily bias.
    Returns dict with 'bias' (BULL/BEAR), 'rsi' (float), 'ema_50' (float).
    Returns None if insufficient data.
    Cached with 1h TTL — daily bias changes once per day but the pipeline runs
    as a long-lived process and must pick up new daily bars without restart.
    """
    cached = _daily_bias_cache.get(symbol)
    if cached and (time.time() - cached["_ts"] < _DAILY_BIAS_TTL_SECONDS):
        return cached["data"]

    df = _load_parquet_tf(symbol, "1d")
    if len(df) < 50:
        _daily_bias_cache[symbol] = {"data": None, "_ts": time.time()}
        return None

    close = df["close"]
    df["ema_50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(span=14, adjust=False).mean()
    avg_loss = loss.ewm(span=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi_daily"] = 100 - (100 / (1 + rs))

    last = df.iloc[-1]
    bias = "BULL" if float(last["close"]) > float(last["ema_50"]) else "BEAR"
    rsi_daily = float(last["rsi_daily"]) if not pd.isna(last["rsi_daily"]) else 50.0

    result = {
        "bias": bias,
        "rsi": rsi_daily,
        "ema_50": float(last["ema_50"]),
        "close": float(last["close"]),
    }
    _daily_bias_cache[symbol] = {"data": result, "_ts": time.time()}
    return result


_btc_mom_cache = {"ts": None, "mom_6": 0.0}


def get_btc_momentum():
    """
    Get BTC 6-bar (90min) momentum percentage.
    Cached for 5 minutes to avoid reloading data every symbol scan.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    if _btc_mom_cache["ts"] is not None:
        elapsed = (now - _btc_mom_cache["ts"]).total_seconds()
        if elapsed < 300:  # 5 min cache
            return _btc_mom_cache["mom_6"]

    df = _load_parquet_tf("BTCUSDT", "15m")
    if len(df) < 10:
        return 0.0

    close = df["close"]
    mom_6 = float(close.pct_change(6).iloc[-1] * 100) if len(close) > 6 else 0.0
    if pd.isna(mom_6):
        mom_6 = 0.0

    _btc_mom_cache["ts"] = now
    _btc_mom_cache["mom_6"] = mom_6
    return mom_6


def compute_vol_ratio(enriched):
    """Compute volume ratio vs 20-bar rolling mean from enriched data."""
    if len(enriched) < 21:
        return 1.0
    vol = enriched["volume"]
    vol_mean_20 = vol.rolling(20).mean()
    last_vol = float(vol.iloc[-1])
    last_mean = float(vol_mean_20.iloc[-1]) if not pd.isna(vol_mean_20.iloc[-1]) else 0
    if last_mean <= 0:
        return 1.0
    ratio = last_vol / last_mean
    return ratio if not pd.isna(ratio) else 1.0


def compute_rsi_momentum(enriched):
    """Compute RSI 3-bar change from enriched data."""
    if len(enriched) < 4 or "rsi" not in enriched.columns:
        return 0.0
    rsi = enriched["rsi"]
    if len(rsi) < 4:
        return 0.0
    change = float(rsi.iloc[-1] - rsi.iloc[-4])
    return change if not pd.isna(change) else 0.0


def compute_regime(enriched):
    """
    Classify market regime from enriched data.
    Returns 'trending', 'ranging', or 'volatile'.
    """
    if len(enriched) < 55 or "adx" not in enriched.columns or "atr" not in enriched.columns:
        return "ranging"

    last = enriched.iloc[-1]
    adx = float(last.get("adx", 25))
    atr = float(last.get("atr", 0))
    close = float(last.get("close", 0))

    if pd.isna(adx) or pd.isna(atr) or close <= 0:
        return "ranging"

    atr_pct = (atr / close) * 100

    # Compute ATR percentile rank from last 50 bars
    if len(enriched) >= 50:
        atr_series = (enriched["atr"] / enriched["close"] * 100).tail(50)
        atr_rank = float((atr_series.rank(pct=True).iloc[-1])) if not pd.isna(atr_series.rank(pct=True).iloc[-1]) else 0.5
    else:
        atr_rank = 0.5

    if atr_rank >= 0.8:
        return "volatile"
    if adx >= 25 and atr_rank < 0.7:
        return "trending"
    return "ranging"


def compute_atr_pct_rank(enriched):
    """Compute ATR percentile rank (0-1) from enriched data."""
    if len(enriched) < 50 or "atr" not in enriched.columns or "close" not in enriched.columns:
        return 0.5
    atr_pct = (enriched["atr"] / enriched["close"] * 100).tail(50)
    rank = atr_pct.rank(pct=True)
    val = float(rank.iloc[-1]) if len(rank) > 0 else 0.5
    return val if not pd.isna(val) else 0.5


def apply_v2_filters(symbol, side, strategy, enriched, daily_bias_data, btc_mom_6):
    """
    Apply all V2 filters. Returns (passed: bool, strategy_override: str, tp_mult_scale: float, reason: str).

    Parameters:
        symbol: e.g. "BTCUSDT"
        side: "LONG" or "SHORT"
        strategy: "trend_following" or "mean_reversion"
        enriched: DataFrame from load_enriched()
        daily_bias_data: dict from load_daily_bias() or None
        btc_mom_6: float, BTC 6-bar momentum percentage
    """
    # ── Filter 1: Daily Bias ──
    if daily_bias_data is not None:
        daily_bias = daily_bias_data["bias"]
        rsi_daily = daily_bias_data["rsi"]

        # Compute 4h log return from enriched data for short-term momentum override.
        # A strong short-term drop signals a pullback worth trading as SHORT,
        # even when the daily bias is BULL. This prevents the system from being
        # structurally LONG-only in bull markets.
        log_ret_4h = 0.0
        if "ctx_log_ret_4h" in enriched.columns and len(enriched) > 0:
            val = enriched["ctx_log_ret_4h"].iloc[-1]
            log_ret_4h = float(val) if not pd.isna(val) else 0.0

        if strategy == "trend_following":
            if side == "LONG" and daily_bias == "BEAR":
                return False, strategy, 1.0, f"Daily bias BEAR rejects LONG trend_following"
            if side == "SHORT" and daily_bias == "BULL" and log_ret_4h >= -0.015:
                return False, strategy, 1.0, f"Daily bias BULL rejects SHORT trend_following (4h log_ret {log_ret_4h:.4f} >= -0.015)"
        elif strategy == "mean_reversion":
            if side == "SHORT" and daily_bias == "BULL" and rsi_daily < 70 and log_ret_4h >= -0.015:
                return False, strategy, 1.0, f"MR SHORT in BULL but RSI daily {rsi_daily:.0f} < 70 and 4h log_ret {log_ret_4h:.4f} >= -0.015"
            if side == "LONG" and daily_bias == "BEAR" and rsi_daily > 30 and log_ret_4h <= 0.015:
                return False, strategy, 1.0, f"MR LONG in BEAR but RSI daily {rsi_daily:.0f} > 30 and 4h log_ret {log_ret_4h:.4f} <= 0.015"

    # ── Filter 2: Volume + RSI Momentum ──
    vol_ratio = compute_vol_ratio(enriched)
    if vol_ratio < 0.15:
        return False, strategy, 1.0, f"Volume ratio {vol_ratio:.2f} < 0.15"

    rsi_change = compute_rsi_momentum(enriched)
    if strategy == "trend_following":
        if side == "LONG" and rsi_change < -5:
            return False, strategy, 1.0, f"RSI momentum {rsi_change:.1f} < -5 for LONG trend_following"
        if side == "SHORT" and rsi_change > 5:
            return False, strategy, 1.0, f"RSI momentum {rsi_change:.1f} > 5 for SHORT trend_following"

    # ── Filter 3: Dynamic Strategy by Regime ──
    # Gated by REGIME_STRATEGY_OVERRIDE_ENABLED. When disabled, the strategy
    # from adaptive_scorer (resolved via get_strategy) passes through unchanged.
    # This ensures strategy resolution happens in one place (get_strategy).
    from portfolio_config import REGIME_STRATEGY_OVERRIDE_ENABLED
    if REGIME_STRATEGY_OVERRIDE_ENABLED:
        regime = compute_regime(enriched)
        try:
            from regime_router import classify_regime as _classify_hmm
            hmm_regime, _, _, _ = _classify_hmm(enriched, symbol=symbol)
            if hmm_regime == "TRENDING":
                regime = "trending"
            elif hmm_regime == "RANGING":
                regime = "ranging"
            elif hmm_regime == "VOLATILE":
                regime = "volatile"
        except Exception:
            pass  # keep rule-based fallback
        if regime == "trending":
            strategy = "trend_following"
        elif regime == "ranging":
            strategy = "mean_reversion"
        # volatile: keep original strategy

    # ── Filter 4: TP Scaling by ATR Percentile ──
    atr_pct_rank = compute_atr_pct_rank(enriched)
    tp_mult_scale = 1.0
    if atr_pct_rank > 0.7:
        tp_mult_scale = 1.3
    elif atr_pct_rank < 0.3:
        tp_mult_scale = 0.8

    # ── Filter 5: BTC Correlation Filter (XRP only) ──
    if symbol == "XRPUSDT":
        if side == "LONG" and btc_mom_6 < -2.5:
            return False, strategy, tp_mult_scale, f"BTC momentum {btc_mom_6:.1f}% < -2.5% rejects XRP LONG"
        if side == "SHORT" and btc_mom_6 > 2.5:
            return False, strategy, tp_mult_scale, f"BTC momentum {btc_mom_6:.1f}% > 2.5% rejects XRP SHORT"

    return True, strategy, tp_mult_scale, "V2 filters passed"
