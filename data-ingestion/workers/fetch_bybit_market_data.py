"""
Fetch Bybit market microstructure data for all symbols.
Data types: funding_rate, open_interest, orderbook (L2 snapshot)
Usage: python data/bybit/workers/fetch_bybit_market_data.py [--data-types funding oi orderbook] [--dry-run]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bybit_market_data_core import run_symbol, SYMBOLS, DELAY

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bybit V5 market data ingestion")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help=f"Symbols (default: {SYMBOLS})")
    parser.add_argument("--data-types", nargs="+", default=["funding", "oi", "orderbook"], choices=["funding", "oi", "orderbook"], help="Data types to fetch")
    parser.add_argument("--oi-interval", default="1h", help="Open interest interval (5min,15min,30min,1h,4h,1d)")
    parser.add_argument("--depth", type=int, default=50, help="Orderbook depth levels to store")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=DELAY)
    parser.add_argument("--testnet", action="store_true")
    args = parser.parse_args()

    for sym in args.symbols:
        run_symbol(sym, data_types=args.data_types, oi_interval=args.oi_interval, depth=args.depth, dry_run=args.dry_run, delay=args.delay, testnet=args.testnet)
        print()
