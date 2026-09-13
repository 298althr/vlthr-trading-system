"""
Enrich all Bybit OHLCV data with open_interest and funding_rate.
Usage: python data/bybit/workers/fetch_bybit_enrich.py [--dry-run] [--symbols BTCUSDT ETHUSDT ...] [--tfs 4h 1h ...]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bybit_enrich_core import run_symbol

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","DOGEUSDT"]
TFS = ["4h","1h","30m","15m","5m"]

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build enriched Bybit parquet data")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--tfs", nargs="+", default=TFS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for sym in args.symbols:
        run_symbol(sym, tfs=args.tfs, dry_run=args.dry_run)
        print()
