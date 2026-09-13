"""
Enrich DOGEUSDT OHLCV with OI + funding.
Usage: python data/bybit/workers/DOGEUSDT/enrich.py [--dry-run] [--tfs 4h 1h ...]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bybit_enrich_core import run_symbol

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tfs", nargs="+", default=["4h","1h","30m","15m","5m"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_symbol("DOGEUSDT", tfs=args.tfs, dry_run=args.dry_run)
