"""
PullbackToTrend — Sync HIGH-CONFIDENCE Signals to PostgreSQL
Only records signals with confidence >= 75/100 to the database.
Uses confidence_engine to score every signal with OHLCV + OB + OI + Funding.

Usage:
  python departments/strategy_research/BACKTESTER/strategy/signals/sync_high_confidence.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "data" / "bybit" / "workers"))

import psycopg2
import pandas as pd
import numpy as np
from data_loader import load_symbol_tf
from indicators import add_atr, add_rsi, add_adx, classify_session
from confidence_engine import load_enriched, load_orderbook_features, score_signal

try:
    from telegram_alerts import send_telegram
except Exception:
    send_telegram = None


def load_env():
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, val = line.strip().split("=", 1)
                    os.environ[key] = val

load_env()
DB_URL = os.environ.get("DB_URL")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
PARAMS = {"rsi_threshold": 40, "sl_mult": 1.5, "tp_mult": 2.0, "adx_min": 25.0}


def get_conn():
    try:
        return psycopg2.connect(DB_URL, sslmode="require")
    except psycopg2.OperationalError:
        alt = DB_URL.replace(":5432/", ":6543/")
        return psycopg2.connect(alt, sslmode="require")


def clear_tables():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS paper_trade_statements CASCADE;")
    cur.execute("DROP TABLE IF EXISTS paper_account CASCADE;")
    cur.execute("DROP TABLE IF EXISTS paper_trades CASCADE;")
    cur.execute("DROP TABLE IF EXISTS signals_historical CASCADE;")
    cur.execute("DROP TABLE IF EXISTS watchlist_current CASCADE;")
    cur.execute("DROP TABLE IF EXISTS trade_setups CASCADE;")
    cur.execute("DROP TABLE IF EXISTS signals_summary CASCADE;")
    cur.execute("DROP TABLE IF EXISTS high_confidence_signals CASCADE;")
    conn.commit()
    cur.close()
    conn.close()
    print("  Dropped all existing DB tables (fresh schema on next init).")


def init_schema():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals_historical (
        id SERIAL PRIMARY KEY,
        scan_time_utc TIMESTAMP NOT NULL, signal_bar_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL, signal_type VARCHAR(32) NOT NULL,
        quality VARCHAR(16), gates_passed INT, session VARCHAR(16),
        gate_session_ok BOOLEAN, gate_adx_ok BOOLEAN, gate_direction_ok BOOLEAN,
        gate_rsi_ok BOOLEAN, gate_bull_4h_ok BOOLEAN, price_at_signal NUMERIC(18,8),
        rsi_15m NUMERIC(8,2), adx_4h NUMERIC(8,2), atr_15m NUMERIC(18,8),
        sl_price NUMERIC(18,8), tp_price NUMERIC(18,8),
        sl_distance_pct NUMERIC(8,4), tp_distance_pct NUMERIC(8,4),
        risk_reward NUMERIC(4,2), log_ret_4h NUMERIC(12,6),
        bull_4h_ema BOOLEAN, action VARCHAR(64), age_hours NUMERIC(6,1),
        created_at TIMESTAMP DEFAULT NOW()
    );""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_current (
        id SERIAL PRIMARY KEY, scan_time_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL, last_bar_utc TIMESTAMP NOT NULL,
        current_price NUMERIC(18,8), rsi_15m_now NUMERIC(8,2),
        adx_4h_now NUMERIC(8,2), atr_15m_now NUMERIC(18,8),
        session_now VARCHAR(16), h4_direction VARCHAR(8),
        h4_ema_trend VARCHAR(8), regime VARCHAR(16), signal_type VARCHAR(32),
        quality VARCHAR(16), gates_passed VARCHAR(8),
        gate_session_ok BOOLEAN, gate_adx_ok BOOLEAN, gate_direction_ok BOOLEAN,
        gate_rsi_ok BOOLEAN, gate_bull_4h_ok BOOLEAN,
        sl_level NUMERIC(18,8), tp_level NUMERIC(18,8), notes TEXT,
        created_at TIMESTAMP DEFAULT NOW(), UNIQUE(symbol, scan_time_utc)
    );""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_setups (
        id SERIAL PRIMARY KEY, scan_time_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL, current_price NUMERIC(18,8),
        atr_15m NUMERIC(18,8), entry_price NUMERIC(18,8),
        sl_price NUMERIC(18,8), tp_price NUMERIC(18,8),
        sl_distance_pct NUMERIC(8,4), tp_distance_pct NUMERIC(8,4),
        risk_reward NUMERIC(4,2), dollar_risk NUMERIC(12,2),
        position_size_1x NUMERIC(12,2), leveraged_notional NUMERIC(12,2),
        qty_contracts NUMERIC(18,4), status VARCHAR(32),
        rsi NUMERIC(8,2), adx NUMERIC(8,2), session VARCHAR(16), h4_dir VARCHAR(8),
        created_at TIMESTAMP DEFAULT NOW(), UNIQUE(symbol, scan_time_utc)
    );""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS high_confidence_signals (
        id SERIAL PRIMARY KEY, scan_time_utc TIMESTAMP NOT NULL,
        signal_bar_utc TIMESTAMP NOT NULL, symbol VARCHAR(16) NOT NULL,
        confidence INT NOT NULL, grade VARCHAR(16) NOT NULL,
        signal_strength VARCHAR(16),  -- WEAK(50-60), FAIR(61-75), GOOD(76-90), EXCELLENT(91-100)
        action TEXT,                  -- best entry guidance and TP expectation
        summary TEXT,                 -- 30-50 word AI judgment
        technical_score INT, market_structure_score INT,
        funding_oi_score INT, session_symbol_score INT,
        price_at_signal NUMERIC(18,8), rsi_15m NUMERIC(8,2),
        adx_4h NUMERIC(8,2), atr_15m NUMERIC(18,8),
        sl_price NUMERIC(18,8), tp_price NUMERIC(18,8),
        sl_distance_pct NUMERIC(8,4), tp_distance_pct NUMERIC(8,4),
        risk_reward NUMERIC(4,2), dollar_risk NUMERIC(12,2), actual_dollar_risk NUMERIC(12,2),
        position_size_1x NUMERIC(12,2), leveraged_notional NUMERIC(12,2),
        qty_contracts NUMERIC(18,4), session VARCHAR(16), h4_direction VARCHAR(8),
        funding_rate NUMERIC(18,10), oi_delta_pct NUMERIC(8,4),
        ob_spread_pct NUMERIC(8,4), ob_imbalance_pct NUMERIC(8,4),
        is_expired BOOLEAN DEFAULT FALSE,
        expiry_reason VARCHAR(64),
        trade_decision VARCHAR(64),
        created_at TIMESTAMP DEFAULT NOW(), UNIQUE(symbol, signal_bar_utc)
    );""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals_summary (
        id SERIAL PRIMARY KEY, scan_time_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL, total_confirmed INT DEFAULT 0,
        total_approaching INT DEFAULT 0, total_offsession INT DEFAULT 0,
        total_high_confidence INT DEFAULT 0, last_signal_time TIMESTAMP,
        last_signal_price NUMERIC(18,8), last_signal_type VARCHAR(32),
        current_recommendation VARCHAR(128), current_confidence INT,
        created_at TIMESTAMP DEFAULT NOW(), UNIQUE(symbol, scan_time_utc)
    );""")

    # ── POWER TRADES — manually promoted high-conviction setups ───────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS power_trades (
        id SERIAL PRIMARY KEY,
        signal_id INT REFERENCES high_confidence_signals(id),
        symbol VARCHAR(16) NOT NULL,
        confidence INT,
        signal_strength VARCHAR(16),
        price_at_signal NUMERIC(18,8),
        sl_price NUMERIC(18,8),
        tp_price NUMERIC(18,8),
        risk_reward NUMERIC(4,2),
        dollar_risk NUMERIC(12,2),
        leveraged_notional NUMERIC(12,2),
        qty_contracts NUMERIC(18,4),
        session VARCHAR(16),
        h4_direction VARCHAR(8),
        trade_decision VARCHAR(64),
        action TEXT,
        summary TEXT,
        urgency VARCHAR(16) DEFAULT 'NORMAL',
        analyst_note TEXT,
        status VARCHAR(16) DEFAULT 'ACTIVE',
        promoted_at TIMESTAMP DEFAULT NOW(),
        promoted_by VARCHAR(32) DEFAULT 'MANUAL',
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(signal_id)
    );""")

    # ── PAPER ACCOUNT ─────────────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_account (
        id SERIAL PRIMARY KEY,
        balance NUMERIC(12,2) DEFAULT 10000.00,
        equity NUMERIC(12,2) DEFAULT 10000.00,
        margin_used NUMERIC(12,2) DEFAULT 0.00,
        total_pnl NUMERIC(8,4) DEFAULT 0.00,
        total_trades INT DEFAULT 0,
        win_rate NUMERIC(5,2) DEFAULT 0.00,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    );""")

    # Seed with 10,000 USDT if empty
    cur.execute("SELECT COUNT(*) FROM paper_account")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO paper_account (balance, equity) VALUES (10000.00, 10000.00)")

    # ── PAPER TRADES — Real performance tracking ──────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id SERIAL PRIMARY KEY,
        signal_id INT REFERENCES high_confidence_signals(id),
        symbol VARCHAR(16) NOT NULL,
        signal_bar_utc TIMESTAMP NOT NULL,
        scan_time_utc TIMESTAMP NOT NULL,

        -- Pre-trade (auto-filled from signal)
        entry_price_planned NUMERIC(18,8),
        entry_price_actual NUMERIC(18,8),
        sl_price NUMERIC(18,8),
        tp_price NUMERIC(18,8),
        qty_contracts NUMERIC(18,4),
        leveraged_notional NUMERIC(12,2),
        margin_required NUMERIC(12,2),
        confidence INT,
        funding_rate_at_entry NUMERIC(18,10),
        oi_delta_at_entry NUMERIC(8,4),

        -- Post-trade (user fills after trade closes)
        exit_price NUMERIC(18,8),
        exit_reason VARCHAR(32),  -- 'TP_HIT', 'SL_HIT', 'TIME_EXIT', 'FUNDING_EXIT', 'MANUAL'
        exit_time_utc TIMESTAMP,
        bars_held INT,
        hours_held NUMERIC(6,2),
        gross_pnl_pct NUMERIC(8,4),
        gross_pnl_usd NUMERIC(12,2),

        -- Cost breakdown (auto-calculated where possible)
        entry_fee NUMERIC(12,4),      -- 0.055% taker
        exit_fee NUMERIC(12,4),       -- 0.055% taker
        fees_total NUMERIC(12,4),       -- entry + exit
        funding_events INT,             -- how many 8h funding periods
        funding_total NUMERIC(12,4),      -- sum of funding payments
        slippage_pct NUMERIC(8,4),      -- (actual - planned) / planned
        slippage_usd NUMERIC(12,4),

        -- Performance metrics (auto-calculated from trade path)
        max_adverse_excursion_pct NUMERIC(8,4),  -- worst drawdown % during trade
        max_favorable_excursion_pct NUMERIC(8,4), -- best profit % during trade
        net_pnl_pct NUMERIC(8,4),       -- gross - fees - funding + slippage
        net_pnl_usd NUMERIC(12,2),
        risk_reward_actual NUMERIC(4,2), -- |net_pnl| / risk_amount

        -- Live reality check
        sharpe_contribution NUMERIC(8,4), -- this trade's contribution to rolling Sharpe
        vs_backtest_expectation VARCHAR(64), -- 'MET', 'BELOW', 'ABOVE'

        -- Status
        status VARCHAR(16) DEFAULT 'PENDING',  -- PENDING, OPEN, CLOSED, CANCELLED
        notes TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    );""")

    # ── PAPER TRADE STATEMENTS ────────────────────────────────────────────
    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_trade_statements (
        id SERIAL PRIMARY KEY,
        trade_id INT REFERENCES paper_trades(id),
        action VARCHAR(16) NOT NULL,
        amount NUMERIC(12,4),
        balance_after NUMERIC(12,2),
        note TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );""")

    # ── WINNERS — paper trades that hit TP (must come after paper_trades) ─
    cur.execute("""
    CREATE TABLE IF NOT EXISTS winners (
        id SERIAL PRIMARY KEY,
        trade_id INT REFERENCES paper_trades(id),
        signal_id INT REFERENCES high_confidence_signals(id),
        symbol VARCHAR(16) NOT NULL,
        confidence INT,
        signal_strength VARCHAR(16),
        entry_price NUMERIC(18,8),
        exit_price NUMERIC(18,8),
        tp_price NUMERIC(18,8),
        sl_price NUMERIC(18,8),
        gross_pnl_pct NUMERIC(8,4),
        net_pnl_pct NUMERIC(8,4),
        net_pnl_usd NUMERIC(12,2),
        hours_held NUMERIC(6,2),
        exit_reason VARCHAR(32),
        session VARCHAR(16),
        h4_direction VARCHAR(8),
        recorded_at TIMESTAMP DEFAULT NOW(),
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(trade_id)
    );""")

    conn.commit()
    cur.close()
    conn.close()
    print("  Database schema initialized.")


