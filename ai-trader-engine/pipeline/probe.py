"""Data Probing — multi-timeframe, multi-symbol scanner for pattern & regime detection."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .data import load_ohlcv
from .indicators import add_all


def _native(obj):
    """Recursively convert numpy/pandas scalars to native Python types."""
    if isinstance(obj, dict):
        return {k: _native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_native(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj) if not np.isnan(obj) else None
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Series):
        return obj.tolist()
    return obj


def _detect_regimes(df: pd.DataFrame) -> dict:
    """Classify each bar into a regime and return summary stats."""
    df = df.copy()
    # Trend: ADX > 25 = trending, <= 25 = ranging
    df["regime_trend"] = np.where(df["adx"] > 25, "trending", "ranging")
    # Vol: ATR > 75th percentile = high_vol
    atr_75 = df["atr"].quantile(0.75)
    df["regime_vol"] = np.where(df["atr"] >= atr_75, "high_vol", "low_vol")
    # Direction: ctx_bull
    df["regime_dir"] = np.where(df["ctx_bull"] == 1, "bull", "bear")
    # Combined regime
    df["regime"] = df["regime_trend"] + "_" + df["regime_vol"] + "_" + df["regime_dir"]

    regimes = {}
    for name, group in df.groupby("regime"):
        if len(group) < 20:
            continue
        fwd = group["close"].pct_change(1).shift(-1)
        regimes[name] = {
            "count": int(len(group)),
            "avg_return": round(float(fwd.mean()), 6),
            "return_std": round(float(fwd.std()), 6),
            "sharpe": round(float(fwd.mean() / (fwd.std() + 1e-9)), 3),
            "win_rate": round(float((fwd > 0).mean()), 3),
            "avg_rsi": round(float(group["rsi"].mean()), 1),
            "avg_adx": round(float(group["adx"].mean()), 1),
            "avg_atr": round(float(group["atr"].mean()), 2),
        }
    return regimes


def _find_mean_reversion_zones(df: pd.DataFrame) -> dict:
    """Find RSI extremes that historically bounce."""
    df = df.copy()
    df["fwd_ret_1"] = df["close"].pct_change(1).shift(-1)
    df["fwd_ret_5"] = df["close"].pct_change(5).shift(-5)

    # Oversold bounces
    oversold = df[df["rsi"] < 30].dropna(subset=["fwd_ret_1"])
    oversold5 = df[df["rsi"] < 30].dropna(subset=["fwd_ret_5"])

    # Overbought reversals
    overbought = df[df["rsi"] > 70].dropna(subset=["fwd_ret_1"])
    overbought5 = df[df["rsi"] > 70].dropna(subset=["fwd_ret_5"])

    return {
        "oversold_1d": {
            "count": int(len(oversold)),
            "win_rate": round(float((oversold["fwd_ret_1"] > 0).mean()), 3) if len(oversold) > 0 else None,
            "avg_return": round(float(oversold["fwd_ret_1"].mean()), 5) if len(oversold) > 0 else None,
        },
        "oversold_5d": {
            "count": int(len(oversold5)),
            "win_rate": round(float((oversold5["fwd_ret_5"] > 0).mean()), 3) if len(oversold5) > 0 else None,
            "avg_return": round(float(oversold5["fwd_ret_5"].mean()), 5) if len(oversold5) > 0 else None,
        },
        "overbought_1d": {
            "count": int(len(overbought)),
            "win_rate": round(float((overbought["fwd_ret_1"] > 0).mean()), 3) if len(overbought) > 0 else None,
            "avg_return": round(float(overbought["fwd_ret_1"].mean()), 5) if len(overbought) > 0 else None,
        },
        "overbought_5d": {
            "count": int(len(overbought5)),
            "win_rate": round(float((overbought5["fwd_ret_5"] > 0).mean()), 3) if len(overbought5) > 0 else None,
            "avg_return": round(float(overbought5["fwd_ret_5"].mean()), 5) if len(overbought5) > 0 else None,
        },
    }


def _find_momentum_bursts(df: pd.DataFrame) -> dict:
    """Find ADX surge + price follow-through patterns."""
    df = df.copy()
    df["adx_surge"] = df["adx"] > df["adx"].rolling(20).mean() * 1.3
    df["adx_high"] = df["adx"] > 30
    df["fwd_ret_3"] = df["close"].pct_change(3).shift(-3)

    bursts = df[df["adx_surge"] & df["adx_high"]].dropna(subset=["fwd_ret_3"])
    return {
        "count": int(len(bursts)),
        "win_rate": round(float((bursts["fwd_ret_3"] > 0).mean()), 3) if len(bursts) > 0 else None,
        "avg_return": round(float(bursts["fwd_ret_3"].mean()), 5) if len(bursts) > 0 else None,
        "avg_adx_at_burst": round(float(bursts["adx"].mean()), 1) if len(bursts) > 0 else None,
    }


def _rank_features(df: pd.DataFrame) -> dict:
    """Rank indicator predictive power by forward-return correlation."""
    df = df.copy()
    features = ["rsi", "atr", "adx", "ema_fast", "ema_slow", "log_ret", "ctx_bull"]
    horizons = [1, 5, 10]
    results = {}
    for h in horizons:
        col = f"fwd_ret_{h}"
        df[col] = df["close"].pct_change(h).shift(-h)
        results[f"horizon_{h}"] = {}
        for f in features:
            if f in df.columns:
                corr = df[[f, col]].dropna().corr().iloc[0, 1]
                results[f"horizon_{h}"][f] = round(abs(float(corr)), 4)
    return results


def _compute_summary(df: pd.DataFrame) -> dict:
    """High-level summary stats."""
    returns = df["close"].pct_change().dropna()
    return {
        "total_bars": int(len(df)),
        "date_range": {"start": str(df["timestamp"].min()), "end": str(df["timestamp"].max())},
        "volatility": {
            "annualized": round(float(returns.std() * np.sqrt(252 * 24 * 4)), 4),  # approx for 15m
            "avg_daily_range_pct": round(float((df["high"] - df["low"]).div(df["close"]).mean()), 4),
        },
        "trend": {
            "overall_return": round(float(df["close"].iloc[-1] / df["close"].iloc[0] - 1), 4),
            "in_bull_regime_pct": round(float(df["ctx_bull"].mean()), 3),
            "adx_avg": round(float(df["adx"].mean()), 1),
        },
        "price": {
            "start": round(float(df["close"].iloc[0]), 2),
            "end": round(float(df["close"].iloc[-1]), 2),
            "max": round(float(df["high"].max()), 2),
            "min": round(float(df["low"].min()), 2),
        },
    }


def probe_symbol(symbol: str, interval: str, start: str, end: str) -> dict:
    """Full probe of a single symbol/timeframe. Returns regimes, patterns, feature ranks, summary."""
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df)

    summary = _compute_summary(df)
    regimes = _detect_regimes(df)
    mr_zones = _find_mean_reversion_zones(df)
    momentum = _find_momentum_bursts(df)
    feature_ranks = _rank_features(df)

    # Suggest best-fitting strategy archetype based on probe data
    best_regime = max(regimes.items(), key=lambda x: x[1].get("sharpe", -99))[0] if regimes else None
    best_regime_sharpe = regimes[best_regime]["sharpe"] if best_regime else None

    mr_quality = mr_zones["oversold_5d"]
    mom_quality = momentum

    suggestion = "unknown"
    if mr_quality.get("win_rate", 0) and mr_quality["win_rate"] > 0.55 and mr_quality.get("count", 0) > 20:
        suggestion = "mean_reversion"
    elif mom_quality.get("win_rate", 0) and mom_quality["win_rate"] > 0.55 and mom_quality.get("count", 0) > 20:
        suggestion = "momentum"
    elif best_regime and "trending" in best_regime and best_regime_sharpe and best_regime_sharpe > 0.3:
        suggestion = "trend_following"

    return _native({
        "probe_type": "symbol",
        "symbol": symbol,
        "interval": interval,
        "summary": summary,
        "regimes": regimes,
        "mean_reversion_zones": mr_zones,
        "momentum_bursts": momentum,
        "feature_importance": feature_ranks,
        "suggested_archetype": suggestion,
        "best_regime": best_regime,
    })


def probe_compare(symbols: list[str], interval: str, start: str, end: str) -> dict:
    """Compare multiple symbols on the same timeframe."""
    results = {}
    for sym in symbols:
        try:
            results[sym] = probe_symbol(sym, interval, start, end)
        except Exception as e:
            results[sym] = {"error": str(e)}

    # Rank symbols by mean-reversion quality
    mr_scores = {
        sym: data.get("mean_reversion_zones", {}).get("oversold_5d", {}).get("win_rate", 0) or 0
        for sym, data in results.items()
        if "error" not in data
    }
    mom_scores = {
        sym: data.get("momentum_bursts", {}).get("win_rate", 0) or 0
        for sym, data in results.items()
        if "error" not in data
    }

    return _native({
        "probe_type": "compare",
        "symbols": symbols,
        "interval": interval,
        "best_mean_reversion": max(mr_scores, key=mr_scores.get) if mr_scores else None,
        "best_momentum": max(mom_scores, key=mom_scores.get) if mom_scores else None,
        "symbol_results": results,
    })


def probe_suggest(symbol: str, interval: str, start: str, end: str, archetype_hint: str | None = None) -> dict:
    """After probing, suggest concrete strategy parameters for the detected regime."""
    probe = probe_symbol(symbol, interval, start, end)
    summary = probe["summary"]
    regimes = probe["regimes"]
    mr = probe["mean_reversion_zones"]

    # Compute ATR-based SL/TP suggestions
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df)
    atr_mean = float(df["atr"].mean())
    atr_90 = float(df["atr"].quantile(0.90))
    close_mean = float(df["close"].mean())
    atr_pct = round(atr_mean / close_mean, 4)

    suggested = {
        "symbol": symbol,
        "interval": interval,
        "archetype": archetype_hint or probe["suggested_archetype"],
        "parameters": {
            "rsi_threshold": 30 if (mr.get("oversold_5d", {}).get("win_rate", 0) or 0) > 0.55 else 35,
            "adx_min": 25,
            "sl_mult": round(max(1.0, atr_90 / atr_mean * 0.8), 2),
            "tp_mult": round(max(1.5, atr_90 / atr_mean * 1.5), 2),
            "fast": 10,
            "slow": 30,
        },
        "risk_notes": [
            f"Average ATR = {round(atr_mean, 2)} ({atr_pct*100:.2f}% of price)",
            f"90th pct ATR = {round(atr_90, 2)} — use for extreme stop sizing",
        ],
        "regime_context": {
            "dominant_regime": probe.get("best_regime"),
            "bull_pct": summary["trend"]["in_bull_regime_pct"],
        },
    }
    return _native(suggested)
