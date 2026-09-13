#!/usr/bin/env python3
"""
VLTHR Backtest Runner — Control vs Treatment A/B
=================================================
Standalone bar-by-bar replay of the pipeline logic over historical enriched data.
No DB required — all portfolio state tracked in memory.

Modes:
  control           — Current pipeline (V7 veto in scanner + reprice + gate)
  treatment_a_phase1 — V7 veto moved to risk gate as sizing rule, equity fix
  treatment_b_phase2 — Signal engine as pure function, intent-vs-outcome logging

Usage:
  cd /path/to/DEVOPS/backtest
  python3 backtest_runner.py --mode control --start 2026-01-01 --end 2026-06-30
  python3 backtest_runner.py --mode treatment_a_phase1 --start 2026-01-01 --end 2026-06-30
  python3 backtest_runner.py --mode treatment_b_phase2 --start 2026-01-01 --end 2026-06-30
  python3 backtest_runner.py --all --start 2026-01-01 --end 2026-06-30
"""
import sys
import os
import argparse
import bisect
import csv
import glob
import json
import random
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

# ── PATH SETUP ──────────────────────────────────────────────────────────────
_BACKTEST_DIR = Path(__file__).resolve().parent
_DEVOPS = _BACKTEST_DIR.parent
_ENGINE = _DEVOPS / "pipeline" / "engine"
_DEPARTMENTS = _DEVOPS / "departments" / "strategy_research" / "BACKTESTER"

sys.path.insert(0, str(_ENGINE))
sys.path.insert(0, str(_DEPARTMENTS / "scripts" / "common"))
sys.path.insert(0, str(_DEPARTMENTS / "strategy" / "signals"))

# ── IMPORTS FROM PIPELINE ───────────────────────────────────────────────────
from portfolio_config import (
    SYMBOLS, DISABLED_SYMBOLS, DQS_THRESHOLDS, DQS_VETO_THRESHOLD,
    KELLY_V7_BANDS, MR_BOOST, PORTFOLIO, RISK_TIERS,
    get_risk_tier, get_sl_tp, get_sl_tp_pct, get_symbol_params,
    cap_position_size, get_kelly_v7, get_max_trade_hours,
    QTY_STEP, MMR, TAKER_FEE, MIN_NOTIONAL,
    REGIME_MIN_DQS, REGIME_SL_TP_MULTIPLIERS, MIN_RR_FLOOR,
    CAPITAL_PER_SYMBOL, SYMBOL_ALLOC_CAP,
    SL_TIME_DECAY_RATE, REGIME_STRATEGY_MIN_DQS,
)
from adaptive_scorer import score_signal_adaptive
from v2_filters import load_daily_bias, get_btc_momentum, apply_v2_filters, compute_regime
from patterns import detect_all_patterns

warnings.filterwarnings("ignore", category=UserWarning)

# ── SCAN CACHE ──────────────────────────────────────────────────────────────
# Scan results don't depend on mode/latency/gaps/downtime — only on symbol+ts.
# Cache them to avoid recomputing across stress test runs (15x speedup).
_SCAN_MISS = object()
_SCAN_CACHE: Dict[Tuple[str, pd.Timestamp], Optional[dict]] = {}
_DQ_CACHE: Dict[Tuple[str, pd.Timestamp], dict] = {}

# ── CALIBRATION GATE (matches live Step 4 gate) ─────────────────────────────
_CAL_DATA = None

def _load_calibration():
    global _CAL_DATA
    if _CAL_DATA is None:
        cal_path = _ENGINE / "calibration.json"
        _CAL_DATA = json.loads(cal_path.read_text())
    return _CAL_DATA

def _lookup_calibrated_prob_bt(dqs, symbol):
    """Simplified calibration lookup for backtest (no DB needed).
    Uses calibration.json symbol_curves. Returns (win_prob, sample_size, fallback_level)."""
    cal = _load_calibration()
    gates = cal.get("calibration_gates", {})
    min_samples = gates.get("min_sample_size_to_enforce_gate", 5)
    dqs_bucket = (int(dqs) // 5) * 5

    symbol_curves = cal.get("calibration_curve", {}).get("symbol_curves", {})
    if symbol in symbol_curves:
        curve = symbol_curves[symbol]
        n = curve.get("n_samples_used", 0)
        if n >= min_samples:
            bps = curve["dqs_breakpoints"]
            probs = curve["win_probabilities"]
            for i, bp in enumerate(bps):
                if dqs_bucket <= bp:
                    return probs[i], n, "symbol_curve"
            return probs[-1], n, "symbol_curve"

    global_curve = cal.get("calibration_curve", {})
    bps = global_curve.get("dqs_breakpoints", [])
    probs = global_curve.get("win_probabilities", [])
    n = global_curve.get("n_samples_used", 0)
    if bps and probs and n >= min_samples:
        for i, bp in enumerate(bps):
            if dqs_bucket <= bp:
                return probs[i], n, "global_curve"
        return probs[-1], n, "global_curve"

    return 0.5, 0, "prior"

def _get_calibration_gate_threshold(symbol):
    """Return per-symbol calibration gate threshold."""
    cal = _load_calibration()
    gates = cal.get("calibration_gates", {})
    return gates.get("per_symbol_overrides", {}).get(symbol,
           gates.get("min_calibrated_prob_to_open", 0.48))


def _extract_market_state(enriched: pd.DataFrame, atr_window: int = 96) -> dict:
    """Extract full market state from the last bar of an enriched slice.
    Used for logging at signal time, entry time, and exit time."""
    if enriched is None or len(enriched) == 0:
        return {}
    row = enriched.iloc[-1]
    atr = float(row.get("atr", 0) or 0)
    close = float(row.get("close", 0) or 0)

    # ATR percentile rank (where is current ATR vs recent history)
    atr_rank = 0.0
    if atr_window > 1 and len(enriched) >= 20:
        recent_atr = enriched["atr"].iloc[-atr_window:] if len(enriched) >= atr_window else enriched["atr"]
        atr_vals = recent_atr.dropna()
        if len(atr_vals) > 1 and atr > 0:
            atr_rank = float((atr_vals <= atr).sum() / len(atr_vals))

    # ATR as % of price
    atr_pct = (atr / close * 100) if close > 0 else 0.0

    # Efficiency ratio (price change / sum of bar-to-bar moves)
    er = 0.0
    if len(enriched) >= 20:
        period = enriched["close"].iloc[-20:]
        net_move = abs(period.iloc[-1] - period.iloc[0])
        total_move = period.diff().abs().sum()
        er = float(net_move / total_move) if total_move > 0 else 0.0

    return {
        "rsi": float(row.get("rsi", 0) or 0),
        "adx": float(row.get("adx", 0) or 0),
        "atr": atr,
        "atr_pct": atr_pct,
        "atr_rank": atr_rank,
        "volume": float(row.get("volume", 0) or 0),
        "turnover": float(row.get("turnover", 0) or 0),
        "oi": float(row.get("open_interest", 0) or 0),
        "funding_rate": float(row.get("funding_rate", 0) or 0),
        "buy_ratio": float(row.get("buyRatio", 0) or 0),
        "sell_ratio": float(row.get("sellRatio", 0) or 0),
        "bull_4h": int(row.get("bull_4h", 0) or 0),
        "ctx_adx": float(row.get("ctx_adx", 0) or 0),
        "ctx_log_ret_4h": float(row.get("ctx_log_ret_4h", 0) or 0),
        "session": str(row.get("session", "") or ""),
        "efficiency_ratio": er,
        "close": close,
    }


def _extract_pattern_fields(enriched: pd.DataFrame) -> dict:
    """Extract Phase 1 pattern fields from the last bar of enriched slice."""
    if enriched is None or len(enriched) == 0:
        return {}
    row = enriched.iloc[-1]
    return {
        "pat_wedge_detected": bool(row.get("pat_wedge_detected", False)),
        "pat_wedge_type": str(row.get("pat_wedge_type", "") or ""),
        "pat_wedge_strength": float(row.get("pat_wedge_strength", 0) or 0),
        "pat_asc_triangle_detected": bool(row.get("pat_asc_triangle_detected", False)),
        "pat_desc_triangle_detected": bool(row.get("pat_desc_triangle_detected", False)),
        "pat_channel_detected": bool(row.get("pat_channel_detected", False)),
        "pat_channel_type": str(row.get("pat_channel_type", "") or ""),
        "pat_trend_break_detected": bool(row.get("pat_trend_break_detected", False)),
        "pat_trend_break_direction": str(row.get("pat_trend_break_direction", "") or ""),
        "pat_trend_break_retest": bool(row.get("pat_trend_break_retest", False)),
        "pat_sr_level_count": int(row.get("pat_sr_level_count", 0) or 0),
        "pat_near_support": bool(row.get("pat_near_support", False)),
        "pat_near_resistance": bool(row.get("pat_near_resistance", False)),
        "pat_sr_recency_weighted_strength": float(row.get("pat_sr_recency_weighted_strength", 0) or 0),
        "pat_rising_support_detected": bool(row.get("pat_rising_support_detected", False)),
        "pat_falling_resistance_detected": bool(row.get("pat_falling_resistance_detected", False)),
        "pat_bos_detected": bool(row.get("pat_bos_detected", False)),
        "pat_bos_direction": str(row.get("pat_bos_direction", "") or ""),
        "pat_choch_detected": bool(row.get("pat_choch_detected", False)),
        "pat_choch_direction": str(row.get("pat_choch_direction", "") or ""),
    }


def _extract_signal_breakdown(breakdown: dict) -> dict:
    """Extract signal breakdown fields from the adaptive_scorer breakdown dict."""
    if not breakdown:
        return {}
    return {
        "tech_raw": float(breakdown.get("tech_raw", 0) or 0),
        "struct_raw": float(breakdown.get("struct_raw", 0) or 0),
        "ctx_raw": float(breakdown.get("ctx_raw", 0) or 0),
        "contra_penalty": float(breakdown.get("contra_penalty", 0) or 0),
        "direction": str(breakdown.get("direction", "") or ""),
        "calibrated_prob_signal": float(breakdown.get("calibrated_prob", 0) or 0),
    }


def _extract_hmm_data(symbol: str, enriched: pd.DataFrame) -> dict:
    """Extract HMM confidence and raw regime for logging."""
    try:
        from regime_hmm import predict_regime, _REGIME_CACHE
        # Check cache first (fast path)
        if symbol in _REGIME_CACHE and len(_REGIME_CACHE[symbol]) > 0:
            ts = enriched.iloc[-1].get("timestamp") if "timestamp" in enriched.columns else None
            if ts is not None and ts in _REGIME_CACHE[symbol]:
                regime, conf = _REGIME_CACHE[symbol][ts]
                return {"hmm_confidence": float(conf), "hmm_regime_raw": str(regime)}
        # Live prediction
        regime, conf = predict_regime(symbol, enriched)
        return {"hmm_confidence": float(conf), "hmm_regime_raw": str(regime)}
    except Exception:
        return {"hmm_confidence": 0.0, "hmm_regime_raw": ""}


def _extract_portfolio_state(portfolio: 'PortfolioState', fix_equity: bool = True) -> dict:
    """Extract portfolio state for logging at trade entry."""
    equity = portfolio.get_true_equity(fix_equity)
    open_risk = portfolio.get_total_open_risk()
    max_risk = equity * (PORTFOLIO["max_portfolio_risk_pct"] / 100) if equity > 0 else 0
    return {
        "equity_at_entry": round(equity, 2),
        "open_trades_at_entry": len(portfolio.open_trades),
        "open_risk_at_entry": round(open_risk, 2),
        "margin_used_at_entry": round(portfolio.margin_used, 2),
        "risk_budget_used_pct": round((open_risk / max_risk * 100) if max_risk > 0 else 0, 1),
    }

# ── PHASE 2: CROWDING DETECTION ─────────────────────────────────────────────
# Correlation clusters: if 2+ symbols in same cluster fire same direction
# with DQS within 5 points, apply 50% Kelly reduction (not veto — risk management)
CORRELATION_CLUSTERS = [
    {"symbols": {"BTCUSDT", "ETHUSDT", "SOLUSDT"}, "min_fire": 2},
    {"symbols": {"BTCUSDT", "ETHUSDT", "XRPUSDT"}, "min_fire": 2},
]
CROWDING_DQS_RANGE = 5  # max DQS spread within cluster to trigger crowding
CROWDING_KELLY_REDUCTION = 0.50  # reduce Kelly by 50% when crowded

# ── PHASE 2: DATA QUALITY SCORING ───────────────────────────────────────────
# Gate signals on data freshness, completeness, and outlier presence
DATA_QUALITY_MIN_SCORE = 60.0  # reject signal if data quality < 60


def _compute_data_quality(enriched: pd.DataFrame, ts: pd.Timestamp) -> dict:
    """Compute data quality score from enriched slice.
    Returns {score, freshness, completeness, outliers, gated}."""
    if enriched is None or len(enriched) == 0:
        return {"score": 0.0, "freshness": 0, "completeness": 0, "outliers": 0, "gated": True}

    # Freshness: how recent is the last bar relative to ts
    last_ts = pd.to_datetime(enriched["timestamp"].iloc[-1], utc=True)
    staleness_min = (ts - last_ts).total_seconds() / 60
    freshness = max(0, 100 - (staleness_min / 15 * 100))  # 15min bar cadence

    # Completeness: gap count in last 96 bars (1 day)
    if len(enriched) >= 2:
        ts_series = pd.to_datetime(enriched["timestamp"], utc=True)
        gaps = ts_series.diff().dt.total_seconds() / 60
        gap_count = (gaps > 20).sum()  # gaps > 20 min when 15m expected
        completeness = max(0, 100 - int(gap_count) * 5)
    else:
        completeness = 50.0

    # Outlier: bars where |return| > 5 sigma in last 96 bars
    if "close" in enriched.columns and len(enriched) >= 20:
        rets = enriched["close"].pct_change()
        rolling_std = rets.rolling(96, min_periods=20).std()
        z = (rets - rets.rolling(96, min_periods=20).mean()) / rolling_std.replace(0, np.nan)
        outlier_count = int((z.abs() > 5).sum())
        outlier_score = max(0, 100 - outlier_count * 10)
    else:
        outlier_score = 100.0

    score = float(freshness) * 0.40 + float(completeness) * 0.40 + float(outlier_score) * 0.20
    return {
        "score": round(score, 2),
        "freshness": round(float(freshness), 2),
        "completeness": round(float(completeness), 2),
        "outliers": round(float(outlier_score), 2),
        "gated": bool(score < DATA_QUALITY_MIN_SCORE),
    }


def _detect_crowding(signals: list) -> set:
    """Detect crowded symbols from signals at the same scan bar.
    Returns set of crowded symbol names."""
    if len(signals) < 2:
        return set()

    trackable = [s for s in signals if s.get("dqs", 0) >= 50]
    if len(trackable) < 2:
        return set()

    crowded = set()
    for cluster in CORRELATION_CLUSTERS:
        cluster_sigs = [s for s in trackable if s["symbol"] in cluster["symbols"]]
        if len(cluster_sigs) < cluster["min_fire"]:
            continue

        for side in ["LONG", "SHORT"]:
            side_sigs = [s for s in cluster_sigs if s.get("side", "LONG") == side]
            if len(side_sigs) >= cluster["min_fire"]:
                dqs_vals = [s.get("dqs", 0) for s in side_sigs]
                dqs_range = max(dqs_vals) - min(dqs_vals) if dqs_vals else 0
                if dqs_range <= CROWDING_DQS_RANGE:
                    for s in side_sigs:
                        crowded.add(s["symbol"])

    return crowded


# ── CONSTANTS ───────────────────────────────────────────────────────────────
DATA_ROOT = _DEVOPS / "data" / "bybit"
INIT_BALANCE = 10000.0
LEVERAGE = 4
CSV_COLUMNS = [
    # ── Identity ──
    "symbol", "side", "entry_date", "entry_price", "sl_price", "tp_price",
    "dqs_score", "regime", "strategy", "approved", "reject_reason", "promoted",
    "exit_date", "exit_price", "exit_reason", "hours_held",
    "pnl_usd", "pnl_pct", "risk_usd", "kelly_fraction", "win", "notes",
    "intent", "outcome", "crowded", "data_quality",
    "sl_original", "atr_at_entry", "calibration_prob", "calibration_level",
    # ── Market State at Entry ──
    "rsi_entry", "adx_entry", "atr_pct_entry", "volume_entry", "turnover_entry",
    "oi_entry", "funding_rate_entry", "buy_ratio_entry", "sell_ratio_entry",
    "bull_4h_entry", "ctx_adx_entry", "ctx_log_ret_4h_entry", "session",
    "efficiency_ratio_entry", "atr_rank_entry",
    # ── Market State at Exit ──
    "rsi_exit", "adx_exit", "atr_exit", "atr_pct_exit", "volume_exit",
    "regime_exit", "close_exit",
    # ── MFE / MAE ──
    "mfe_price", "mfe_atr", "mfe_pct_of_tp", "mae_price", "mae_atr", "mae_pct_of_sl",
    "bars_to_mfe", "bars_to_mae", "time_in_profit_bars", "time_in_loss_bars",
    # ── Signal Breakdown ──
    "tech_raw", "struct_raw", "ctx_raw", "contra_penalty",
    "direction", "calibrated_prob_signal",
    # ── Portfolio State at Entry ──
    "equity_at_entry", "open_trades_at_entry", "open_risk_at_entry",
    "margin_used_at_entry", "risk_budget_used_pct",
    # ── HMM Data ──
    "hmm_confidence", "hmm_regime_raw",
    # ── Fees ──
    "fees_usd", "gross_pnl_usd",
    # ── Position Sizing ──
    "notional", "qty", "risk_tier", "kelly_band", "sl_dist_atr",
    "tp_dist_atr", "rr_ratio",
    # ── Phase 1 Pattern fields ──
    "pat_wedge_detected", "pat_wedge_type", "pat_wedge_strength",
    "pat_asc_triangle_detected", "pat_desc_triangle_detected",
    "pat_channel_detected", "pat_channel_type",
    "pat_trend_break_detected", "pat_trend_break_direction", "pat_trend_break_retest",
    "pat_sr_level_count", "pat_near_support", "pat_near_resistance",
    "pat_sr_recency_weighted_strength",
    "pat_rising_support_detected", "pat_falling_resistance_detected",
    "pat_bos_detected", "pat_bos_direction",
    "pat_choch_detected", "pat_choch_direction",
]

# ── DATA LOADING ────────────────────────────────────────────────────────────

def _add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=period, min_periods=period).mean()
    return df