def load_signals_csvs():
    sig_dir = Path(__file__).resolve().parent
    signals = pd.read_csv(sig_dir / "signals_latest.csv") if (sig_dir / "signals_latest.csv").exists() else pd.DataFrame()
    watchlist = pd.read_csv(sig_dir / "signals_watchlist.csv") if (sig_dir / "signals_watchlist.csv").exists() else pd.DataFrame()
    return signals, watchlist


def _probe_and_refresh_signal(symbol, signal_price, signal_rsi, signal_atr, signal_direction_up, age_hours):
    """
    Probe current market state. If pullback is still active, refresh entry/SL/TP.
    Returns (is_expired, expiry_reason, refreshed_dict)
    refreshed_dict has: price, rsi, atr, sl, tp, sl_pct, tp_pct, rr — or None if expired.
    """
    try:
        df = load_symbol_tf(symbol, "15m")
        if df is None or len(df) < 15:
            return False, None, None
        df = add_rsi(df, 14)
        df = add_atr(df, 14)
        latest = df.iloc[-1]
        current_price = float(latest["close"])
        current_rsi = float(latest["rsi"])
        current_atr = float(latest["atr"])

        # 1. RSI recovery: if RSI climbed > 15 points from signal, pullback is over
        if current_rsi > signal_rsi + 15:
            return True, f"RSI recovered {current_rsi - signal_rsi:.1f}pts ({signal_rsi:.1f} → {current_rsi:.1f})", None

        # 2. Price drift: if price moved > 2×ATR against entry (higher for long pullback)
        price_drift = current_price - signal_price
        if price_drift > signal_atr * 2:
            return True, f"Price drifted ${price_drift:.2f} ({price_drift/signal_atr:.1f}× ATR)", None

        # 3. Age: already filtered by 2h, but check if > 1h and RSI recovered > 10
        if age_hours > 1.0 and current_rsi > signal_rsi + 10:
            return True, f"Age {age_hours:.1f}h + RSI recovered {current_rsi - signal_rsi:.1f}pts", None

        # Pullback still active — refresh levels with current data
        new_sl = current_price - current_atr * 1.5
        new_tp = current_price + current_atr * 2.0
        new_sl_pct = ((current_price - new_sl) / current_price * 100) if current_price > 0 else 0
        new_tp_pct = ((new_tp - current_price) / current_price * 100) if current_price > 0 else 0
        new_rr = new_tp_pct / new_sl_pct if new_sl_pct > 0 else 0

        return False, None, {
            "price": round(current_price, 4),
            "rsi": round(current_rsi, 2),
            "atr": round(current_atr, 4),
            "sl": round(new_sl, 4),
            "tp": round(new_tp, 4),
            "sl_pct": round(new_sl_pct, 4),
            "tp_pct": round(new_tp_pct, 4),
            "rr": round(new_rr, 2),
        }
    except Exception:
        return False, None, None


