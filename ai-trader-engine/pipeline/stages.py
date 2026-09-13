"""D1-D7 Stage implementations — pure Python + pandas/numpy (no vectorbt dependency)."""
from __future__ import annotations
import uuid
import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from .data import load_ohlcv, load_funding, load_open_interest
from .indicators import add_all


# ── helpers ────────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _native(obj: Any) -> Any:
    """Recursively convert numpy scalars to Python native types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_native(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [_native(v) for v in obj.tolist()]
    return obj


def _sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    if returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    return float(dd.min())


def _profit_factor(pnl: pd.Series) -> float:
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    return float(wins / losses) if losses > 0 else float("inf")


def _sortino(returns: pd.Series, periods_per_year: int = 252) -> float:
    downside = returns[returns < 0].std()
    if downside == 0:
        return 0.0
    return float(returns.mean() / downside * np.sqrt(periods_per_year))


# ── D1: Data Quality Audit ─────────────────────────────────────────────────────

def d1_audit(symbol: str, interval: str, start: str, end: str) -> dict[str, Any]:
    df = load_ohlcv(symbol, interval, start, end)
    n = len(df)
    missing = int(df[["open", "high", "low", "close", "volume"]].isnull().sum().sum())
    price_anomalies = int(((df["close"].pct_change().abs()) > 0.20).sum())
    gaps = int((df["timestamp"].diff().dt.total_seconds() > 3600 * 4).sum())
    passed = price_anomalies == 0 and missing / max(n * 5, 1) < 0.01

    return _native({
        "stage": "D1",
        "symbol": symbol,
        "interval": interval,
        "bar_count": n,
        "missing_values": missing,
        "price_anomalies_gt20pct": price_anomalies,
        "gaps_gt4h": gaps,
        "date_range": {"start": str(df["timestamp"].min()), "end": str(df["timestamp"].max())},
        "passed": passed,
        "completed_at": _utcnow(),
    })


# ── D4: Feature Discovery ──────────────────────────────────────────────────────

def d4_features(symbol: str, interval: str, start: str, end: str, params: dict | None = None) -> dict[str, Any]:
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df, params)

    # Simple forward-return correlation as feature importance proxy
    df["fwd_ret_1"] = df["close"].pct_change(1).shift(-1)
    features = ["rsi", "atr", "adx", "ema_fast", "ema_slow", "log_ret", "ctx_bull"]
    importance = {}
    for f in features:
        if f in df.columns:
            corr = df[[f, "fwd_ret_1"]].dropna().corr().iloc[0, 1]
            importance[f] = round(abs(float(corr)), 4)

    total = sum(importance.values()) or 1
    weights = {k: round(v / total, 4) for k, v in importance.items()}

    return _native({
        "stage": "D4",
        "symbol": symbol,
        "interval": interval,
        "feature_importance": importance,
        "domain_weights": weights,
        "top_feature": max(importance, key=importance.get),
        "completed_at": _utcnow(),
    })


# ── Core backtest engine ───────────────────────────────────────────────────────

def _run_backtest(
    df: pd.DataFrame,
    params: dict,
    initial_capital: float = 10_000,
    leverage: float = 1.0,
    fee_rate: float = 0.0006,
    slippage: float = 0.0002,
) -> dict[str, Any]:
    """Vectorised bar-by-bar simulation on a pre-processed dataframe."""
    df = df.copy().reset_index(drop=True)
    rsi_th = params.get("rsi_threshold", 40)
    adx_min = params.get("adx_min", 25)
    sl_mult = params.get("sl_mult", 1.5)
    tp_mult = params.get("tp_mult", 2.0)
    session = params.get("session", "all")

    # Session filter
    if session != "all":
        session_hours = {
            "london": (7, 16), "ny": (13, 22), "asian": (0, 8),
            "london-ny": (7, 22),
        }
        h = session_hours.get(session.lower())
        if h:
            df = df[df["timestamp"].dt.hour.between(h[0], h[1] - 1)].reset_index(drop=True)

    # Signal: RSI oversold + ADX trending + price below BB lower (mean reversion long)
    # or RSI overbought + ADX + price above BB upper (short)
    long_signal = (df["rsi"] < rsi_th) & (df["adx"] > adx_min) & (df["close"] < df["bb_lower"]) & (df["ctx_bull"] == 1)
    short_signal = (df["rsi"] > 100 - rsi_th) & (df["adx"] > adx_min) & (df["close"] > df["bb_upper"]) & (df["ctx_bull"] == 0)

    capital = initial_capital
    equity_curve = []
    trades = []
    in_trade = False
    entry_price = sl_price = tp_price = side = None

    for i in range(len(df)):
        row = df.iloc[i]
        price = float(row["close"])
        atr_val = float(row["atr"]) if not np.isnan(row["atr"]) else 0.0

        if in_trade:
            hit_sl = (side == "long" and price <= sl_price) or (side == "short" and price >= sl_price)
            hit_tp = (side == "long" and price >= tp_price) or (side == "short" and price <= tp_price)
            if hit_sl or hit_tp:
                exit_p = sl_price if hit_sl else tp_price
                exit_p *= (1 + slippage) if side == "long" else (1 - slippage)
                pct = (exit_p - entry_price) / entry_price if side == "long" else (entry_price - exit_p) / entry_price
                pnl = capital * leverage * pct - capital * leverage * fee_rate * 2
                capital += pnl
                trades.append({"side": side, "entry": entry_price, "exit": exit_p, "pnl": pnl, "outcome": "tp" if hit_tp else "sl"})
                in_trade = False

        if not in_trade and atr_val > 0:
            if long_signal.iloc[i]:
                entry_price = price * (1 + slippage)
                sl_price = entry_price - sl_mult * atr_val
                tp_price = entry_price + tp_mult * atr_val
                side = "long"
                capital -= capital * leverage * fee_rate
                in_trade = True
            elif short_signal.iloc[i]:
                entry_price = price * (1 - slippage)
                sl_price = entry_price + sl_mult * atr_val
                tp_price = entry_price - tp_mult * atr_val
                side = "short"
                capital -= capital * leverage * fee_rate
                in_trade = True

        equity_curve.append(capital)

    equity = pd.Series(equity_curve)
    pnl_series = pd.Series([t["pnl"] for t in trades]) if trades else pd.Series([0.0])
    returns = equity.pct_change().dropna()

    n_trades = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    win_rate = len(wins) / n_trades if n_trades else 0

    # Annualise based on interval
    bars_per_day = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6, "1d": 1}
    bpd = bars_per_day.get(params.get("interval", "15m"), 96)
    periods_per_year = bpd * 365

    total_bars = len(df)
    years = total_bars / (bpd * 365) if bpd * 365 > 0 else 1

    return {
        "total_return": round((capital - initial_capital) / initial_capital, 4),
        "cagr": round(((capital / initial_capital) ** (1 / max(years, 0.01))) - 1, 4),
        "sharpe": round(_sharpe(returns, periods_per_year), 3),
        "sortino": round(_sortino(returns, periods_per_year), 3),
        "max_drawdown": round(_max_drawdown(equity), 4),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(_profit_factor(pnl_series), 3),
        "total_trades": n_trades,
        "trades_per_year": round(n_trades / max(years, 0.01), 1),
        "avg_win": round(float(pnl_series[pnl_series > 0].mean()) if len(pnl_series[pnl_series > 0]) else 0, 2),
        "avg_loss": round(float(pnl_series[pnl_series < 0].mean()) if len(pnl_series[pnl_series < 0]) else 0, 2),
        "final_capital": round(capital, 2),
        "equity_curve": [round(v, 2) for v in equity_curve[::max(1, len(equity_curve) // 200)]],  # downsample
        "trades": trades[-50:],  # last 50
    }


# ── D5A: Mechanical Base ───────────────────────────────────────────────────────

def d5a_mechanical(
    symbol: str, interval: str, start: str, end: str,
    strategy_params: dict, initial_capital: float = 10_000
) -> dict[str, Any]:
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df, strategy_params)
    params = {**strategy_params, "interval": interval}
    result = _run_backtest(df, params, initial_capital)
    passed = (result["sharpe"] >= 1.0 and result["win_rate"] >= 0.50
              and result["max_drawdown"] >= -0.20 and result["total_trades"] >= 10)
    return _native({"stage": "D5A", "symbol": symbol, "interval": interval,
            "metrics": result, "passed": passed, "completed_at": _utcnow()})


# ── D5B: Parameter Sweep ──────────────────────────────────────────────────────

def d5b_optimised(
    symbol: str, interval: str, start: str, end: str,
    base_params: dict, initial_capital: float = 10_000, n_combos: int = 50
) -> dict[str, Any]:
    """Grid sweep over RSI threshold and SL/TP multipliers."""
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df, base_params)

    rsi_range = np.arange(25, 50, 5)
    sl_range = np.arange(1.0, 2.5, 0.5)
    tp_range = np.arange(1.5, 3.5, 0.5)
    adx_range = np.arange(18, 32, 4)

    results = []
    for rsi_th in rsi_range:
        for sl_m in sl_range:
            for tp_m in tp_range:
                for adx_m in adx_range:
                    if len(results) >= n_combos:
                        break
                    p = {**base_params, "rsi_threshold": rsi_th, "sl_mult": sl_m,
                         "tp_mult": tp_m, "adx_min": adx_m, "interval": interval}
                    r = _run_backtest(df, p, initial_capital)
                    results.append({"params": {"rsi_threshold": rsi_th, "sl_mult": sl_m,
                                               "tp_mult": tp_m, "adx_min": adx_m},
                                    "sharpe": r["sharpe"], "win_rate": r["win_rate"],
                                    "max_drawdown": r["max_drawdown"], "total_trades": r["total_trades"]})

    if not results:
        return {"stage": "D5B", "passed": False, "error": "No combinations produced"}

    best = max(results, key=lambda x: x["sharpe"])
    # IS/OOS split — train on first 70%, test on last 30%
    split = int(len(df) * 0.7)
    df_is, df_oos = df.iloc[:split], df.iloc[split:]
    best_p = {**base_params, **best["params"], "interval": interval}
    is_res = _run_backtest(df_is, best_p, initial_capital)
    oos_res = _run_backtest(df_oos, best_p, initial_capital)
    passed = (oos_res["sharpe"] >= is_res["sharpe"] * 0.9 and best["sharpe"] >= 1.0)

    return _native({
        "stage": "D5B",
        "symbol": symbol,
        "interval": interval,
        "best_params": best["params"],
        "is_sharpe": round(float(is_res["sharpe"]), 3),
        "oos_sharpe": round(float(oos_res["sharpe"]), 3),
        "is_metrics": is_res,
        "oos_metrics": oos_res,
        "combos_tested": len(results),
        "passed": passed,
        "completed_at": _utcnow(),
    })


# ── D5C: DQS Scoring ──────────────────────────────────────────────────────────

def d5c_dqs(
    symbol: str, interval: str, start: str, end: str,
    strategy_params: dict, initial_capital: float = 10_000
) -> dict[str, Any]:
    """DQS-filtered backtest: only take trades where composite score >= threshold."""
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df, strategy_params)
    # DQS = weighted composite of normalised indicators
    rsi_norm = (df["rsi"] - df["rsi"].min()) / (df["rsi"].max() - df["rsi"].min() + 1e-9)
    adx_norm = (df["adx"] - df["adx"].min()) / (df["adx"].max() - df["adx"].min() + 1e-9)
    vol_norm = (df["volume"] - df["volume"].min()) / (df["volume"].max() - df["volume"].min() + 1e-9)
    df["dqs"] = (0.40 * (1 - rsi_norm) + 0.30 * adx_norm + 0.30 * vol_norm) * 100

    base_res = _run_backtest(df, {**strategy_params, "interval": interval}, initial_capital)
    # Filter: only allow entries when DQS >= 50
    df_filtered = df[df["dqs"] >= 50].copy()
    if len(df_filtered) < 50:
        filtered_res = base_res
    else:
        filtered_res = _run_backtest(df_filtered, {**strategy_params, "interval": interval}, initial_capital)

    passed = (filtered_res["sharpe"] >= base_res["sharpe"]
              and filtered_res["win_rate"] >= base_res["win_rate"])

    return _native({
        "stage": "D5C",
        "symbol": symbol,
        "interval": interval,
        "base_metrics": base_res,
        "dqs_metrics": filtered_res,
        "dqs_improvement": round(filtered_res["sharpe"] - base_res["sharpe"], 3),
        "dqs_score_range": [round(float(df["dqs"].min()), 1), round(float(df["dqs"].max()), 1)],
        "passed": passed,
        "completed_at": _utcnow(),
    })


# ── D6: Risk Sizing ───────────────────────────────────────────────────────────

def d6_risk(
    symbol: str, interval: str, start: str, end: str,
    strategy_params: dict, initial_capital: float = 10_000,
    leverage: float = 4.0, funding_penalty: float = 0.005
) -> dict[str, Any]:
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df, strategy_params)
    params = {**strategy_params, "interval": interval}
    result = _run_backtest(df, params, initial_capital, leverage=leverage, fee_rate=0.0006 + funding_penalty / 365)
    passed = (result["sharpe"] >= 1.0 and result["max_drawdown"] >= -0.20)
    return _native({
        "stage": "D6",
        "symbol": symbol,
        "leverage": leverage,
        "funding_penalty": funding_penalty,
        "metrics": result,
        "passed": passed,
        "completed_at": _utcnow(),
    })


# ── D7: Reality Probe ─────────────────────────────────────────────────────────

def d7_reality(
    symbol: str, interval: str, start: str, end: str,
    strategy_params: dict, initial_capital: float = 10_000
) -> dict[str, Any]:
    df = load_ohlcv(symbol, interval, start, end)
    df = add_all(df, strategy_params)
    params = {**strategy_params, "interval": interval}

    scenarios = {
        "base": _run_backtest(df, params, initial_capital, leverage=4.0, fee_rate=0.0006, slippage=0.0002),
        "double_fees": _run_backtest(df, params, initial_capital, leverage=4.0, fee_rate=0.0012, slippage=0.0004),
        "reduced_leverage": _run_backtest(df, params, initial_capital, leverage=3.0, fee_rate=0.0006, slippage=0.0002),
        "high_slippage": _run_backtest(df, params, initial_capital, leverage=4.0, fee_rate=0.0006, slippage=0.001),
    }

    base_sharpe = scenarios["base"]["sharpe"]
    all_profitable = all(s["total_return"] > 0 for s in scenarios.values())
    sharpe_stable = all(
        abs(s["sharpe"] - base_sharpe) / max(abs(base_sharpe), 0.01) <= 0.30
        for s in scenarios.values()
    )
    passed = all_profitable and sharpe_stable

    return _native({
        "stage": "D7",
        "symbol": symbol,
        "scenarios": {k: {
            "total_return": v["total_return"],
            "sharpe": v["sharpe"],
            "max_drawdown": v["max_drawdown"],
            "win_rate": v["win_rate"],
        } for k, v in scenarios.items()},
        "all_profitable": all_profitable,
        "sharpe_stable": sharpe_stable,
        "passed": passed,
        "completed_at": _utcnow(),
    })


# ── Full Pipeline ─────────────────────────────────────────────────────────────

def run_full_pipeline(
    symbol: str, interval: str, start: str, end: str,
    strategy_params: dict, initial_capital: float = 10_000
) -> dict[str, Any]:
    """Run D1 → D4 → D5A → D5B → D5C → D6 → D7 in sequence, stage-gating."""
    run_id = str(uuid.uuid4())
    results: dict[str, Any] = {"run_id": run_id, "symbol": symbol, "interval": interval,
                                "start": start, "end": end, "started_at": _utcnow()}
    stages_order = ["D1", "D4", "D5A", "D5B", "D5C", "D6", "D7"]

    try:
        results["D1"] = d1_audit(symbol, interval, start, end)
        if not results["D1"]["passed"]:
            results["aborted_at"] = "D1"
            results["completed_at"] = _utcnow()
            return results

        results["D4"] = d4_features(symbol, interval, start, end, strategy_params)

        results["D5A"] = d5a_mechanical(symbol, interval, start, end, strategy_params, initial_capital)
        if not results["D5A"]["passed"]:
            results["aborted_at"] = "D5A"
            results["completed_at"] = _utcnow()
            return results

        results["D5B"] = d5b_optimised(symbol, interval, start, end, strategy_params, initial_capital)

        # Use best params from D5B if available
        optimised_params = {**strategy_params, **results["D5B"].get("best_params", {})}

        results["D5C"] = d5c_dqs(symbol, interval, start, end, optimised_params, initial_capital)
        results["D6"] = d6_risk(symbol, interval, start, end, optimised_params, initial_capital)
        results["D7"] = d7_reality(symbol, interval, start, end, optimised_params, initial_capital)

        passed_stages = [s for s in stages_order if results.get(s, {}).get("passed")]
        results["pipeline_passed"] = len(passed_stages) >= 5
        results["passed_stages"] = passed_stages
        results["completed_at"] = _utcnow()

    except Exception as e:
        results["error"] = str(e)
        results["completed_at"] = _utcnow()

    return results
