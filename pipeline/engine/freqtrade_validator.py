"""
FreqAI Triple-Barrier Validator (Phase 5)
=========================================
Python-native replacement for freqtrade container.
Downloads OHLCV from Bybit public API, generates triple-barrier labels,
and bootstraps pipeline_brain.py with historical training data.

Since Docker/freqtrade CLI are not available in this environment,
this module uses urllib + pandas to achieve the same goal.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple
from pathlib import Path

import numpy as np
import pandas as pd

# Bybit public API base
BYBIT_API = "https://api.bybit.com/v5/market/kline"

TRACKED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]


def download_ohlcv(symbol: str, interval: str = "15", days: int = 90) -> Optional[pd.DataFrame]:
    """
    Download 15m (or other) OHLCV from Bybit public API.
    Returns DataFrame with columns: timestamp, open, high, low, close, volume
    """
    all_rows: List[List[float]] = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    max_per_call = 1000

    # 15m interval mapping for Bybit
    bybit_interval = interval  # "15" works for Bybit v5

    print(f"[Validator] Downloading {days} days of {interval}m data for {symbol}...")

    while True:
        url = (
            f"{BYBIT_API}?category=linear&symbol={symbol}&interval={bybit_interval}"
            f"&end={end_ms}&limit={max_per_call}"
        )
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[Validator] API error for {symbol}: {e}")
            break

        if data.get("retCode") != 0:
            print(f"[Validator] Bybit API returned retCode={data.get('retCode')} : {data.get('retMsg')}")
            break

        rows = data.get("result", {}).get("list", [])
        if not rows:
            break

        # rows are newest-first from Bybit; reverse to chronological
        rows.reverse()

        parsed = []
        for r in rows:
            # Bybit returns: [timestamp, open, high, low, close, volume, turnover]
            parsed.append([
                int(r[0]),   # timestamp ms
                float(r[1]), # open
                float(r[2]), # high
                float(r[3]), # low
                float(r[4]), # close
                float(r[5]), # volume
            ])

        if not parsed:
            break

        all_rows.extend(parsed)
        earliest_ts = parsed[0][0]

        if earliest_ts <= start_ms:
            break
        if len(rows) < max_per_call:
            break

        # Move end_ms back to just before the earliest we got
        end_ms = earliest_ts - 1

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    print(f"[Validator] {symbol}: {len(df)} rows | {df['datetime'].min()} -> {df['datetime'].max()}")
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()
    return atr


def triple_barrier_label(
    df: pd.DataFrame,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    max_hold: int = 20,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Triple-barrier labeling (Lopez de Prado style).
    For each bar i:
      - upper = close[i] + pt_mult * ATR[i]
      - lower = close[i] - sl_mult * ATR[i]
      - Scan forward up to max_hold bars
      - If high touches upper first -> label = 1 (UP)
      - If low  touches lower first -> label = 0 (DOWN)
      - If neither within max_hold -> label based on net return vs 0.05% threshold
    """
    df = df.copy().reset_index(drop=True)
    df["atr"] = compute_atr(df, atr_period)
    df = df.dropna(subset=["atr"]).reset_index(drop=True)

    labels = []
    for i in range(len(df)):
        if i + max_hold >= len(df):
            labels.append(np.nan)
            continue

        entry_price = df.loc[i, "close"]
        atr = df.loc[i, "atr"]
        if atr <= 0 or pd.isna(atr):
            labels.append(np.nan)
            continue

        upper = entry_price + pt_mult * atr
        lower = entry_price - sl_mult * atr

        label = np.nan
        for j in range(i + 1, min(i + 1 + max_hold, len(df))):
            h = df.loc[j, "high"]
            l = df.loc[j, "low"]
            if h >= upper:
                label = 1
                break
            if l <= lower:
                label = 0
                break

        if pd.isna(label):
            # Neither barrier touched — use sign of return vs noise threshold
            ret = (df.loc[min(i + max_hold, len(df) - 1), "close"] - entry_price) / entry_price
            if ret > 0.0005:
                label = 1
            elif ret < -0.0005:
                label = 0
            else:
                label = np.nan  # ambiguous

        labels.append(label)

    df["label"] = labels
    df = df.dropna(subset=["label"]).reset_index(drop=True)
    df["label"] = df["label"].astype(int)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build feature columns matching pipeline_brain.FEATURE_KEYS.
    Returns DataFrame with added feature columns.
    """
    df = df.copy()
    df["returns"] = df["close"].pct_change()
    df["log_ret_4h"] = np.log(df["close"] / df["close"].shift(16))  # ~4h = 16 * 15m
    df["rsi"] = _rsi(df["close"], 14)
    df["adx"] = _adx(df["high"], df["low"], df["close"], 14)
    df["atr"] = compute_atr(df, 14)
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

    # Simplified stand-ins for microstructure (not available in OHLCV)
    df["funding_rate"] = 0.0
    df["oi_delta_pct"] = 0.0
    df["ob_spread_pct"] = 0.0
    df["ob_imbalance_pct"] = 0.0
    df["consecutive_improvements"] = 0
    df["consecutive_declines"] = 0
    df["runs_tracked"] = 0

    # dqs synthetic: map returns + rsi into a 0-100 score
    df["dqs"] = _synthetic_dqs(df)
    return df


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()

    plus_dm = (high - high.shift(1)).clip(lower=0)
    minus_dm = (low.shift(1) - low).clip(lower=0)
    plus_dm = plus_dm.where(plus_dm > minus_dm, 0.0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)

    plus_di = 100 * plus_dm.rolling(window=period, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.rolling(window=period, min_periods=period).mean() / atr
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    adx = dx.rolling(window=period, min_periods=period).mean()
    return adx


def _synthetic_dqs(df: pd.DataFrame) -> pd.Series:
    """Map recent price action into a crude 0-100 DQS-like score."""
    mom = df["close"].pct_change(4).fillna(0) * 1000  # 4-bar momentum
    rsi_norm = (df["rsi"].fillna(50) - 50) * 1.5
    vol_bonus = df["volume_ratio"].fillna(1).clip(0, 3) * 5
    score = 50 + mom + rsi_norm + vol_bonus
    return score.clip(0, 100).round(0)


def bootstrap_brain(conn, labeled_df: pd.DataFrame, symbol: str, model_type: str = "river_logistic") -> Dict:
    """
    Feed triple-barrier labeled historical data into pipeline_brain models.
    Returns diagnostics after bootstrap.
    """
    from pipeline_brain import PipelineBrain, _make_model, _save_model, _rolling_accuracy, _brier_score, _shadow_pnl

    brain = PipelineBrain(conn, model_type=model_type)
    model = brain._models.get(symbol)
    if model is None:
        model = _make_model()
        brain._models[symbol] = model

    if model is None:
        print(f"[Validator] River not available — cannot bootstrap {symbol}")
        return {"symbol": symbol, "bootstrapped": False, "reason": "river_unavailable"}

    n_learned = 0
    for _, row in labeled_df.iterrows():
        features = {
            "dqs": float(row.get("dqs", 50)),
            "rsi": float(row.get("rsi", 50)),
            "adx": float(row.get("adx", 25)),
            "log_ret_4h": float(row.get("log_ret_4h", 0)),
            "oi_delta_pct": float(row.get("oi_delta_pct", 0)),
            "funding_rate": float(row.get("funding_rate", 0)),
            "ob_spread_pct": float(row.get("ob_spread_pct", 0)),
            "ob_imbalance_pct": float(row.get("ob_imbalance_pct", 0)),
            "consecutive_improvements": int(row.get("consecutive_improvements", 0)),
            "consecutive_declines": int(row.get("consecutive_declines", 0)),
            "runs_tracked": int(row.get("runs_tracked", 0)),
        }
        label = int(row["label"])
        try:
            model.learn_one(features, label)
            n_learned += 1
        except Exception as e:
            print(f"[Validator] learn_one error at row {n_learned}: {e}")
            break

    # Save model state
    brain._save_model(symbol, n_learned)
    print(f"[Validator] {symbol}: bootstrapped with {n_learned} samples")

    return {
        "symbol": symbol,
        "bootstrapped": True,
        "n_learned": n_learned,
    }


def dry_run_backtest(
    df: pd.DataFrame,
    entry_dqs_min: int = 65,
    sl_pct: float = 0.02,
    tp_pct: float = 0.04,
    max_hold_bars: int = 20,
    fee_pct: float = 0.00055,
) -> pd.DataFrame:
    """
    Run a simple backtest on labeled OHLCV using VLTHR-style entry/exit rules.
    Entry: DQS >= entry_dqs_min
    Exit: SL/TP hit or max_hold reached
    Returns trades DataFrame.
    """
    trades = []
    in_trade = False
    entry_price = 0.0
    entry_bar = 0
    side = "LONG"

    for i in range(len(df)):
        if not in_trade:
            if df.loc[i, "dqs"] >= entry_dqs_min:
                in_trade = True
                entry_price = df.loc[i, "close"]
                entry_bar = i
                side = "LONG"
        else:
            current = df.loc[i, "close"]
            high = df.loc[i, "high"]
            low = df.loc[i, "low"]
            bars_held = i - entry_bar

            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)

            reason = None
            if high >= tp_price:
                reason = "TP_HIT"
                exit_price = tp_price
            elif low <= sl_price:
                reason = "SL_HIT"
                exit_price = sl_price
            elif bars_held >= max_hold_bars:
                reason = "TIME_EXIT"
                exit_price = current

            if reason:
                gross = (exit_price - entry_price) / entry_price
                fees = fee_pct * 2
                net = gross - fees
                trades.append({
                    "entry_bar": entry_bar,
                    "exit_bar": i,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "side": side,
                    "reason": reason,
                    "gross_return": gross,
                    "net_return": net,
                    "bars_held": bars_held,
                })
                in_trade = False

    return pd.DataFrame(trades)


def compare_vltr_vs_dry_run(
    symbol: str = "BTCUSDT",
    days: int = 90,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    max_hold: int = 20,
) -> Dict:
    """
    End-to-end Phase 5 validation for a single symbol.
    1. Download OHLCV
    2. Generate triple-barrier labels
    3. Run VLTHR-style dry-run backtest
    4. Return comparison metrics
    """
    df = download_ohlcv(symbol, "15", days)
    if df is None or len(df) < 500:
        return {"error": f"Insufficient data for {symbol}"}

    labeled = triple_barrier_label(df, pt_mult, sl_mult, max_hold)
    labeled = build_features(labeled)

    trades = dry_run_backtest(labeled)

    win_rate = trades["net_return"].gt(0).mean() if len(trades) > 0 else 0.0
    avg_return = trades["net_return"].mean() if len(trades) > 0 else 0.0
    total_return = trades["net_return"].sum() if len(trades) > 0 else 0.0
    n_trades = len(trades)

    label_dist = labeled["label"].value_counts(normalize=True).to_dict()

    return {
        "symbol": symbol,
        "bars": len(labeled),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_return, 6),
        "total_return": round(total_return, 4),
        "label_distribution": label_dist,
        "triple_barrier_params": {"pt_mult": pt_mult, "sl_mult": sl_mult, "max_hold": max_hold},
    }


def run_all_symbols(days: int = 30) -> List[Dict]:
    """Run comparison for all tracked symbols."""
    results = []
    for sym in TRACKED_SYMBOLS:
        try:
            res = compare_vltr_vs_dry_run(sym, days=days)
            results.append(res)
        except Exception as e:
            print(f"[Validator] {sym} failed: {e}")
            results.append({"symbol": sym, "error": str(e)})
    return results


if __name__ == "__main__":
    # Quick smoke test — 7 days for speed
    print("=== Freqtrade Validator Smoke Test ===")
    for sym in ["BTCUSDT"]:
        res = compare_vltr_vs_dry_run(sym, days=7)
        print(json.dumps(res, indent=2, default=str))