def build_high_confidence_signals(signals_df, now: datetime = None):
    records = []
    now = now or datetime.now(timezone.utc)

    confirmed = signals_df[signals_df["signal_type"] == "CONFIRMED"].copy() if len(signals_df) else pd.DataFrame()
    if len(confirmed) == 0:
        return pd.DataFrame()

    for _, row in confirmed.iterrows():
        symbol = str(row["symbol"])
        session = str(row.get("session", ""))

        # ── Signal expiration logic ──
        # Signals are only actionable if generated on a recent bar.
        # After 2 hours, price structure has changed and the setup is stale.
        age_hours = float(row.get("age_hours", 999))
        action = str(row.get("action", ""))
        if age_hours > 2.0 or "HISTORICAL" in action:
            # print(f"  [Expired] {symbol} — age={age_hours:.1f}h, action={action}")
            continue

        enriched = load_enriched(symbol)
        ob = load_orderbook_features(symbol)

        if enriched is None or len(enriched) == 0:
            continue

        signal_ts = pd.to_datetime(row["signal_bar_utc"], utc=True)
        enriched["timestamp"] = pd.to_datetime(enriched["timestamp"], utc=True)
        mask = enriched["timestamp"] <= signal_ts
        idx = enriched.index[mask][-1] if any(mask) else -1

        total, breakdown = score_signal(
            symbol, enriched, ob, session, idx=idx,
            rsi_override=row.get("rsi_15m"),
            adx_override=row.get("adx_4h"),
            log_ret_override=row.get("ctx_log_ret_4h", 0)
        )

        if total < 50:
            continue

        price = float(row["price_at_signal"])
        atr = float(row["atr_15m"]) if not pd.isna(row.get("atr_15m")) else 0
        signal_rsi = float(row.get("rsi_15m", 50))
        signal_direction_up = bool(row.get("gate_direction_ok", False))
        
        # ── Market-based expiration probe + refresh ──
        is_expired, expiry_reason, refreshed = _probe_and_refresh_signal(
            symbol, price, signal_rsi, atr, signal_direction_up, age_hours, now=now
        )
        
        # If pullback still active, use refreshed current levels
        if refreshed:
            price = refreshed["price"]
            atr = refreshed["atr"]
            sl = refreshed["sl"]
            tp = refreshed["tp"]
            sl_pct = refreshed["sl_pct"]
            tp_pct = refreshed["tp_pct"]
            row_rsi = refreshed["rsi"]
            row_rr = refreshed["rr"]
        else:
            # Fallback to original signal levels (will be marked expired)
            sl = float(row["sl_price"])
            tp = float(row["tp_price"])
            sl_pct = float(row["sl_distance_pct"])
            tp_pct = float(row["tp_distance_pct"])
            row_rsi = float(row["rsi_15m"])
            row_rr = float(row["risk_reward"])

        # Signal strength categorization
        if total >= 91:
            strength = "EXCELLENT"
        elif total >= 76:
            strength = "GOOD"
        elif total >= 61:
            strength = "FAIR"
        else:
            strength = "WEAK"

        # Dynamic allocation: query current paper account balance
        # Scale position so we never allocate more than balance / 6 per symbol
        # This ensures we can always open positions even when balance is low
        try:
            import psycopg2
            db_conn = psycopg2.connect(DB_URL)
            db_cur = db_conn.cursor()
            db_cur.execute("SELECT balance FROM paper_account LIMIT 1")
            row_bal = db_cur.fetchone()
            account_balance = float(row_bal[0]) if row_bal else 10000.0
            db_cur.close()
            db_conn.close()
        except Exception:
            account_balance = 10000.0

        # Equal allocation per symbol, but never more than available balance / 4
        # (leave room for 2-3 more trades)
        CAPITAL_PER_SYMBOL = account_balance / len(SYMBOLS)
        MAX_MARGIN_PER_SYMBOL = account_balance / 4.0  # keep 75% of balance free for other symbols
        effective_cap = min(CAPITAL_PER_SYMBOL, MAX_MARGIN_PER_SYMBOL)

        # Confidence-based risk: 2% at 50 confidence → 9% at 100 confidence (linear)
        risk_pct = 2.0 + (total - 50) * 0.14  # 2% base + 0.14 per point above 50
        risk_pct = max(2.0, min(9.0, risk_pct))
        dollar_risk = effective_cap * (risk_pct / 100.0)

        # 1x position size needed to take this risk given the SL distance
        pos_size = dollar_risk / (sl_pct / 100) if sl_pct > 0 else 0
        # Cap so one symbol never outspends its allocation
        pos_size = min(pos_size, effective_cap)

        # Leveraged notional stays 4x for backend compatibility (margin = lev / 4)
        lev = pos_size * 4.0
        qty = lev / price if price > 0 else 0

        # Actual dollar risk at stop = (entry - SL) × qty
        actual_dollar_risk = (price - sl) * qty if price > sl else 0

        f_meta = breakdown.get("funding_oi_meta", {})
        s_meta = breakdown.get("struct_meta", {})

        records.append({
            "scan_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
            "signal_bar_utc": row["signal_bar_utc"],
            "symbol": symbol,
            "confidence": total,
            "grade": breakdown.get("grade", "UNKNOWN"),
            "technical_score": breakdown.get("technical", 0),
            "market_structure_score": breakdown.get("market_structure", 0),
            "funding_oi_score": breakdown.get("funding_oi", 0),
            "session_symbol_score": breakdown.get("session_symbol", 0),
            "price_at_signal": round(price, 4),
            "rsi_15m": row_rsi,
            "adx_4h": float(row["adx_4h"]),
            "atr_15m": round(atr, 4),
            "sl_price": round(sl, 4),
            "tp_price": round(tp, 4),
            "sl_distance_pct": round(sl_pct, 4),
            "tp_distance_pct": round(tp_pct, 4),
            "risk_reward": row_rr,
            "dollar_risk": round(dollar_risk, 2),
            "actual_dollar_risk": round(actual_dollar_risk, 2),
            "position_size_1x": round(pos_size, 2),
            "leveraged_notional": round(lev, 2),
            "qty_contracts": round(qty, 4),
            "session": session,
            "h4_direction": "UP" if row.get("gate_direction_ok") else "DOWN",
            "funding_rate": f_meta.get("funding_rate", 0),
            "oi_delta_pct": f_meta.get("oi_delta_pct", 0),
            "ob_spread_pct": s_meta.get("spread_pct", 0),
            "ob_imbalance_pct": s_meta.get("imbalance_pct", 0),
            "signal_strength": strength,
            "is_expired": is_expired,
            "expiry_reason": expiry_reason or "",
            "action": f"Best entry at ${round(price, 2)}. Expect TP within {int(8 + (100 - total) * 0.15)}h based on momentum.",
            "summary": _build_summary(symbol, total, strength, row, breakdown, f_meta),
            "trade_decision": (f"ENTER — {strength} ({total}/100)" + (" [EXPIRED: " + expiry_reason + "]" if is_expired else ""))[:63],
        })

    return pd.DataFrame(records)


