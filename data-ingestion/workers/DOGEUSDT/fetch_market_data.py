"""
Fetch Bybit market data for DOGEUSDT.
Usage: python data/bybit/workers/DOGEUSDT/fetch_market_data.py [--data-types funding oi orderbook] [--dry-run]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bybit_market_data_core import run_symbol

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bybit market data for DOGEUSDT")
    parser.add_argument("--data-types", nargs="+", default=["funding", "oi", "orderbook"], choices=["funding", "oi", "orderbook"])
    parser.add_argument("--oi-interval", default="1h")
    parser.add_argument("--depth", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--testnet", action="store_true")
    args = parser.parse_args()
    run_symbol("DOGEUSDT", data_types=args.data_types, oi_interval=args.oi_interval, depth=args.depth, dry_run=args.dry_run, delay=args.delay, testnet=args.testnet)
