"""
Fetch Bybit OHLCV for BNBUSDT — all timeframes (4h, 1h, 30m, 15m, 5m)
Usage:  python data/bybit/workers/BNBUSDT/fetch_BNBUSDT.py [--dry-run] [--delay 1.0]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bybit_ingest_core import run_symbol

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bybit ingestion for BNBUSDT")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD override")
    parser.add_argument("--testnet", action="store_true")
    args = parser.parse_args()
    run_symbol("BNBUSDT", dry_run=args.dry_run, delay=args.delay, start=args.start, testnet=args.testnet)