def _add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def _add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high_diff = df["high"].diff()
    low_diff = (-df["low"]).diff()
    dm_plus = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    dm_minus = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    tr = df["high"] - df["low"]
    tr = tr.combine_first((df["high"] - df["close"].shift()).abs())
    tr = tr.combine_first((df["low"] - df["close"].shift()).abs())
    atr = tr.rolling(window=period, min_periods=period).mean()
    di_plus = (dm_plus.rolling(window=period).mean() / atr) * 100
    di_minus = (dm_minus.rolling(window=period).mean() / atr) * 100
    dx = (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan) * 100
    df["adx"] = dx.rolling(window=period).mean()
    return df


def _classify_session(ts) -> str:
    """Classify UTC timestamp into trading session.

    Delegates to portfolio_config.classify_session for single source of truth.
    Falls back to local definition if portfolio_config is not importable.
    """
    try:
        from portfolio_config import classify_session
        return classify_session(ts)
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


def load_symbol_enriched(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Load enriched 15m parquet + 4h context, compute indicators."""
    enriched_dir = DATA_ROOT / symbol / "enriched" / "15m"
    if not enriched_dir.exists():
        raise FileNotFoundError(f"No enriched data for {symbol}")

    files = sorted(enriched_dir.rglob("*.parquet"))
    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            if not df.empty and "timestamp" in df.columns:
                dfs.append(df)
        except Exception:
            continue
    if not dfs:
        raise FileNotFoundError(f"No readable parquet for {symbol}")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    # Add indicators
    df = _add_atr(df, 14)
    df = _add_rsi(df, 14)
    df = _add_adx(df, 14)
    df["session"] = df["timestamp"].apply(_classify_session)

    # Load 4h context
    ctx_dir = DATA_ROOT / symbol / "enriched" / "4h"
    if not ctx_dir.exists():
        ctx_dir = DATA_ROOT / symbol / "4h"
    if ctx_dir.exists():
        ctx_files = sorted(ctx_dir.rglob("*.parquet"))
        if ctx_files:
            ctx_dfs = []
            for f in ctx_files:
                try:
                    ctx_dfs.append(pd.read_parquet(f))
                except Exception:
                    continue
            if ctx_dfs:
                ctx_df = pd.concat(ctx_dfs, ignore_index=True)
                ctx_df["timestamp"] = pd.to_datetime(ctx_df["timestamp"], utc=True)
                ctx_df = ctx_df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
                ctx_df = _add_adx(ctx_df, 14)
                ctx_df["ctx_log_ret_4h"] = np.log(ctx_df["close"] / ctx_df["close"].shift(1))
                # EMA trend
                ctx_df["ema_8"] = ctx_df["close"].ewm(span=8, adjust=False).mean()
                ctx_df["ema_21"] = ctx_df["close"].ewm(span=21, adjust=False).mean()
                ctx_df["bull_4h"] = (ctx_df["ema_8"] > ctx_df["ema_21"]).astype(int)

                df["timestamp"] = df["timestamp"].astype("datetime64[ns, UTC]")
                ctx_df["timestamp"] = ctx_df["timestamp"].astype("datetime64[ns, UTC]")
                df = pd.merge_asof(
                    df.sort_values("timestamp"),
                    ctx_df[["timestamp", "adx", "ctx_log_ret_4h", "bull_4h"]].rename(
                        columns={"adx": "ctx_adx"}
                    ).sort_values("timestamp"),
                    on="timestamp",
                    direction="backward",
                ).sort_values("timestamp").reset_index(drop=True)

    # Fill NaN context
    if "ctx_adx" not in df.columns:
        df["ctx_adx"] = 0.0
    if "ctx_log_ret_4h" not in df.columns:
        df["ctx_log_ret_4h"] = 0.0
    if "bull_4h" not in df.columns:
        df["bull_4h"] = 0

    df["ctx_adx"] = df["ctx_adx"].fillna(0)
    df["ctx_log_ret_4h"] = df["ctx_log_ret_4h"].fillna(0)
    df["bull_4h"] = df["bull_4h"].fillna(0)

    # Filter to date range
    start_ts = pd.to_datetime(start, utc=True)
    end_ts = pd.to_datetime(end, utc=True)
    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].reset_index(drop=True)

    return df


# ── PORTFOLIO STATE (in-memory) ─────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    sl_price: float
    tp_price: float
    qty: float
    entry_time: pd.Timestamp
    dqs: float
    regime: str
    strategy: str
    kelly_fraction: float
    risk_usd: float
    status: str = "OPEN"  # OPEN, CLOSED, EXPIRED
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_time: Optional[pd.Timestamp] = None
    hours_held: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    crowded: bool = False
    data_quality: float = 0.0
    sl_original: float = 0.0
    atr_at_entry: float = 0.0
    calibration_prob: float = 0.0
    calibration_level: str = ""
    # ── Market state at entry ──
    rsi_entry: float = 0.0
    adx_entry: float = 0.0
    atr_pct_entry: float = 0.0
    volume_entry: float = 0.0
    turnover_entry: float = 0.0
    oi_entry: float = 0.0
    funding_rate_entry: float = 0.0
    buy_ratio_entry: float = 0.0
    sell_ratio_entry: float = 0.0
    bull_4h_entry: int = 0
    ctx_adx_entry: float = 0.0
    ctx_log_ret_4h_entry: float = 0.0
    session: str = ""
    efficiency_ratio_entry: float = 0.0
    atr_rank_entry: float = 0.0
    # ── Signal breakdown ──
    tech_raw: float = 0.0
    struct_raw: float = 0.0
    ctx_raw: float = 0.0
    contra_penalty: float = 0.0
    direction: str = ""
    calibrated_prob_signal: float = 0.0
    # ── Portfolio state at entry ──
    equity_at_entry: float = 0.0
    open_trades_at_entry: int = 0
    open_risk_at_entry: float = 0.0
    margin_used_at_entry: float = 0.0
    risk_budget_used_pct: float = 0.0
    # ── HMM ──
    hmm_confidence: float = 0.0
    hmm_regime_raw: str = ""
    # ── Position sizing ──
    notional: float = 0.0
    risk_tier: str = ""
    kelly_band: str = ""
    sl_dist_atr: float = 0.0
    tp_dist_atr: float = 0.0
    rr_ratio: float = 0.0
    # ── MFE / MAE (updated during trade lifecycle) ──
    mfe_price: float = 0.0
    mfe_atr: float = 0.0
    mfe_pct_of_tp: float = 0.0
    mae_price: float = 0.0
    mae_atr: float = 0.0
    mae_pct_of_sl: float = 0.0
    bars_to_mfe: int = 0
    bars_to_mae: int = 0
    time_in_profit_bars: int = 0
    time_in_loss_bars: int = 0
    # ── Market state at exit ──
    rsi_exit: float = 0.0
    adx_exit: float = 0.0
    atr_exit: float = 0.0
    atr_pct_exit: float = 0.0
    volume_exit: float = 0.0
    regime_exit: str = ""
    close_exit: float = 0.0
    # ── Fees ──
    fees_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    # ── Bar counter for MFE/MAE tracking ──
    _bars_open: int = 0
    # ── Phase 1 Pattern fields ──
    pat_wedge_detected: bool = False
    pat_wedge_type: str = ""
    pat_wedge_strength: float = 0.0
    pat_asc_triangle_detected: bool = False
    pat_desc_triangle_detected: bool = False
    pat_channel_detected: bool = False
    pat_channel_type: str = ""
    pat_trend_break_detected: bool = False
    pat_trend_break_direction: str = ""
    pat_trend_break_retest: bool = False
    pat_sr_level_count: int = 0
    pat_near_support: bool = False
    pat_near_resistance: bool = False
    pat_sr_recency_weighted_strength: float = 0.0
    pat_rising_support_detected: bool = False
    pat_falling_resistance_detected: bool = False
    pat_bos_detected: bool = False
    pat_bos_direction: str = ""
    pat_choch_detected: bool = False
    pat_choch_direction: str = ""


@dataclass
class PendingTrade:
    symbol: str
    side: str
    entry_price: float
    sl_price: float
    tp_price: float
    qty: float
    signal_bar_utc: pd.Timestamp
    dqs: float
    regime: str
    strategy: str
    kelly_fraction: float
    risk_usd: float
    crowded: bool = False
    data_quality: float = 0.0
    # ── Market state at signal time ──
    rsi_entry: float = 0.0
    adx_entry: float = 0.0
    atr_pct_entry: float = 0.0
    volume_entry: float = 0.0
    turnover_entry: float = 0.0
    oi_entry: float = 0.0
    funding_rate_entry: float = 0.0
    buy_ratio_entry: float = 0.0
    sell_ratio_entry: float = 0.0
    bull_4h_entry: int = 0
    ctx_adx_entry: float = 0.0
    ctx_log_ret_4h_entry: float = 0.0
    session: str = ""
    efficiency_ratio_entry: float = 0.0
    atr_rank_entry: float = 0.0
    # ── Signal breakdown ──
    tech_raw: float = 0.0
    struct_raw: float = 0.0
    ctx_raw: float = 0.0
    contra_penalty: float = 0.0
    direction: str = ""
    calibrated_prob_signal: float = 0.0
    # ── HMM ──
    hmm_confidence: float = 0.0
    hmm_regime_raw: str = ""
    # ── Position sizing ──
    notional: float = 0.0
    risk_tier: str = ""
    kelly_band: str = ""
    sl_dist_atr: float = 0.0
    tp_dist_atr: float = 0.0
    rr_ratio: float = 0.0
    # ── Phase 1 Pattern fields ──
    pat_wedge_detected: bool = False
    pat_wedge_type: str = ""
    pat_wedge_strength: float = 0.0
    pat_asc_triangle_detected: bool = False
    pat_desc_triangle_detected: bool = False
    pat_channel_detected: bool = False
    pat_channel_type: str = ""
    pat_trend_break_detected: bool = False
    pat_trend_break_direction: str = ""
    pat_trend_break_retest: bool = False
    pat_sr_level_count: int = 0
    pat_near_support: bool = False
    pat_near_resistance: bool = False
    pat_sr_recency_weighted_strength: float = 0.0
    pat_rising_support_detected: bool = False
    pat_falling_resistance_detected: bool = False
    pat_bos_detected: bool = False
    pat_bos_direction: str = ""
    pat_choch_detected: bool = False
    pat_choch_direction: str = ""


@dataclass
class SignalRecord:
    """Complete record for CSV output — tracks signal from birth to death."""
    symbol: str
    side: str
    entry_date: str
    entry_price: float
    sl_price: float
    tp_price: float
    dqs_score: float
    regime: str
    strategy: str
    approved: bool
    reject_reason: str
    promoted: bool
    exit_date: str
    exit_price: float
    exit_reason: str
    hours_held: float
    pnl_usd: float
    pnl_pct: float
    risk_usd: float
    kelly_fraction: float
    win: bool
    notes: str
    intent: str = ""
    outcome: str = ""
    crowded: bool = False
    data_quality: float = 0.0
    sl_original: float = 0.0
    atr_at_entry: float = 0.0
    calibration_prob: float = 0.0
    calibration_level: str = ""
    # ── Market state at entry ──
    rsi_entry: float = 0.0
    adx_entry: float = 0.0
    atr_pct_entry: float = 0.0
    volume_entry: float = 0.0
    turnover_entry: float = 0.0
    oi_entry: float = 0.0
    funding_rate_entry: float = 0.0
    buy_ratio_entry: float = 0.0
    sell_ratio_entry: float = 0.0
    bull_4h_entry: int = 0
    ctx_adx_entry: float = 0.0
    ctx_log_ret_4h_entry: float = 0.0
    session: str = ""
    efficiency_ratio_entry: float = 0.0
    atr_rank_entry: float = 0.0
    # ── Market state at exit ──
    rsi_exit: float = 0.0
    adx_exit: float = 0.0
    atr_exit: float = 0.0
    atr_pct_exit: float = 0.0
    volume_exit: float = 0.0
    regime_exit: str = ""
    close_exit: float = 0.0
    # ── MFE / MAE ──
    mfe_price: float = 0.0
    mfe_atr: float = 0.0
    mfe_pct_of_tp: float = 0.0
    mae_price: float = 0.0
    mae_atr: float = 0.0
    mae_pct_of_sl: float = 0.0
    bars_to_mfe: int = 0
    bars_to_mae: int = 0
    time_in_profit_bars: int = 0
    time_in_loss_bars: int = 0
    # ── Signal breakdown ──
    tech_raw: float = 0.0
    struct_raw: float = 0.0
    ctx_raw: float = 0.0
    contra_penalty: float = 0.0
    direction: str = ""
    calibrated_prob_signal: float = 0.0
    # ── Portfolio state at entry ──
    equity_at_entry: float = 0.0
    open_trades_at_entry: int = 0
    open_risk_at_entry: float = 0.0
    margin_used_at_entry: float = 0.0
    risk_budget_used_pct: float = 0.0
    # ── HMM ──
    hmm_confidence: float = 0.0
    hmm_regime_raw: str = ""
    # ── Fees ──
    fees_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    # ── Position sizing ──
    notional: float = 0.0
    qty: float = 0.0
    risk_tier: str = ""
    kelly_band: str = ""
    sl_dist_atr: float = 0.0
    tp_dist_atr: float = 0.0
    rr_ratio: float = 0.0
    # ── Phase 1 Pattern fields (log only, no scoring impact) ──
    pat_wedge_detected: bool = False
    pat_wedge_type: str = ""
    pat_wedge_strength: float = 0.0
    pat_asc_triangle_detected: bool = False
    pat_desc_triangle_detected: bool = False
    pat_channel_detected: bool = False
    pat_channel_type: str = ""
    pat_trend_break_detected: bool = False
    pat_trend_break_direction: str = ""
    pat_trend_break_retest: bool = False
    pat_sr_level_count: int = 0
    pat_near_support: bool = False
    pat_near_resistance: bool = False
    pat_sr_recency_weighted_strength: float = 0.0
    pat_rising_support_detected: bool = False
    pat_falling_resistance_detected: bool = False
    pat_bos_detected: bool = False
    pat_bos_direction: str = ""
    pat_choch_detected: bool = False
    pat_choch_direction: str = ""


class PortfolioState:
    """In-memory portfolio state tracker."""
    def __init__(self, initial_balance: float = INIT_BALANCE):
        self.balance = initial_balance
        self.equity = initial_balance
        self.margin_used = 0.0
        self.open_trades: List[Trade] = []
        self.pending_trades: List[PendingTrade] = []
        self.daily_trade_count: Dict[str, int] = {}  # date_str -> count
        self.session_trade_count: Dict[str, int] = {}  # "date|session" -> count
        self.daily_realized_pnl: Dict[str, float] = {}  # date_str -> net PnL

    def get_active_symbols(self) -> List[str]:
        return [t.symbol for t in self.open_trades]

    def get_active_sides(self) -> List[str]:
        return [t.side.upper() for t in self.open_trades]

    def get_total_open_risk(self) -> float:
        return sum(t.risk_usd for t in self.open_trades)

    def get_true_equity(self, fix_equity: bool = False) -> float:
        if fix_equity:
            return self.equity + self.margin_used
        return self.equity

    def can_open_correlation(self, symbol: str) -> Tuple[bool, str]:
        active = self.get_active_symbols()
        if len(active) >= PORTFOLIO["max_active_trades"]:
            return False, f"max {PORTFOLIO['max_active_trades']} open positions"
        if symbol in active:
            return False, f"{symbol} already has open position"
        return True, ""

    def can_open_count(self, session: str, date_str: str) -> Tuple[bool, str]:
        if len(self.open_trades) >= PORTFOLIO["max_active_trades"]:
            return False, "max active trades"
        daily = self.daily_trade_count.get(date_str, 0)
        if daily >= PORTFOLIO["max_daily_trades"]:
            return False, "daily limit"
        sess_key = f"{date_str}|{session}"
        if session in PORTFOLIO.get("session_limits", {}):
            if self.session_trade_count.get(sess_key, 0) >= PORTFOLIO["session_limits"][session]:
                return False, f"session limit ({session})"
        return True, ""

    def can_open_directional(self, side: str) -> Tuple[bool, str]:
        sides = self.get_active_sides()
        same_side = sides.count(side.upper())
        if same_side >= PORTFOLIO.get("max_same_side", 3):
            return False, f"directional: {same_side} {side} open (max {PORTFOLIO['max_same_side']})"
        return True, ""

    def has_risk_capacity(self, new_risk: float, fix_equity: bool = False) -> Tuple[bool, str]:
        eq = self.get_true_equity(fix_equity)
        if eq <= 0:
            return False, "equity <= 0"
        current = self.get_total_open_risk()
        max_allowed = eq * (PORTFOLIO["max_portfolio_risk_pct"] / 100)
        if current + new_risk > max_allowed:
            return False, f"risk budget full ({current + new_risk:.2f} > {max_allowed:.2f})"
        return True, ""

    def can_open_daily_loss(self, date_str: str) -> Tuple[bool, str]:
        halt_pct = PORTFOLIO.get("daily_loss_halt_pct", 2.0)
        daily_pnl = self.daily_realized_pnl.get(date_str, 0.0)
        if self.balance > 0 and daily_pnl <= -(self.balance * halt_pct / 100.0):
            return False, f"DailyLossBreaker: {daily_pnl:.2f} <= -{halt_pct}% of balance"
        return True, ""

    def record_trade_open(self, session: str, date_str: str):
        self.daily_trade_count[date_str] = self.daily_trade_count.get(date_str, 0) + 1
        sess_key = f"{date_str}|{session}"
        self.session_trade_count[sess_key] = self.session_trade_count.get(sess_key, 0) + 1

    def close_trade(self, trade: Trade, exit_price: float, exit_reason: str,
                    exit_time: pd.Timestamp, fees: float = 0.0):
        is_long = trade.side.upper() == "LONG"
        gross = (exit_price - trade.entry_price) * trade.qty if is_long else (trade.entry_price - exit_price) * trade.qty
        net = gross - fees
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.exit_time = exit_time
        trade.pnl_usd = net
        trade.pnl_pct = (net / trade.risk_usd * 100) if trade.risk_usd > 0 else 0
        trade.hours_held = (exit_time - trade.entry_time).total_seconds() / 3600
        trade.status = "CLOSED"
        self.balance += net
        self.margin_used -= trade.entry_price * trade.qty / LEVERAGE
        if self.margin_used < 0:
            self.margin_used = 0
        if trade in self.open_trades:
            self.open_trades.remove(trade)
        date_str = exit_time.strftime("%Y-%m-%d")
        self.daily_realized_pnl[date_str] = self.daily_realized_pnl.get(date_str, 0.0) + net


# ── BACKTEST ENGINE ─────────────────────────────────────────────────────────

class BacktestEngine:
    """Bar-by-bar pipeline simulation."""

    def __init__(self, mode: str, start: str, end: str,
                 walk_forward: bool = False, verbose: bool = True,
                 latency_bars: int = 0, data_gap_prob: float = 0.0,
                 downtime_periods: list = None, seed: int = 42):
        self.mode = mode
        self.start = start
        self.end = end
        self.walk_forward = walk_forward
        self.verbose = verbose
        self.results: List[SignalRecord] = []
        self.portfolio = PortfolioState(INIT_BALANCE)

        # Live condition simulation
        self.latency_bars = latency_bars
        self.data_gap_prob = data_gap_prob
        self.downtime_periods = downtime_periods or []
        self.delayed_signals: List[Tuple[dict, pd.Timestamp, int]] = []
        self._rng = random.Random(seed)
        self._data_gap_bars: Dict[str, set] = {}  # symbol -> set of bar indices skipped

        # Load data for all symbols
        self.data: Dict[str, pd.DataFrame] = {}
        self._load_data()

        # Build timestamp -> row index lookup for each symbol (perf critical)
        self._bar_index: Dict[str, dict] = {}
        self._sorted_ts: Dict[str, list] = {}
        for sym, df in self.data.items():
            ts_list = df["timestamp"].tolist()
            self._bar_index[sym] = {ts: i for i, ts in enumerate(ts_list)}
            self._sorted_ts[sym] = sorted(ts_list)

        # Build unified timeline
        all_ts = set()
        for df in self.data.values():
            all_ts.update(df["timestamp"].tolist())
        self.timeline = sorted(all_ts)
        self.n_bars = len(self.timeline)

        # Pre-sort downtime periods for fast lookup
        self._downtime_sorted = sorted(self.downtime_periods, key=lambda p: p[0])
        self._downtime_starts = [p[0] for p in self._downtime_sorted]

        # Walk-forward split
        if walk_forward:
            split_idx = int(self.n_bars * 0.7)
            self.is_end = self.timeline[split_idx]
        else:
            self.is_end = None

    def _load_data(self):
        for sym in SYMBOLS:
            try:
                df = load_symbol_enriched(sym, self.start, self.end)
                if len(df) > 0:
                    # Phase 1: detect patterns (log only, no scoring impact)
                    try:
                        df = detect_all_patterns(df)
                    except Exception as e:
                        print(f"  [WARN] Pattern detection failed for {sym}: {e}")
                    self.data[sym] = df
                    # Pre-compute HMM regimes for this symbol (one Viterbi pass)
                    try:
                        from regime_hmm import precompute_regimes
                        df_indexed = df.set_index("timestamp") if "timestamp" in df.columns else df
                        precompute_regimes(sym, df_indexed)
                    except Exception:
                        pass
                    if self.verbose:
                        print(f"  Loaded {sym}: {len(df)} bars | {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
            except Exception as e:
                print(f"  [WARN] Failed to load {sym}: {e}")

    def _get_bar(self, symbol: str, ts: pd.Timestamp) -> Optional[pd.Series]:
        idx_map = self._bar_index.get(symbol)
        if idx_map is None:
            return None
        idx = idx_map.get(ts)
        if idx is None:
            return None
        return self.data[symbol].iloc[idx]

    def _get_enriched_slice(self, symbol: str, ts: pd.Timestamp) -> Optional[pd.DataFrame]:
        """Get enriched data up to and including ts (for DQS scoring).
        Returns last 200 rows only — all scoring functions need at most 97 rows."""
        idx_map = self._bar_index.get(symbol)
        if idx_map is None:
            return None
        idx = idx_map.get(ts)
        if idx is None:
            sorted_ts = self._sorted_ts.get(symbol)
            if sorted_ts is None:
                return None
            pos = bisect.bisect_right(sorted_ts, ts) - 1
            if pos < 0:
                return None
            idx = idx_map[sorted_ts[pos]]
        df = self.data[symbol]
        if idx < 50:
            return None
        start = max(0, idx - 200)
        return df.iloc[start:idx + 1]

    def _is_downtime(self, ts: pd.Timestamp) -> bool:
        """Check if timestamp falls within a downtime period (binary search)."""
        if not self._downtime_sorted:
            return False
        idx = bisect.bisect_right(self._downtime_starts, ts) - 1
        if idx < 0:
            return False
        return ts <= self._downtime_sorted[idx][1]

    def _compute_dynamic_sl(self, trade: Trade, live_price: float,
                            hours_open: float, current_atr: float) -> float:
        """Port of live computeDynamicSL from server.cjs.
        Returns updated SL price (or original if no adjustment)."""
        if trade.sl_original <= 0 or trade.entry_price <= 0:
            return trade.sl_price

        side = trade.side.upper()
        entry = trade.entry_price
        tp = trade.tp_price
        sl_original = trade.sl_original
        atr_entry = trade.atr_at_entry
        new_sl = sl_original

        # 1. Break-even stop: move SL to entry when PnL > 75% of TP distance
        tp_dist = abs(tp - entry) if tp > 0 else 0
        if tp_dist > 0:
            pnl_frac = (live_price - entry) / tp_dist if side == "LONG" else (entry - live_price) / tp_dist
            if pnl_frac > 0.75:
                new_sl = entry * 0.997 if side == "LONG" else entry * 1.003

        # 2. Time decay: tighten SL by SL_TIME_DECAY_RATE x ATR per hour after hour 3
        if atr_entry > 0 and hours_open > 3:
            tighten_amt = SL_TIME_DECAY_RATE * atr_entry * (hours_open - 3)
            if side == "LONG":
                decay_sl = max(entry, sl_original + tighten_amt)
                if decay_sl > new_sl:
                    new_sl = decay_sl
            else:
                decay_sl = min(entry, sl_original - tighten_amt)
                if decay_sl < new_sl:
                    new_sl = decay_sl

        # 3. ATR expansion: widen SL when live volatility > 1.5x entry ATR
        if atr_entry > 0 and current_atr > 1.5 * atr_entry:
            expansion_factor = current_atr / atr_entry
            if side == "LONG":
                expanded_sl = entry - (entry - sl_original) * expansion_factor
                if expanded_sl < new_sl:
                    new_sl = expanded_sl
            else:
                expanded_sl = entry + (sl_original - entry) * expansion_factor
                if expanded_sl > new_sl:
                    new_sl = expanded_sl

        return new_sl

    def _check_exits(self, ts: pd.Timestamp):
        """Check all open trades for SL/TP/time-exit using current bar.
        Matches live exit order: TIME_EXIT first, then SL before TP (conservative).
        Applies dynamic SL adjustment before checking exits.
        Tracks MFE/MAE and market state at exit for every bar the trade is open."""
        for trade in list(self.portfolio.open_trades):
            bar = self._get_bar(trade.symbol, ts)
            if bar is None:
                continue

            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            current_atr = float(bar.get("atr", 0) or 0)
            is_long = trade.side.upper() == "LONG"
            entry_atr = trade.atr_at_entry if trade.atr_at_entry > 0 else current_atr

            # ── MFE/MAE tracking (every bar) ──
            trade._bars_open += 1
            if is_long:
                favorable = high - trade.entry_price
                adverse = trade.entry_price - low
            else:
                favorable = trade.entry_price - low
                adverse = high - trade.entry_price

            if favorable > trade.mfe_price:
                trade.mfe_price = favorable
                trade.bars_to_mfe = trade._bars_open
            if adverse > trade.mae_price:
                trade.mae_price = adverse
                trade.bars_to_mae = trade._bars_open

            # Time in profit/loss
            unrealized = (close - trade.entry_price) * trade.qty if is_long else (trade.entry_price - close) * trade.qty
            if unrealized > 0:
                trade.time_in_profit_bars += 1
            else:
                trade.time_in_loss_bars += 1

            # Apply dynamic SL adjustment (matches live computeDynamicSL)
            hours_held = (ts - trade.entry_time).total_seconds() / 3600
            adjusted_sl = self._compute_dynamic_sl(trade, close, hours_held, current_atr)
            if adjusted_sl != trade.sl_price:
                trade.sl_price = adjusted_sl

            # Determine exit
            reason = None
            exit_price = 0.0

            # Time exit (checked first, matching live order)
            max_hours = get_max_trade_hours(trade.symbol)
            is_winning = (close - trade.entry_price) * trade.qty > 0 if is_long else (trade.entry_price - close) * trade.qty > 0
            effective_max = max_hours + 2 if is_winning else max_hours

            if hours_held >= effective_max:
                reason = "TIME_EXIT"
                exit_price = close
            elif is_long:
                sl_hit = trade.sl_price > 0 and low <= trade.sl_price
                tp_hit = trade.tp_price > 0 and high >= trade.tp_price
                if sl_hit:
                    reason = "SL_HIT"
                    exit_price = trade.sl_price
                elif tp_hit:
                    reason = "TP_HIT"
                    exit_price = trade.tp_price
            else:
                sl_hit = trade.sl_price > 0 and high >= trade.sl_price
                tp_hit = trade.tp_price > 0 and low <= trade.tp_price
                if sl_hit:
                    reason = "SL_HIT"
                    exit_price = trade.sl_price
                elif tp_hit:
                    reason = "TP_HIT"
                    exit_price = trade.tp_price

            if reason:
                notional = trade.entry_price * trade.qty
                exit_notional = exit_price * trade.qty
                open_fee = notional * TAKER_FEE
                close_fee = exit_notional * TAKER_FEE
                fees = open_fee + close_fee

                # ── Compute MFE/MAE in ATR units before closing ──
                if entry_atr > 0:
                    trade.mfe_atr = trade.mfe_price / entry_atr
                    trade.mae_atr = trade.mae_price / entry_atr
                tp_dist = abs(trade.tp_price - trade.entry_price) if trade.tp_price > 0 else 0
                sl_dist = abs(trade.sl_original - trade.entry_price) if trade.sl_original > 0 else 0
                if tp_dist > 0:
                    trade.mfe_pct_of_tp = (trade.mfe_price / tp_dist * 100)
                if sl_dist > 0:
                    trade.mae_pct_of_sl = (trade.mae_price / sl_dist * 100)

                # ── Capture market state at exit ──
                exit_enriched = self._get_enriched_slice(trade.symbol, ts)
                if exit_enriched is not None and len(exit_enriched) > 0:
                    ms = _extract_market_state(exit_enriched)
                    trade.rsi_exit = ms.get("rsi", 0.0)
                    trade.adx_exit = ms.get("adx", 0.0)
                    trade.atr_exit = ms.get("atr", 0.0)
                    trade.atr_pct_exit = ms.get("atr_pct", 0.0)
                    trade.volume_exit = ms.get("volume", 0.0)
                    trade.close_exit = close
                    # Regime at exit
                    try:
                        from regime_router import classify_regime
                        exit_regime, _, _, _ = classify_regime(exit_enriched, symbol=trade.symbol)
                        trade.regime_exit = exit_regime
                    except Exception:
                        trade.regime_exit = trade.regime

                # Store fees and gross PnL
                trade.fees_usd = fees
                gross = (exit_price - trade.entry_price) * trade.qty if is_long else (trade.entry_price - exit_price) * trade.qty
                trade.gross_pnl_usd = gross

                self.portfolio.close_trade(trade, exit_price, reason, ts, fees)
                self._log_closed_trade(trade)

    def _reprice_pending(self, ts: pd.Timestamp, gapped_symbols: set = None):
        """Re-price PENDING trades — simulate Step 1c.
        Skip symbols with data gaps (no data to reprice with)."""
        fix_equity = self.mode in ("treatment_a_phase1", "treatment_b_phase2")
        gapped_symbols = gapped_symbols or set()

        for pending in list(self.portfolio.pending_trades):
            if pending.symbol in gapped_symbols:
                continue

            enriched = self._get_enriched_slice(pending.symbol, ts)
            if enriched is None or len(enriched) == 0:
                # Expire — no data
                self._expire_pending(pending, "No data on reprice", ts)
                continue

            last_bar = enriched.iloc[-1]
            close_price = float(last_bar["close"])
            atr = float(last_bar.get("atr", 0) or 0)

            # Recompute DQS
            session = last_bar.get("session", "")
            dqs, breakdown = score_signal_adaptive(pending.symbol, enriched, None, session)

            # DQS drop check
            if dqs < DQS_THRESHOLDS["min_to_track"]:
                self._expire_pending(pending, f"DQS dropped to {dqs:.0f}", ts)
                continue

            # V7 veto in reprice — CONTROL ONLY
            if self.mode == "control" and dqs >= DQS_VETO_THRESHOLD:
                self._expire_pending(pending, f"V7 veto: DQS {dqs:.0f} >= {DQS_VETO_THRESHOLD}", ts)
                continue

            # Recompute SL/TP — use max(reprice_dqs, 50) to avoid get_sl_tp returning None
            # when DQS dropped to 40-49 (still trackable but below executable threshold)
            sl_tp_dqs = max(int(dqs), 50)
            sym_params = get_symbol_params(pending.symbol, session)
            regime_val = breakdown.get("regime", "MIXED")
            sl_mode = sym_params.get("sl_mode", "atr")
            if sl_mode == "pct":
                sl_price, tp_price, rr = get_sl_tp_pct(
                    close_price, sym_params["sl_pct"], sym_params["tp_pct"],
                    side=pending.side, regime=regime_val)
            else:
                if atr <= 0:
                    self._expire_pending(pending, "ATR is zero on reprice", ts)
                    continue
                sl_price, tp_price, rr = get_sl_tp(close_price, atr, sl_tp_dqs,
                                                    side=pending.side, regime=regime_val, symbol=pending.symbol)

            if sl_price is None:
                self._expire_pending(pending, "SL computation failed on reprice", ts)
                continue

            # Recompute Kelly and sizing
            tier = get_risk_tier(int(dqs))
            strategy = pending.strategy or sym_params.get("strategy", "trend_following")
            kelly_mult = get_kelly_v7(dqs, strategy)
            risk_pct = tier["risk_pct"] * kelly_mult

            # Phase 2: re-apply crowding reduction at reprice
            if self.mode == "treatment_b_phase2" and pending.crowded:
                risk_pct *= CROWDING_KELLY_REDUCTION

            # Treatment A/B: Kelly=0 means zero size — block but log
            if kelly_mult == 0.0 and self.mode in ("treatment_a_phase1", "treatment_b_phase2"):
                self._expire_pending(pending, "V7_KELLY_ZERO: Kelly=0 (sizing veto)", ts)
                continue

            sl_dist = abs(close_price - sl_price)
            account_balance = self.portfolio.balance
            dollar_risk = account_balance * (risk_pct / 100)
            qty = (dollar_risk / sl_dist) if sl_dist > 0 else 0
            position_size = close_price * qty
            position_size = cap_position_size(position_size, account_balance)
            qty = position_size / close_price if close_price > 0 else 0

            step = QTY_STEP.get(pending.symbol, 0.001)
            qty = (int(qty / step) * step) if step > 0 else qty
            notional = close_price * qty

            if notional < MIN_NOTIONAL:
                self._expire_pending(pending, f"Below min notional (${notional:.2f})", ts)
                continue

            dollar_risk = sl_dist * qty
            margin_req = (notional / LEVERAGE) + (notional * TAKER_FEE) if LEVERAGE > 0 else 0

            # Gate checks at promotion
            date_str = ts.strftime("%Y-%m-%d")
            ok_corr, reason_corr = self.portfolio.can_open_correlation(pending.symbol)
            if not ok_corr:
                self._expire_pending(pending, f"CorrelationGuard: {reason_corr}", ts)
                continue

            ok_count, reason_count = self.portfolio.can_open_count(session, date_str)
            if not ok_count:
                self._expire_pending(pending, f"TradeCountGuard: {reason_count}", ts)
                continue

            ok_dir, reason_dir = self.portfolio.can_open_directional(pending.side)
            if not ok_dir:
                self._expire_pending(pending, f"DirectionalGuard: {reason_dir}", ts)
                continue

            ok_risk, reason_risk = self.portfolio.has_risk_capacity(dollar_risk, fix_equity)
            if not ok_risk:
                self._expire_pending(pending, f"RiskBudgetLedger: {reason_risk}", ts)
                continue

            # Promote PENDING → OPEN
            fix_equity = self.mode in ("treatment_a_phase1", "treatment_b_phase2")
            port_state = _extract_portfolio_state(self.portfolio, fix_equity)
            trade = Trade(
                symbol=pending.symbol,
                side=pending.side,
                entry_price=close_price,
                sl_price=sl_price,
                tp_price=tp_price,
                qty=qty,
                entry_time=ts,
                dqs=dqs,
                regime=regime_val,
                strategy=strategy,
                kelly_fraction=kelly_mult * (CROWDING_KELLY_REDUCTION if (self.mode == "treatment_b_phase2" and pending.crowded) else 1.0),
                risk_usd=dollar_risk,
                crowded=pending.crowded,
                data_quality=pending.data_quality,
                sl_original=sl_price,
                atr_at_entry=atr,
                # ── Market state at entry ──
                rsi_entry=pending.rsi_entry,
                adx_entry=pending.adx_entry,
                atr_pct_entry=pending.atr_pct_entry,
                volume_entry=pending.volume_entry,
                turnover_entry=pending.turnover_entry,
                oi_entry=pending.oi_entry,
                funding_rate_entry=pending.funding_rate_entry,
                buy_ratio_entry=pending.buy_ratio_entry,
                sell_ratio_entry=pending.sell_ratio_entry,
                bull_4h_entry=pending.bull_4h_entry,
                ctx_adx_entry=pending.ctx_adx_entry,
                ctx_log_ret_4h_entry=pending.ctx_log_ret_4h_entry,
                session=session,
                efficiency_ratio_entry=pending.efficiency_ratio_entry,
                atr_rank_entry=pending.atr_rank_entry,
                # ── Signal breakdown ──
                tech_raw=pending.tech_raw,
                struct_raw=pending.struct_raw,
                ctx_raw=pending.ctx_raw,
                contra_penalty=pending.contra_penalty,
                direction=pending.direction,
                calibrated_prob_signal=pending.calibrated_prob_signal,
                # ── Portfolio state ──
                equity_at_entry=port_state["equity_at_entry"],
                open_trades_at_entry=port_state["open_trades_at_entry"],
                open_risk_at_entry=port_state["open_risk_at_entry"],
                margin_used_at_entry=port_state["margin_used_at_entry"],
                risk_budget_used_pct=port_state["risk_budget_used_pct"],
                # ── HMM ──
                hmm_confidence=pending.hmm_confidence,
                hmm_regime_raw=pending.hmm_regime_raw,
                # ── Position sizing ──
                notional=notional,
                risk_tier=str(tier.get("tier", "")),
                kelly_band=str(kelly_mult),
                sl_dist_atr=abs(close_price - sl_price) / atr if atr > 0 else 0.0,
                tp_dist_atr=abs(tp_price - close_price) / atr if atr > 0 else 0.0,
                rr_ratio=rr,
                pat_wedge_detected=pending.pat_wedge_detected,
                pat_wedge_type=pending.pat_wedge_type,
                pat_wedge_strength=pending.pat_wedge_strength,
                pat_asc_triangle_detected=pending.pat_asc_triangle_detected,
                pat_desc_triangle_detected=pending.pat_desc_triangle_detected,
                pat_channel_detected=pending.pat_channel_detected,
                pat_channel_type=pending.pat_channel_type,
                pat_trend_break_detected=pending.pat_trend_break_detected,
                pat_trend_break_direction=pending.pat_trend_break_direction,
                pat_trend_break_retest=pending.pat_trend_break_retest,
                pat_sr_level_count=pending.pat_sr_level_count,
                pat_near_support=pending.pat_near_support,
                pat_near_resistance=pending.pat_near_resistance,
                pat_sr_recency_weighted_strength=pending.pat_sr_recency_weighted_strength,
                pat_rising_support_detected=pending.pat_rising_support_detected,
                pat_falling_resistance_detected=pending.pat_falling_resistance_detected,
                pat_bos_detected=pending.pat_bos_detected,
                pat_bos_direction=pending.pat_bos_direction,
                pat_choch_detected=pending.pat_choch_detected,
                pat_choch_direction=pending.pat_choch_direction,
            )
            self.portfolio.open_trades.append(trade)
            self.portfolio.margin_used += margin_req
            self.portfolio.record_trade_open(session, date_str)
            self.portfolio.pending_trades.remove(pending)

            if self.verbose:
                print(f"  [Reprice] {pending.symbol} PENDING→OPEN — DQS={dqs:.0f}, entry={close_price:.2f}")

    def _expire_pending(self, pending: PendingTrade, reason: str, ts: pd.Timestamp):
        """Expire a PENDING trade and log to results."""
        self.portfolio.pending_trades.remove(pending)
        self.results.append(SignalRecord(
            symbol=pending.symbol, side=pending.side,
            entry_date=pending.signal_bar_utc.strftime("%Y-%m-%d %H:%M:%S"),
            entry_price=pending.entry_price,
            sl_price=pending.sl_price, tp_price=pending.tp_price,
            dqs_score=round(pending.dqs, 2),
            regime=pending.regime, strategy=pending.strategy,
            approved=True, reject_reason=reason,
            promoted=False,
            exit_date=ts.strftime("%Y-%m-%d %H:%M:%S"),
            exit_price=0, exit_reason="EXPIRED",
            hours_held=0, pnl_usd=0, pnl_pct=0,
            risk_usd=pending.risk_usd,
            kelly_fraction=pending.kelly_fraction,
            win=False, notes=self.mode,
            intent="TRADE",
            outcome="EXPIRED",
            # ── Market state at signal time ──
            rsi_entry=pending.rsi_entry,
            adx_entry=pending.adx_entry,
            atr_pct_entry=pending.atr_pct_entry,
            volume_entry=pending.volume_entry,
            turnover_entry=pending.turnover_entry,
            oi_entry=pending.oi_entry,
            funding_rate_entry=pending.funding_rate_entry,
            buy_ratio_entry=pending.buy_ratio_entry,
            sell_ratio_entry=pending.sell_ratio_entry,
            bull_4h_entry=pending.bull_4h_entry,
            ctx_adx_entry=pending.ctx_adx_entry,
            ctx_log_ret_4h_entry=pending.ctx_log_ret_4h_entry,
            session=pending.session,
            efficiency_ratio_entry=pending.efficiency_ratio_entry,
            atr_rank_entry=pending.atr_rank_entry,
            # ── Signal breakdown ──
            tech_raw=pending.tech_raw,
            struct_raw=pending.struct_raw,
            ctx_raw=pending.ctx_raw,
            contra_penalty=pending.contra_penalty,
            direction=pending.direction,
            calibrated_prob_signal=pending.calibrated_prob_signal,
            # ── HMM ──
            hmm_confidence=pending.hmm_confidence,
            hmm_regime_raw=pending.hmm_regime_raw,
            # ── Position sizing ──
            notional=pending.notional,
            risk_tier=pending.risk_tier,
            kelly_band=pending.kelly_band,
            sl_dist_atr=pending.sl_dist_atr,
            tp_dist_atr=pending.tp_dist_atr,
            rr_ratio=pending.rr_ratio,
            pat_wedge_detected=pending.pat_wedge_detected,
            pat_wedge_type=pending.pat_wedge_type,
            pat_wedge_strength=pending.pat_wedge_strength,
            pat_asc_triangle_detected=pending.pat_asc_triangle_detected,
            pat_desc_triangle_detected=pending.pat_desc_triangle_detected,
            pat_channel_detected=pending.pat_channel_detected,
            pat_channel_type=pending.pat_channel_type,
            pat_trend_break_detected=pending.pat_trend_break_detected,
            pat_trend_break_direction=pending.pat_trend_break_direction,
            pat_trend_break_retest=pending.pat_trend_break_retest,
            pat_sr_level_count=pending.pat_sr_level_count,
            pat_near_support=pending.pat_near_support,
            pat_near_resistance=pending.pat_near_resistance,
            pat_sr_recency_weighted_strength=pending.pat_sr_recency_weighted_strength,
            pat_rising_support_detected=pending.pat_rising_support_detected,
            pat_falling_resistance_detected=pending.pat_falling_resistance_detected,
            pat_bos_detected=pending.pat_bos_detected,
            pat_bos_direction=pending.pat_bos_direction,
            pat_choch_detected=pending.pat_choch_detected,
            pat_choch_direction=pending.pat_choch_direction,
        ))

    def _scan_symbol(self, symbol: str, ts: pd.Timestamp) -> Optional[dict]:
        """Scan a single symbol — returns signal dict or None."""
        # Check scan cache — expensive scoring is mode-independent
        cache_key = (symbol, ts)
        cached = _SCAN_CACHE.get(cache_key, _SCAN_MISS)
        if cached is _SCAN_MISS:
            # Cache miss: compute expensive scoring
            enriched = self._get_enriched_slice(symbol, ts)
            if enriched is None or len(enriched) == 0:
                _SCAN_CACHE[cache_key] = None
                return None

            last_bar = enriched.iloc[-1]
            session = last_bar.get("session", "")
            dqs, breakdown = score_signal_adaptive(symbol, enriched, None, session)

            if dqs < DQS_THRESHOLDS["min_to_track"]:
                _SCAN_CACHE[cache_key] = None
                return None

            close_price = float(last_bar["close"])
            atr = float(last_bar.get("atr", 0) or 0)
            side = breakdown.get("direction", "LONG")
            strategy = breakdown.get("strategy", "trend_following")
            regime_val = breakdown.get("regime", "MIXED")

            # V2 filters
            daily_bias = None
            try:
                daily_bias = load_daily_bias(symbol)
            except Exception:
                pass
            btc_mom = 0.0
            try:
                btc_mom = get_btc_momentum()
            except Exception:
                pass

            v2_passed, strategy, tp_mult_scale, v2_reason = apply_v2_filters(
                symbol, side, strategy, enriched, daily_bias, btc_mom)
            if not v2_passed:
                _SCAN_CACHE[cache_key] = None
                return None

            # Cache the mode-independent parts
            # Extract full market state, signal breakdown, and HMM data for logging
            market_state = _extract_market_state(enriched)
            sig_breakdown = _extract_signal_breakdown(breakdown)
            hmm_data = _extract_hmm_data(symbol, enriched)
            pattern_fields = _extract_pattern_fields(enriched)
            cached = {
                "close_price": close_price, "atr": atr, "side": side,
                "strategy": strategy, "regime_val": regime_val,
                "session": session, "dqs": dqs, "breakdown": breakdown,
                "tp_mult_scale": tp_mult_scale,
                # ── Full market state at signal time ──
                "market_state": market_state,
                "sig_breakdown": sig_breakdown,
                "hmm_data": hmm_data,
                "pattern_fields": pattern_fields,
            }
            _SCAN_CACHE[cache_key] = cached
        elif cached is None:
            return None

        # Use cached values for mode-dependent processing
        close_price = cached["close_price"]
        atr = cached["atr"]
        side = cached["side"]
        strategy = cached["strategy"]
        regime_val = cached["regime_val"]
        session = cached["session"]
        dqs = cached["dqs"]
        breakdown = cached["breakdown"]
        tp_mult_scale = cached["tp_mult_scale"]

        # V7 veto in scanner — CONTROL ONLY
        if self.mode == "control" and dqs >= DQS_VETO_THRESHOLD:
            return {"_vetoed": True, "symbol": symbol, "dqs": dqs, "side": side,
                    "strategy": strategy, "regime": regime_val,
                    "close": close_price, "atr": atr, "session": session,
                    "signal_bar_utc": ts}

        # SL/TP computation
        sym_params = get_symbol_params(symbol, session)
        sl_mode = sym_params.get("sl_mode", "atr")
        if sl_mode == "pct":
            sl_price, tp_price, rr = get_sl_tp_pct(
                close_price, sym_params["sl_pct"], sym_params["tp_pct"],
                side=side, regime=regime_val)
        else:
            if atr <= 0:
                return None
            sl_price, tp_price, rr = get_sl_tp(close_price, atr, int(dqs),
                                                side=side, regime=regime_val, symbol=symbol)
        if sl_price is None:
            return None

        # TP scaling by ATR percentile
        if tp_mult_scale != 1.0 and sl_mode != "pct":
            tp_dist = abs(tp_price - close_price)
            new_tp_dist = tp_dist * tp_mult_scale
            if side.upper() == "SHORT":
                tp_price = round(close_price - new_tp_dist, 6)
            else:
                tp_price = round(close_price + new_tp_dist, 6)

        # Kelly and sizing
        tier = get_risk_tier(int(dqs))
        kelly_mult = get_kelly_v7(dqs, strategy)
        risk_pct = tier["risk_pct"] * kelly_mult
        sl_dist = abs(close_price - sl_price)
        account_balance = self.portfolio.balance
        dollar_risk = account_balance * (risk_pct / 100)
        qty = (dollar_risk / sl_dist) if sl_dist > 0 else 0
        position_size = close_price * qty
        position_size = cap_position_size(position_size, account_balance)
        qty = position_size / close_price if close_price > 0 else 0

        step = QTY_STEP.get(symbol, 0.001)
        qty = (int(qty / step) * step) if step > 0 else qty
        notional = close_price * qty

        # When Kelly=0 (DQS >= 85 in treatment mode), notional will be 0.
        # Don't drop the signal — let it through to the risk gate which will
        # apply the Kelly=0 sizing veto and log it properly.
        if kelly_mult > 0 and notional < MIN_NOTIONAL:
            return None

        dollar_risk = sl_dist * qty

        # Phase 2: compute data quality score for treatment_b
        dq_score = 100.0
        if self.mode == "treatment_b_phase2":
            dq_key = (symbol, ts)
            dq = _DQ_CACHE.get(dq_key)
            if dq is None:
                enriched = self._get_enriched_slice(symbol, ts)
                dq = _compute_data_quality(enriched, ts)
                _DQ_CACHE[dq_key] = dq
            dq_score = dq["score"]

        return {
            "symbol": symbol,
            "dqs": dqs,
            "side": side,
            "strategy": strategy,
            "regime": regime_val,
            "close": close_price,
            "atr": atr,
            "sl": sl_price,
            "tp": tp_price,
            "rr": rr,
            "kelly_mult": kelly_mult,
            "risk_pct": risk_pct,
            "dollar_risk": dollar_risk,
            "qty": qty,
            "notional": notional,
            "session": session,
            "signal_bar_utc": ts,
            "breakdown": breakdown,
            "data_quality": dq_score,
            # ── Full market state at signal time ──
            "market_state": cached.get("market_state", {}),
            "sig_breakdown": cached.get("sig_breakdown", {}),
            "hmm_data": cached.get("hmm_data", {}),
            "pattern_fields": cached.get("pattern_fields", {}),
            # ── Position sizing detail ──
            "risk_tier": str(tier.get("tier", "")),
            "kelly_band": str(kelly_mult),
            "sl_dist_atr": abs(close_price - sl_price) / atr if atr > 0 else 0.0,
            "tp_dist_atr": abs(tp_price - close_price) / atr if atr > 0 else 0.0,
        }

    def _apply_risk_gate(self, sig: dict, ts: pd.Timestamp,
                         crowded_symbols: set = None) -> Tuple[bool, str]:
        """Apply portfolio gates. Returns (approved, reject_reason).

        Phase 2 additions for treatment_b_phase2:
        - Data quality gate: reject if data quality < 60
        - Crowding detection: reduce Kelly by 50% if symbol is crowded
        """
        symbol = sig["symbol"]
        dqs = sig["dqs"]
        session = sig.get("session", "")
        date_str = ts.strftime("%Y-%m-%d")
        fix_equity = self.mode in ("treatment_a_phase1", "treatment_b_phase2")

        # V7 veto in risk gate — CONTROL ONLY
        if self.mode == "control" and dqs >= DQS_VETO_THRESHOLD:
            return False, f"V7_DQS_VETO: DQS {dqs:.0f} >= {DQS_VETO_THRESHOLD}"

        # Disabled symbols
        if symbol in DISABLED_SYMBOLS:
            return False, "Symbol disabled in V7"

        # Phase 2: Data quality gate
        if self.mode == "treatment_b_phase2":
            dq_score = sig.get("data_quality", 100.0)
            if dq_score < DATA_QUALITY_MIN_SCORE:
                return False, f"DataQualityGate: score {dq_score:.1f} < {DATA_QUALITY_MIN_SCORE}"

        # Regime gate
        sig_regime = (sig.get("regime") or "MIXED").upper()
        min_dqs_for_regime = REGIME_MIN_DQS.get(sig_regime, 50)
        if dqs < min_dqs_for_regime:
            return False, f"RegimeGate: DQS {dqs:.0f} < {min_dqs_for_regime} for {sig_regime}"

        # Strategy-specific regime DQS override (matches live GATE_0C)
        sig_strategy = sig.get("strategy", "trend_following")
        strategy_regime_min = REGIME_STRATEGY_MIN_DQS.get((sig_strategy, sig_regime))
        if strategy_regime_min is not None and dqs < strategy_regime_min:
            return False, f"RegimeStrategyGate: DQS {dqs:.0f} < {strategy_regime_min} for {sig_strategy}/{sig_regime}"

        # Daily loss breaker (matches live GATE_DAILY_BREAKER)
        ok, reason = self.portfolio.can_open_daily_loss(date_str)
        if not ok:
            return False, reason

        # Correlation guard
        ok, reason = self.portfolio.can_open_correlation(symbol)
        if not ok:
            return False, f"CorrelationGuard: {reason}"

        # Trade count guard
        ok, reason = self.portfolio.can_open_count(session, date_str)
        if not ok:
            return False, f"TradeCountGuard: {reason}"

        # Directional guard
        ok, reason = self.portfolio.can_open_directional(sig["side"])
        if not ok:
            return False, f"DirectionalGuard: {reason}"

        # Risk budget
        ok, reason = self.portfolio.has_risk_capacity(sig["dollar_risk"], fix_equity)
        if not ok:
            return False, f"RiskBudgetLedger: {reason}"

        # Calibration gate (matches live Step 4 gate)
        cal_prob, cal_n, cal_level = _lookup_calibrated_prob_bt(dqs, symbol)
        sig["calibration_prob"] = cal_prob
        sig["calibration_level"] = cal_level
        cal_threshold = _get_calibration_gate_threshold(symbol)
        if cal_level != "prior" and cal_prob < cal_threshold:
            return False, f"CalibrationGate: prob {cal_prob:.2f} < {cal_threshold:.2f} ({cal_level})"

        # Treatment A/B: Kelly=0 is a sizing veto
        if self.mode in ("treatment_a_phase1", "treatment_b_phase2"):
            if sig["kelly_mult"] == 0.0:
                return False, "V7_KELLY_ZERO: Kelly=0 (sizing veto)"

        # Phase 2: Crowding — reduce Kelly (not veto) for risk management
        if self.mode == "treatment_b_phase2" and crowded_symbols:
            if symbol in crowded_symbols:
                sig["kelly_mult"] *= CROWDING_KELLY_REDUCTION
                sig["risk_pct"] *= CROWDING_KELLY_REDUCTION
                sig["dollar_risk"] *= CROWDING_KELLY_REDUCTION
                sig["crowded"] = True

        return True, ""

    def _create_pending(self, sig: dict, ts: pd.Timestamp):
        """Create a PENDING trade for an approved signal."""
        ms = sig.get("market_state", {})
        sb = sig.get("sig_breakdown", {})
        hmm = sig.get("hmm_data", {})
        pf = sig.get("pattern_fields", {})
        pending = PendingTrade(
            symbol=sig["symbol"],
            side=sig["side"],
            entry_price=sig["close"],
            sl_price=sig["sl"],
            tp_price=sig["tp"],
            qty=sig["qty"],
            signal_bar_utc=ts,
            dqs=sig["dqs"],
            regime=sig["regime"],
            strategy=sig["strategy"],
            kelly_fraction=sig["kelly_mult"],
            risk_usd=sig["dollar_risk"],
            crowded=sig.get("crowded", False),
            data_quality=sig.get("data_quality", 0.0),
            # ── Market state at signal time ──
            rsi_entry=ms.get("rsi", 0.0),
            adx_entry=ms.get("adx", 0.0),
            atr_pct_entry=ms.get("atr_pct", 0.0),
            volume_entry=ms.get("volume", 0.0),
            turnover_entry=ms.get("turnover", 0.0),
            oi_entry=ms.get("oi", 0.0),
            funding_rate_entry=ms.get("funding_rate", 0.0),
            buy_ratio_entry=ms.get("buy_ratio", 0.0),
            sell_ratio_entry=ms.get("sell_ratio", 0.0),
            bull_4h_entry=ms.get("bull_4h", 0),
            ctx_adx_entry=ms.get("ctx_adx", 0.0),
            ctx_log_ret_4h_entry=ms.get("ctx_log_ret_4h", 0.0),
            session=sig.get("session", ""),
            efficiency_ratio_entry=ms.get("efficiency_ratio", 0.0),
            atr_rank_entry=ms.get("atr_rank", 0.0),
            # ── Signal breakdown ──
            tech_raw=sb.get("tech_raw", 0.0),
            struct_raw=sb.get("struct_raw", 0.0),
            ctx_raw=sb.get("ctx_raw", 0.0),
            contra_penalty=sb.get("contra_penalty", 0.0),
            direction=sb.get("direction", ""),
            calibrated_prob_signal=sb.get("calibrated_prob_signal", 0.0),
            # ── HMM ──
            hmm_confidence=hmm.get("hmm_confidence", 0.0),
            hmm_regime_raw=hmm.get("hmm_regime_raw", ""),
            # ── Position sizing ──
            notional=sig.get("notional", 0.0),
            risk_tier=sig.get("risk_tier", ""),
            kelly_band=sig.get("kelly_band", ""),
            sl_dist_atr=sig.get("sl_dist_atr", 0.0),
            tp_dist_atr=sig.get("tp_dist_atr", 0.0),
            rr_ratio=sig.get("rr", 0.0),
            pat_wedge_detected=pf.get("pat_wedge_detected", False),
            pat_wedge_type=pf.get("pat_wedge_type", ""),
            pat_wedge_strength=pf.get("pat_wedge_strength", 0.0),
            pat_asc_triangle_detected=pf.get("pat_asc_triangle_detected", False),
            pat_desc_triangle_detected=pf.get("pat_desc_triangle_detected", False),
            pat_channel_detected=pf.get("pat_channel_detected", False),
            pat_channel_type=pf.get("pat_channel_type", ""),
            pat_trend_break_detected=pf.get("pat_trend_break_detected", False),
            pat_trend_break_direction=pf.get("pat_trend_break_direction", ""),
            pat_trend_break_retest=pf.get("pat_trend_break_retest", False),
            pat_sr_level_count=pf.get("pat_sr_level_count", 0),
            pat_near_support=pf.get("pat_near_support", False),
            pat_near_resistance=pf.get("pat_near_resistance", False),
            pat_sr_recency_weighted_strength=pf.get("pat_sr_recency_weighted_strength", 0.0),
            pat_rising_support_detected=pf.get("pat_rising_support_detected", False),
            pat_falling_resistance_detected=pf.get("pat_falling_resistance_detected", False),
            pat_bos_detected=pf.get("pat_bos_detected", False),
            pat_bos_direction=pf.get("pat_bos_direction", ""),
            pat_choch_detected=pf.get("pat_choch_detected", False),
            pat_choch_direction=pf.get("pat_choch_direction", ""),
        )
        self.portfolio.pending_trades.append(pending)

    def _log_rejected_signal(self, sig: dict, reason: str, ts: pd.Timestamp):
        """Log a signal that was rejected by the risk gate."""
        ms = sig.get("market_state", {})
        sb = sig.get("sig_breakdown", {})
        hmm = sig.get("hmm_data", {})
        pf = sig.get("pattern_fields", {})
        self.results.append(SignalRecord(
            symbol=sig["symbol"], side=sig["side"],
            entry_date=ts.strftime("%Y-%m-%d %H:%M:%S"),
            entry_price=sig["close"],
            sl_price=sig["sl"], tp_price=sig["tp"],
            dqs_score=round(sig["dqs"], 2),
            regime=sig["regime"], strategy=sig["strategy"],
            approved=False, reject_reason=reason,
            promoted=False,
            exit_date="", exit_price=0, exit_reason="REJECTED",
            hours_held=0, pnl_usd=0, pnl_pct=0,
            risk_usd=sig["dollar_risk"],
            kelly_fraction=sig["kelly_mult"],
            win=False, notes=self.mode,
            intent="REJECT",
            outcome="NOT_TRADED",
            crowded=sig.get("crowded", False),
            data_quality=sig.get("data_quality", 0.0),
            sl_original=sig.get("sl", 0.0),
            atr_at_entry=sig.get("atr", 0.0),
            calibration_prob=sig.get("calibration_prob", 0.0),
            calibration_level=sig.get("calibration_level", ""),
            # ── Market state at signal time ──
            rsi_entry=ms.get("rsi", 0.0),
            adx_entry=ms.get("adx", 0.0),
            atr_pct_entry=ms.get("atr_pct", 0.0),
            volume_entry=ms.get("volume", 0.0),
            turnover_entry=ms.get("turnover", 0.0),
            oi_entry=ms.get("oi", 0.0),
            funding_rate_entry=ms.get("funding_rate", 0.0),
            buy_ratio_entry=ms.get("buy_ratio", 0.0),
            sell_ratio_entry=ms.get("sell_ratio", 0.0),
            bull_4h_entry=ms.get("bull_4h", 0),
            ctx_adx_entry=ms.get("ctx_adx", 0.0),
            ctx_log_ret_4h_entry=ms.get("ctx_log_ret_4h", 0.0),
            session=sig.get("session", ""),
            efficiency_ratio_entry=ms.get("efficiency_ratio", 0.0),
            atr_rank_entry=ms.get("atr_rank", 0.0),
            # ── Signal breakdown ──
            tech_raw=sb.get("tech_raw", 0.0),
            struct_raw=sb.get("struct_raw", 0.0),
            ctx_raw=sb.get("ctx_raw", 0.0),
            contra_penalty=sb.get("contra_penalty", 0.0),
            direction=sb.get("direction", ""),
            calibrated_prob_signal=sb.get("calibrated_prob_signal", 0.0),
            # ── HMM ──
            hmm_confidence=hmm.get("hmm_confidence", 0.0),
            hmm_regime_raw=hmm.get("hmm_regime_raw", ""),
            # ── Position sizing ──
            notional=sig.get("notional", 0.0),
            qty=sig.get("qty", 0.0),
            risk_tier=sig.get("risk_tier", ""),
            kelly_band=sig.get("kelly_band", ""),
            sl_dist_atr=sig.get("sl_dist_atr", 0.0),
            tp_dist_atr=sig.get("tp_dist_atr", 0.0),
            rr_ratio=sig.get("rr", 0.0),
            pat_wedge_detected=pf.get("pat_wedge_detected", False),
            pat_wedge_type=pf.get("pat_wedge_type", ""),
            pat_wedge_strength=pf.get("pat_wedge_strength", 0.0),
            pat_asc_triangle_detected=pf.get("pat_asc_triangle_detected", False),
            pat_desc_triangle_detected=pf.get("pat_desc_triangle_detected", False),
            pat_channel_detected=pf.get("pat_channel_detected", False),
            pat_channel_type=pf.get("pat_channel_type", ""),
            pat_trend_break_detected=pf.get("pat_trend_break_detected", False),
            pat_trend_break_direction=pf.get("pat_trend_break_direction", ""),
            pat_trend_break_retest=pf.get("pat_trend_break_retest", False),
            pat_sr_level_count=pf.get("pat_sr_level_count", 0),
            pat_near_support=pf.get("pat_near_support", False),
            pat_near_resistance=pf.get("pat_near_resistance", False),
            pat_sr_recency_weighted_strength=pf.get("pat_sr_recency_weighted_strength", 0.0),
            pat_rising_support_detected=pf.get("pat_rising_support_detected", False),
            pat_falling_resistance_detected=pf.get("pat_falling_resistance_detected", False),
            pat_bos_detected=pf.get("pat_bos_detected", False),
            pat_bos_direction=pf.get("pat_bos_direction", ""),
            pat_choch_detected=pf.get("pat_choch_detected", False),
            pat_choch_direction=pf.get("pat_choch_direction", ""),
        ))

    def _log_closed_trade(self, trade: Trade):
        """Log a closed trade to results with full market state and MFE/MAE."""
        self.results.append(SignalRecord(
            symbol=trade.symbol, side=trade.side,
            entry_date=trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            entry_price=trade.entry_price,
            sl_price=trade.sl_price, tp_price=trade.tp_price,
            dqs_score=round(trade.dqs, 2),
            regime=trade.regime, strategy=trade.strategy,
            approved=True, reject_reason="",
            promoted=True,
            exit_date=trade.exit_time.strftime("%Y-%m-%d %H:%M:%S") if trade.exit_time else "",
            exit_price=trade.exit_price,
            exit_reason=trade.exit_reason,
            hours_held=round(trade.hours_held, 2),
            pnl_usd=round(trade.pnl_usd, 2),
            pnl_pct=round(trade.pnl_pct, 2),
            risk_usd=round(trade.risk_usd, 2),
            kelly_fraction=trade.kelly_fraction,
            win=trade.pnl_usd > 0,
            notes=self.mode,
            intent="TRADE",
            outcome="WIN" if trade.pnl_usd > 0 else "LOSS",
            crowded=getattr(trade, 'crowded', False),
            data_quality=getattr(trade, 'data_quality', 0.0),
            sl_original=getattr(trade, 'sl_original', 0.0),
            atr_at_entry=getattr(trade, 'atr_at_entry', 0.0),
            calibration_prob=getattr(trade, 'calibration_prob', 0.0),
            calibration_level=getattr(trade, 'calibration_level', ""),
            # ── Market state at entry ──
            rsi_entry=trade.rsi_entry,
            adx_entry=trade.adx_entry,
            atr_pct_entry=trade.atr_pct_entry,
            volume_entry=trade.volume_entry,
            turnover_entry=trade.turnover_entry,
            oi_entry=trade.oi_entry,
            funding_rate_entry=trade.funding_rate_entry,
            buy_ratio_entry=trade.buy_ratio_entry,
            sell_ratio_entry=trade.sell_ratio_entry,
            bull_4h_entry=trade.bull_4h_entry,
            ctx_adx_entry=trade.ctx_adx_entry,
            ctx_log_ret_4h_entry=trade.ctx_log_ret_4h_entry,
            session=trade.session,
            efficiency_ratio_entry=trade.efficiency_ratio_entry,
            atr_rank_entry=trade.atr_rank_entry,
            # ── Market state at exit ──
            rsi_exit=trade.rsi_exit,
            adx_exit=trade.adx_exit,
            atr_exit=trade.atr_exit,
            atr_pct_exit=trade.atr_pct_exit,
            volume_exit=trade.volume_exit,
            regime_exit=trade.regime_exit,
            close_exit=trade.close_exit,
            # ── MFE / MAE ──
            mfe_price=trade.mfe_price,
            mfe_atr=trade.mfe_atr,
            mfe_pct_of_tp=trade.mfe_pct_of_tp,
            mae_price=trade.mae_price,
            mae_atr=trade.mae_atr,
            mae_pct_of_sl=trade.mae_pct_of_sl,
            bars_to_mfe=trade.bars_to_mfe,
            bars_to_mae=trade.bars_to_mae,
            time_in_profit_bars=trade.time_in_profit_bars,
            time_in_loss_bars=trade.time_in_loss_bars,
            # ── Signal breakdown ──
            tech_raw=trade.tech_raw,
            struct_raw=trade.struct_raw,
            ctx_raw=trade.ctx_raw,
            contra_penalty=trade.contra_penalty,
            direction=trade.direction,
            calibrated_prob_signal=trade.calibrated_prob_signal,
            # ── Portfolio state ──
            equity_at_entry=trade.equity_at_entry,
            open_trades_at_entry=trade.open_trades_at_entry,
            open_risk_at_entry=trade.open_risk_at_entry,
            margin_used_at_entry=trade.margin_used_at_entry,
            risk_budget_used_pct=trade.risk_budget_used_pct,
            # ── HMM ──
            hmm_confidence=trade.hmm_confidence,
            hmm_regime_raw=trade.hmm_regime_raw,
            # ── Fees ──
            fees_usd=trade.fees_usd,
            gross_pnl_usd=trade.gross_pnl_usd,
            # ── Position sizing ──
            notional=trade.notional,
            qty=trade.qty,
            risk_tier=trade.risk_tier,
            kelly_band=trade.kelly_band,
            sl_dist_atr=trade.sl_dist_atr,
            tp_dist_atr=trade.tp_dist_atr,
            rr_ratio=trade.rr_ratio,
            pat_wedge_detected=trade.pat_wedge_detected,
            pat_wedge_type=trade.pat_wedge_type,
            pat_wedge_strength=trade.pat_wedge_strength,
            pat_asc_triangle_detected=trade.pat_asc_triangle_detected,
            pat_desc_triangle_detected=trade.pat_desc_triangle_detected,
            pat_channel_detected=trade.pat_channel_detected,
            pat_channel_type=trade.pat_channel_type,
            pat_trend_break_detected=trade.pat_trend_break_detected,
            pat_trend_break_direction=trade.pat_trend_break_direction,
            pat_trend_break_retest=trade.pat_trend_break_retest,
            pat_sr_level_count=trade.pat_sr_level_count,
            pat_near_support=trade.pat_near_support,
            pat_near_resistance=trade.pat_near_resistance,
            pat_sr_recency_weighted_strength=trade.pat_sr_recency_weighted_strength,
            pat_rising_support_detected=trade.pat_rising_support_detected,
            pat_falling_resistance_detected=trade.pat_falling_resistance_detected,
            pat_bos_detected=trade.pat_bos_detected,
            pat_bos_direction=trade.pat_bos_direction,
            pat_choch_detected=trade.pat_choch_detected,
            pat_choch_direction=trade.pat_choch_direction,
        ))

    def run(self) -> List[SignalRecord]:
        """Execute the full bar-by-bar backtest."""
        print(f"\n{'='*70}")
        print(f"  VLTHR Backtest — Mode: {self.mode}")
        print(f"  Period: {self.start} → {self.end}")
        print(f"  Bars: {self.n_bars} | Symbols: {list(self.data.keys())}")
        if self.walk_forward:
            print(f"  Walk-forward: IS ends at {self.is_end}")
        if self.latency_bars > 0:
            print(f"  Latency sim: {self.latency_bars} bars delay")
        if self.data_gap_prob > 0:
            print(f"  Data gap sim: {self.data_gap_prob*100:.1f}% chance per bar")
        if self.downtime_periods:
            print(f"  Downtime sim: {len(self.downtime_periods)} periods")
        print(f"{'='*70}")

        # Track closed trades for logging
        closed_trades_log: List[Trade] = []

        for i, ts in enumerate(self.timeline):
            # 0. Downtime simulation: server is down, but exchange SL/TP orders
            # still execute. We check exits but skip scanning and pending creation.
            is_downtime = self._is_downtime(ts)

            # 0b. Data gap simulation: determine which symbols have gaps this bar
            # Exchange SL/TP still executes during gaps, so exits are NOT skipped.
            # Only signal scanning and pending repricing are skipped (server can't
            # generate signals or reprice without data feed).
            gapped_symbols = set()
            if self.data_gap_prob > 0:
                for sym in SYMBOLS:
                    if sym in DISABLED_SYMBOLS:
                        continue
                    if self._rng.random() < self.data_gap_prob:
                        gapped_symbols.add(sym)

            # 1. Check exits for open trades (ALWAYS runs — exchange handles SL/TP
            # even during data gaps and server downtime)
            self._check_exits(ts)

            # 2. Reprice PENDING trades (skip during downtime or data gaps)
            if self.portfolio.pending_trades and not is_downtime:
                self._reprice_pending(ts, gapped_symbols)

            # 2b. Process delayed signals from latency simulation
            # (delayed signals still tick down during downtime, but won't be processed)
            newly_ready = []
            remaining_delayed = []
            for sig, orig_ts, delay_left in self.delayed_signals:
                if delay_left <= 0 and not is_downtime:
                    newly_ready.append(sig)
                else:
                    remaining_delayed.append((sig, orig_ts, max(0, delay_left - 1)))
            self.delayed_signals = remaining_delayed

            # 3. Scan all symbols (Step 1) — skip during downtime
            new_signals = []
            if is_downtime:
                # Still process any newly_ready delayed signals from before downtime
                new_signals = newly_ready
            else:
                for sym in SYMBOLS:
                    if sym in DISABLED_SYMBOLS:
                        continue

                    # Data gap simulation: skip scanning if symbol has gap this bar
                    if sym in gapped_symbols:
                        continue

                    sig = self._scan_symbol(sym, ts)
                    if sig is not None:
                        if sig.get("_vetoed"):
                            # Control mode: V7 veto at scanner — log as rejected
                            ms = sig.get("market_state", {})
                            sb = sig.get("sig_breakdown", {})
                            hmm = sig.get("hmm_data", {})
                            self.results.append(SignalRecord(
                                symbol=sig["symbol"], side=sig["side"],
                                entry_date=ts.strftime("%Y-%m-%d %H:%M:%S"),
                                entry_price=sig["close"],
                                sl_price=0, tp_price=0,
                                dqs_score=round(sig["dqs"], 2),
                                regime=sig["regime"], strategy=sig["strategy"],
                                approved=False,
                                reject_reason=f"V7_SCANNER_VETO: DQS {sig['dqs']:.0f} >= {DQS_VETO_THRESHOLD}",
                                promoted=False,
                                exit_date="", exit_price=0, exit_reason="REJECTED",
                                hours_held=0, pnl_usd=0, pnl_pct=0,
                                risk_usd=0, kelly_fraction=0,
                                win=False, notes=self.mode,
                                intent="VETO",
                                outcome="NOT_TRADED",
                                rsi_entry=ms.get("rsi", 0.0),
                                adx_entry=ms.get("adx", 0.0),
                                session=sig.get("session", ""),
                                tech_raw=sb.get("tech_raw", 0.0),
                                struct_raw=sb.get("struct_raw", 0.0),
                                ctx_raw=sb.get("ctx_raw", 0.0),
                                hmm_confidence=hmm.get("hmm_confidence", 0.0),
                                hmm_regime_raw=hmm.get("hmm_regime_raw", ""),
                            ))
                            continue
                        new_signals.append(sig)

            # 3b. Latency simulation: delay signals by latency_bars
            if self.latency_bars > 0 and new_signals:
                for sig in new_signals:
                    self.delayed_signals.append((sig, ts, self.latency_bars))
                new_signals = []

            # Combine newly ready delayed signals with new signals
            new_signals = newly_ready + new_signals

            # 4. Apply risk gates (Step 4)
            # Phase 2: detect crowding across all signals at this bar before gating
            crowded_symbols = set()
            if self.mode == "treatment_b_phase2" and new_signals:
                crowded_symbols = _detect_crowding(new_signals)

            for sig in new_signals:
                approved, reason = self._apply_risk_gate(sig, ts, crowded_symbols)
                if approved:
                    # 5. Create PENDING trade
                    self._create_pending(sig, ts)
                else:
                    self._log_rejected_signal(sig, reason, ts)

            # Progress
            if self.verbose and i > 0 and i % 288 == 0:  # ~every 3 days
                print(f"  Day {i//96}: Balance=${self.portfolio.balance:.2f}, "
                      f"Open={len(self.portfolio.open_trades)}, "
                      f"Pending={len(self.portfolio.pending_trades)}, "
                      f"Results={len(self.results)}")

        # Force-close any remaining open trades at the end
        last_ts = self.timeline[-1] if self.timeline else pd.Timestamp.now(tz="UTC")
        for trade in list(self.portfolio.open_trades):
            bar = self._get_bar(trade.symbol, last_ts)
            close_price = float(bar["close"]) if bar is not None else trade.entry_price
            notional = trade.entry_price * trade.qty
            fees = notional * TAKER_FEE * 2

            # Finalize MFE/MAE in ATR units
            entry_atr = trade.atr_at_entry if trade.atr_at_entry > 0 else 1.0
            trade.mfe_atr = trade.mfe_price / entry_atr
            trade.mae_atr = trade.mae_price / entry_atr
            tp_dist = abs(trade.tp_price - trade.entry_price) if trade.tp_price > 0 else 0
            sl_dist = abs(trade.sl_original - trade.entry_price) if trade.sl_original > 0 else 0
            if tp_dist > 0:
                trade.mfe_pct_of_tp = trade.mfe_price / tp_dist * 100
            if sl_dist > 0:
                trade.mae_pct_of_sl = trade.mae_price / sl_dist * 100

            # Capture exit market state
            exit_enriched = self._get_enriched_slice(trade.symbol, last_ts)
            if exit_enriched is not None and len(exit_enriched) > 0:
                ms = _extract_market_state(exit_enriched)
                trade.rsi_exit = ms.get("rsi", 0.0)
                trade.adx_exit = ms.get("adx", 0.0)
                trade.atr_exit = ms.get("atr", 0.0)
                trade.atr_pct_exit = ms.get("atr_pct", 0.0)
                trade.volume_exit = ms.get("volume", 0.0)
                trade.close_exit = close_price
                try:
                    from regime_router import classify_regime
                    exit_regime, _, _, _ = classify_regime(exit_enriched, symbol=trade.symbol)
                    trade.regime_exit = exit_regime
                except Exception:
                    trade.regime_exit = trade.regime

            trade.fees_usd = fees
            is_long = trade.side.upper() == "LONG"
            trade.gross_pnl_usd = (close_price - trade.entry_price) * trade.qty if is_long else (trade.entry_price - close_price) * trade.qty

            self.portfolio.close_trade(trade, close_price, "BACKTEST_END", last_ts, fees)
            self._log_closed_trade(trade)

        # Expire remaining pending trades
        for pending in list(self.portfolio.pending_trades):
            self._expire_pending(pending, "Backtest ended", last_ts)

        print(f"\n  Complete: {len(self.results)} signal records")
        return self.results


# ── METRICS COMPUTATION ─────────────────────────────────────────────────────

def _slice_metrics(records: list) -> dict:
    """Compute basic metrics for a slice of closed records."""
    if not records:
        return {"n": 0, "win_rate": 0, "pf": 0, "exp": 0, "pnl": 0}
    wins = [r for r in records if r.pnl_usd > 0]
    losses = [r for r in records if r.pnl_usd <= 0]
    gp = sum(r.pnl_usd for r in wins)
    gl = abs(sum(r.pnl_usd for r in losses))
    return {
        "n": len(records),
        "win_rate": round(len(wins) / len(records) * 100, 1) if records else 0,
        "pf": round(gp / gl, 2) if gl > 0 else float('inf') if gp > 0 else 0,
        "exp": round(sum(r.pnl_usd for r in records) / len(records), 2) if records else 0,
        "pnl": round(sum(r.pnl_usd for r in records), 2),
    }


def compute_metrics(records: List[SignalRecord]) -> dict:
    """Compute validation metrics from signal records.
    Includes per-symbol, per-session, per-regime, per-strategy breakdowns
    and MFE/MAE aggregates for feature engineering."""
    closed = [r for r in records if r.exit_reason in ("TP_HIT", "SL_HIT", "TIME_EXIT", "BACKTEST_END")]
    expired = [r for r in records if r.exit_reason == "EXPIRED"]
    rejected = [r for r in records if not r.approved]
    approved = [r for r in records if r.approved]
    promoted = [r for r in records if r.promoted]

    wins = [r for r in closed if r.pnl_usd > 0]
    losses = [r for r in closed if r.pnl_usd <= 0]

    gross_profit = sum(r.pnl_usd for r in wins)
    gross_loss = abs(sum(r.pnl_usd for r in losses))

    win_rate = (len(wins) / len(closed) * 100) if closed else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
    expectancy = (sum(r.pnl_usd for r in closed) / len(closed)) if closed else 0
    tp_hits = sum(1 for r in closed if r.exit_reason == "TP_HIT")
    sl_hits = sum(1 for r in closed if r.exit_reason == "SL_HIT")
    time_exits = sum(1 for r in closed if r.exit_reason == "TIME_EXIT")

    expired_rate = (len(expired) / len(records) * 100) if records else 0
    tp_hit_rate = (tp_hits / len(closed) * 100) if closed else 0
    sl_hit_rate = (sl_hits / len(closed) * 100) if closed else 0

    approval_rate = (len(approved) / len(records) * 100) if records else 0
    promote_rate = (len(promoted) / len(approved) * 100) if approved else 0

    avg_hold = (sum(r.hours_held for r in closed) / len(closed)) if closed else 0

    # Max drawdown (simplified from PnL stream)
    equity = INIT_BALANCE
    peak = equity
    max_dd = 0
    pnl_stream = []
    for r in closed:
        equity += r.pnl_usd
        pnl_stream.append(r.pnl_usd)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # ── Sharpe ratio (annualized, assuming 15m bars) ──
    import math
    sharpe = 0.0
    if len(pnl_stream) > 1:
        avg_pnl = sum(pnl_stream) / len(pnl_stream)
        std_pnl = (sum((x - avg_pnl) ** 2 for x in pnl_stream) / (len(pnl_stream) - 1)) ** 0.5
        if std_pnl > 0:
            # 15m bars: ~4 per hour, ~96 per day, ~35040 per year
            sharpe = round((avg_pnl / std_pnl) * math.sqrt(35040), 2)

    # ── MFE/MAE aggregates ──
    avg_mfe_atr = round(sum(r.mfe_atr for r in closed) / len(closed), 2) if closed else 0
    avg_mae_atr = round(sum(r.mae_atr for r in closed) / len(closed), 2) if closed else 0
    avg_mfe_pct_tp = round(sum(r.mfe_pct_of_tp for r in closed) / len(closed), 1) if closed else 0
    avg_mae_pct_sl = round(sum(r.mae_pct_of_sl for r in closed) / len(closed), 1) if closed else 0
    avg_time_profit = round(sum(r.time_in_profit_bars for r in closed) / len(closed), 1) if closed else 0
    avg_time_loss = round(sum(r.time_in_loss_bars for r in closed) / len(closed), 1) if closed else 0

    # ── Fee tracking ──
    total_fees = round(sum(r.fees_usd for r in closed), 2) if closed else 0
    total_gross = round(sum(r.gross_pnl_usd for r in closed), 2) if closed else 0

    # ── Per-symbol breakdown ──
    per_symbol = {}
    for sym in set(r.symbol for r in closed):
        sym_records = [r for r in closed if r.symbol == sym]
        per_symbol[sym] = _slice_metrics(sym_records)

    # ── Per-session breakdown ──
    per_session = {}
    for sess in set(r.session for r in closed if r.session):
        sess_records = [r for r in closed if r.session == sess]
        per_session[sess] = _slice_metrics(sess_records)

    # ── Per-regime breakdown ──
    per_regime = {}
    for reg in set(r.regime for r in closed if r.regime):
        reg_records = [r for r in closed if r.regime == reg]
        per_regime[reg] = _slice_metrics(reg_records)

    # ── Per-strategy breakdown ──
    per_strategy = {}
    for strat in set(r.strategy for r in closed if r.strategy):
        strat_records = [r for r in closed if r.strategy == strat]
        per_strategy[strat] = _slice_metrics(strat_records)

    # ── Per-DQS-band breakdown ──
    per_dqs_band = {}
    for lo, hi, label in [(40, 49, "40-49"), (50, 59, "50-59"), (60, 69, "60-69"), (70, 79, "70-79"), (80, 100, "80+")]:
        band_records = [r for r in closed if lo <= r.dqs_score < hi]
        per_dqs_band[label] = _slice_metrics(band_records)

    # ── Reject reason breakdown ──
    reject_reasons = {}
    for r in rejected:
        reason = r.reject_reason.split(":")[0] if r.reject_reason else "UNKNOWN"
        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

    return {
        "total_signals": len(records),
        "approved": len(approved),
        "rejected": len(rejected),
        "promoted": len(promoted),
        "closed_trades": len(closed),
        "expired": len(expired),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "expectancy_usd": round(expectancy, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "expired_rate_pct": round(expired_rate, 1),
        "tp_hit_rate_pct": round(tp_hit_rate, 1),
        "sl_hit_rate_pct": round(sl_hit_rate, 1),
        "approval_rate_pct": round(approval_rate, 1),
        "promote_rate_pct": round(promote_rate, 1),
        "avg_hold_hours": round(avg_hold, 1),
        "tp_hits": tp_hits,
        "sl_hits": sl_hits,
        "time_exits": time_exits,
        "final_balance": round(equity, 2),
        # ── New aggregate metrics ──
        "sharpe_ratio": sharpe,
        "total_fees_usd": total_fees,
        "total_gross_pnl_usd": total_gross,
        "avg_mfe_atr": avg_mfe_atr,
        "avg_mae_atr": avg_mae_atr,
        "avg_mfe_pct_of_tp": avg_mfe_pct_tp,
        "avg_mae_pct_of_sl": avg_mae_pct_sl,
        "avg_time_in_profit_bars": avg_time_profit,
        "avg_time_in_loss_bars": avg_time_loss,
        # ── Breakdowns ──
        "per_symbol": per_symbol,
        "per_session": per_session,
        "per_regime": per_regime,
        "per_strategy": per_strategy,
        "per_dqs_band": per_dqs_band,
        "reject_reasons": reject_reasons,
    }


def check_gates(metrics: dict) -> dict:
    """Check validation gates."""
    return {
        "win_rate_ge_30": metrics["win_rate_pct"] >= 30,
        "profit_factor_ge_1.5": metrics["profit_factor"] >= 1.5,
        "expectancy_positive": metrics["expectancy_usd"] > 0,
        "max_dd_lt_15": metrics["max_drawdown_pct"] < 15,
        "expired_rate_lt_30": metrics["expired_rate_pct"] < 30,
        "tp_hit_rate_ge_40": metrics["tp_hit_rate_pct"] >= 40,
    }


def print_report(metrics: dict, gates: dict, mode: str):
    """Print a formatted metrics report with breakdowns."""
    print(f"\n{'='*70}")
    print(f"  Backtest Report — {mode}")
    print(f"{'='*70}")
    print(f"  SIGNAL FUNNEL:")
    print(f"    Total signals:    {metrics['total_signals']}")
    print(f"    Approved:         {metrics['approved']} ({metrics['approval_rate_pct']:.1f}%)")
    print(f"    Rejected:         {metrics['rejected']}")
    print(f"    Promoted:         {metrics['promoted']} ({metrics['promote_rate_pct']:.1f}% of approved)")
    print(f"    Expired:          {metrics['expired']} ({metrics['expired_rate_pct']:.1f}%)")
    print(f"    Closed:           {metrics['closed_trades']}")
    print(f"")
    print(f"  TRADE RESULTS:")
    print(f"    Wins:             {metrics['wins']}")
    print(f"    Losses:           {metrics['losses']}")
    print(f"    Win rate:         {metrics['win_rate_pct']:.1f}%")
    print(f"    Profit factor:    {metrics['profit_factor']:.2f}")
    print(f"    Expectancy:       ${metrics['expectancy_usd']:.2f}")
    print(f"    Max drawdown:     {metrics['max_drawdown_pct']:.2f}%")
    print(f"    Sharpe ratio:     {metrics.get('sharpe_ratio', 0):.2f}")
    print(f"    TP hits:          {metrics['tp_hits']} ({metrics['tp_hit_rate_pct']:.1f}%)")
    print(f"    SL hits:          {metrics['sl_hits']} ({metrics['sl_hit_rate_pct']:.1f}%)")
    print(f"    Time exits:       {metrics['time_exits']}")
    print(f"    Avg hold:         {metrics['avg_hold_hours']:.1f}h")
    print(f"    Final balance:    ${metrics['final_balance']:.2f}")
    print(f"")
    print(f"  MFE / MAE:")
    print(f"    Avg MFE (ATR):    {metrics.get('avg_mfe_atr', 0):.2f} ATR")
    print(f"    Avg MAE (ATR):    {metrics.get('avg_mae_atr', 0):.2f} ATR")
    print(f"    Avg MFE % of TP:  {metrics.get('avg_mfe_pct_of_tp', 0):.1f}%")
    print(f"    Avg MAE % of SL:  {metrics.get('avg_mae_pct_of_sl', 0):.1f}%")
    print(f"    Avg time profit:  {metrics.get('avg_time_in_profit_bars', 0):.1f} bars")
    print(f"    Avg time loss:    {metrics.get('avg_time_in_loss_bars', 0):.1f} bars")
    print(f"")
    print(f"  FEES:")
    print(f"    Total fees:       ${metrics.get('total_fees_usd', 0):.2f}")
    print(f"    Total gross PnL:  ${metrics.get('total_gross_pnl_usd', 0):.2f}")
    print(f"")
    print(f"  PER-SYMBOL BREAKDOWN:")
    for sym, s in sorted(metrics.get("per_symbol", {}).items()):
        print(f"    {sym:<12}  n={s['n']:>4}  WR={s['win_rate']:>5}%  PF={s['pf']:>5}  exp=${s['exp']:>7}  pnl=${s['pnl']:>10}")
    print(f"")
    print(f"  PER-SESSION BREAKDOWN:")
    for sess, s in sorted(metrics.get("per_session", {}).items()):
        print(f"    {sess:<16}  n={s['n']:>4}  WR={s['win_rate']:>5}%  PF={s['pf']:>5}  exp=${s['exp']:>7}")
    print(f"")
    print(f"  PER-REGIME BREAKDOWN:")
    for reg, s in sorted(metrics.get("per_regime", {}).items()):
        print(f"    {reg:<12}  n={s['n']:>4}  WR={s['win_rate']:>5}%  PF={s['pf']:>5}  exp=${s['exp']:>7}")
    print(f"")
    print(f"  PER-STRATEGY BREAKDOWN:")
    for strat, s in sorted(metrics.get("per_strategy", {}).items()):
        print(f"    {strat:<20}  n={s['n']:>4}  WR={s['win_rate']:>5}%  PF={s['pf']:>5}  exp=${s['exp']:>7}")
    print(f"")
    print(f"  PER-DQS-BAND BREAKDOWN:")
    for band, s in sorted(metrics.get("per_dqs_band", {}).items()):
        print(f"    DQS {band:<8}  n={s['n']:>4}  WR={s['win_rate']:>5}%  PF={s['pf']:>5}  exp=${s['exp']:>7}")
    print(f"")
    print(f"  REJECT REASONS:")
    for reason, count in sorted(metrics.get("reject_reasons", {}).items(), key=lambda x: -x[1]):
        print(f"    {reason:<40}  {count}")
    print(f"")
    print(f"  VALIDATION GATES:")
    for gate, passed in gates.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"    {gate}: {status}")
    all_pass = all(gates.values())
    print(f"    ALL GATES: {'✅ PASS' if all_pass else '❌ FAIL'}")
    print(f"{'='*70}")


# ── CSV OUTPUT ──────────────────────────────────────────────────────────────

def write_csv(records: List[SignalRecord], filepath: str, append: bool = False):
    """Write signal records to CSV. Append if file exists and append=True."""
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    mode = "a" if (append and file_exists) else "w"

    with open(filepath, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if mode == "w":
            writer.writeheader()
        for r in records:
            writer.writerow({
                "symbol": r.symbol,
                "side": r.side,
                "entry_date": r.entry_date,
                "entry_price": r.entry_price,
                "sl_price": r.sl_price,
                "tp_price": r.tp_price,
                "dqs_score": r.dqs_score,
                "regime": r.regime,
                "strategy": r.strategy,
                "approved": r.approved,
                "reject_reason": r.reject_reason,
                "promoted": r.promoted,
                "exit_date": r.exit_date,
                "exit_price": r.exit_price,
                "exit_reason": r.exit_reason,
                "hours_held": r.hours_held,
                "pnl_usd": r.pnl_usd,
                "pnl_pct": r.pnl_pct,
                "risk_usd": r.risk_usd,
                "kelly_fraction": r.kelly_fraction,
                "win": r.win,
                "notes": r.notes,
                "intent": r.intent,
                "outcome": r.outcome,
                "crowded": r.crowded,
                "data_quality": r.data_quality,
                "sl_original": r.sl_original,
                "atr_at_entry": r.atr_at_entry,
                "calibration_prob": r.calibration_prob,
                "calibration_level": r.calibration_level,
                # ── Market state at entry ──
                "rsi_entry": r.rsi_entry,
                "adx_entry": r.adx_entry,
                "atr_pct_entry": r.atr_pct_entry,
                "volume_entry": r.volume_entry,
                "turnover_entry": r.turnover_entry,
                "oi_entry": r.oi_entry,
                "funding_rate_entry": r.funding_rate_entry,
                "buy_ratio_entry": r.buy_ratio_entry,
                "sell_ratio_entry": r.sell_ratio_entry,
                "bull_4h_entry": r.bull_4h_entry,
                "ctx_adx_entry": r.ctx_adx_entry,
                "ctx_log_ret_4h_entry": r.ctx_log_ret_4h_entry,
                "session": r.session,
                "efficiency_ratio_entry": r.efficiency_ratio_entry,
                "atr_rank_entry": r.atr_rank_entry,
                # ── Market state at exit ──
                "rsi_exit": r.rsi_exit,
                "adx_exit": r.adx_exit,
                "atr_exit": r.atr_exit,
                "atr_pct_exit": r.atr_pct_exit,
                "volume_exit": r.volume_exit,
                "regime_exit": r.regime_exit,
                "close_exit": r.close_exit,
                # ── MFE / MAE ──
                "mfe_price": r.mfe_price,
                "mfe_atr": r.mfe_atr,
                "mfe_pct_of_tp": r.mfe_pct_of_tp,
                "mae_price": r.mae_price,
                "mae_atr": r.mae_atr,
                "mae_pct_of_sl": r.mae_pct_of_sl,
                "bars_to_mfe": r.bars_to_mfe,
                "bars_to_mae": r.bars_to_mae,
                "time_in_profit_bars": r.time_in_profit_bars,
                "time_in_loss_bars": r.time_in_loss_bars,
                # ── Signal breakdown ──
                "tech_raw": r.tech_raw,
                "struct_raw": r.struct_raw,
                "ctx_raw": r.ctx_raw,
                "contra_penalty": r.contra_penalty,
                "direction": r.direction,
                "calibrated_prob_signal": r.calibrated_prob_signal,
                # ── Portfolio state at entry ──
                "equity_at_entry": r.equity_at_entry,
                "open_trades_at_entry": r.open_trades_at_entry,
                "open_risk_at_entry": r.open_risk_at_entry,
                "margin_used_at_entry": r.margin_used_at_entry,
                "risk_budget_used_pct": r.risk_budget_used_pct,
                # ── HMM ──
                "hmm_confidence": r.hmm_confidence,
                "hmm_regime_raw": r.hmm_regime_raw,
                # ── Fees ──
                "fees_usd": r.fees_usd,
                "gross_pnl_usd": r.gross_pnl_usd,
                # ── Position sizing ──
                "notional": r.notional,
                "qty": r.qty,
                "risk_tier": r.risk_tier,
                "kelly_band": r.kelly_band,
                "sl_dist_atr": r.sl_dist_atr,
                "tp_dist_atr": r.tp_dist_atr,
                "rr_ratio": r.rr_ratio,
                "pat_wedge_detected": r.pat_wedge_detected,
                "pat_wedge_type": r.pat_wedge_type,
                "pat_wedge_strength": r.pat_wedge_strength,
                "pat_asc_triangle_detected": r.pat_asc_triangle_detected,
                "pat_desc_triangle_detected": r.pat_desc_triangle_detected,
                "pat_channel_detected": r.pat_channel_detected,
                "pat_channel_type": r.pat_channel_type,
                "pat_trend_break_detected": r.pat_trend_break_detected,
                "pat_trend_break_direction": r.pat_trend_break_direction,
                "pat_trend_break_retest": r.pat_trend_break_retest,
                "pat_sr_level_count": r.pat_sr_level_count,
                "pat_near_support": r.pat_near_support,
                "pat_near_resistance": r.pat_near_resistance,
                "pat_sr_recency_weighted_strength": r.pat_sr_recency_weighted_strength,
                "pat_rising_support_detected": r.pat_rising_support_detected,
                "pat_falling_resistance_detected": r.pat_falling_resistance_detected,
                "pat_bos_detected": r.pat_bos_detected,
                "pat_bos_direction": r.pat_bos_direction,
                "pat_choch_detected": r.pat_choch_detected,
                "pat_choch_direction": r.pat_choch_direction,
            })


# ── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VLTHR Backtest Runner")
    parser.add_argument("--mode", choices=["control", "treatment_a_phase1", "treatment_b_phase2"],
                        default="control", help="Backtest mode")
    parser.add_argument("--all", action="store_true", help="Run all three modes sequentially")
    parser.add_argument("--start", type=str, default="2026-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-06-30", help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--walk-forward", action="store_true", help="Split 70% IS / 30% OOS")
    parser.add_argument("--quiet", action="store_true", help="Reduce verbose output")
    parser.add_argument("--latency-bars", type=int, default=0,
                        help="Simulate latency by delaying signals N bars")
    parser.add_argument("--data-gap-prob", type=float, default=0.0,
                        help="Probability (0-1) of data gap per symbol per bar")
    parser.add_argument("--downtime", type=str, default=None,
                        help="Downtime periods as JSON list of [start, end] ISO strings")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for data gap simulation (default: 42)")
    args = parser.parse_args()

    output_path = args.output or str(_BACKTEST_DIR / "backtest_results.csv")
    verbose = not args.quiet

    # Parse downtime periods
    downtime_periods = []
    if args.downtime:
        try:
            raw = json.loads(args.downtime)
            for pair in raw:
                downtime_periods.append((pd.Timestamp(pair[0], tz="UTC"),
                                         pd.Timestamp(pair[1], tz="UTC")))
        except Exception as e:
            print(f"  [WARN] Failed to parse downtime: {e}")

    modes = ["control", "treatment_a_phase1", "treatment_b_phase2"] if args.all else [args.mode]

    all_metrics = {}

    for i, mode in enumerate(modes):
        print(f"\n{'#'*70}")
        print(f"  Running mode: {mode}")
        print(f"{'#'*70}")

        engine = BacktestEngine(mode, args.start, args.end,
                                walk_forward=args.walk_forward, verbose=verbose,
                                latency_bars=args.latency_bars,
                                data_gap_prob=args.data_gap_prob,
                                downtime_periods=downtime_periods,
                                seed=args.seed)
        records = engine.run()

        # Write CSV
        append = i > 0  # append for subsequent modes
        write_csv(records, output_path, append=append)
        print(f"  CSV written to: {output_path} ({len(records)} rows, {'appended' if append else 'new'})")

        # Metrics
        metrics = compute_metrics(records)
        gates = check_gates(metrics)
        all_metrics[mode] = (metrics, gates)
        print_report(metrics, gates, mode)

        # Walk-forward OOS
        if args.walk_forward and engine.is_end:
            oos_records = [r for r in records if r.entry_date >= engine.is_end.strftime("%Y-%m-%d")]
            if oos_records:
                oos_metrics = compute_metrics(oos_records)
                oos_gates = check_gates(oos_metrics)
                print(f"\n  --- OOS Results (after {engine.is_end.date()}) ---")
                print_report(oos_metrics, oos_gates, f"{mode}_OOS")

    # Summary comparison
    if len(modes) > 1:
        print(f"\n{'='*70}")
        print(f"  COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"  {'Metric':<25} {'Control':>12} {'Treatment A':>12} {'Treatment B':>12}")
        print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*12}")
        key_metrics = ["total_signals", "approved", "closed_trades", "win_rate_pct",
                       "profit_factor", "expectancy_usd", "max_drawdown_pct",
                       "expired_rate_pct", "tp_hit_rate_pct", "final_balance"]
        for key in key_metrics:
            vals = []
            for m in modes:
                metrics = all_metrics.get(m, (None, None))[0]
                if metrics:
                    vals.append(f"{metrics[key]:>12}" if isinstance(metrics[key], (int, float)) else f"{str(metrics[key]):>12}")
                else:
                    vals.append(f"{'N/A':>12}")
            print(f"  {key:<25} {vals[0]} {vals[1] if len(vals) > 1 else '':>12} {vals[2] if len(vals) > 2 else '':>12}")

        print(f"\n  GATES:")
        for gate_name in ["win_rate_ge_30", "profit_factor_ge_1.5", "expectancy_positive",
                          "max_dd_lt_15", "expired_rate_lt_30", "tp_hit_rate_ge_40"]:
            vals = []
            for m in modes:
                gates = all_metrics.get(m, (None, None))[1]
                if gates:
                    vals.append("✅" if gates.get(gate_name) else "❌")
                else:
                    vals.append("N/A")
            print(f"    {gate_name:<25} {vals[0]:>6} {vals[1] if len(vals) > 1 else '':>6} {vals[2] if len(vals) > 2 else '':>6}")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