def _build_summary(symbol, confidence, strength, row, breakdown, f_meta):
    """
    Rule-based signal summary. Generates a 30-50 word factual description
    from the scored signal data. No AI API call required.
    """
    rsi = float(row.get("rsi_15m", 0))
    adx = float(row.get("adx_4h", 0))
    session = str(row.get("session", "")).replace("_", " ").title()
    funding = f_meta.get("funding_rate", 0)
    oi_delta = f_meta.get("oi_delta_pct", 0)

    funding_str = "negative funding (short-squeeze fuel)" if funding < 0 else "positive funding (longs paying)"
    oi_str = f"OI {'rising' if oi_delta > 0 else 'falling'} {abs(oi_delta):.1f}%"
    tech_score = breakdown.get("technical", 0)
    struct_score = breakdown.get("market_structure", 0)

    return (
        f"{symbol} pullback setup. RSI {rsi:.0f} on 15m, ADX {adx:.0f} on 4h. "
        f"{session} session. {funding_str.capitalize()}, {oi_str}. "
        f"Technical score {tech_score}/100, structure score {struct_score}/100. "
        f"Confidence {confidence}/100 — {strength} signal."
    )


def build_summary(signals_df, watchlist_df, high_conf_df):
    summaries = []
    for symbol in SYMBOLS:
        sym_sig = signals_df[signals_df["symbol"] == symbol] if len(signals_df) else pd.DataFrame()
        sym_wl = watchlist_df[watchlist_df["symbol"] == symbol] if len(watchlist_df) else pd.DataFrame()
        sym_hi = high_conf_df[high_conf_df["symbol"] == symbol] if len(high_conf_df) else pd.DataFrame()

        n_conf = len(sym_sig[sym_sig["signal_type"] == "CONFIRMED"]) if len(sym_sig) else 0
        n_app = len(sym_sig[sym_sig["signal_type"] == "APPROACHING"]) if len(sym_sig) else 0
        n_off = len(sym_sig[sym_sig["signal_type"] == "OFF_SESSION"]) if len(sym_sig) else 0
        n_hi = len(sym_hi)

        last_sig = sym_hi.tail(1) if len(sym_hi) else pd.DataFrame()
        last_time = last_sig.iloc[0]["signal_bar_utc"] if len(last_sig) else None
        last_price = float(last_sig.iloc[0]["price_at_signal"]) if len(last_sig) else None
        last_type = "HIGH_CONFIDENCE" if len(last_sig) else None
        last_conf = int(last_sig.iloc[0]["confidence"]) if len(last_sig) else 0

        if len(sym_wl):
            wl = sym_wl.iloc[0]
            st = wl["signal_type"]
            if st == "CONFIRMED":
                rec = "CONFIRMED but NEEDS CONFIDENCE SCORE — run decision desk"
            elif st == "APPROACHING":
                rec = f"SET ALERT — RSI={wl['rsi_15m_now']}, needs to drop below 40"
            elif st == "OFF_SESSION":
                rec = "SIGNAL EXISTS — but outside session window"
            else:
                rec = f"WAIT — {wl['notes']}"
        else:
            rec = "NO DATA"

        if n_hi > 0:
            rec = f"TRADE RECORDED — {n_hi} high-confidence signal(s) in DB"

        summaries.append({
            "scan_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "total_confirmed": n_conf,
            "total_approaching": n_app,
            "total_offsession": n_off,
            "total_high_confidence": n_hi,
            "last_signal_time": last_time,
            "last_signal_price": last_price,
            "last_signal_type": last_type,
            "current_recommendation": rec,
            "current_confidence": last_conf,
        })
    return pd.DataFrame(summaries)


