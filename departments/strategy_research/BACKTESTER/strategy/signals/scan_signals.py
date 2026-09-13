"""
PullbackToTrend — Live Signal Scanner
Scans all 6 symbols against the 7-gate strategy conditions on the most recent data.
Outputs: signals_latest.csv and signals_watchlist.csv

Run from repo root:
  python departments/strategy_research/BACKTESTER/strategy/signals/scan_signals.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))
from data_loader import load_symbol_tf
from indicators import add_atr, add_rsi, add_adx, classify_session

# Import per-symbol proven config from portfolio_config
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "paper_trade_unzipped" / "vlthr-signal-dashboard" / "engine"))
from portfolio_config import SYMBOLS, get_symbol_params

SESSIONS = {"london", "ny_late"}
OUT_DIR = Path(__file__).resolve().parent


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


def classify_signal(row, symbol):
    """Return signal type and quality score using proven per-symbol params."""
    params = get_symbol_params(symbol)
    adx = row.get("ctx_adx", 0)
    log_ret = row.get("ctx_log_ret_4h", 0)
    rsi = row.get("rsi", 100)
    bull_4h = row.get("ctx_bull_4h", 0)
    session = row.get("session", "")

    gate_session = session in SESSIONS
    gate_adx = adx >= params["adx_min"]
    gate_direction = log_ret > 0
    gate_rsi = rsi < params["rsi_threshold"]
    gate_bull = bull_4h == 1

    gates_passed = sum([gate_session, gate_adx, gate_direction, gate_rsi])

    if gate_adx and gate_direction and gate_rsi and gate_session:
        signal_type = "CONFIRMED"
        quality = "HIGH" if gate_bull else "MEDIUM"
    elif gate_adx and gate_direction and gate_rsi and not gate_session:
        signal_type = "OFF_SESSION"
        quality = "MONITOR"
    elif gate_adx and gate_direction and params["rsi_threshold"] <= rsi < 50:
        signal_type = "APPROACHING"
        quality = "WATCH"
    elif gate_adx and gate_direction and rsi < params["rsi_threshold"] and not gate_session:
        signal_type = "OFF_SESSION"
        quality = "MONITOR"
    else:
        signal_type = "NO_SIGNAL"
        quality = "SKIP"

    return signal_type, quality, gates_passed, gate_adx, gate_direction, gate_rsi, gate_session, gate_bull


def scan(now: datetime = None):
    now = now or datetime.now(timezone.utc)
    print(f"\nPullbackToTrend Signal Scanner")
    print(f"Run at: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Sessions: London (07:00-12:00 UTC) | NY Late (20:00-00:00 UTC)")
    print("=" * 80)

    all_signals = []
    watchlist = []

    for symbol in SYMBOLS:
        params = get_symbol_params(symbol)
        print(f"\n  Scanning {symbol}...", end=" ")
        try:
            df = load_and_compute(symbol)
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        # Use last 200 bars (50h of 15m data) for recent signal scan
        recent = df.tail(200).copy()
        last = df.iloc[-1]
        last_4h_ctx = df[df["ctx_adx"].notna()].iloc[-1]

        print(f"OK — last bar: {last['timestamp'].strftime('%Y-%m-%d %H:%M')} UTC | "
              f"RSI={last['rsi']:.1f} | ADX={last['ctx_adx']:.1f} | "
              f"Session={last['session']}")

        # Scan last 200 bars for fired signals
        for _, row in recent.iterrows():
            sig_type, quality, n_gates, g_adx, g_dir, g_rsi, g_sess, g_bull = classify_signal(row, symbol)

            if sig_type in ("CONFIRMED", "OFF_SESSION", "APPROACHING"):
                entry = float(row["close"])
                atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0
                sl = round(entry - atr * params["sl_mult"], 4)
                tp = round(entry + atr * params["tp_mult"], 4)
                rr = round(params["tp_mult"] / params["sl_mult"], 2)

                bar_age_minutes = (now - row["timestamp"].to_pydatetime().replace(tzinfo=timezone.utc)).total_seconds() / 60
                is_fresh = bar_age_minutes <= 120  # within last 2 hours

                if sig_type == "CONFIRMED":
                    action = "NOW — ENTER" if is_fresh else "HISTORICAL — Already passed"
                elif sig_type == "APPROACHING":
                    action = "SET ALERT — RSI approaching threshold" if is_fresh else "HISTORICAL — RSI no longer approaching"
                else:
                    action = "WAIT FOR SESSION WINDOW" if is_fresh else "HISTORICAL — Outside session"

                record = {
                    "scan_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "signal_bar_utc": row["timestamp"].strftime("%Y-%m-%d %H:%M"),
                    "age_hours": round(bar_age_minutes / 60, 1),
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
                    "action": action,
                }
                all_signals.append(record)

        # Current bar watchlist entry (always)
        sig_type, quality, n_gates, g_adx, g_dir, g_rsi, g_sess, g_bull = classify_signal(last, symbol)
        entry = float(last["close"])
        atr_val = float(last["atr"]) if not pd.isna(last["atr"]) else 0
        watchlist.append({
            "scan_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
            "signal_type": sig_type,
            "quality": quality,
            "gates_passed": f"{n_gates}/4",
            "gate_session_ok": g_sess,
            "gate_adx_ok": g_adx,
            "gate_direction_ok": g_dir,
            "gate_rsi_ok": g_rsi,
            "gate_bull_4h_ok": g_bull,
            "sl_level": round(entry - atr_val * params["sl_mult"], 4),
            "tp_level": round(entry + atr_val * params["tp_mult"], 4),
            "notes": (
                "ALL GATES PASSED — ENTER ON NEXT CONFIRMED 15m CLOSE" if sig_type == "CONFIRMED" else
                f"RSI={last['rsi']:.1f} — NEED < {params['rsi_threshold']} | ADX={last['ctx_adx']:.1f}" if sig_type == "APPROACHING" else
                f"Signal exists but outside session window — next window: {'London 07:00 UTC' if last['timestamp'].hour < 7 else 'NY Late 20:00 UTC'}" if sig_type == "OFF_SESSION" else
                f"Not ready — {4 - n_gates} gate(s) failing"
            )
        })

    # --- Write outputs ---
    signals_df = pd.DataFrame(all_signals)
    watchlist_df = pd.DataFrame(watchlist)

    # Sort: CONFIRMED first, then APPROACHING, then OFF_SESSION
    order = {"CONFIRMED": 0, "APPROACHING": 1, "OFF_SESSION": 2, "NO_SIGNAL": 3}
    if len(signals_df):
        signals_df["_sort"] = signals_df["signal_type"].map(order)
        signals_df = signals_df.sort_values(["_sort", "signal_bar_utc"], ascending=[True, False]).drop(columns=["_sort"])
        # Keep only most recent 3 per symbol per signal type
        signals_df = signals_df.groupby(["symbol", "signal_type"]).head(3).reset_index(drop=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    signals_path = OUT_DIR / "signals_latest.csv"
    watchlist_path = OUT_DIR / "signals_watchlist.csv"

    signals_df.to_csv(signals_path, index=False)
    watchlist_df.to_csv(watchlist_path, index=False)

    print(f"\n{'=' * 80}")
    print(f"WATCHLIST — RIGHT NOW ({now.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
    print(f"{'=' * 80}")
    print(watchlist_df[["symbol", "current_price", "rsi_15m_now", "adx_4h_now",
                         "4h_direction", "regime", "signal_type", "quality", "gates_passed", "notes"]].to_string(index=False))

    confirmed = signals_df[signals_df["signal_type"] == "CONFIRMED"] if len(signals_df) else pd.DataFrame()
    approaching = signals_df[signals_df["signal_type"] == "APPROACHING"] if len(signals_df) else pd.DataFrame()

    fresh_confirmed = confirmed[confirmed["action"].str.contains("NOW")] if len(confirmed) else pd.DataFrame()
    hist_confirmed = confirmed[~confirmed["action"].str.contains("NOW")] if len(confirmed) else pd.DataFrame()

    if len(fresh_confirmed):
        print(f"\n{'=' * 80}")
        print(f"CONFIRMED SIGNALS — ACTIONABLE RIGHT NOW ({len(fresh_confirmed)} found)")
        print(f"{'=' * 80}")
        print(fresh_confirmed[["signal_bar_utc", "symbol", "price_at_signal", "rsi_15m", "adx_4h",
                          "sl_price", "tp_price", "risk_reward", "action"]].to_string(index=False))
    else:
        print(f"\n  No actionable CONFIRMED signals right now.")

    if len(hist_confirmed):
        print(f"\n  HISTORICAL CONFIRMED signals (last 50h, already passed):")
        print(hist_confirmed[["signal_bar_utc", "symbol", "price_at_signal", "rsi_15m", "adx_4h", "age_hours", "action"]].to_string(index=False))

    fresh_approaching = approaching[approaching["action"].str.contains("SET ALERT")] if len(approaching) else pd.DataFrame()
    if len(fresh_approaching):
        print(f"\n  APPROACHING signals right now (RSI 40-50, in trend): {len(fresh_approaching)}")
        print(fresh_approaching[["signal_bar_utc", "symbol", "rsi_15m", "adx_4h", "action"]].to_string(index=False))

    print(f"\n  Saved: {signals_path}")
    print(f"  Saved: {watchlist_path}")
    print(f"\n  REMINDER: Verify all 7 gates manually before execution (see STRATEGY.md)")


if __name__ == "__main__":
    scan()
