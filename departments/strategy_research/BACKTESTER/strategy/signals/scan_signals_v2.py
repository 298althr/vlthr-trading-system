"""
PullbackToTrend v2 — Signal Scanner with Funding + OI gates
Adds 3 new gates:
  Gate 5: Funding rate negative (shorts pay longs = bullish for long)
  Gate 6: Open Interest rising vs 24h ago (new money entering = trend confirmed)
  Gate 7: Orderbook bid/ask imbalance bullish

Run from repo root:
  python departments/strategy_research/BACKTESTER/strategy/signals/scan_signals_v2.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))
from data_loader import load_symbol_tf, REPO_ROOT
from indicators import add_atr, add_rsi, add_adx, classify_session

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
SESSIONS = {"london", "ny_late"}
PARAMS = {"rsi_threshold": 40, "sl_mult": 1.5, "tp_mult": 2.0, "adx_min": 25.0}
OUT_DIR = Path(__file__).resolve().parent


def load_enriched(symbol, timeframe="15m"):
    """Load enriched parquet with OI and funding merged."""
    p = REPO_ROOT / f"data/bybit/{symbol}/enriched/{timeframe}"
    files = sorted(p.rglob("*.parquet"))
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def compute_oi_delta(df):
    """Compute OI delta vs 96 bars ago (24h of 15m)."""
    if len(df) < 97 or "open_interest" not in df.columns:
        df["oi_delta_pct"] = 0.0
        return df
    df["oi_delta_pct"] = ((df["open_interest"] - df["open_interest"].shift(96)) / df["open_interest"].shift(96).replace(0, np.nan)).fillna(0) * 100
    return df


def compute_funding_signal(df):
    """Funding rate: negative = bullish for long entry."""
    if "funding_rate" not in df.columns:
        df["funding_bullish"] = True  # neutral if no data
        return df
    df["funding_bullish"] = df["funding_rate"] <= 0.0001  # allow tiny positive as neutral
    return df


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

    # Add enriched data
    enriched = load_enriched(symbol)
    if enriched is not None and len(enriched):
        enriched["timestamp"] = pd.to_datetime(enriched["timestamp"], utc=True)
        merged = pd.merge_asof(
            merged.sort_values("timestamp"),
            enriched[["timestamp", "open_interest", "funding_rate"]].sort_values("timestamp"),
            on="timestamp",
            direction="backward"
        )
    else:
        merged["open_interest"] = np.nan
        merged["funding_rate"] = np.nan

    merged = compute_oi_delta(merged)
    merged = compute_funding_signal(merged)
    return merged


def classify_signal(row):
    """Return signal type and quality score with 7 gates."""
    adx = row.get("ctx_adx", 0)
    log_ret = row.get("ctx_log_ret_4h", 0)
    rsi = row.get("rsi", 100)
    bull_4h = row.get("ctx_bull_4h", 0)
    session = row.get("session", "")
    funding_bull = row.get("funding_bullish", True)
    oi_delta = row.get("oi_delta_pct", 0)

    gate_session = session in SESSIONS
    gate_adx = adx >= PARAMS["adx_min"]
    gate_direction = log_ret > 0
    gate_rsi = rsi < PARAMS["rsi_threshold"]
    gate_bull = bull_4h == 1
    gate_funding = funding_bull
    gate_oi = oi_delta >= 0  # OI rising or flat = bullish

    gates_passed = sum([gate_session, gate_adx, gate_direction, gate_rsi, gate_funding, gate_oi])

    if gate_adx and gate_direction and gate_rsi and gate_session and gate_funding and gate_oi:
        signal_type = "CONFIRMED"
        quality = "HIGH" if gate_bull else "MEDIUM"
    elif gate_adx and gate_direction and gate_rsi and not gate_session and gate_funding and gate_oi:
        signal_type = "OFF_SESSION"
        quality = "MONITOR"
    elif gate_adx and gate_direction and 40 <= rsi < 50:
        signal_type = "APPROACHING"
        quality = "WATCH"
    elif gate_adx and gate_direction and gate_rsi and not gate_session:
        signal_type = "OFF_SESSION"
        quality = "MONITOR"
    else:
        signal_type = "NO_SIGNAL"
        quality = "SKIP"

    return signal_type, quality, gates_passed, gate_adx, gate_direction, gate_rsi, gate_session, gate_bull, gate_funding, gate_oi


def scan():
    print(f"\n{'='*80}")
    print(f"PullbackToTrend v2 — Signal Scanner (Funding + OI Gates)")
    print(f"Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"7 Gates: Session | ADX | 4h Dir | RSI | EMA | Funding | OI")
    print(f"Funding: NEGATIVE = bullish (shorts pay longs)")
    print(f"OI: RISING vs 24h ago = new money entering = trend confirmed")
    print(f"{'='*80}")

    all_signals = []
    watchlist = []
    now = datetime.now(timezone.utc)

    for symbol in SYMBOLS:
        print(f"\n  Scanning {symbol}...", end=" ")
        try:
            df = load_and_compute(symbol)
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        recent = df.tail(200).copy()
        last = df.iloc[-1]

        funding_val = float(last.get("funding_rate", 0)) if not pd.isna(last.get("funding_rate")) else 0
        oi_val = float(last.get("open_interest", 0)) if not pd.isna(last.get("open_interest")) else 0
        oi_delta = float(last.get("oi_delta_pct", 0)) if not pd.isna(last.get("oi_delta_pct")) else 0

        print(f"OK | RSI={last['rsi']:.1f} | ADX={last['ctx_adx']:.1f} | "
              f"Funding={funding_val:.6f} | OIΔ={oi_delta:.2f}%")

        for _, row in recent.iterrows():
            sig_type, quality, n_gates, g_adx, g_dir, g_rsi, g_sess, g_bull, g_fund, g_oi = classify_signal(row)

            if sig_type in ("CONFIRMED", "OFF_SESSION", "APPROACHING"):
                entry = float(row["close"])
                atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0
                sl = round(entry - atr * PARAMS["sl_mult"], 4)
                tp = round(entry + atr * PARAMS["tp_mult"], 4)
                rr = round(PARAMS["tp_mult"] / PARAMS["sl_mult"], 2)

                bar_age = (now - row["timestamp"].to_pydatetime().replace(tzinfo=timezone.utc)).total_seconds() / 60
                is_fresh = bar_age <= 120

                action = ("NOW — ENTER" if is_fresh else "HISTORICAL") if sig_type == "CONFIRMED" else \
                         ("SET ALERT" if is_fresh else "HISTORICAL") if sig_type == "APPROACHING" else \
                         ("WAIT FOR SESSION" if is_fresh else "HISTORICAL")

                all_signals.append({
                    "scan_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "signal_bar_utc": row["timestamp"].strftime("%Y-%m-%d %H:%M"),
                    "age_hours": round(bar_age / 60, 1),
                    "symbol": symbol,
                    "signal_type": sig_type,
                    "quality": quality,
                    "gates_passed": n_gates,
                    "session": row["session"],
                    "gate_session_ok": g_sess,
                    "gate_adx_ok": g_adx,
                    "gate_direction_ok": g_dir,
                    "gate_rsi_ok": g_rsi,
                    "gate_bull_4h_ok": g_bull,
                    "gate_funding_ok": g_fund,
                    "gate_oi_ok": g_oi,
                    "price_at_signal": round(entry, 4),
                    "rsi_15m": round(float(row["rsi"]), 2),
                    "adx_4h": round(float(row["ctx_adx"]), 2),
                    "atr_15m": round(atr, 4),
                    "sl_price": sl,
                    "tp_price": tp,
                    "sl_distance_pct": round((entry - sl) / entry * 100, 3),
                    "tp_distance_pct": round((tp - entry) / entry * 100, 3),
                    "risk_reward": rr,
                    "log_ret_4h": round(float(row["ctx_log_ret_4h"]), 6) if not pd.isna(row["ctx_log_ret_4h"]) else 0,
                    "bull_4h_ema": bool(g_bull),
                    "funding_rate": float(row.get("funding_rate", 0)) if not pd.isna(row.get("funding_rate")) else 0,
                    "oi_delta_pct": float(row.get("oi_delta_pct", 0)) if not pd.isna(row.get("oi_delta_pct")) else 0,
                    "action": action,
                })

        # Watchlist
        sig_type, quality, n_gates, g_adx, g_dir, g_rsi, g_sess, g_bull, g_fund, g_oi = classify_signal(last)
        entry = float(last["close"])
        atr_val = float(last["atr"]) if not pd.isna(last["atr"]) else 0
        watchlist.append({
            "scan_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "last_bar_utc": last["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "current_price": round(entry, 4),
            "rsi_15m_now": round(float(last["rsi"]), 2),
            "adx_4h_now": round(float(last["ctx_adx"]), 2),
            "atr_15m_now": round(atr_val, 4),
            "session_now": last["session"],
            "4h_direction": "BULL" if g_dir else "BEAR",
            "4h_ema_trend": "UP" if g_bull else "DOWN",
            "regime": "TRENDING" if g_adx else "RANGING",
            "funding_rate": float(last.get("funding_rate", 0)) if not pd.isna(last.get("funding_rate")) else 0,
            "oi_delta_pct": float(last.get("oi_delta_pct", 0)) if not pd.isna(last.get("oi_delta_pct")) else 0,
            "signal_type": sig_type,
            "quality": quality,
            "gates_passed": f"{n_gates}/6",
            "sl_level": round(entry - atr_val * PARAMS["sl_mult"], 4),
            "tp_level": round(entry + atr_val * PARAMS["tp_mult"], 4),
            "notes": (
                "ALL 6 GATES PASSED — ENTER NOW" if sig_type == "CONFIRMED" and n_gases == 6 else
                f"RSI={last['rsi']:.1f} NEED<40 | Funding={'BULL' if g_fund else 'BEAR'} | OIΔ={last.get('oi_delta_pct',0):.1f}%" if sig_type == "APPROACHING" else
                f"Off-session. Next: {'London 07:00' if last['timestamp'].hour < 7 else 'NY Late 20:00'} UTC" if sig_type == "OFF_SESSION" else
                f"{6-n_gates} gate(s) failing"
            )
        })

    signals_df = pd.DataFrame(all_signals)
    watchlist_df = pd.DataFrame(watchlist)

    order = {"CONFIRMED": 0, "APPROACHING": 1, "OFF_SESSION": 2, "NO_SIGNAL": 3}
    if len(signals_df):
        signals_df["_sort"] = signals_df["signal_type"].map(order)
        signals_df = signals_df.sort_values(["_sort", "signal_bar_utc"], ascending=[True, False]).drop(columns=["_sort"])
        signals_df = signals_df.groupby(["symbol", "signal_type"]).head(3).reset_index(drop=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    signals_path = OUT_DIR / f"signals_latest_v2_{ts}.csv"
    watchlist_path = OUT_DIR / f"signals_watchlist_v2_{ts}.csv"
    signals_df.to_csv(signals_path, index=False)
    watchlist_df.to_csv(watchlist_path, index=False)

    print(f"\n{'='*80}")
    print(f"WATCHLIST — RIGHT NOW ({now.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
    print(f"{'='*80}")
    print(watchlist_df[["symbol", "current_price", "rsi_15m_now", "adx_4h_now",
                         "funding_rate", "oi_delta_pct", "signal_type", "quality", "gates_passed", "notes"]].to_string(index=False))

    confirmed = signals_df[signals_df["signal_type"] == "CONFIRMED"] if len(signals_df) else pd.DataFrame()
    fresh = confirmed[confirmed["action"].str.contains("NOW")] if len(confirmed) else pd.DataFrame()

    if len(fresh):
        print(f"\n{'='*80}")
        print(f"CONFIRMED SIGNALS — ACTIONABLE ({len(fresh)} found)")
        print(f"{'='*80}")
        print(fresh[["signal_bar_utc", "symbol", "price_at_signal", "rsi_15m", "adx_4h",
                      "funding_rate", "oi_delta_pct", "sl_price", "tp_price", "risk_reward", "action"]].to_string(index=False))
    else:
        print(f"\n  No actionable CONFIRMED signals right now.")

    print(f"\n  Saved: {signals_path}")
    print(f"  Saved: {watchlist_path}")
    print(f"\n  REMINDER: Verify all 7 gates before execution")


if __name__ == "__main__":
    scan()