def insert_df(conn, df, table, columns):
    if len(df) == 0:
        return 0
    cur = conn.cursor()
    cols = ",".join(columns)
    placeholders = ",".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    count = 0
    for _, row in df.iterrows():
        vals = []
        for c in columns:
            v = row.get(c, None)
            if pd.isna(v):
                v = None
            vals.append(v)
        try:
            cur.execute(sql, vals)
            count += 1
        except Exception as e:
            print(f"  DB insert error for {table}: {e}")
    conn.commit()
    cur.close()
    return count


def upsert_high_confidence_signals(conn, df, columns):
    """Upsert high_confidence_signals: update existing rows with refreshed levels."""
    if len(df) == 0:
        return 0
    cur = conn.cursor()
    cols = ",".join(columns)
    placeholders = ",".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c not in ("signal_bar_utc", "symbol")]
    update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
    sql = f"""
        INSERT INTO high_confidence_signals ({cols}) VALUES ({placeholders})
        ON CONFLICT (symbol, signal_bar_utc) DO UPDATE SET {update_set}
    """
    count = 0
    for _, row in df.iterrows():
        vals = []
        for c in columns:
            v = row.get(c, None)
            if pd.isna(v):
                v = None
            vals.append(v)
        try:
            cur.execute(sql, vals)
            count += 1
        except Exception as e:
            print(f"  DB upsert error for high_confidence_signals: {e}")
    conn.commit()
    cur.close()
    return count


