"""
PullbackToTrend — Sync Signals to PostgreSQL
Reads latest signals and writes to Supabase Postgres.

Usage:
  python departments/strategy_research/BACKTESTER/strategy/signals/sync_to_db.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))

import psycopg2
import pandas as pd
import numpy as np
from data_loader import load_symbol_tf
from indicators import add_atr, add_rsi, add_adx, classify_session

# Load DB URL from .env
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
        # Try Supabase transaction pooler port
        alt = DB_URL.replace(":5432/", ":6543/")
        return psycopg2.connect(alt, sslmode="require")


def init_schema():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals_historical (
        id SERIAL PRIMARY KEY,
        scan_time_utc TIMESTAMP NOT NULL,
        signal_bar_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL,
        signal_type VARCHAR(32) NOT NULL,
        quality VARCHAR(16),
        gates_passed INT,
        session VARCHAR(16),
        gate_session_ok BOOLEAN,
        gate_adx_ok BOOLEAN,
        gate_direction_ok BOOLEAN,
        gate_rsi_ok BOOLEAN,
        gate_bull_4h_ok BOOLEAN,
        price_at_signal NUMERIC(18,8),
        rsi_15m NUMERIC(8,2),
        adx_4h NUMERIC(8,2),
        atr_15m NUMERIC(18,8),
        sl_price NUMERIC(18,8),
        tp_price NUMERIC(18,8),
        sl_distance_pct NUMERIC(8,4),
        tp_distance_pct NUMERIC(8,4),
        risk_reward NUMERIC(4,2),
        log_ret_4h NUMERIC(12,6),
        bull_4h_ema BOOLEAN,
        action VARCHAR(64),
        age_hours NUMERIC(6,1),
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_current (
        id SERIAL PRIMARY KEY,
        scan_time_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL,
        last_bar_utc TIMESTAMP NOT NULL,
        current_price NUMERIC(18,8),
        rsi_15m_now NUMERIC(8,2),
        adx_4h_now NUMERIC(8,2),
        atr_15m_now NUMERIC(18,8),
        session_now VARCHAR(16),
        h4_direction VARCHAR(8),
        h4_ema_trend VARCHAR(8),
        regime VARCHAR(16),
        signal_type VARCHAR(32),
        quality VARCHAR(16),
        gates_passed VARCHAR(8),
        sl_level NUMERIC(18,8),
        tp_level NUMERIC(18,8),
        notes TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(symbol, scan_time_utc)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_setups (
        id SERIAL PRIMARY KEY,
        scan_time_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL,
        current_price NUMERIC(18,8),
        atr_15m NUMERIC(18,8),
        entry_price NUMERIC(18,8),
        sl_price NUMERIC(18,8),
        tp_price NUMERIC(18,8),
        sl_distance_pct NUMERIC(8,4),
        tp_distance_pct NUMERIC(8,4),
        risk_reward NUMERIC(4,2),
        dollar_risk NUMERIC(12,2),
        position_size_1x NUMERIC(12,2),
        leveraged_notional NUMERIC(12,2),
        qty_contracts NUMERIC(18,4),
        status VARCHAR(32),
        rsi NUMERIC(8,2),
        adx NUMERIC(8,2),
        session VARCHAR(16),
        h4_dir VARCHAR(8),
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(symbol, scan_time_utc)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals_summary (
        id SERIAL PRIMARY KEY,
        scan_time_utc TIMESTAMP NOT NULL,
        symbol VARCHAR(16) NOT NULL,
        total_confirmed INT DEFAULT 0,
        total_approaching INT DEFAULT 0,
        total_offsession INT DEFAULT 0,
        last_signal_time TIMESTAMP,
        last_signal_price NUMERIC(18,8),
        last_signal_type VARCHAR(32),
        current_recommendation VARCHAR(128),
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(symbol, scan_time_utc)
    );
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("  Database schema initialized.")


def load_signals_csvs():
    sig_dir = Path(__file__).resolve().parent
    signals = pd.read_csv(sig_dir / "signals_latest.csv") if (sig_dir / "signals_latest.csv").exists() else pd.DataFrame()
    watchlist = pd.read_csv(sig_dir / "signals_watchlist.csv") if (sig_dir / "signals_watchlist.csv").exists() else pd.DataFrame()
    return signals, watchlist


def build_trade_setups(watchlist_df):
    setups = []
    for _, row in watchlist_df.iterrows():
        price = float(row["current_price"])
        atr = float(row["atr_15m_now"])
        sl = round(price - atr * PARAMS["sl_mult"], 4)
        tp = round(price + atr * PARAMS["tp_mult"], 4)
        sl_pct = (price - sl) / price * 100
        tp_pct = (tp - price) / price * 100

        dollar_risk = 1000.0 * 0.01
        pos_size = dollar_risk / (sl_pct / 100)
        lev = pos_size * 4.0
        qty = lev / price

        setups.append({
            "scan_time_utc": row["scan_time_utc"],
            "symbol": row["symbol"],
            "current_price": round(price, 4),
            "atr_15m": round(atr, 4),
            "entry_price": round(price, 4),
            "sl_price": sl,
            "tp_price": tp,
            "sl_distance_pct": round(sl_pct, 4),
            "tp_distance_pct": round(tp_pct, 4),
            "risk_reward": round(PARAMS["tp_mult"] / PARAMS["sl_mult"], 2),
            "dollar_risk": round(dollar_risk, 2),
            "position_size_1x": round(pos_size, 2),
            "leveraged_notional": round(lev, 2),
            "qty_contracts": round(qty, 4),
            "status": str(row["signal_type"]),
            "rsi": float(row["rsi_15m_now"]),
            "adx": float(row["adx_4h_now"]),
            "session": str(row["session_now"]),
            "h4_dir": str(row["4h_direction"]),
        })
    return pd.DataFrame(setups)


def build_summary(signals_df, watchlist_df):
    summaries = []
    for symbol in SYMBOLS:
        sym_sig = signals_df[signals_df["symbol"] == symbol] if len(signals_df) else pd.DataFrame()
        sym_wl = watchlist_df[watchlist_df["symbol"] == symbol] if len(watchlist_df) else pd.DataFrame()

        n_conf = len(sym_sig[sym_sig["signal_type"] == "CONFIRMED"]) if len(sym_sig) else 0
        n_app = len(sym_sig[sym_sig["signal_type"] == "APPROACHING"]) if len(sym_sig) else 0
        n_off = len(sym_sig[sym_sig["signal_type"] == "OFF_SESSION"]) if len(sym_sig) else 0

        last_sig = sym_sig[sym_sig["signal_type"] == "CONFIRMED"].tail(1) if len(sym_sig) else pd.DataFrame()
        last_time = last_sig.iloc[0]["signal_bar_utc"] if len(last_sig) else None
        last_price = float(last_sig.iloc[0]["price_at_signal"]) if len(last_sig) else None
        last_type = last_sig.iloc[0]["signal_type"] if len(last_sig) else None

        # Recommendation
        if len(sym_wl):
            wl = sym_wl.iloc[0]
            st = wl["signal_type"]
            if st == "CONFIRMED":
                rec = "ENTER NOW — all gates passed"
            elif st == "APPROACHING":
                rec = f"SET ALERT — RSI={wl['rsi_15m_now']}, needs to drop below 40"
            elif st == "OFF_SESSION":
                rec = "SIGNAL EXISTS — but outside session window, wait for London/NY Late"
            else:
                rec = f"WAIT — {wl['notes']}"
        else:
            rec = "NO DATA"

        summaries.append({
            "scan_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "total_confirmed": n_conf,
            "total_approaching": n_app,
            "total_offsession": n_off,
            "last_signal_time": last_time,
            "last_signal_price": last_price,
            "last_signal_type": last_type,
            "current_recommendation": rec,
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


def sync():
    print("=" * 70)
    print("  PullbackToTrend — Sync Signals to Database")
    print("=" * 70)

    if not DB_URL:
        print("  ERROR: DB_URL not found in .env")
        return

    print(f"  DB: {DB_URL.split('@')[1].split('/')[0]}")

    init_schema()

    signals_df, watchlist_df = load_signals_csvs()
    print(f"  Loaded signals_latest: {len(signals_df)} rows")
    print(f"  Loaded watchlist: {len(watchlist_df)} rows")

    setups_df = build_trade_setups(watchlist_df)
    summary_df = build_summary(signals_df, watchlist_df)

    conn = get_conn()

    # Insert signals_historical
    sig_cols = ["scan_time_utc", "signal_bar_utc", "symbol", "signal_type", "quality",
                "gates_passed", "session", "gate_session_ok", "gate_adx_ok",
                "gate_direction_ok", "gate_rsi_ok", "gate_bull_4h_ok",
                "price_at_signal", "rsi_15m", "adx_4h", "atr_15m",
                "sl_price", "tp_price", "sl_distance_pct", "tp_distance_pct",
                "risk_reward", "log_ret_4h", "bull_4h_ema", "action", "age_hours"]
    n1 = insert_df(conn, signals_df, "signals_historical", sig_cols)
    print(f"  Inserted {n1} rows into signals_historical")

    # Insert watchlist_current
    wl_cols = ["scan_time_utc", "symbol", "last_bar_utc", "current_price", "rsi_15m_now",
               "adx_4h_now", "atr_15m_now", "session_now", "h4_direction", "h4_ema_trend",
               "regime", "signal_type", "quality", "gates_passed", "sl_level", "tp_level", "notes"]
    n2 = insert_df(conn, watchlist_df, "watchlist_current", wl_cols)
    print(f"  Inserted {n2} rows into watchlist_current")

    # Insert trade_setups
    setup_cols = ["scan_time_utc", "symbol", "current_price", "atr_15m", "entry_price",
                  "sl_price", "tp_price", "sl_distance_pct", "tp_distance_pct",
                  "risk_reward", "dollar_risk", "position_size_1x", "leveraged_notional",
                  "qty_contracts", "status", "rsi", "adx", "session", "h4_dir"]
    n3 = insert_df(conn, setups_df, "trade_setups", setup_cols)
    print(f"  Inserted {n3} rows into trade_setups")

    # Insert summary
    sum_cols = ["scan_time_utc", "symbol", "total_confirmed", "total_approaching",
                "total_offsession", "last_signal_time", "last_signal_price",
                "last_signal_type", "current_recommendation"]
    n4 = insert_df(conn, summary_df, "signals_summary", sum_cols)
    print(f"  Inserted {n4} rows into signals_summary")

    conn.close()
    print(f"\n  Sync complete.")


if __name__ == "__main__":
    sync()
