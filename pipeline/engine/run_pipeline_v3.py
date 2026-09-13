"""
V3 Pipeline Entry Point — Shadow Mode
======================================
Runs the V3 shadow mode pipeline alongside the V2 live pipeline.

Usage:
    # Single run
    python engine/run_pipeline_v3.py

    # Continuous loop
    python engine/run_pipeline_v3.py --loop --interval 900

    # Run both V2 and V3 in parallel
    python engine/run_pipeline_v3.py --with-v2 --loop
"""
import sys
import os
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

_engine = Path(__file__).resolve().parent
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent

sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_engine / "v3"))

# Load .env
_env = _repo / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

from v3.orchestrator import run_v3_shadow_iteration, run_v3_shadow_loop

# Start health server on port 8201 (V2 uses 8200)
from pipeline_health_server import start_health_server
HEALTH_PORT = int(os.environ.get("PIPELINE_HEALTH_PORT", "8201"))
start_health_server(port=HEALTH_PORT)


def run_dual_mode(interval_sec: int = 900):
    """Run both V2 (live) and V3 (shadow) in the same loop."""
    print(f"[Dual] V2 (LIVE) + V3 (SHADOW) loop started. Interval: {interval_sec}s")

    # Import V2 pipeline
    from pipeline_flow import run_pipeline_iteration

    while True:
        t0 = time.time()

        # V2: Live pipeline
        print("\n=== V2 LIVE ===")
        try:
            v2_result = run_pipeline_iteration()
            print(f"  V2: {v2_result.get('status', 'OK')}")
        except Exception as e:
            print(f"  V2 ERROR: {e}")

        # V3: Shadow pipeline
        print("\n=== V3 SHADOW ===")
        try:
            v3_result = run_v3_shadow_iteration()
            print(f"  V3: {v3_result.get('status', 'OK')}")
        except Exception as e:
            print(f"  V3 ERROR: {e}")

        elapsed = time.time() - t0
        sleep_sec = max(0, interval_sec - elapsed)
        print(f"\n[Dual] Cycle took {elapsed:.1f}s, sleeping {sleep_sec:.0f}s...")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V3 Shadow Mode Pipeline")
    parser.add_argument("--loop", action="store_true", help="Run in continuous loop")
    parser.add_argument("--interval", type=int, default=900, help="Loop interval in seconds")
    parser.add_argument("--with-v2", action="store_true", help="Run V2 (live) and V3 (shadow) in parallel")
    args = parser.parse_args()

    if args.with_v2:
        run_dual_mode(args.interval)
    elif args.loop:
        run_v3_shadow_loop(args.interval)
    else:
        result = run_v3_shadow_iteration()
        import json
        print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