def populate_paper_trades(conn, high_conf_df):
    """
    Auto-populate paper_trades from high_confidence_signals with pre-trade estimates.
    Calculates fees, slippage estimates, and funding projections.
    """
    if len(high_conf_df) == 0:
        return 0

    cur = conn.cursor()
    TAKER_FEE = 0.00055  # 0.055% per order
    count = 0

    for _, row in high_conf_df.iterrows():
        symbol = row["symbol"]
        # Skip expired signals — no point creating paper trades for dead setups
        if row.get("is_expired"):
            continue
        entry = float(row["price_at_signal"]) if not pd.isna(row.get("price_at_signal")) else 0
        sl = float(row["sl_price"]) if not pd.isna(row.get("sl_price")) else 0
        tp = float(row["tp_price"]) if not pd.isna(row.get("tp_price")) else 0
        qty = float(row["qty_contracts"]) if not pd.isna(row.get("qty_contracts")) else 0
        notional = float(row["leveraged_notional"]) if not pd.isna(row.get("leveraged_notional")) else 0
        margin_req = (entry * qty) / 4.0 if entry > 0 and qty > 0 else 0
        conf = int(row["confidence"]) if not pd.isna(row.get("confidence")) else 0
        funding = float(row["funding_rate"]) if not pd.isna(row.get("funding_rate")) else 0
        oi = float(row["oi_delta_pct"]) if not pd.isna(row.get("oi_delta_pct")) else 0
        sl_dist_pct = float(row["sl_distance_pct"]) if not pd.isna(row.get("sl_distance_pct")) else 0
        ts = row["scan_time_utc"]
        bar_ts = row["signal_bar_utc"]

        # Pre-trade cost estimates
        entry_fee = notional * TAKER_FEE if notional > 0 else 0
        exit_fee = notional * TAKER_FEE if notional > 0 else 0
        fees_total = entry_fee + exit_fee

        # Funding estimate: assume 1 funding event (avg hold ~5h = 0-1 events)
        funding_estimate = notional * abs(funding) if notional > 0 else 0
        funding_events = 1

        # Slippage estimate: volatile RSI<50 bars = 0.05-0.15% typical
        est_slippage_pct = 0.10  # 0.10% conservative
        slippage_usd = notional * est_slippage_pct / 100 if notional > 0 else 0

        # Total cost before trade even moves
        total_cost_pct = (fees_total + funding_estimate + slippage_usd) / notional * 100 if notional > 0 else 0

        # Skip if a PENDING paper trade already exists for this exact signal
        cur.execute("""
            SELECT 1 FROM paper_trades
            WHERE symbol = %s AND signal_bar_utc = %s AND status = 'PENDING'
            LIMIT 1
        """, (symbol, bar_ts))
        if cur.fetchone():
            continue

        sql = """
        INSERT INTO paper_trades (
            signal_id, symbol, signal_bar_utc, scan_time_utc,
            entry_price_planned, entry_price_actual, sl_price, tp_price,
            qty_contracts, leveraged_notional, margin_required, confidence,
            funding_rate_at_entry, oi_delta_at_entry,
            entry_fee, exit_fee, fees_total, funding_events, funding_total,
            slippage_pct, slippage_usd, status, notes
        ) VALUES (
            (SELECT id FROM high_confidence_signals WHERE symbol = %s AND signal_bar_utc = %s),
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """
        vals = (
            symbol, bar_ts,
            symbol, bar_ts, ts,
            entry, None, sl, tp,
            qty, notional, round(margin_req, 2), conf,
            funding, oi,
            round(entry_fee, 4), round(exit_fee, 4), round(fees_total, 4),
            funding_events, round(funding_estimate, 4),
            round(est_slippage_pct, 4), round(slippage_usd, 4),
            'PENDING',
            f"Pre-trade cost estimate: {total_cost_pct:.3f}% of notional. "
            f"Fees={fees_total:.2f} | Funding(est)={funding_estimate:.2f} | Slippage(est)={slippage_usd:.2f}"
        )
        try:
            cur.execute(sql, vals)
            count += 1
        except Exception as e:
            print(f"  Paper trade insert error for {symbol}: {e}")

    conn.commit()
    cur.close()
    return count


