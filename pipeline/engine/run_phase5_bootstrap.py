"""
Phase 5 Bootstrap Script
========================
Download 90 days of 15m OHLCV, generate triple-barrier labels,
and bootstrap pipeline_brain.py models with historical data.

Usage:
    python run_phase5_bootstrap.py [--symbol BTCUSDT] [--days 90]

Requires DB_URL env var (or .env file).
"""
import os, sys, argparse
from pathlib import Path

_engine = Path(__file__).resolve().parent
sys.path.insert(0, str(_engine))

import psycopg2
from freqtrade_validator import download_ohlcv, triple_barrier_label, build_features, bootstrap_brain

DB_URL = os.environ.get("DB_URL")
if not DB_URL:
    env = _engine.parent / ".env"
    if env.exists():
        with open(env) as f:
            for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.strip().split("=", 1)
                    if k == "DB_URL":
                        DB_URL = v
                        break


def connect():
    try:
        return psycopg2.connect(DB_URL, sslmode="prefer")
    except psycopg2.OperationalError:
        return psycopg2.connect(DB_URL.replace(":5432/", ":6543/"), sslmode="prefer")


def main():
    parser = argparse.ArgumentParser(description="Phase 5 Triple-Barrier Bootstrap")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to bootstrap (default: BTCUSDT)")
    parser.add_argument("--days", type=int, default=90, help="Days of OHLCV to download (default: 90)")
    parser.add_argument("--all", action="store_true", help="Bootstrap all tracked symbols")
    args = parser.parse_args()

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"] if args.all else [args.symbol]

    conn = connect()
    try:
        for sym in symbols:
            print(f"\n=== Bootstrapping {sym} ===")
            df = download_ohlcv(sym, "15", args.days)
            if df is None or len(df) < 500:
                print(f"  Skipped: insufficient data")
                continue
            labeled = triple_barrier_label(df)
            labeled = build_features(labeled)
            diag = bootstrap_brain(conn, labeled, sym)
            print(f"  Result: {diag}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
