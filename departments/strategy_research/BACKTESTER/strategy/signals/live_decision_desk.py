"""
PullBackToTrend — Live Decision Desk v2
Confidence-scored trade setups. Only HIGH (>=75) signals are actionable.
Uses OHLCV + Orderbook + Open Interest + Funding Rate.

Usage:
  python departments/strategy_research/BACKTESTER/strategy/signals/live_decision_desk.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))
from data_loader import load_symbol_tf, REPO_ROOT
from indicators import add_atr, add_rsi, add_adx, classify_session

sys.path.insert(0, str(Path(__file__).resolve().parent))
from confidence_engine import load_enriched, load_orderbook_features, score_signal, SYMBOL_QUALITY

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
SESSIONS = {"london", "ny_late"}
PARAMS = {"rsi_threshold": 40, "sl_mult": 1.5, "tp_mult": 2.0, "adx_min": 25.0}
ACCOUNT_CAPITAL = 1000.0
RISK_PCT = 1.0
LEVERAGE = 4.0


def load_and_compute(symbol):
    exec_df = load_symbol_tf(symbol, "15m")
    exec_df["timestamp"] = pd.to_datetime(exec_df["timestamp"], utc=True)
    exec_df = exec_df.sort_values("timestamp").reset_index(drop=True)

    ctx_df = load_symbol_tf(symbol, "4h")
    ctx_df["timestamp"] = pd.to_datetime(ctx_df["timestamp"], utc=True)
    ctx_df = ctx_df.sort_values("timestamp").reset_index(drop=True)

    exec_df = add_atr(exec_df, 14)
    exec_df = add_rsi(exec_df, 14)

    ctx_df = add_adx(ctx_df, 14)
    ctx_df["log_ret_4h"] = np.log(ctx_df["close"] / ctx_df["close"].shift(1))
    ctx_df["ema8"] = ctx_df["close"].ewm(span=8, adjust=False).mean()
    ctx_df["ema21"] = ctx_df["close"].ewm(span=21, adjust=False).mean()
    ctx_df["bull_4h"] = (ctx_df["ema8"] > ctx_df["ema21"]).astype(int)

    ctx = ctx_df[["timestamp", "adx", "plus_di", "minus_di", "log_ret_4h", "bull_4h", "close"]].copy()
    ctx = ctx.rename(columns={c: f"ctx_{c}" for c in ctx.columns if c != "timestamp"})

    merged = pd.merge_asof(
        exec_df.sort_values("timestamp"),
        ctx.sort_values("timestamp"),
        on="timestamp",
        direction="backward"
    )
    merged["session"] = merged["timestamp"].apply(classify_session)
    return merged


def classify_current(row):
    adx = row.get("ctx_adx", 0)
    log_ret = row.get("ctx_log_ret_4h", 0)
    rsi = row.get("rsi", 100)
    session = row.get("session", "")

    g_sess = session in SESSIONS
    g_adx = adx >= PARAMS["adx_min"]
    g_dir = log_ret > 0
    g_rsi = rsi < PARAMS["rsi_threshold"]

    n_pass = sum([g_sess, g_adx, g_dir, g_rsi])

    if g_adx and g_dir and g_rsi and g_sess:
        status = "SIGNAL_ACTIVE"
    elif g_adx and g_dir and rsi < 50:
        status = "APPROACHING"
    elif g_adx and g_dir:
        status = "WAITING_RSI"
    elif g_adx and not g_dir:
        status = "4H_BEARISH"
    elif not g_adx:
        status = "NO_TREND"
    else:
        status = "NOT_READY"

    return status, n_pass, g_sess, g_adx, g_dir, g_rsi


def calculate_trade_setup(symbol, price, atr):
    sl_dist = atr * PARAMS["sl_mult"]
    tp_dist = atr * PARAMS["tp_mult"]
    sl_price = round(price - sl_dist, 4)
    tp_price = round(price + tp_dist, 4)
    sl_pct = sl_dist / price * 100
    tp_pct = tp_dist / price * 100

    dollar_risk = ACCOUNT_CAPITAL * (RISK_PCT / 100)
    pos_size = dollar_risk / (sl_pct / 100)
    leveraged = pos_size * LEVERAGE
    qty = leveraged / price

    return {
        "symbol": symbol,
        "entry_price": round(price, 4),
        "atr_15m": round(atr, 4),
        "sl_price": sl_price,
        "tp_price": tp_price,
        "sl_distance_usd": round(sl_dist, 4),
        "tp_distance_usd": round(tp_dist, 4),
        "sl_distance_pct": round(sl_pct, 3),
        "tp_distance_pct": round(tp_pct, 3),
        "risk_reward": round(PARAMS["tp_mult"] / PARAMS["sl_mult"], 2),
        "dollar_risk": round(dollar_risk, 2),
        "position_size_1x": round(pos_size, 2),
        "leveraged_notional": round(leveraged, 2),
        "qty_contracts": round(qty, 4),
    }


def run_desk():
    now = datetime.now(timezone.utc)
    is_session = now.hour in list(range(7, 13)) + list(range(20, 24))

    print("=" * 100)
    print(f"  PULLBACKTOTREND — LIVE DECISION DESK v2 (ENHANCED)")
    print(f"  Time: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC | Session: {'ACTIVE' if is_session else 'CLOSED'}")
    print(f"  Account: ${ACCOUNT_CAPITAL:,.0f} | Risk: {RISK_PCT}% | Leverage: {LEVERAGE}x")
    print(f"  7 Gates: Session | ADX | 4h Dir | EMA | RSI | Funding | OI")
    print(f"  Realistic Live: Sharpe ~2.5-3.5 | Win% ~48-54% | Expect ~20-40 trades/year")
    print(f"  Thresholds: HIGH >=75 (TRADE) | MEDIUM 50-74 (WATCH) | LOW <50 (IGNORE)")
    print("=" * 100)

    if not is_session:
        print(f"\n  ⚠️  NO TRADING SESSION ACTIVE")
        print(f"      Next London:  07:00 UTC  ({(7 - now.hour) % 24}h away)")
        print(f"      Next NY Late: 20:00 UTC  ({(20 - now.hour) % 24}h away)")

    all_results = []

    for symbol in SYMBOLS:
        try:
            df = load_and_compute(symbol)
        except Exception as e:
            print(f"\n  {symbol}: DATA ERROR — {e}")
            continue

        last = df.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0
        rsi = float(last["rsi"])
        adx = float(last["ctx_adx"])
        log_ret = float(last["ctx_log_ret_4h"]) if not pd.isna(last["ctx_log_ret_4h"]) else 0
        session = last["session"]

        status, n_pass, g_sess, g_adx, g_dir, g_rsi = classify_current(last)
        setup = calculate_trade_setup(symbol, price, atr)

        # Confidence scoring
        enriched = load_enriched(symbol)
        ob = load_orderbook_features(symbol)

        if enriched is not None and len(enriched):
            total, breakdown = score_signal(symbol, enriched, ob, session)
        else:
            total, breakdown = 0, {"grade": "NO_DATA", "tradeable": False}

        setup["status"] = status
        setup["rsi"] = round(rsi, 1)
        setup["adx"] = round(adx, 1)
        setup["session"] = session
        setup["4h_dir"] = "UP" if g_dir else "DOWN"
        setup["gates"] = f"{n_pass}/4"
        setup["confidence"] = total
        setup["grade"] = breakdown.get("grade", "UNKNOWN")
        setup["tradeable"] = breakdown.get("tradeable", False)
        setup["sharpe"] = SYMBOL_QUALITY.get(symbol, 0)
        setup["technical_score"] = breakdown.get("technical", 0)
        setup["market_structure_score"] = breakdown.get("market_structure", 0)
        setup["funding_oi_score"] = breakdown.get("funding_oi", 0)
        setup["session_symbol_score"] = breakdown.get("session_symbol", 0)
        setup["missing"] = []
        if not g_sess: setup["missing"].append("session")
        if not g_adx: setup["missing"].append("adx")
        if not g_dir: setup["missing"].append("4h_dir")
        if not g_rsi: setup["missing"].append(f"rsi({rsi:.0f})")

        all_results.append(setup)

    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values("confidence", ascending=False)

    # ---- SECTION 1: HIGH CONFIDENCE TRADE SIGNALS ----
    high = results_df[results_df["grade"] == "HIGH"]
    if len(high):
        print(f"\n  🟢 HIGH CONFIDENCE SIGNALS — TRADE THESE ({len(high)} found)")
        print("-" * 100)
        for _, row in high.iterrows():
            print(f"""
    ┌─ {row['symbol']} — CONFIDENCE {row['confidence']}/100 ─────────────────────────────┐
    │ Grade: HIGH | Status: {row['status']} | Session: {row['session']}
    │ Price: ${row['entry_price']:,.4f}  |  RSI(15m): {row['rsi']:.1f}  |  ADX(4h): {row['adx']:.1f}
    │ Score Breakdown: Tech {row['technical_score']}/40 | OB {row['market_structure_score']}/30 | Fund/OI {row['funding_oi_score']}/20 | Sess {row['session_symbol_score']}/10
    │
    │ 🎯 ENTRY:   ${row['entry_price']:,.4f}
    │ 🛑 SL:      ${row['sl_price']:,.4f}  ({row['sl_distance_pct']:.3f}%)
    │ 🏁 TP:      ${row['tp_price']:,.4f}  ({row['tp_distance_pct']:.3f}%)
    │
    │ Position:   {row['qty_contracts']} contracts @ 4x
    │ Risk:       ${row['dollar_risk']:.2f}  (1% of ${ACCOUNT_CAPITAL:,.0f})
    │ R:R:        1 : {row['risk_reward']}
    │
    │ ⚡ ACTION: Place LIMIT order at ${row['entry_price']:,.4f}
    │            SL ${row['sl_price']:,.4f}  |  TP ${row['tp_price']:,.4f}
    │            Verify: 4x cross, LONG, qty {row['qty_contracts']}
    │            THEN: Record trade in 60-trade tracker (STRATEGY.md)
    └──────────────────────────────────────────────────────────────────────────────────────┘""")

    # ---- SECTION 2: MEDIUM — WATCH ----
    medium = results_df[results_df["grade"] == "MEDIUM"]
    if len(medium):
        print(f"\n  🟡 MEDIUM CONFIDENCE — WATCH, DO NOT TRADE ({len(medium)} found)")
        print("-" * 100)
        for _, row in medium.iterrows():
            print(f"    {row['symbol']:8s} | Conf: {row['confidence']:>2.0f}/100 | Status: {row['status']:12s} | RSI: {row['rsi']:>5.1f} | ADX: {row['adx']:>5.1f} | Missing: {', '.join(row['missing']) if row['missing'] else 'None'}")

    # ---- SECTION 3: LOW — IGNORE ----
    low = results_df[results_df["grade"] == "LOW"]
    if len(low):
        print(f"\n  🔴 LOW CONFIDENCE — IGNORE ({len(low)} found)")
        print("-" * 100)
        for _, row in low.iterrows():
            print(f"    {row['symbol']:8s} | Conf: {row['confidence']:>2.0f}/100 | Status: {row['status']:12s} | RSI: {row['rsi']:>5.1f} | ADX: {row['adx']:>5.1f}")

    # ---- SUMMARY TABLE ----
    print(f"\n{'=' * 100}")
    print(f"  CONFIDENCE SUMMARY TABLE")
    print(f"{'=' * 100}")
    summary = results_df[["symbol", "grade", "confidence", "status", "rsi", "adx", "session", "4h_dir", "entry_price", "sl_price", "tp_price", "risk_reward"]].copy()
    summary.columns = ["Symbol", "Grade", "Conf", "Status", "RSI", "ADX", "Session", "4h", "Price", "SL", "TP", "R:R"]
    print(summary.to_string(index=False))

    # Save CSV
    out_dir = Path(__file__).resolve().parent
    ts = now.strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"live_decision_{ts}.csv"
    results_df.to_csv(csv_path, index=False)
    print(f"\n  💾 Saved: {csv_path}")

    # Tradeable signals summary
    tradeable = results_df[results_df["tradeable"] == True]
    print(f"\n{'=' * 100}")
    print(f"  TRADER'S VERDICT")
    print(f"{'=' * 100}")
    if len(tradeable):
        print(f"  ✅ {len(tradeable)} HIGH-CONFIDENCE SIGNALS READY TO TRADE")
        for _, row in tradeable.iterrows():
            print(f"     → {row['symbol']} @ ${row['entry_price']:,.4f} (Conf: {row['confidence']}/100)")
        print(f"\n  Execute now. Record in your 60-trade tracker.")
    else:
        print(f"  ❌ NO HIGH-CONFIDENCE SIGNALS RIGHT NOW")
        print(f"     All symbols below 75/100 confidence threshold.")
        print(f"     Wait for: RSI < 45-50 | ADX ≥ 25-35 | Funding ≤ 0 | OI rising")
        print(f"     Realistic frequency: ~1-2 signals per week across all 6 symbols")

    print(f"{'=' * 100}")
    print("  7-GATE CHECKLIST BEFORE EVERY TRADE:")
    print("    1. Session active? (London/NY Late)")
    print("    2. ADX ≥ 25? (trend confirmed)")
    print("    3. 4h bar bullish? (log_ret > 0)")
    print("    4. EMA8 > EMA21? (trend alignment)")
    print("    5. RSI < 45-50? (pullback deep enough)")
    print("    6. Funding ≤ 0.0001? (not crowded long)")
    print("    7. OI rising vs 24h? (new money entering)")
    print("  REALISTIC EXPECTATIONS: Sharpe ~2.5-3.5 | Win% ~48-54% | 20-40 trades/year")
    print("=" * 100)


if __name__ == "__main__":
    run_desk()