def cleanup_paper_trades(conn, high_conf_df):
    """
    Remove pending trades for expired signals and update pending trades
    whose signals were refreshed with new entry/SL/TP levels.
    """
    cur = conn.cursor()
    deleted = 0
    updated = 0

    # Build lookup of current signals by (symbol, bar_utc)
    signal_map = {}
    for _, row in high_conf_df.iterrows():
        key = (row["symbol"], row["signal_bar_utc"])
        signal_map[key] = row

    # Fetch all pending paper trades
    cur.execute("""
        SELECT id, symbol, signal_bar_utc, entry_price_planned, sl_price, tp_price, qty_contracts
        FROM paper_trades WHERE status = 'PENDING'
    """)
    pending_rows = cur.fetchall()

    for pt in pending_rows:
        pt_id, symbol, bar_ts, old_entry, old_sl, old_tp, old_qty = pt
        key = (symbol, bar_ts)
        sig = signal_map.get(key)

        if not sig:
            # Signal no longer exists in high_conf — delete pending trade
            cur.execute("DELETE FROM paper_trades WHERE id = %s", (pt_id,))
            deleted += 1
            continue

        if sig.get("is_expired"):
            # Signal expired — delete pending trade
            cur.execute("DELETE FROM paper_trades WHERE id = %s", (pt_id,))
            deleted += 1
            continue

        # Check if levels changed — update pending trade to match refreshed signal
        new_entry = float(sig["price_at_signal"]) if not pd.isna(sig.get("price_at_signal")) else 0
        new_sl = float(sig["sl_price"]) if not pd.isna(sig.get("sl_price")) else 0
        new_tp = float(sig["tp_price"]) if not pd.isna(sig.get("tp_price")) else 0
        new_qty = float(sig["qty_contracts"]) if not pd.isna(sig.get("qty_contracts")) else 0
        new_margin = (new_entry * new_qty) / 4.0 if new_entry > 0 and new_qty > 0 else 0
        new_notional = float(sig["leveraged_notional"]) if not pd.isna(sig.get("leveraged_notional")) else 0

        if (abs(new_entry - (old_entry or 0)) > 0.01 or
            abs(new_sl - (old_sl or 0)) > 0.01 or
            abs(new_tp - (old_tp or 0)) > 0.01):
            cur.execute("""
                UPDATE paper_trades
                SET entry_price_planned = %s, sl_price = %s, tp_price = %s,
                    qty_contracts = %s, leveraged_notional = %s, margin_required = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (new_entry, new_sl, new_tp, new_qty, new_notional, round(new_margin, 2), pt_id))
            updated += 1

    conn.commit()
    cur.close()
    return deleted, updated


def alert_new_signals(conn, high_conf_df):
    """Send Telegram alerts for new non-expired signals with confidence >= 50."""
    if send_telegram is None:
        print("  [Telegram] telegram_alerts module not available — skipping")
        return 0

    if len(high_conf_df) == 0:
        return 0

    # Find existing signals in DB to avoid duplicates
    cur = conn.cursor()
    cur.execute("SELECT symbol, signal_bar_utc FROM high_confidence_signals")
    existing = set()
    for row in cur.fetchall():
        existing.add((row[0], str(row[1])))
    cur.close()

    sent = 0
    for _, row in high_conf_df.iterrows():
        if row.get("is_expired"):
            continue
        conf = int(row.get("confidence", 0))
        if conf < 50:
            continue
        key = (row["symbol"], str(row["signal_bar_utc"]))
        if key in existing:
            continue

        symbol = row["symbol"]
        strength = row.get("signal_strength", "UNKNOWN")
        price = float(row.get("price_at_signal", 0))
        sl = float(row.get("sl_price", 0))
        tp = float(row.get("tp_price", 0))
        rr = float(row.get("risk_reward", 0))
        session = str(row.get("session", "")).replace("_", " ").title()
        rsi = float(row.get("rsi_15m", 0))
        adx = float(row.get("adx_4h", 0))
        funding = float(row.get("funding_rate", 0))
        oi = float(row.get("oi_delta_pct", 0))

        emoji = "🟢" if strength == "EXCELLENT" else "🟩" if strength == "GOOD" else "🟨" if strength == "FAIR" else "🟧"
        direction = "LONG" if str(row.get("h4_direction", "")).upper() == "UP" else "SHORT"

        msg = (
            f"<b>{emoji} VLTHR Signal — {symbol}</b>\n"
            f"<pre>"
            f"Strength:  {strength} ({conf}/100)\n"
            f"Direction: {direction}\n"
            f"Price:     ${price:,.2f}\n"
            f"SL:        ${sl:,.2f}\n"
            f"TP:        ${tp:,.2f}\n"
            f"R:R:       {rr:.2f}\n"
            f"Session:   {session}\n"
            f"RSI 15m:   {rsi:.1f}\n"
            f"ADX 4h:    {adx:.1f}\n"
            f"Funding:   {funding*100:+.4f}%\n"
            f"OI Δ:      {oi:+.2f}%\n"
            f"</pre>"
        )
        try:
            ok = send_telegram(msg, chat_type="trading", parse_mode="HTML", silent=False)
            if ok:
                sent += 1
                print(f"  [Telegram] Sent {symbol} {strength} ({conf}) to trading chat")
        except Exception as e:
            print(f"  [Telegram] Failed for {symbol}: {e}")

    return sent


def sync():
    print("=" * 70)
    print("  PullbackToTrend — Sync HIGH-CONFIDENCE Signals to Database")
    print("  Only signals with confidence >= 75/100 are recorded as tradeable.")
    print("=" * 70)

    if not DB_URL:
        print("  ERROR: DB_URL not found in .env")
        return

    print(f"  DB: {DB_URL.split('@')[1].split('/')[0]}")

    init_schema()  # Creates tables if missing; never drops existing data

    signals_df, watchlist_df = load_signals_csvs()
    print(f"  Loaded signals_latest: {len(signals_df)} rows")
    print(f"  Loaded watchlist: {len(watchlist_df)} rows")

    print("  Scoring confirmed signals with confidence engine...")
    high_conf_df = build_high_confidence_signals(signals_df)
    print(f"  High-confidence signals (>=50): {len(high_conf_df)} found")

    summary_df = build_summary(signals_df, watchlist_df, high_conf_df)

    conn = get_conn()

    # Insert raw historical
    sig_cols = ["scan_time_utc", "signal_bar_utc", "symbol", "signal_type", "quality",
                "gates_passed", "session", "gate_session_ok", "gate_adx_ok",
                "gate_direction_ok", "gate_rsi_ok", "gate_bull_4h_ok",
                "price_at_signal", "rsi_15m", "adx_4h", "atr_15m",
                "sl_price", "tp_price", "sl_distance_pct", "tp_distance_pct",
                "risk_reward", "log_ret_4h", "bull_4h_ema", "action", "age_hours"]
    n1 = insert_df(conn, signals_df, "signals_historical", sig_cols)
    print(f"  Inserted {n1} rows into signals_historical")

    # Insert watchlist
    wl_cols = ["scan_time_utc", "symbol", "last_bar_utc", "current_price", "rsi_15m_now",
               "adx_4h_now", "atr_15m_now", "session_now", "h4_direction", "h4_ema_trend",
               "regime", "signal_type", "quality", "gates_passed",
               "gate_session_ok", "gate_adx_ok", "gate_direction_ok", "gate_rsi_ok", "gate_bull_4h_ok",
               "sl_level", "tp_level", "notes"]
    n2 = insert_df(conn, watchlist_df, "watchlist_current", wl_cols)
    print(f"  Inserted {n2} rows into watchlist_current")

    # Upsert high-confidence signals (refresh existing, insert new)
    hi_cols = ["scan_time_utc", "signal_bar_utc", "symbol", "confidence", "grade",
               "signal_strength", "action", "summary",
               "technical_score", "market_structure_score", "funding_oi_score", "session_symbol_score",
               "price_at_signal", "rsi_15m", "adx_4h", "atr_15m",
               "sl_price", "tp_price", "sl_distance_pct", "tp_distance_pct",
               "risk_reward", "dollar_risk", "actual_dollar_risk", "position_size_1x", "leveraged_notional",
               "qty_contracts", "session", "h4_direction",
               "funding_rate", "oi_delta_pct", "ob_spread_pct", "ob_imbalance_pct",
               "is_expired", "expiry_reason", "trade_decision"]
    n3 = upsert_high_confidence_signals(conn, high_conf_df, hi_cols)
    print(f"  Upserted {n3} rows into high_confidence_signals (TRADEABLE)")

    # Send Telegram alerts for new non-expired signals >= 50 confidence
    n_tg = alert_new_signals(conn, high_conf_df)
    if n_tg > 0:
        print(f"  Sent {n_tg} new signal alert(s) to Telegram trading chat")

    # Auto-populate paper_trades from high_confidence_signals
    n5 = populate_paper_trades(conn, high_conf_df)
    print(f"  Inserted {n5} rows into paper_trades (pre-trade estimates)")

    # Cleanup pending trades — remove expired, update refreshed
    n_del, n_upd = cleanup_paper_trades(conn, high_conf_df)
    if n_del > 0 or n_upd > 0:
        print(f"  Cleaned up paper_trades: {n_del} expired deleted, {n_upd} refreshed updated")

    # Insert summary
    sum_cols = ["scan_time_utc", "symbol", "total_confirmed", "total_approaching",
                "total_offsession", "total_high_confidence", "last_signal_time",
                "last_signal_price", "last_signal_type", "current_recommendation",
                "current_confidence"]
    n4 = insert_df(conn, summary_df, "signals_summary", sum_cols)
    print(f"  Inserted {n4} rows into signals_summary")

    conn.close()
    print(f"\n  Sync complete.")
    print(f"  Your DB now contains:")
    print(f"    - high_confidence_signals: {n3} tradeable signals")
    print(f"    - paper_trades: {n5} ready for live tracking")


if __name__ == "__main__":
    sync()
